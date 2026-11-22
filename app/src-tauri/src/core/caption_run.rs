//! Captioning a set of files and storing the result (port of `services.caption_files`): groups of images go
//! to `claude` in parallel windows, results are checked and written to the database, and new files are batched.
//! Shared by the commands; it prints nothing and reports progress through a callback.

use std::collections::HashSet;
use std::path::PathBuf;

use rusqlite::Connection;

use super::caption::{caption_group, CaptionResult, ClaudeCallError};
use super::metadata::{check_items, with_ai_label, Item};
use super::state::{self, Generated};
use crate::error::AppResult;

pub const MAX_CONSECUTIVE_CALL_FAILURES: usize = 2;

#[derive(Debug, Default, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CaptionReport {
    pub total: usize,
    pub captioned: usize,
    /// "name: reason" for files held back for review.
    pub flagged: Vec<String>,
    pub errors: Vec<String>,
    pub new_batches: usize,
    pub stopped: bool,
}

pub struct CaptionOptions<'a> {
    pub model: &'a str,
    pub claude: &'a str,
    pub limit: usize,
    pub per_call: usize,
    pub workers: usize,
    pub batch_size: usize,
    pub names: Option<&'a [String]>,
    pub redo: bool,
    pub ai_label: &'a str,
}

fn file_name(path: &std::path::Path) -> String {
    path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
}

fn store_results(
    conn: &Connection,
    results: &[CaptionResult],
    report: &mut CaptionReport,
    options: &CaptionOptions,
) -> AppResult<usize> {
    let mut stored = 0;
    for result in results {
        let name = file_name(&result.path);
        if let Some(error) = &result.error {
            state::record_error(conn, &result.path, error)?;
            state::log_event(conn, "caption", error, "error", Some(&name))?;
            report.errors.push(format!("{name}: {error}"));
            continue;
        }
        let item = Item {
            filename: name.clone(),
            title: result.title.clone(),
            keywords: result.keywords.clone(),
            category: result.category.clone(),
        };
        let problems = check_items(&[item], &HashSet::from([name.clone()]), "");
        if !problems.is_empty() {
            let text = problems.join("; ");
            state::record_error(conn, &result.path, &text)?;
            state::log_event(conn, "caption", &text, "error", Some(&name))?;
            report.errors.push(format!("{name}: {text}"));
            continue;
        }
        let title = with_ai_label(&result.title, options.ai_label);
        state::set_metadata(
            conn,
            &result.path,
            &Generated {
                title: &title,
                keywords: &result.keywords,
                category: &result.category,
                people_visible: result.people_visible,
                content_type: &result.content_type,
                review_reason: &result.review_reason,
            },
            options.redo,
        )?;
        if !result.review_reason.is_empty() {
            state::log_event(conn, "caption", &result.review_reason, "warning", Some(&name))?;
            report.flagged.push(format!("{name}: {}", result.review_reason));
            continue;
        }
        stored += 1;
    }
    Ok(stored)
}

