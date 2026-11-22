//! Preparing a batch for upload: a copy of every pending picture with its title and keywords embedded
//! (replaces the Adobe CSV). The database stays the source of truth; the copies are only output.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use rusqlite::{params, Connection};
use serde::Serialize;
use sha2::{Digest, Sha256};

use super::embed::{self, EmbedMeta};
use super::metadata_csv::{check_items, Item};
use super::state;
use crate::error::AppResult;

/// Bump when what is written into the copies changes, so existing copies count as stale.
const EMBED_FORMAT: &str = "1";
const FIELD_SEPARATOR: char = '\u{1f}';
const KEYWORD_SEPARATOR: char = '\u{1e}';

#[derive(Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PrepareReport {
    pub batch: i64,
    pub total: usize,
    /// Copies written (or rewritten) now.
    pub prepared: usize,
    /// Copies that were already up to date.
    pub current: usize,
    /// Metadata Adobe would not accept; nothing is written while there are any.
    pub problems: Vec<String>,
    /// Files whose copy could not be written.
    pub failed: Vec<String>,
    pub stopped: bool,
}

/// Whether a batch may be uploaded: every pending file needs a copy made from its current metadata.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum ExportReadiness {
    Ready,
    /// A file has no copy yet (or the copy is gone).
    Missing,
    /// A title or keywords changed after the copy was made.
    Stale,
}

