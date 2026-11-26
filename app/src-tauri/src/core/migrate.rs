//! Moves the data of the app's previous name (`Adobe Stock Uploader`) into the new data folder. It only
//! copies: the old folder stays as it was, and until the copy is complete and verified the app keeps using it.

use std::fs;
use std::path::{MAIN_SEPARATOR, Path, PathBuf};
use std::time::Duration;

use rusqlite::Connection;
use rusqlite::backup::Backup;

use crate::core::backup;
use crate::error::{AppError, AppResult};

const DB_NAME: &str = "state.db";
const BACKUPS: &str = "state_backups";
const EXPORT: &str = "export";
/// Present while a copy is unfinished: the new folder must not be used yet.
const MARKER: &str = ".migrating";
const PARTIAL_SUFFIX: &str = ".part";

fn holds_data(dir: &Path) -> bool {
    dir.join(DB_NAME).exists() || !backup::list_backups(&dir.join(BACKUPS), "state").is_empty()
}

/// The data folder to use: never an empty new one while the old one still holds the data.
pub fn data_dir(old: &Path, new: &Path) -> PathBuf {
    let unfinished = new.join(MARKER).exists() || (!holds_data(new) && holds_data(old));
    if !new.join(DB_NAME).exists() && unfinished { old.to_path_buf() } else { new.to_path_buf() }
}

fn partial(target: &Path) -> PathBuf {
    let mut name = target.as_os_str().to_owned();
    name.push(PARTIAL_SUFFIX);
    PathBuf::from(name)
}

/// Copies a folder. A file that already exists must have the same bytes, unless `resumed`: then it is left over
/// from our own unfinished attempt and is replaced.
fn copy_tree(from: &Path, to: &Path, resumed: bool) -> AppResult<()> {
    if !from.is_dir() {
        return Ok(());
    }
    fs::create_dir_all(to)?;
    for entry in fs::read_dir(from)? {
        let source = entry?.path();
        let Some(name) = source.file_name() else { continue };
        let target = to.join(name);
        if source.is_dir() {
            copy_tree(&source, &target, resumed)?;
        } else if target.exists() && fs::read(&source)? == fs::read(&target)? {
            continue;
        } else if target.exists() && !resumed {
            return Err(AppError::StateDb(format!("{} already exists and differs", target.display())));
        } else {
            let part = partial(&target);
            if part.exists() {
                fs::remove_file(&part)?;
            }
            fs::copy(&source, &part)?;
            fs::rename(&part, &target)?;
        }
    }
    Ok(())
}

/// Row counts of the tables that hold the user's work.
fn row_counts(conn: &Connection) -> AppResult<(i64, i64)> {
    let count = |table: &str| conn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| row.get(0));
    Ok((count("files")?, count("events")?))
}

/// Writes a verified copy of the database whose prepared copies point into the new folder.
fn copy_database(old: &Path, new: &Path) -> AppResult<()> {
    let source_path = old.join(DB_NAME);
    let broken = |path: &Path| AppError::StateDb(format!("{} failed the integrity check", path.display()));
    if !backup::integrity_ok(&source_path) {
        return Err(broken(&source_path));
    }
    let target = new.join(DB_NAME);
    let part = partial(&target);
    if part.exists() {
        fs::remove_file(&part)?;
    }
    let source = Connection::open(&source_path)?;
    {
        let mut copy = Connection::open(&part)?;
        Backup::new(&source, &mut copy)?.run_to_completion(256, Duration::ZERO, None)?;
        // databases from before the prepared copies existed have no such column
        let has_export: bool = copy.query_row(
            "SELECT COUNT(*) > 0 FROM pragma_table_info('files') WHERE name = 'export_path'",
            [],
            |row| row.get(0),
        )?;
        if has_export {
            let prefix = |dir: &Path| format!("{}{MAIN_SEPARATOR}", dir.join(EXPORT).display());
            copy.execute(
                "UPDATE files SET export_path = ?1 || substr(export_path, length(?2) + 1)
                 WHERE substr(export_path, 1, length(?2)) = ?2",
                [prefix(new), prefix(old)],
            )?;
        }
        if row_counts(&copy)? != row_counts(&source)? {
            return Err(AppError::StateDb(format!("the copy of {} is incomplete", source_path.display())));
        }
    }
    if !backup::integrity_ok(&part) {
        return Err(broken(&part));
    }
    fs::rename(&part, &target)?;
    Ok(())
}

