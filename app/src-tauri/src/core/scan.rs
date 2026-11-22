//! Scan service: find the JPEGs of a folder, match `metadata.csv` rows and register them.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use rusqlite::Connection;

use super::metadata::{check_items, read_source, Item, DEFAULT_AI_TITLE_LABEL};
use super::state::{self, ProgressFn, ScanResult};
use super::validate::validate_image;
use crate::error::{AppError, AppResult};

pub const METADATA_FILE: &str = "metadata.csv";

fn is_hidden(name: &std::ffi::OsStr) -> bool {
    name.to_string_lossy().starts_with('.')
}

fn is_jpeg_name(path: &Path) -> bool {
    path.extension()
        .map(|ext| ext.to_string_lossy().to_lowercase())
        .is_some_and(|ext| ext == "jpg" || ext == "jpeg")
}

fn walk(dir: &Path, recursive: bool, found: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        if is_hidden(&entry.file_name()) {
            continue;
        }
        let path = entry.path();
        let is_dir = entry.file_type().is_ok_and(|kind| kind.is_dir());
        if is_dir {
            if recursive {
                walk(&path, recursive, found);
            }
        } else if path.is_file() && is_jpeg_name(&path) {
            found.push(path.canonicalize().unwrap_or(path));
        }
    }
}

/// JPEGs under `root` (at any depth unless not recursive); hidden files and folders (macOS `._x`) are skipped.
pub fn find_images(root: &Path, recursive: bool) -> Vec<PathBuf> {
    let mut found = Vec::new();
    walk(root, recursive, &mut found);
    found.sort();
    found
}

/// `metadata.csv` files next to the images (shown to the user).
pub fn metadata_files(images: &[PathBuf]) -> Vec<PathBuf> {
    let sources: HashSet<PathBuf> =
        images.iter().filter_map(|image| image.parent()).map(|dir| dir.join(METADATA_FILE)).collect();
    let mut present: Vec<PathBuf> = sources.into_iter().filter(|source| source.exists()).collect();
    present.sort();
    present
}

pub struct LoadedMetadata {
    pub items: HashMap<PathBuf, Item>,
    pub problems: HashMap<PathBuf, Vec<String>>,
}

/// Matches images to the rows of the `metadata.csv` next to each image, and checks those rows.
pub fn load_metadata(images: &[PathBuf]) -> AppResult<LoadedMetadata> {
    let mut rows_by_source: HashMap<PathBuf, HashMap<String, Item>> = HashMap::new();
    let mut loaded = LoadedMetadata { items: HashMap::new(), problems: HashMap::new() };
    for image in images {
        let Some(parent) = image.parent() else { continue };
        let source = parent.join(METADATA_FILE);
        if !rows_by_source.contains_key(&source) {
            let rows = if source.exists() {
                read_source(&source).map_err(AppError::Check)?.into_iter().map(|item| (item.filename.clone(), item)).collect()
            } else {
                HashMap::new()
            };
            rows_by_source.insert(source.clone(), rows);
        }
        let name = image.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
        if let Some(item) = rows_by_source[&source].get(&name) {
            let existing = HashSet::from([item.filename.clone()]);
            loaded.problems.insert(image.clone(), check_items(std::slice::from_ref(item), &existing, DEFAULT_AI_TITLE_LABEL));
            loaded.items.insert(image.clone(), item.clone());
        }
    }
    Ok(loaded)
}

pub struct ScanReport {
    pub result: ScanResult,
    pub found: usize,
    pub stopped: bool,
}

