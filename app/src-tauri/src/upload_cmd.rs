//! The upload page's commands: batches waiting for upload, a batch's queue, a run with progress and pause.

use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, State};

use crate::core::csv_batches::{csv_readiness, readiness_text, CsvReadiness};
use crate::core::sftp::{self, Login, Uploader};
use crate::core::upload::{self, UploadReport};
use crate::core::{backup, state};
use crate::error::{AppError, AppResult};
use crate::scan_cmd::ScanControl;
use crate::{db, settings};

const PROGRESS_EVENT: &str = "upload-progress";
const PROGRESS_INTERVAL: Duration = Duration::from_millis(100);

/// Shared between the upload command and the pause command (a separate state from the scan's).
#[derive(Clone, Default)]
pub struct UploadControl(ScanControl);

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StatusCount {
    batch_id: i64,
    status: String,
    n: i64,
}

impl StatusCount {
    pub(crate) fn new(batch_id: i64, status: String, n: i64) -> Self {
        Self { batch_id, status, n }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UploadOverview {
    batches: Vec<state::PendingBatch>,
    counts: Vec<StatusCount>,
    /// Whether each batch has an up-to-date CSV: without one it cannot be uploaded.
    csv: Vec<BatchCsv>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchCsv {
    batch_id: i64,
    readiness: CsvReadiness,
    /// Why the batch cannot be uploaded; empty when it can.
    reason: String,
}

/// Batches with files waiting for upload, and what every batch is made of.
#[tauri::command]
pub async fn upload_overview() -> AppResult<UploadOverview> {
    tauri::async_runtime::spawn_blocking(|| {
        backup::guard_state_db(&db::db_path())?;
        let Some(conn) = db::open_read_only()? else {
            return Ok(UploadOverview { batches: vec![], counts: vec![], csv: vec![] });
        };
        let counts = state::batch_status_counts(&conn)?
            .into_iter()
            .map(|(batch_id, status, n)| StatusCount::new(batch_id, status, n))
            .collect();
        let batches = state::upload_batches(&conn)?;
        let csv = batches
            .iter()
            .map(|batch| {
                let readiness = csv_readiness(&conn, batch.batch_id)?;
                Ok(BatchCsv { batch_id: batch.batch_id, readiness, reason: readiness_text(batch.batch_id, readiness) })
            })
            .collect::<AppResult<Vec<_>>>()?;
        Ok(UploadOverview { batches, counts, csv })
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

/// Every file of a batch with its status, in upload order.
#[tauri::command]
pub async fn upload_queue(batch: i64) -> AppResult<Vec<state::QueueFile>> {
    tauri::async_runtime::spawn_blocking(move || {
        backup::guard_state_db(&db::db_path())?;
        match db::open_read_only()? {
            Some(conn) => state::batch_files(&conn, batch),
            None => Ok(vec![]),
        }
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

fn run(app: &AppHandle, control: &UploadControl, batch: i64) -> AppResult<UploadReport> {
    let database = db::db_path();
    let mut last_emit: Option<Instant> = None;
    let mut emit = |progress: upload::UploadProgress| {
        // a finished file always goes out; chunks of the current one are thinned out
        let finished = progress.sent == 0 || progress.sent >= progress.size;
        if finished || last_emit.is_none_or(|at| at.elapsed() >= PROGRESS_INTERVAL) {
            last_emit = Some(Instant::now());
            let _ = app.emit(PROGRESS_EVENT, progress);
        }
    };
    let should_stop = || control.0.stop_requested();
    backup::protected(&database, || {
        let conn = state::open_db(&database)?;
        // the password is read from the keychain once, and only when there is something to upload
        let connect = || -> Result<Box<dyn Uploader>, String> {
            let login = settings::sftp_login().map_err(|error| error.to_string())?;
            let uploader = sftp::connect(&Login {
                host: &login.host,
                port: login.port,
                user: &login.user,
                password: &login.password,
            })?;
            Ok(Box::new(uploader))
        };
        upload::upload_batch(&conn, batch, connect, &mut emit, &should_stop)
    })
}

/// Uploads the pending files of a batch; what is already uploaded is not sent again.
#[tauri::command]
pub async fn upload_start(app: AppHandle, control: State<'_, UploadControl>, batch: i64) -> AppResult<UploadReport> {
    let control = control.inner().clone();
    let guard = control.0.begin("Загрузка уже идёт")?;
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = guard;
        run(&app, &control, batch)
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

#[tauri::command]
pub fn upload_pause(control: State<'_, UploadControl>) {
    control.0.request_stop();
}
