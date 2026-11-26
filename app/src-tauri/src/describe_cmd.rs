//! The description page's commands: list files, save edits, bulk edits, statuses, approve.

use rusqlite::Connection;
use serde::Serialize;

use crate::core::{backup, describe, state};
use crate::db;
use crate::error::{AppError, AppResult};

/// Runs a change against the database on a worker thread: guarded and backed up before and after.
pub(crate) async fn mutate<T: Send + 'static>(
    work: impl FnOnce(&Connection) -> AppResult<T> + Send + 'static,
) -> AppResult<T> {
    tauri::async_runtime::spawn_blocking(move || {
        let database = db::db_path();
        if let Some(parent) = database.parent() {
            std::fs::create_dir_all(parent)?;
        }
        backup::protected(&database, || work(&state::open_db(&database)?))
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

/// New, described, flagged and uploaded files (invalid ones stay on the scan page).
#[tauri::command]
pub async fn describe_files() -> AppResult<Vec<state::FileRow>> {
    tauri::async_runtime::spawn_blocking(|| {
        backup::guard_state_db(&db::db_path())?;
        match db::open_read_only()? {
            Some(conn) => state::files_to_describe(&conn),
            None => Ok(vec![]),
        }
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

/// Saves a person's edit; `false` when the file is final (submitted) or not editable.
#[tauri::command]
pub async fn save_metadata(
    filename: String,
    title: String,
    keywords: Vec<String>,
    category: String,
) -> AppResult<bool> {
    mutate(move |conn| state::update_metadata(conn, &filename, title.trim(), &keywords, &category)).await
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BulkResult {
    edited: Option<usize>,
    moved: Option<usize>,
}

/// A keyword and/or category for several files, and/or a new status; empty parts change nothing.
#[tauri::command]
pub async fn bulk_apply(
    names: Vec<String>,
    tag: String,
    category: Option<String>,
    status: Option<String>,
) -> AppResult<BulkResult> {
    mutate(move |conn| {
        let wants_edit = !tag.trim().is_empty() || category.is_some();
        let edited = wants_edit.then(|| state::bulk_edit(conn, &names, &tag, category.as_deref())).transpose()?;
        let moved = status.as_deref().map(|target| describe::set_status(conn, &names, target)).transpose()?;
        Ok(BulkResult { edited, moved: moved.as_ref().map(|report| report.count) })
    })
    .await
}

#[tauri::command]
pub async fn set_files_status(names: Vec<String>, status: String) -> AppResult<describe::ApproveReport> {
    mutate(move |conn| describe::set_status(conn, &names, &status)).await
}

/// Releases reviewed files into the upload queue; no names approves all of them.
#[tauri::command]
pub async fn approve_files(names: Option<Vec<String>>) -> AppResult<describe::ApproveReport> {
    mutate(move |conn| describe::approve(conn, names.as_deref())).await
}

/// Pixel sizes from the file headers (no decoding); (0, 0) when a file cannot be read.
#[tauri::command]
pub async fn image_sizes(paths: Vec<String>) -> Vec<(u32, u32)> {
    tauri::async_runtime::spawn_blocking(move || {
        paths
            .iter()
            .map(|path| {
                image::ImageReader::open(path)
                    .and_then(|reader| reader.with_guessed_format())
                    .ok()
                    .and_then(|reader| reader.into_dimensions().ok())
                    .unwrap_or((0, 0))
            })
            .collect()
    })
    .await
    .unwrap_or_default()
}
