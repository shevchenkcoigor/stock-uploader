//! The upload page's commands: the queue, preparing copies, a run with progress and pause.

use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, State};

use crate::core::export::{self, PrepareReport};
use crate::core::submit::{self, SubmittedReport};
use crate::core::sftp::{self, Login, Uploader};
use crate::core::upload::{self, UploadReport};
use crate::core::{backup, state};
use crate::describe_cmd::mutate;
use crate::error::{AppError, AppResult};
use crate::scan_cmd::ScanControl;
use crate::{db, settings};

const PROGRESS_EVENT: &str = "upload-progress";
const PREPARE_EVENT: &str = "prepare-progress";
const PROGRESS_INTERVAL: Duration = Duration::from_millis(100);

/// Shared between the upload command and the pause command (a separate state from the scan's).
#[derive(Clone, Default)]
pub struct UploadControl(ScanControl);

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct PrepareProgress {
    done: usize,
    total: usize,
    file: String,
}

/// Every file that is part of the upload with its status, in upload order.
#[tauri::command]
pub async fn upload_queue() -> AppResult<Vec<state::QueueFile>> {
    tauri::async_runtime::spawn_blocking(move || {
        backup::guard_state_db(&db::db_path())?;
        match db::open_read_only()? {
            Some(conn) => state::queue_files(&conn),
            None => Ok(vec![]),
        }
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

fn run(app: &AppHandle, control: &UploadControl) -> AppResult<UploadReport> {
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
        upload::upload_pending(&conn, connect, &mut emit, &should_stop)
    })
}

fn prepare(app: &AppHandle, control: &UploadControl) -> AppResult<PrepareReport> {
    let database = db::db_path();
    let export_dir = db::export_dir();
    let ai_label = settings::get_settings()?.settings.ai_title_label;
    let mut emit = |done: usize, total: usize, file: &str| {
        let _ = app.emit(PREPARE_EVENT, PrepareProgress { done, total, file: file.to_string() });
    };
    let should_stop = || control.0.stop_requested();
    backup::protected(&database, || {
        let conn = state::open_db(&database)?;
        export::prepare_pending(&conn, &export_dir, &ai_label, &mut emit, &should_stop)
    })
}

/// Makes the copies of the pending files that carry their title and keywords; they are what gets uploaded.
#[tauri::command]
pub async fn upload_prepare(app: AppHandle, control: State<'_, UploadControl>) -> AppResult<PrepareReport> {
    let control = control.inner().clone();
    let guard = control.0.begin("Загрузка уже идёт")?;
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = guard;
        prepare(&app, &control)
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

/// Uploads every pending file; what is already uploaded is not sent again.
#[tauri::command]
pub async fn upload_start(app: AppHandle, control: State<'_, UploadControl>) -> AppResult<UploadReport> {
    let control = control.inner().clone();
    let guard = control.0.begin("Загрузка уже идёт")?;
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = guard;
        run(&app, &control)
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

#[tauri::command]
pub fn upload_pause(control: State<'_, UploadControl>) {
    control.0.request_stop();
}

/// Records that everything uploaded was submitted in the portal (a manual step).
#[tauri::command]
pub async fn upload_mark_submitted() -> AppResult<SubmittedReport> {
    mutate(submit::mark_submitted).await
}