/// Fingerprint of everything embedded in a copy; it changes when the metadata or the format does.
pub fn metadata_fingerprint(title: &str, keywords: &[String]) -> String {
    let text = format!("{EMBED_FORMAT}{FIELD_SEPARATOR}{title}{FIELD_SEPARATOR}{}", keywords.join(&KEYWORD_SEPARATOR.to_string()));
    Sha256::digest(text.as_bytes()).iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Where the copy of `filename` from `batch_id` lives.
pub fn export_path_for(export_dir: &Path, batch_id: i64, filename: &str) -> PathBuf {
    export_dir.join(batch_id.to_string()).join(filename)
}

/// A pending file with what its copy was made from.
struct PendingRow {
    source: PathBuf,
    filename: String,
    title: String,
    keywords: Vec<String>,
    export_path: String,
    embedded_hash: String,
}

impl PendingRow {
    fn fingerprint(&self) -> String {
        metadata_fingerprint(&self.title, &self.keywords)
    }

    fn item(&self) -> Item {
        Item {
            filename: self.filename.clone(),
            title: self.title.clone(),
            keywords: self.keywords.clone(),
            category: String::new(),
        }
    }
}

fn pending_rows(conn: &Connection, batch_id: i64) -> AppResult<Vec<PendingRow>> {
    let mut stmt = conn.prepare(
        "SELECT path, filename, title, keywords, export_path, embedded_hash FROM files
         WHERE status = ? AND batch_id = ? ORDER BY id",
    )?;
    let rows = stmt.query_map(params![state::PENDING, batch_id], |row| {
        Ok(PendingRow {
            source: PathBuf::from(row.get::<_, String>(0)?),
            filename: row.get(1)?,
            title: row.get(2)?,
            keywords: state::parse_keywords(&row.get::<_, String>(3)?),
            export_path: row.get(4)?,
            embedded_hash: row.get(5)?,
        })
    })?;
    Ok(rows.collect::<Result<_, _>>()?)
}

fn record_export(conn: &Connection, source: &Path, copy: &Path, fingerprint: &str) -> AppResult<()> {
    conn.execute(
        "UPDATE files SET export_path = ?, embedded_hash = ?, error = NULL WHERE path = ?",
        params![copy.to_string_lossy(), fingerprint, source.to_string_lossy()],
    )?;
    Ok(())
}

/// Makes (or refreshes) the copy of every pending file of `batch_id` under `export_dir`, with its title and
/// keywords embedded. Copies that already match the database are left alone. Invalid metadata blocks the whole
/// batch, as it did for the CSV.
pub fn prepare_batch(
    conn: &Connection,
    batch_id: i64,
    export_dir: &Path,
    ai_label: &str,
    on_progress: &mut dyn FnMut(usize, usize, &str),
    should_stop: &dyn Fn() -> bool,
) -> AppResult<PrepareReport> {
    let rows = pending_rows(conn, batch_id)?;
    let total = rows.len();
    let mut report = PrepareReport { batch: batch_id, total, ..PrepareReport::default() };
    if rows.is_empty() {
        return Ok(report);
    }
    let items: Vec<Item> = rows.iter().map(PendingRow::item).collect();
    let names: HashSet<String> = rows.iter().map(|row| row.filename.clone()).collect();
    report.problems = check_items(&items, &names, ai_label);
    if !report.problems.is_empty() {
        let message = format!("batch {batch_id}: not prepared, {} problems", report.problems.len());
        state::log_event(conn, "prepare", &message, "warning", None)?;
        return Ok(report);
    }
    state::log_event(conn, "prepare", &format!("batch {batch_id}: started, {total} files"), "info", None)?;
    for (index, row) in rows.iter().enumerate() {
        if should_stop() {
            report.stopped = true;
            break;
        }
        on_progress(index, total, &row.filename);
        let copy = export_path_for(export_dir, batch_id, &row.filename);
        let fingerprint = row.fingerprint();
        if row.embedded_hash == fingerprint && row.export_path == copy.to_string_lossy() && copy.exists() {
            report.current += 1;
            continue;
        }
        let meta = EmbedMeta { title: row.title.clone(), description: row.title.clone(), keywords: row.keywords.clone() };
        match embed::write_embedded(&row.source, &copy, &meta) {
            Ok(()) => {
                record_export(conn, &row.source, &copy, &fingerprint)?;
                report.prepared += 1;
            }
            Err(error) => {
                state::log_event(conn, "prepare", &error.to_string(), "error", Some(&row.filename))?;
                report.failed.push(format!("{}: {error}", row.filename));
            }
        }
    }
    on_progress(total, total, "");
    let level = if report.failed.is_empty() { "info" } else { "error" };
    let outcome = format!(
        "batch {batch_id}: {}, {} prepared, {} already current, {} failed",
        if report.stopped { "cancelled" } else { "finished" },
        report.prepared,
        report.current,
        report.failed.len()
    );
    state::log_event(conn, "prepare", &outcome, level, None)?;
    Ok(report)
}

pub fn export_readiness(conn: &Connection, batch_id: i64) -> AppResult<ExportReadiness> {
    let mut readiness = ExportReadiness::Ready;
    for row in pending_rows(conn, batch_id)? {
        let has_copy = !row.embedded_hash.is_empty() && !row.export_path.is_empty() && Path::new(&row.export_path).exists();
        if !has_copy {
            return Ok(ExportReadiness::Missing);
        }
        if row.embedded_hash != row.fingerprint() {
            readiness = ExportReadiness::Stale;
        }
    }
    Ok(readiness)
}

/// Why the batch cannot be uploaded; empty when it can.
pub fn readiness_text(batch_id: i64, readiness: ExportReadiness) -> String {
    match readiness {
        ExportReadiness::Ready => String::new(),
        ExportReadiness::Missing => {
            format!("У батча {batch_id} нет подготовленных файлов: сначала подготовьте батч к загрузке.")
        }
        ExportReadiness::Stale => format!(
            "Подготовленные файлы батча {batch_id} устарели (название или ключевые слова изменились): подготовьте батч заново."
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::embed::read_embedded;
    use crate::core::state;
    use image::{ExtendedColorType, codecs::jpeg::JpegEncoder};

    const LABEL: &str = "Generative AI";

    fn write_jpeg(path: &Path, shade: u8) {
        let pixels = vec![shade; 16 * 16 * 3];
        let file = std::fs::File::create(path).unwrap();
        JpegEncoder::new(std::io::BufWriter::new(file)).encode(&pixels, 16, 16, ExtendedColorType::Rgb8).unwrap();
    }

    fn setup(count: usize) -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = state::open_db(&dir.path().join("state.db")).unwrap();
        conn.execute("INSERT INTO batches (folder, part, created_at) VALUES ('/r', 1, 't')", []).unwrap();
        for index in 0..count {
            let path = dir.path().join(format!("p{index}.jpg"));
            write_jpeg(&path, index as u8 * 40);
            conn.execute(
                "INSERT INTO files (path, filename, size, sha256, root, status, batch_id, title, keywords, category, updated_at)
                 VALUES (?, ?, 5, ?, '/r', 'pending', 1, ?, '[\"sea\", \"surf\"]', '', 't')",
                rusqlite::params![
                    path.to_string_lossy(),
                    format!("p{index}.jpg"),
                    format!("h{index}"),
                    format!("Photo {index} Generative AI")
                ],
            )
            .unwrap();
        }
        (dir, conn)
    }

    fn prepare(conn: &Connection, dir: &Path) -> PrepareReport {
        prepare_batch(conn, 1, &dir.join("export"), LABEL, &mut |_, _, _| {}, &|| false).unwrap()
    }

    #[test]
    fn writes_a_copy_per_file_records_it_and_leaves_the_originals_and_statuses_alone() {
        let (dir, conn) = setup(2);
        let originals: Vec<Vec<u8>> = (0..2).map(|i| std::fs::read(dir.path().join(format!("p{i}.jpg"))).unwrap()).collect();
        let report = prepare(&conn, dir.path());
        assert_eq!((report.total, report.prepared, report.current, report.failed.len()), (2, 2, 0, 0));
        assert!(report.problems.is_empty(), "{:?}", report.problems);
        let copy = export_path_for(&dir.path().join("export"), 1, "p0.jpg");
        assert_eq!(read_embedded(&copy).unwrap().xmp.title.as_deref(), Some("Photo 0 Generative AI"));
        assert_eq!(read_embedded(&copy).unwrap().iptc.keywords, vec!["sea", "surf"]);
        for (index, original) in originals.iter().enumerate() {
            assert_eq!(&std::fs::read(dir.path().join(format!("p{index}.jpg"))).unwrap(), original);
        }
        let stored: String = conn.query_row("SELECT export_path FROM files WHERE filename = 'p0.jpg'", [], |r| r.get(0)).unwrap();
        assert_eq!(stored, copy.to_string_lossy());
        let statuses: Vec<String> = conn.prepare("SELECT status FROM files").unwrap().query_map([], |r| r.get(0)).unwrap().map(|r| r.unwrap()).collect();
        assert!(statuses.iter().all(|status| status == "pending"));
        assert_eq!(export_readiness(&conn, 1).unwrap(), ExportReadiness::Ready);
    }

    #[test]
    fn a_batch_is_missing_until_prepared_and_again_when_a_copy_disappears() {
        let (dir, conn) = setup(2);
        assert_eq!(export_readiness(&conn, 1).unwrap(), ExportReadiness::Missing);
        prepare(&conn, dir.path());
        std::fs::remove_file(export_path_for(&dir.path().join("export"), 1, "p1.jpg")).unwrap();
        assert_eq!(export_readiness(&conn, 1).unwrap(), ExportReadiness::Missing);
    }

    #[test]
    fn editing_metadata_makes_the_batch_stale_and_only_that_copy_is_rewritten() {
        let (dir, conn) = setup(2);
        prepare(&conn, dir.path());
        conn.execute("UPDATE files SET title = 'Edited later Generative AI' WHERE filename = 'p0.jpg'", []).unwrap();
        assert_eq!(export_readiness(&conn, 1).unwrap(), ExportReadiness::Stale);
        let report = prepare(&conn, dir.path());
        assert_eq!((report.prepared, report.current), (1, 1));
        let copy = export_path_for(&dir.path().join("export"), 1, "p0.jpg");
        assert_eq!(read_embedded(&copy).unwrap().xmp.title.as_deref(), Some("Edited later Generative AI"));
        assert_eq!(export_readiness(&conn, 1).unwrap(), ExportReadiness::Ready);
    }

    #[test]
    fn preparing_again_without_changes_rewrites_nothing() {
        let (dir, conn) = setup(2);
        prepare(&conn, dir.path());
        let report = prepare(&conn, dir.path());
        assert_eq!((report.prepared, report.current), (0, 2));
    }

    #[test]
    fn invalid_metadata_blocks_the_whole_batch_and_writes_nothing() {
        let (dir, conn) = setup(2);
        conn.execute("UPDATE files SET keywords = '[]' WHERE filename = 'p1.jpg'", []).unwrap();
        let report = prepare(&conn, dir.path());
        assert!(report.problems.iter().any(|problem| problem.contains("p1.jpg") && problem.contains("no keywords")), "{:?}", report.problems);
        assert_eq!(report.prepared, 0);
        assert!(!dir.path().join("export").exists());
        let hashes: i64 = conn.query_row("SELECT COUNT(*) FROM files WHERE embedded_hash != ''", [], |r| r.get(0)).unwrap();
        assert_eq!(hashes, 0);
    }

    #[test]
    fn a_missing_original_fails_that_file_and_the_rest_go_on() {
        let (dir, conn) = setup(3);
        std::fs::remove_file(dir.path().join("p1.jpg")).unwrap();
        let report = prepare(&conn, dir.path());
        assert_eq!((report.prepared, report.failed.len()), (2, 1));
        assert!(report.failed[0].starts_with("p1.jpg"), "{:?}", report.failed);
        assert_eq!(export_readiness(&conn, 1).unwrap(), ExportReadiness::Missing);
    }

    #[test]
    fn stopping_leaves_the_rest_unprepared() {
        let (dir, conn) = setup(3);
        let calls = std::cell::Cell::new(0);
        let stop_after_one = || {
            calls.set(calls.get() + 1);
            calls.get() > 1
        };
        let report = prepare_batch(&conn, 1, &dir.path().join("export"), LABEL, &mut |_, _, _| {}, &stop_after_one).unwrap();
        assert!(report.stopped);
        assert_eq!(report.prepared, 1);
    }

    #[test]
    fn only_pending_files_of_the_batch_are_prepared() {
        let (dir, conn) = setup(2);
        conn.execute("UPDATE files SET status = 'uploaded' WHERE filename = 'p1.jpg'", []).unwrap();
        let report = prepare(&conn, dir.path());
        assert_eq!((report.total, report.prepared), (1, 1));
        assert!(!export_path_for(&dir.path().join("export"), 1, "p1.jpg").exists());
    }

    #[test]
    fn the_fingerprint_follows_title_and_keywords() {
        let base = metadata_fingerprint("T", &["a".to_string(), "b".to_string()]);
        assert_eq!(base, metadata_fingerprint("T", &["a".to_string(), "b".to_string()]));
        assert_ne!(base, metadata_fingerprint("T2", &["a".to_string(), "b".to_string()]));
        assert_ne!(base, metadata_fingerprint("T", &["b".to_string(), "a".to_string()]));
        assert_ne!(base, metadata_fingerprint("T", &["a".to_string(), "b".to_string(), "c".to_string()]));
    }

    #[test]
    fn readiness_texts_name_the_batch() {
        assert_eq!(readiness_text(1, ExportReadiness::Ready), "");
        assert!(readiness_text(7, ExportReadiness::Missing).contains("батч"));
        assert!(readiness_text(7, ExportReadiness::Missing).contains('7'));
        assert!(readiness_text(7, ExportReadiness::Stale).contains('7'));
    }

    #[test]
    fn an_older_database_gets_the_export_columns() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.db");
        let old = Connection::open(&path).unwrap();
        old.execute_batch("CREATE TABLE files (id INTEGER PRIMARY KEY, filename TEXT, status TEXT);").unwrap();
        drop(old);
        let conn = state::open_db(&path).unwrap();
        let columns: Vec<String> = conn
            .prepare("SELECT name FROM pragma_table_info('files')")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .map(|row| row.unwrap())
            .collect();
        assert!(columns.iter().any(|name| name == "export_path") && columns.iter().any(|name| name == "embedded_hash"));
    }
}
