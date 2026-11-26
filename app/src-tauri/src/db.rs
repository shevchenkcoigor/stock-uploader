//! Where `state.db` lives and how it is opened.

use std::path::{Path, PathBuf};

use rusqlite::{Connection, OpenFlags};

use crate::core::env;
use crate::error::AppResult;
use crate::prefs;

const DB_FILE_NAME: &str = "state.db";
const DB_ENV_VAR: &str = "STOCK_STATE_DB";
const LEGACY_DB_ENV_VAR: &str = "ADOBE_STATE_DB";

pub fn db_path() -> PathBuf {
    if let Some(path) = env::var(DB_ENV_VAR, LEGACY_DB_ENV_VAR) {
        return PathBuf::from(path);
    }
    chosen_db(prefs::db_path_override(), &prefs::old_data_dir(), &prefs::data_dir())
}

/// The user's own database path wins, unless it is just the old default spelled out.
fn chosen_db(saved: Option<PathBuf>, old_dir: &Path, data_dir: &Path) -> PathBuf {
    saved.filter(|path| *path != old_dir.join(DB_FILE_NAME)).unwrap_or_else(|| data_dir.join(DB_FILE_NAME))
}

/// Where the prepared copies of the pictures are written: next to the database.
pub fn export_dir() -> PathBuf {
    db_path().parent().map_or_else(|| PathBuf::from("export"), |dir| dir.join("export"))
}

/// Opens the database read-only; `None` when it does not exist yet (an empty app).
pub fn open_read_only() -> AppResult<Option<Connection>> {
    let path = db_path();
    if !path.exists() {
        return Ok(None);
    }
    Ok(Some(Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_saved_path_wins_but_the_old_default_follows_the_move() {
        let (old, new) = (Path::new("/support/old"), Path::new("/support/new"));
        let pick = |saved: Option<&str>| chosen_db(saved.map(PathBuf::from), old, new);

        assert_eq!(pick(Some("/mine/s.db")), PathBuf::from("/mine/s.db"));
        assert_eq!(pick(Some("/support/old/state.db")), new.join("state.db"));
        assert_eq!(pick(None), new.join("state.db"));
    }
}
