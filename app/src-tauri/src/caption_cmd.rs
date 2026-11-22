//! The description page's Claude captioning: run with progress and cancel.

use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, State};

use crate::checks::find_claude;
use crate::core::caption::{CLAUDE_NOT_FOUND, MODELS};
use crate::core::caption_run::{self, CaptionOptions, CaptionReport};
use crate::core::{backup, state};
use crate::error::{AppError, AppResult};
use crate::scan_cmd::ScanControl;
use crate::{db, prefs, settings};

const PROGRESS_EVENT: &str = "caption-progress";
const PROGRESS_INTERVAL: Duration = Duration::from_millis(80);
const MODEL_KEY: &str = "model";

/// Shared between the caption command and the cancel command (a separate state from the scan's).
#[derive(Clone, Default)]
pub struct CaptionControl(ScanControl);

#[derive(Serialize, Clone)]
struct Progress {
    done: usize,
    total: usize,
    message: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelChoice {
    id: &'static str,
    note: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelList {
    models: Vec<ModelChoice>,
    selected: String,
}

/// The selectable models and the one used last.
#[tauri::command]
pub fn caption_models() -> AppResult<ModelList> {
    let map = prefs::load()?;
    let saved = prefs::text(&map, MODEL_KEY, "");
    let selected = MODELS.iter().map(|(id, _)| *id).find(|id| *id == saved).unwrap_or(MODELS[0].0).to_string();
    Ok(ModelList { models: MODELS.iter().map(|(id, note)| ModelChoice { id, note }).collect(), selected })
}

fn run(app: &AppHandle, control: &CaptionControl, names: Option<Vec<String>>, redo: bool, model: String) -> AppResult<CaptionReport> {
    let config = settings::get_settings()?.settings;
    let claude = find_claude(&config.claude_path).ok_or_else(|| AppError::Check(CLAUDE_NOT_FOUND.to_string()))?;
    prefs::set_many(&[(MODEL_KEY, model.clone())])?;
    let database = db::db_path();
    if let Some(parent) = database.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut last_emit: Option<Instant> = None;
    let mut emit = |done: usize, total: usize, message: &str| {
        if last_emit.is_none_or(|at| at.elapsed() >= PROGRESS_INTERVAL) || done == total {
            last_emit = Some(Instant::now());
            let _ = app.emit(PROGRESS_EVENT, Progress { done, total, message: message.to_string() });
        }
    };
    let claude = claude.to_string_lossy().into_owned();
    let options = CaptionOptions {
        model: &model,
        claude: &claude,
        limit: config.caption_limit as usize,
        per_call: config.caption_per_call as usize,
        workers: config.caption_workers as usize,
        batch_size: config.batch_size as usize,
        names: names.as_deref(),
        redo,
        ai_label: &config.ai_title_label,
    };
    let should_stop = || control.0.stop_requested();
    backup::protected(&database, || {
        let conn = state::open_db(&database)?;
        emit(0, 0, "Подписываю…");
        caption_run::caption_files(&conn, &options, &mut emit, &should_stop)
    })
}

/// Captions `names`, or every new file without them; `redo` replaces an existing description.
#[tauri::command]
pub async fn caption_start(
    app: AppHandle,
    control: State<'_, CaptionControl>,
    names: Option<Vec<String>>,
    redo: bool,
    model: String,
) -> AppResult<CaptionReport> {
    if !MODELS.iter().any(|(id, _)| *id == model) {
        return Err(AppError::Invalid(format!("Неизвестная модель: {model}")));
    }
    let control = control.inner().clone();
    let guard = control.0.begin("Подпись уже идёт")?;
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = guard;
        run(&app, &control, names, redo, model)
    })
    .await
    .map_err(|error| AppError::Check(error.to_string()))?
}

#[tauri::command]
pub fn caption_cancel(control: State<'_, CaptionControl>) {
    control.0.request_stop();
}
