//! Read access to the same `state.db` the Python app uses (the schema is owned by Python for now).

use std::path::PathBuf;

use rusqlite::{Connection, OpenFlags};

use crate::error::AppResult;

const APP_DIR_NAME: &str = "Adobe Stock Uploader";
const DB_FILE_NAME: &str = "state.db";
const DB_ENV_VAR: &str = "ADOBE_STATE_DB";

pub fn db_path() -> PathBuf {
    if let Some(path) = std::env::var_os(DB_ENV_VAR) {
        return PathBuf::from(path);
    }
    dirs::data_dir()
        .unwrap_or_default()
        .join(APP_DIR_NAME)
        .join(DB_FILE_NAME)
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