/// Registers the images of `root` (from `find_images`), splits new ones into batches and logs it.
pub fn scan(
    conn: &mut Connection,
    root: &Path,
    images: &[PathBuf],
    batch_size: usize,
    on_progress: ProgressFn,
    should_stop: &dyn Fn() -> bool,
) -> AppResult<ScanReport> {
    let loaded = load_metadata(images)?;
    let check = |image: &Path| {
        let mut problems = validate_image(image);
        problems.extend(loaded.problems.get(image).cloned().unwrap_or_default());
        problems
    };
    let result = state::scan_folder(conn, root, images, &loaded.items, &check, Some(batch_size), on_progress, should_stop)?;
    state::log_event(
        conn,
        "scan",
        &format!(
            "{}: added {}, need metadata {}, unchanged {}, invalid {}, new batches {}",
            root.display(),
            result.added,
            result.needs_metadata,
            result.unchanged,
            result.invalid,
            result.batches_created
        ),
        "info",
        None,
    )?;
    for (path, error) in state::invalid_files(conn, root)? {
        let name = Path::new(&path).file_name().map(|name| name.to_string_lossy().into_owned());
        state::log_event(conn, "scan", &format!("invalid: {error}"), "warning", name.as_deref())?;
    }
    for name in &result.changed_after_upload {
        state::log_event(conn, "scan", "changed after upload; not re-uploaded", "warning", Some(name))?;
    }
    Ok(ScanReport { found: images.len(), stopped: should_stop(), result })
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{codecs::jpeg::JpegEncoder, ExtendedColorType};
    use rusqlite::params;

    fn write_jpeg(path: &Path, shade: u8) {
        let pixels = vec![shade; 2000 * 2000 * 3];
        let file = std::fs::File::create(path).unwrap();
        JpegEncoder::new(std::io::BufWriter::new(file))
            .encode(&pixels, 2000, 2000, ExtendedColorType::Rgb8)
            .unwrap();
    }

    fn folder_with(root: &Path, name: &str, files: &[(&str, u8)]) -> PathBuf {
        let folder = root.join(name);
        std::fs::create_dir_all(&folder).unwrap();
        for (file, shade) in files {
            write_jpeg(&folder.join(file), *shade);
        }
        folder.canonicalize().unwrap()
    }

    fn run_scan(conn: &mut Connection, folder: &Path, batch_size: usize) -> ScanReport {
        let images = find_images(folder, true);
        scan(conn, folder, &images, batch_size, &mut |_, _, _| {}, &|| false).unwrap()
    }

    fn count(conn: &Connection, sql: &str) -> i64 {
        conn.query_row(sql, [], |row| row.get(0)).unwrap()
    }

    #[test]
    fn new_files_wait_for_metadata_and_a_rescan_changes_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let folder = folder_with(dir.path(), "f1", &[("a.jpg", 10), ("b.jpg", 60)]);
        let mut conn = state::open_db(&dir.path().join("s.db")).unwrap();

        let first = run_scan(&mut conn, &folder, 1000);
        assert_eq!((first.found, first.result.needs_metadata, first.result.invalid), (2, 2, 0));

        let again = run_scan(&mut conn, &folder, 1000);
        assert_eq!((again.result.unchanged, again.result.needs_metadata), (2, 0));
        assert_eq!(count(&conn, "SELECT COUNT(*) FROM files WHERE status = 'needs_metadata'"), 2);
    }

    #[test]
    fn metadata_csv_makes_files_pending_and_batches_them() {
        let dir = tempfile::tempdir().unwrap();
        let folder = folder_with(dir.path(), "f1", &[("a.jpg", 10), ("b.jpg", 60), ("c.jpg", 110)]);
        std::fs::write(
            folder.join(METADATA_FILE),
            "filename,title,keywords\na.jpg,Title a,\"a, b\"\nb.jpg,Title b,\"a, b\"\nc.jpg,Title c,\"a, b\"\n",
        )
        .unwrap();
        let mut conn = state::open_db(&dir.path().join("s.db")).unwrap();

        let report = run_scan(&mut conn, &folder, 2);
        assert_eq!((report.result.added, report.result.batches_created), (3, 2));
        assert_eq!(count(&conn, "SELECT COUNT(*) FROM files WHERE status = 'pending'"), 3);
        assert_eq!(count(&conn, "SELECT COUNT(*) FROM batches"), 2);
    }

    #[test]
    fn identical_content_in_two_folders_is_an_invalid_duplicate() {
        let dir = tempfile::tempdir().unwrap();
        let first = folder_with(dir.path(), "f1", &[("a.jpg", 10)]);
        let second = folder_with(dir.path(), "f2", &[("a.jpg", 10)]);
        let mut conn = state::open_db(&dir.path().join("s.db")).unwrap();
        run_scan(&mut conn, &first, 1000);
        let report = run_scan(&mut conn, &second, 1000);
        assert_eq!(report.result.invalid, 1);
        let error: String = conn.query_row("SELECT error FROM files WHERE status = 'invalid'", [], |r| r.get(0)).unwrap();
        assert!(error.starts_with("same content as "), "{error}");
    }

    #[test]
    fn same_name_different_content_gets_a_unique_remote_name() {
        let dir = tempfile::tempdir().unwrap();
        let first = folder_with(dir.path(), "f1", &[("a.jpg", 10)]);
        let second = folder_with(dir.path(), "f2", &[("a.jpg", 200)]);
        let mut conn = state::open_db(&dir.path().join("s.db")).unwrap();
        run_scan(&mut conn, &first, 1000);
        run_scan(&mut conn, &second, 1000);
        let names: Vec<String> = conn
            .prepare("SELECT filename FROM files ORDER BY id")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<Result<_, _>>()
            .unwrap();
        assert_eq!(names[0], "a.jpg");
        assert!(names[1].starts_with("a_") && names[1].ends_with(".jpg") && names[1].len() == "a_".len() + 8 + 4, "{names:?}");
    }

    #[test]
    fn scan_is_recursive_and_skips_hidden_files() {
        let dir = tempfile::tempdir().unwrap();
        let folder = folder_with(dir.path(), "f1", &[("a.jpg", 10), (".hidden.jpg", 20), ("._mac.jpg", 30)]);
        std::fs::create_dir_all(folder.join("deep")).unwrap();
        write_jpeg(&folder.join("deep/b.jpeg"), 40);
        std::fs::create_dir_all(folder.join(".cache")).unwrap();
        write_jpeg(&folder.join(".cache/c.jpg"), 50);
        let names: Vec<String> = find_images(&folder, true)
            .iter()
            .map(|path| path.strip_prefix(&folder).unwrap().display().to_string())
            .collect();
        assert_eq!(names, vec!["a.jpg", "deep/b.jpeg"]);
        assert_eq!(find_images(&folder, false).len(), 1);
    }

    #[test]
    fn invalid_files_are_logged_and_invalid_ones_are_matched_by_path() {
        let dir = tempfile::tempdir().unwrap();
        let folder = folder_with(dir.path(), "f1", &[("a.jpg", 10)]);
        std::fs::write(folder.join("bad.jpg"), b"nope").unwrap();
        let mut conn = state::open_db(&dir.path().join("s.db")).unwrap();
        let report = run_scan(&mut conn, &folder, 1000);
        assert_eq!(report.result.invalid, 1);
        let warnings: i64 = conn
            .query_row("SELECT COUNT(*) FROM events WHERE level = 'warning' AND filename = ?", params!["bad.jpg"], |r| r.get(0))
            .unwrap();
        assert_eq!(warnings, 1);
        let other = dir.path().join("f1_other");
        assert!(state::invalid_files(&conn, &other).unwrap().is_empty());
    }

    #[test]
    fn stopping_keeps_what_was_registered() {
        let dir = tempfile::tempdir().unwrap();
        let folder = folder_with(dir.path(), "f1", &[("a.jpg", 10), ("b.jpg", 60), ("c.jpg", 110)]);
        let mut conn = state::open_db(&dir.path().join("s.db")).unwrap();
        let images = find_images(&folder, true);
        let seen = std::cell::Cell::new(0);
        let stop_after_one = || seen.get() >= 1;
        let report = scan(&mut conn, &folder, &images, 1000, &mut |done, _, _| seen.set(done + 1), &stop_after_one).unwrap();
        assert!(report.stopped);
        assert_eq!(count(&conn, "SELECT COUNT(*) FROM files"), 1);
    }
}
