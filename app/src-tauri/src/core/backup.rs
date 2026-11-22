//! Backups and integrity checks for the state database (our only source of truth).

use std::fs;
use std::path::{Path, PathBuf};

use chrono::Utc;
use rusqlite::backup::Backup;
use rusqlite::Connection;

use crate::error::{AppError, AppResult};

const DEFAULT_KEEP: usize = 30;
const BACKUP_SUFFIX: &str = ".bak";
const BACKUP_DIR_ENV: &str = "ADOBE_BACKUP_DIR";
#[cfg(unix)]
const READ_ONLY: u32 = 0o444;
#[cfg(unix)]
const WRITABLE: u32 = 0o666;

fn stem_of(db: &Path) -> String {
    db.file_stem().map(|stem| stem.to_string_lossy().into_owned()).unwrap_or_default()
}

/// A sibling folder by default; ADOBE_BACKUP_DIR keeps the copies on another disk.
pub fn backup_dir_for(db: &Path) -> PathBuf {
    match std::env::var_os(BACKUP_DIR_ENV).filter(|value| !value.is_empty()) {
        Some(dir) => PathBuf::from(dir),
        None => db.with_file_name(format!("{}_backups", stem_of(db))),
    }
}

/// Backups of this database, oldest first (timestamped names sort chronologically).
pub fn list_backups(dir: &Path, stem: &str) -> Vec<PathBuf> {
    let prefix = format!("{stem}_");
    let mut found: Vec<PathBuf> = fs::read_dir(dir)
        .map(|entries| {
            entries
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| {
                    path.file_name().and_then(|name| name.to_str()).is_some_and(|name| {
                        name.starts_with(&prefix) && name.ends_with(BACKUP_SUFFIX)
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    found.sort();
    found
}

fn set_mode(path: &Path, writable: bool) -> AppResult<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = if writable { WRITABLE } else { READ_ONLY };
        fs::set_permissions(path, fs::Permissions::from_mode(mode))?;
    }
    #[cfg(not(unix))]
    {
        let mut permissions = fs::metadata(path)?.permissions();
        permissions.set_readonly(!writable);
        fs::set_permissions(path, permissions)?;
    }
    Ok(())
}

/// Copies the live database with SQLite's online backup API, then prunes old copies.
pub fn create_backup(db: &Path, dir: &Path, keep: usize) -> AppResult<PathBuf> {
    fs::create_dir_all(dir)?;
    let stamp = Utc::now().format("%Y%m%dT%H%M%S%6f");
    let target = dir.join(format!("{}_{stamp}{BACKUP_SUFFIX}", stem_of(db)));
    {
        let source = Connection::open(db)?;
        let mut destination = Connection::open(&target)?;
        let backup = Backup::new(&source, &mut destination)?;
        backup.run_to_completion(256, std::time::Duration::from_millis(0), None)?;
    }
    set_mode(&target, false)?;
    let all = list_backups(dir, &stem_of(db));
    let surplus = all.len().saturating_sub(keep);
    for old in &all[..surplus] {
        set_mode(old, true)?;
        fs::remove_file(old)?;
    }
    Ok(target)
}

pub fn integrity_ok(db: &Path) -> bool {
    let check = || -> rusqlite::Result<String> {
        Connection::open(db)?.query_row("PRAGMA integrity_check", [], |row| row.get(0))
    };
    check().is_ok_and(|answer| answer == "ok")
}

/// Never silently starts from an empty DB when it vanished or is corrupt.
pub fn guard_state_db(db: &Path) -> AppResult<()> {
    let backups = list_backups(&backup_dir_for(db), &stem_of(db));
    if !db.exists() && !backups.is_empty() {
        return Err(AppError::StateDb(format!(
            "{} is missing but {} backups exist. Copy the newest one from {} back to {}",
            db.display(),
            backups.len(),
            backup_dir_for(db).display(),
            db.display()
        )));
    }
    if db.exists() && !integrity_ok(db) {
        return Err(AppError::StateDb(format!(
            "{} failed the integrity check. Replace it with the newest backup from {}",
            db.display(),
            backup_dir_for(db).display()
        )));
    }
    Ok(())
}

fn snapshot(db: &Path) -> AppResult<()> {
    if db.exists() {
        create_backup(db, &backup_dir_for(db), DEFAULT_KEEP)?;
    }
    Ok(())
}

/// Checks the DB before an operation and backs it up before and after (also after a failure).
pub fn protected<T>(db: &Path, work: impl FnOnce() -> AppResult<T>) -> AppResult<T> {
    guard_state_db(db)?;
    snapshot(db)?;
    let outcome = work();
    let after = snapshot(db);
    let value = outcome?;
    after?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_db(path: &Path) {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch("CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (1);").unwrap();
    }

    #[test]
    fn backups_are_kept_up_to_the_limit_and_old_read_only_ones_pruned() {
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("s.db");
        make_db(&db);
        let backups = dir.path().join("s_backups");
        for _ in 0..4 {
            create_backup(&db, &backups, 2).unwrap();
        }
        assert_eq!(list_backups(&backups, "s").len(), 2);
    }

    #[test]
    fn a_missing_db_with_backups_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("s.db");
        make_db(&db);
        create_backup(&db, &dir.path().join("s_backups"), 5).unwrap();
        fs::remove_file(&db).unwrap();
        let Err(AppError::StateDb(message)) = guard_state_db(&db) else { panic!("a missing db with backups must be refused") };
        assert!(message.contains("s_backups"), "names where the backups are: {message}");
        assert!(!message.contains("restore"), "no command to run: {message}");
    }

    #[test]
    fn protected_backs_up_before_and_after() {
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("s.db");
        make_db(&db);
        protected(&db, || Ok(())).unwrap();
        assert_eq!(list_backups(&backup_dir_for(&db), "s").len(), 2);
    }
}
