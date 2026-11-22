use serde::Serialize;

use crate::db::open_read_only;
use crate::error::AppResult;

#[derive(Serialize)]
pub struct StatusCount {
    status: String,
    n: i64,
}

/// File counts per status over all files (the status line of the main window).
#[tauri::command]
pub fn status_summary() -> AppResult<Vec<StatusCount>> {
    let Some(conn) = open_read_only()? else {
        return Ok(vec![]);
    };
    let mut stmt = conn.prepare("SELECT status, COUNT(*) FROM files GROUP BY status")?;
    let counts = stmt
        .query_map([], |row| {
            Ok(StatusCount { status: row.get(0)?, n: row.get(1)? })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(counts)
}