/// Generates titles and keywords for files that have none (only `options.names` if given), then batches them.
/// With `redo` the described files among `names` are captioned again and their text is replaced.
pub fn caption_files(
    conn: &Connection,
    options: &CaptionOptions,
    on_progress: &mut dyn FnMut(usize, usize, &str),
    should_stop: &(dyn Fn() -> bool + Sync),
) -> AppResult<CaptionReport> {
    let todo = state::files_needing_metadata(conn, options.limit, options.names, options.redo)?;
    if todo.is_empty() {
        return Ok(CaptionReport::default());
    }
    let groups: Vec<Vec<PathBuf>> = todo.chunks(options.per_call.max(1)).map(<[PathBuf]>::to_vec).collect();
    let workers = options.workers.max(1);
    let mut report = CaptionReport { total: todo.len(), ..CaptionReport::default() };
    let (mut done, mut failed_calls) = (0, 0);

    for window in groups.chunks(workers) {
        if should_stop() {
            report.stopped = true;
            break;
        }
        let outcomes: Vec<Result<Vec<CaptionResult>, ClaudeCallError>> = std::thread::scope(|scope| {
            let handles: Vec<_> = window
                .iter()
                .map(|group| scope.spawn(|| caption_group(group, options.model, options.claude)))
                .collect();
            handles
                .into_iter()
                .map(|handle| handle.join().unwrap_or_else(|_| Err(ClaudeCallError("captioning thread crashed".into()))))
                .collect()
        });
        for (group, outcome) in window.iter().zip(outcomes) {
            match outcome {
                Err(error) => {
                    failed_calls += 1;
                    state::log_event(conn, "caption", &error.0, "error", None)?;
                    report.errors.push(error.0);
                }
                Ok(results) => {
                    failed_calls = 0;
                    report.captioned += store_results(conn, &results, &mut report, options)?;
                }
            }
            done += group.len();
            on_progress(done, todo.len(), &format!("{done} of {} files", todo.len()));
        }
        if failed_calls >= MAX_CONSECUTIVE_CALL_FAILURES {
            report.errors.push("stopping: repeated call failures (usage limit or login problem?)".to_string());
            break;
        }
    }

    report.new_batches = state::assign_batches(conn, Some(options.batch_size))?;
    let level = if report.errors.is_empty() { "info" } else { "error" };
    state::log_event(
        conn,
        "caption",
        &format!(
            "model {}: captioned {} of {}, flagged {}, errors {}, new batches {}{}",
            options.model,
            report.captioned,
            todo.len(),
            report.flagged.len(),
            report.errors.len(),
            report.new_batches,
            if report.stopped { ", cancelled" } else { "" }
        ),
        level,
        None,
    )?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::super::caption::tests::{fake_claude, write_jpeg};
    use super::*;

    fn setup(count: usize) -> (tempfile::TempDir, Connection, Vec<PathBuf>) {
        let dir = tempfile::tempdir().unwrap();
        let conn = state::open_db(&dir.path().join("state.db")).unwrap();
        let mut paths = Vec::new();
        for index in 0..count {
            let path = dir.path().join(format!("p{index}.jpg"));
            write_jpeg(&path, 20 + index as u8);
            conn.execute(
                "INSERT INTO files (path, filename, size, sha256, root, status, updated_at) VALUES (?, ?, 1, ?, ?, 'needs_metadata', 't')",
                rusqlite::params![path.to_string_lossy(), format!("p{index}.jpg"), format!("h{index}"), dir.path().to_string_lossy()],
            )
            .unwrap();
            paths.push(path);
        }
        (dir, conn, paths)
    }

    const GOOD: &str = r#"{"structured_output":{"items":[
        {"file":"img001.jpg","title":"A red fox in snow","keywords":["fox","snow"],"category":1,"people_visible":false,"content_type":"photo","review_reason":""},
        {"file":"img002.jpg","title":"Statue of a famous person","keywords":["statue"],"category":13,"people_visible":true,"content_type":"photo","review_reason":"real person"}
    ]}}"#;

    fn options<'a>(claude: &'a str, label: &'a str) -> CaptionOptions<'a> {
        CaptionOptions {
            model: "claude-haiku-5-5",
            claude,
            limit: 100,
            per_call: 2,
            workers: 2,
            batch_size: 1000,
            names: None,
            redo: false,
            ai_label: label,
        }
    }

    #[test]
    fn good_results_become_pending_and_flagged_ones_go_to_review() {
        let (dir, conn, _) = setup(2);
        let claude = fake_claude(dir.path(), GOOD, 0);
        let mut steps = Vec::new();

        let report = caption_files(&conn, &options(&claude, "Generative AI"), &mut |d, t, _| steps.push((d, t)), &|| false).unwrap();

        assert_eq!((report.total, report.captioned, report.flagged.len(), report.new_batches), (2, 1, 1, 1));
        assert_eq!(steps, vec![(2, 2)]);
        let row = |name: &str| -> (String, String, Option<String>) {
            conn.query_row("SELECT status, title, error FROM files WHERE filename = ?", [name], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))).unwrap()
        };
        assert_eq!(row("p0.jpg"), ("pending".into(), "A red fox in snow Generative AI".into(), None));
        assert_eq!(row("p1.jpg").0, "review");
        assert_eq!(row("p1.jpg").2.as_deref(), Some("real person"));
    }

    #[test]
    fn repeated_call_failures_stop_the_run_and_cancel_is_honoured() {
        let (dir, conn, _) = setup(8);
        let failing = fake_claude(dir.path(), "limit", 1);
        let mut opts = options(&failing, "");
        opts.per_call = 1;
        opts.workers = 1;
        let report = caption_files(&conn, &opts, &mut |_, _, _| {}, &|| false).unwrap();
        assert_eq!(report.captioned, 0);
        assert!(report.errors.last().unwrap().starts_with("stopping: repeated call failures"));
        assert_eq!(report.errors.len(), 3); // two failed calls and the stop note

        let stopped = caption_files(&conn, &opts, &mut |_, _, _| {}, &|| true).unwrap();
        assert!(stopped.stopped);
        assert_eq!(stopped.captioned, 0);
    }

    #[test]
    fn redo_replaces_a_described_file_but_a_plain_run_leaves_it() {
        let (dir, conn, _) = setup(1);
        let claude = fake_claude(dir.path(), GOOD, 0);
        let names = vec!["p0.jpg".to_string()];
        caption_files(&conn, &options(&claude, ""), &mut |_, _, _| {}, &|| false).unwrap();
        conn.execute("UPDATE files SET title = 'mine'", []).unwrap();

        let plain = caption_files(&conn, &options(&claude, ""), &mut |_, _, _| {}, &|| false).unwrap();
        assert_eq!(plain.total, 0);

        let mut redo = options(&claude, "");
        redo.names = Some(&names);
        redo.redo = true;
        caption_files(&conn, &redo, &mut |_, _, _| {}, &|| false).unwrap();
        let title: String = conn.query_row("SELECT title FROM files", [], |r| r.get(0)).unwrap();
        assert_eq!(title, "A red fox in snow");
    }
}
