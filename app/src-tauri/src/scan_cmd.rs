//! The scan page's commands: run a scan with progress and cancel, list the problem files.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use chrono::Local;
use serde::Serialize;
use tauri::{AppHandle, Emitter, State};

use crate::core::{backup, scan, state};
use crate::db;
use crate::error::{AppError, AppResult};
use crate::prefs;

const PROGRESS_EVENT: &str = "scan-progress";
const PROGRESS_INTERVAL: Duration = Duration::from_millis(80);
const LAST_SCAN_KEY: &str = "last_scan";

#[derive(Default)]
struct Flags {
    running: AtomicBool,
    stop: AtomicBool,
}

/// Shared between the scan command and the cancel command.
#[derive(Clone, Default)]
pub struct ScanControl(Arc<Flags>);

/// Marks a scan as running; the mark is removed when the scan ends, however it ends.
pub(crate) struct RunGuard(Arc<Flags>);

impl Drop for RunGuard {
    fn drop(&mut self) {
        self.0.running.store(false, Ordering::SeqCst);
    }
}

impl ScanControl {
    pub(crate) fn begin(&self, already_running: &str) -> AppResult<RunGuard> {
        if self.0.running.swap(true, Ordering::SeqCst) {
            return Err(AppError::Invalid(already_running.into()));
        }
        self.0.stop.store(false, Ordering::SeqCst);
        Ok(RunGuard(self.0.clone()))
    }
}

#[derive(Serialize, Clone)]
struct Progress {
    done: usize,
    total: usize,
    message: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanOutcome {
    found: usize,
    new: usize,
    known: usize,
    problems: usize,
    changed_after_upload: Vec<String>,
    stopped: bool,
}

fn save_last_scan(outcome: &ScanOutcome) -> AppResult<()> {
    let saved = serde_json::json!({
        "at": Local::now().format("%Y-%m-%dT%H:%M:%S").to_string(),
        "found": outcome.found,
        "counts": {
            "found": outcome.found,
            "new": outcome.new,
            "known": outcome.known,
            "problems": outcome.problems,
        },
    });
    prefs::set_many(&[(LAST_SCAN_KEY, saved.to_string())])
}

fn run_scan(app: &AppHandle, control: &ScanControl, root: PathBuf, recursive: bool) -> AppResult<Option<ScanOutcome>> {
    let resolved = root.canonicalize()?;
    prefs::set_many(&[
        ("image_folder", resolved.to_string_lossy().into_owned()),
        ("scan_recursive", if recursive { "1" } else { "0" }.to_string()),
    ])?;
    let database = db::db_path();
    if let Some(parent) = database.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut last_emit: Option<Instant> = None;
    let mut emit = |done: usize, total: usize, message: &str| {
        let due = last_emit.is_none_or(|at| at.elapsed() >= PROGRESS_INTERVAL);
        if due || done == 0 || done + 1 == total {
            last_emit = Some(Instant::now());
            let _ = app.emit(PROGRESS_EVENT, Progress { done, total, message: message.to_string() });
        }
    };
    let should_stop = || control.0.stop.load(Ordering::SeqCst);
    let report = backup::protected(&database, || {
        let mut conn = state::open_db(&database)?;
        emit(0, 0, "Ищу JPEG…");
        let images = scan::find_images(&resolved, recursive);
        if images.is_empty() {
            return Ok(None);
        }
        scan::scan(&mut conn, &resolved, &images, &mut emit, &should_stop).map(Some)
    })?;
    let Some(report) = report else { return Ok(None) };
    let outcome = ScanOutcome {
        found: report.found,
        new: report.result.added + report.result.needs_metadata,
        known: report.result.unchanged,
        problems: report.result.invalid,
        changed_after_upload: report.result.changed_after_upload,
        stopped: report.stopped,
    };
    save_last_scan(&outcome)?;
    Ok(Some(outcome))
}

/// Scans a folder. `None` means no JPEG files were found.
#[tauri::command]
pub async fn scan_start(
    app: AppHandle,
    control: State<'_, ScanControl>,
    folder: String,
    recursive: bool,
) -> AppResult<Option<ScanOutcome>> {
    let root = PathBuf::from(crate::settings::expand_home(folder.trim()));
    if !root.is_dir() {
        return Err(AppError::Invalid(format!("Папка не найдена: {}", root.display())));
    }
    let control = control.inner().clone();
    let guard = control.begin("Сканирование уже идёт")?;
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        let _guard = guard;
        run_scan(&app, &control, root, recursive)
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?;
    outcome
}

impl ScanControl {
    pub(crate) fn request_stop(&self) {
        self.0.stop.store(true, Ordering::SeqCst);
    }

    pub(crate) fn stop_requested(&self) -> bool {
        self.0.stop.load(Ordering::SeqCst)
    }
}

#[tauri::command]
pub fn scan_cancel(control: State<'_, ScanControl>) {
    control.request_stop();
}

#[derive(Serialize)]
pub struct ProblemFile {
    path: String,
    error: String,
    status: String,
}

/// Invalid files and files held back for review, oldest first.
#[tauri::command]
pub fn problem_files() -> AppResult<Vec<ProblemFile>> {
    backup::guard_state_db(&db::db_path())?;
    let Some(conn) = db::open_read_only()? else { return Ok(vec![]) };
    let mut stmt = conn.prepare("SELECT path, error, status FROM files WHERE status IN (?, ?) ORDER BY id")?;
    let rows = stmt
        .query_map([state::INVALID, state::REVIEW], |row| {
            Ok(ProblemFile {
                path: row.get(0)?,
                error: row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                status: row.get(2)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}