/// Copies database, backups and prepared pictures from `old` to `new`; `false` when there is nothing to do.
pub fn migrate_data_dir(old: &Path, new: &Path) -> AppResult<bool> {
    let marker = new.join(MARKER);
    if new.join(DB_NAME).exists() {
        if marker.exists() {
            fs::remove_file(marker)?;
        }
        return Ok(false);
    }
    if !old.join(DB_NAME).exists() {
        return Ok(false);
    }
    let resumed = marker.exists();
    fs::create_dir_all(new)?;
    fs::write(&marker, "")?;
    copy_tree(&old.join(BACKUPS), &new.join(BACKUPS), resumed)?;
    copy_tree(&old.join(EXPORT), &new.join(EXPORT), resumed)?;
    copy_database(old, new)?;
    fs::remove_file(marker)?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use std::fs;

    use rusqlite::Connection;

    use super::*;

    fn old_data(root: &Path) -> (PathBuf, PathBuf) {
        let (old, new) = (root.join("Adobe Stock Uploader"), root.join("Stock Uploader"));
        fs::create_dir_all(old.join("export")).unwrap();
        fs::create_dir_all(old.join("state_backups")).unwrap();
        fs::write(old.join("export/a.jpg"), "copy").unwrap();
        fs::write(old.join("state_backups/state_20260101T000000000000.bak"), "backup").unwrap();
        let conn = Connection::open(old.join("state.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE files (path TEXT, export_path TEXT); CREATE TABLE events (message TEXT);
             INSERT INTO events VALUES ('one');",
        )
        .unwrap();
        let copy = old.join("export/a.jpg").display().to_string();
        for (path, export) in [("/pictures/a.jpg", copy.as_str()), ("/pictures/b.jpg", "/elsewhere/b.jpg")] {
            conn.execute("INSERT INTO files VALUES (?1, ?2)", [path, export]).unwrap();
        }
        (old, new)
    }

    fn rows(db: &Path) -> Vec<(String, String)> {
        let conn = Connection::open(db).unwrap();
        let mut stmt = conn.prepare("SELECT path, export_path FROM files ORDER BY path").unwrap();
        stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?))).unwrap().map(Result::unwrap).collect()
    }

    #[test]
    fn copies_everything_repoints_the_prepared_copies_and_leaves_the_old_folder_alone() {
        let root = tempfile::tempdir().unwrap();
        let (old, new) = old_data(root.path());
        let before = rows(&old.join("state.db"));

        assert!(migrate_data_dir(&old, &new).unwrap());

        let moved = rows(&new.join("state.db"));
        assert_eq!(moved[0], ("/pictures/a.jpg".into(), new.join("export/a.jpg").display().to_string()));
        assert_eq!(moved[1], ("/pictures/b.jpg".into(), "/elsewhere/b.jpg".into()));
        assert_eq!(fs::read_to_string(new.join("export/a.jpg")).unwrap(), "copy");
        assert!(new.join("state_backups/state_20260101T000000000000.bak").exists());
        assert_eq!(rows(&old.join("state.db")), before);
        assert_eq!(data_dir(&old, &new), new);
    }

    #[test]
    fn never_overwrites_an_existing_new_database() {
        let root = tempfile::tempdir().unwrap();
        let (old, new) = old_data(root.path());
        fs::create_dir_all(&new).unwrap();
        fs::write(new.join("state.db"), "mine").unwrap();

        assert!(!migrate_data_dir(&old, &new).unwrap());

        assert_eq!(fs::read_to_string(new.join("state.db")).unwrap(), "mine");
        assert_eq!(data_dir(&old, &new), new);
    }

    #[test]
    fn a_failed_copy_keeps_the_old_folder_in_use_and_can_be_repeated() {
        let root = tempfile::tempdir().unwrap();
        let (old, new) = old_data(root.path());
        fs::create_dir_all(new.join("export")).unwrap();
        fs::write(new.join("export/a.jpg"), "something else").unwrap();

        assert!(migrate_data_dir(&old, &new).is_err());
        assert!(!new.join("state.db").exists());
        assert_eq!(data_dir(&old, &new), old);

        fs::remove_file(new.join("export/a.jpg")).unwrap();
        assert!(migrate_data_dir(&old, &new).unwrap());
        assert_eq!(data_dir(&old, &new), new);
    }

    #[test]
    fn a_prepared_copy_changed_after_an_unfinished_attempt_replaces_the_stale_one() {
        let root = tempfile::tempdir().unwrap();
        let (old, new) = old_data(root.path());
        fs::create_dir_all(new.join("export")).unwrap();
        fs::write(new.join(".migrating"), "").unwrap();
        fs::write(new.join("export/a.jpg"), "stale").unwrap();

        assert!(migrate_data_dir(&old, &new).unwrap());

        assert_eq!(fs::read_to_string(new.join("export/a.jpg")).unwrap(), "copy");
    }

    #[test]
    fn a_corrupt_old_database_is_not_copied() {
        let root = tempfile::tempdir().unwrap();
        let (old, new) = old_data(root.path());
        fs::write(old.join("state.db"), "not a database").unwrap();

        assert!(migrate_data_dir(&old, &new).is_err());
        assert_eq!(data_dir(&old, &new), old);
    }

    #[test]
    fn old_backups_without_a_database_stay_in_use_so_the_missing_database_is_reported() {
        let root = tempfile::tempdir().unwrap();
        let (old, new) = old_data(root.path());
        fs::remove_file(old.join("state.db")).unwrap();

        assert!(!migrate_data_dir(&old, &new).unwrap());
        assert_eq!(data_dir(&old, &new), old);
    }

    #[test]
    fn a_first_install_uses_the_new_folder() {
        let root = tempfile::tempdir().unwrap();
        let (old, new) = (root.path().join("old"), root.path().join("new"));

        assert!(!migrate_data_dir(&old, &new).unwrap());
        assert_eq!(data_dir(&old, &new), new);
        assert!(!new.exists());
    }
}
