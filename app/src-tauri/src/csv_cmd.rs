//! The CSV page's commands: the CSVs of the batches, their rows, building a CSV, marking a batch submitted.

use std::path::PathBuf;

use serde::Serialize;

use crate::core::backup;
use crate::core::csv_batches::{self, CsvEntry, CsvRow};
use crate::core::csv_build::{self, CsvReport, SubmittedReport};
use crate::core::state::{self, PendingBatch};
use crate::describe_cmd::mutate;
use crate::error::{AppError, AppResult};
use crate::upload_cmd::StatusCount;
use crate::{db, settings};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CsvOverview {
    entries: Vec<CsvEntry>,
    /// Batches «+ CSV» can make a CSV for.
    offered: Vec<PendingBatch>,
    counts: Vec<StatusCount>,
}

/// The batches' CSVs (newest first), the batches that could get one, and what every batch is made of.
#[tauri::command]
pub async fn csv_overview() -> AppResult<CsvOverview> {
    tauri::async_runtime::spawn_blocking(|| {
        backup::guard_state_db(&db::db_path())?;
        let ai_label = settings::get_settings()?.settings.ai_title_label;
        let Some(conn) = db::open_read_only()? else {
            return Ok(CsvOverview { entries: vec![], offered: vec![], counts: vec![] });
        };
        let counts = state::batch_status_counts(&conn)?
            .into_iter()
            .map(|(batch_id, status, n)| StatusCount::new(batch_id, status, n))
            .collect();
        Ok(CsvOverview {
            entries: csv_batches::csv_entries(&conn, &ai_label)?,
            offered: csv_batches::batches_without_csv(&conn)?,
            counts,
        })
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

/// The rows a batch's CSV covers.
#[tauri::command]
pub async fn csv_batch_rows(batch: i64) -> AppResult<Vec<CsvRow>> {
    tauri::async_runtime::spawn_blocking(move || {
        backup::guard_state_db(&db::db_path())?;
        match db::open_read_only()? {
            Some(conn) => csv_batches::csv_rows(&conn, batch),
            None => Ok(vec![]),
        }
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

fn csv_dir(view: &settings::SettingsView) -> PathBuf {
    let chosen = view.settings.csv_dir.trim();
    PathBuf::from(if chosen.is_empty() { view.default_csv_dir.clone() } else { chosen.to_string() })
}

/// Writes (or rewrites) the CSV and the portal checklist of a batch.
#[tauri::command]
pub async fn csv_build(batch: i64) -> AppResult<CsvReport> {
    let view = settings::get_settings()?;
    let (dir, ai_label) = (csv_dir(&view), view.settings.ai_title_label);
    mutate(move |conn| csv_build::build_csv(conn, batch, &dir, &ai_label)).await
}

/// Records that the batch's CSV was uploaded to the portal and the files were submitted.
#[tauri::command]
pub async fn csv_mark_submitted(batch: i64) -> AppResult<SubmittedReport> {
    mutate(move |conn| csv_build::mark_submitted(conn, batch)).await
}
