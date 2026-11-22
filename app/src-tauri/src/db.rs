//! Where `state.db` lives and how it is opened.

use std::path::PathBuf;

use rusqlite::{Connection, OpenFlags};

use crate::error::AppResult;
use crate::prefs;

const DB_FILE_NAME: &str = "state.db";
const DB_ENV_VAR: &str = "ADOBE_STATE_DB";

pub fn db_path() -> PathBuf {
    if let Some(path) = std::env::var_os(DB_ENV_VAR) {
        return PathBuf::from(path);
    }
    prefs::db_path_override().unwrap_or_else(|| prefs::legacy_data_dir().join(DB_FILE_NAME))
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
    Ok(Some(Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?))
}
