use crate::core::backup;
use crate::db::db_path;
use crate::error::AppResult;

/// Backs up `state.db` right before the updater installs a new version, so a bad upgrade can be rolled back
/// by copying the newest backup over the database. A missing database (empty app) is not an error.
#[tauri::command]
pub fn backup_before_update() -> AppResult<()> {
    backup::protected(&db_path(), || Ok(()))
}
