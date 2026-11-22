//! Building a batch's Adobe CSV with the portal checklist, and marking a batch as submitted
//! (port of `services.build_csv` / `mark_submitted`).

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use chrono::Local;
use rusqlite::Connection;
use serde::Serialize;

use super::csv_batches::batch_waiting;
use super::metadata_csv::{check_items, missing_category_problems, validate_csv_file, write_adobe_csv};
use super::{py_repr, state};
use crate::error::{AppError, AppResult};

#[derive(Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CsvReport {
    pub batch: i64,
    pub rows: usize,
    pub path: Option<String>,
    pub notes: Option<String>,
    /// Files that need the "People and Property are fictional" tick.
    pub people: usize,
    pub problems: Vec<String>,
}

#[derive(Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SubmittedReport {
    pub count: usize,
    pub warnings: Vec<String>,
    /// Set when refused: (status, files) of the batch not uploaded yet.
    pub waiting: Vec<(String, i64)>,
}

/// metadata_<date>_batch<N>.csv; the date is when the batch's CSV was first made.
pub fn new_csv_path(csv_dir: &Path, batch_id: i64) -> PathBuf {
    csv_dir.join(format!("metadata_{}_batch{batch_id}.csv", Local::now().format("%Y-%m-%d")))
}

/// A checklist for the Contributor Portal: the AI labels cannot be set from the CSV.
pub fn portal_notes_text(batch_id: i64, rows: &[(String, bool, String)]) -> String {
    let names = |keep: &dyn Fn(&(String, bool, String)) -> bool| -> Vec<String> {
        rows.iter().filter(|row| keep(row)).map(|row| format!("   {}", row.0)).collect()
    };
    let people = names(&|row| row.1);
    let unknown = names(&|row| row.2.is_empty());
    let photos = names(&|row| row.2 == "photo");
    let illustrations = names(&|row| row.2 == "illustration");
    let mut lines = vec![
        format!("Portal checklist for batch {batch_id} ({} files)", rows.len()),
        String::new(),
        format!("1. Created using generative AI tools: tick for ALL {} files (required by Adobe).", rows.len()),
        String::new(),
        format!(
            "2. People and Property are fictional: tick for the {} files that show people or human-like figures:",
            people.len()
        ),
    ];
    lines.extend(people.iter().cloned());
    lines.push("   If a file shows a real, identifiable person, Adobe requires a model release instead.".to_string());
    lines.push(String::new());
    lines.push(format!("3. Content type: {} photo-realistic, {} illustrations.", photos.len(), illustrations.len()));
    lines.push("   Photos:".to_string());
    lines.extend(photos.iter().cloned());
    lines.push("   Illustrations:".to_string());
    lines.extend(illustrations.iter().cloned());
    if !unknown.is_empty() {
        lines.push(String::new());
        lines.push(format!(
            "{} files have no people/type information (metadata came from your own CSV), check them yourself:",
            unknown.len()
        ));
        lines.extend(unknown.iter().cloned());
    }
    lines.join("\n") + "\n"
}

fn write_portal_notes(output: &Path, batch_id: i64, rows: &[(String, bool, String)]) -> AppResult<PathBuf> {
    let stem = output.file_stem().map(|stem| stem.to_string_lossy().into_owned()).unwrap_or_default();
    let notes = output.with_file_name(format!("{stem}_portal_notes.txt"));
    std::fs::write(&notes, portal_notes_text(batch_id, rows))?;
    Ok(notes)
}

/// Writes (or rewrites) the Adobe CSV and portal checklist of a batch.
pub fn build_csv(conn: &Connection, batch: i64, csv_dir: &Path, ai_label: &str) -> AppResult<CsvReport> {
    let items = state::batch_items(conn, batch)?;
    if items.is_empty() {
        return Ok(CsvReport { batch, ..CsvReport::default() });
    }
    // A batch keeps one CSV: reuse (overwrite) the path recorded on its first run.
    let target = match state::recorded_csv_path(conn, batch)? {
        Some(path) => path,
        None => new_csv_path(csv_dir, batch),
    };
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)?;
    }
    write_adobe_csv(&items, &target).map_err(AppError::Check)?;
    let names: HashSet<String> = items.iter().map(|item| item.filename.clone()).collect();
    let mut problems = check_items(&items, &names, ai_label);
    problems.extend(missing_category_problems(&items));
    problems.extend(validate_csv_file(&target, &names));
    let shown = target.to_string_lossy().into_owned();
    if !problems.is_empty() {
        for problem in &problems {
            state::log_event(conn, "csv", &format!("batch {batch}: {problem}"), "error", None)?;
        }
        return Ok(CsvReport { batch, rows: items.len(), path: Some(shown), problems, ..CsvReport::default() });
    }
    let resolved = target.canonicalize()?;
    state::record_csv(conn, batch, &resolved)?;
    state::log_event(conn, "csv", &format!("batch {batch}: wrote {shown} ({} rows)", items.len()), "info", None)?;
    let rows = state::portal_notes(conn, batch)?;
    let notes = write_portal_notes(&target, batch, &rows)?;
    Ok(CsvReport {
        batch,
        rows: items.len(),
        path: Some(shown),
        notes: Some(notes.to_string_lossy().into_owned()),
        people: rows.iter().filter(|row| row.1).count(),
        problems: vec![],
    })
}

/// Python's `repr()` of a dict of status counts: `{'pending': 2, 'review': 1}`.
fn dict_repr(waiting: &[(String, i64)]) -> String {
    format!("{{{}}}", waiting.iter().map(|(status, n)| format!("{}: {n}", py_repr(status))).collect::<Vec<_>>().join(", "))
}

/// Records that the batch was uploaded and submitted in the portal (a manual step). Refused while some
/// files of the batch are not on SFTP yet: the portal CSV would not cover them.
pub fn mark_submitted(conn: &Connection, batch: i64) -> AppResult<SubmittedReport> {
    let waiting = batch_waiting(conn, batch)?;
    if !waiting.is_empty() {
        let message = format!("batch {batch}: refused, not uploaded yet: {}", dict_repr(&waiting));
        state::log_event(conn, "submitted", &message, "warning", None)?;
        return Ok(SubmittedReport { waiting, ..SubmittedReport::default() });
    }
    let mut warnings = Vec::new();
    match state::recorded_csv_path(conn, batch)? {
        None => warnings.push(format!("batch {batch} has no CSV; metadata may not have been applied")),
        Some(path) if !path.exists() => warnings.push(format!("recorded CSV is missing on disk: {}", path.display())),
        Some(_) => {}
    }
    let count = state::mark_batch_submitted(conn, batch)?;
    state::log_event(conn, "submitted", &format!("batch {batch}: {count} files marked submitted"), "info", None)?;
    Ok(SubmittedReport { count, warnings, waiting: vec![] })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// a.jpg is uploaded, b.jpg described and waiting, c.jpg not described yet.
    fn setup() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = state::open_db(&dir.path().join("state.db")).unwrap();
        conn.execute("INSERT INTO batches (folder, part, created_at) VALUES ('/r/trip', 1, 't')", []).unwrap();
        let rows = [
            ("a.jpg", "uploaded", "Red fox Generative AI", r#"["fox", "snow"]"#, "1", 0, "photo"),
            ("b.jpg", "pending", "Blue sky Generative AI", r#"["sky"]"#, "11", 1, "illustration"),
            ("c.jpg", "needs_metadata", "", "[]", "", 0, ""),
        ];
        for (name, status, title, keywords, category, people, kind) in rows {
            conn.execute(
                "INSERT INTO files (path, filename, size, sha256, root, status, batch_id, title, keywords, category, people_visible, content_type, updated_at)
                 VALUES (?, ?, 5, ?, '/r', ?, 1, ?, ?, ?, ?, ?, 't')",
                rusqlite::params![format!("/r/{name}"), name, name, status, title, keywords, category, people, kind],
            )
            .unwrap();
        }
        (dir, conn)
    }

    #[test]
    fn the_csv_covers_described_and_uploaded_files_and_writes_a_checklist() {
        let (dir, conn) = setup();
        let report = build_csv(&conn, 1, &dir.path().join("csv"), "Generative AI").unwrap();
        assert_eq!((report.rows, report.people, report.problems.len()), (2, 1, 0));
        let csv = std::fs::read_to_string(report.path.as_ref().unwrap()).unwrap();
        assert!(csv.starts_with("Filename,Title,Keywords,Category,Releases\r\na.jpg,Red fox Generative AI,\"fox, snow\",1,\r\nb.jpg,"));
        assert!(!csv.contains("c.jpg"));
        let notes = std::fs::read_to_string(report.notes.unwrap()).unwrap();
        assert!(notes.starts_with("Portal checklist for batch 1 (2 files)\n\n1. Created using generative AI tools: tick for ALL 2 files"));
        assert!(notes.contains("tick for the 1 files that show people or human-like figures:\n   b.jpg\n"));
        assert_eq!(state::recorded_csv_path(&conn, 1).unwrap().unwrap(), std::path::Path::new(report.path.as_ref().unwrap()).canonicalize().unwrap());
    }

    #[test]
    fn a_batch_without_files_for_the_csv_builds_nothing_and_empty_fields_are_reported() {
        let (dir, conn) = setup();
        assert_eq!(build_csv(&conn, 9, dir.path(), "").unwrap().rows, 0);
        conn.execute("UPDATE files SET title = '' WHERE filename = 'a.jpg'", []).unwrap();
        conn.execute("UPDATE files SET keywords = '[]' WHERE filename = 'b.jpg'", []).unwrap();
        let report = build_csv(&conn, 1, dir.path(), "Generative AI").unwrap();
        assert!(report.problems.contains(&"a.jpg: empty title".to_string()), "{:?}", report.problems);
        assert!(report.problems.contains(&"b.jpg: no keywords".to_string()), "{:?}", report.problems);
        assert!(state::recorded_csv_path(&conn, 1).unwrap().is_none());
    }

    #[test]
    fn a_row_without_a_category_stops_the_csv() {
        let (dir, conn) = setup();
        conn.execute("UPDATE files SET category = '' WHERE filename = 'b.jpg'", []).unwrap();
        let report = build_csv(&conn, 1, dir.path(), "Generative AI").unwrap();
        assert_eq!(report.problems, vec!["b.jpg: no category".to_string()]);
        assert!(state::recorded_csv_path(&conn, 1).unwrap().is_none());
    }

    #[test]
    fn the_csv_list_follows_the_batch_from_built_to_sent_and_the_upload_gate_follows_the_csv() {
        use crate::core::csv_batches::{csv_entries, csv_readiness, CsvReadiness, BUILT, ERROR, SENT};
        let (dir, conn) = setup();
        assert_eq!(csv_readiness(&conn, 1).unwrap(), CsvReadiness::Missing);
        build_csv(&conn, 1, dir.path(), "Generative AI").unwrap();
        assert_eq!(csv_readiness(&conn, 1).unwrap(), CsvReadiness::Ready);
        let entry = &csv_entries(&conn, "Generative AI").unwrap()[0];
        assert_eq!((entry.state, entry.rows, entry.bad_rows), (BUILT, 2, 0));

        conn.execute("UPDATE files SET category = '' WHERE filename = 'a.jpg'", []).unwrap();
        assert_eq!(csv_readiness(&conn, 1).unwrap(), CsvReadiness::Stale);
        assert_eq!(csv_entries(&conn, "Generative AI").unwrap()[0].state, ERROR);

        conn.execute("UPDATE files SET category = '1', status = 'submitted' WHERE batch_id = 1 AND filename != 'c.jpg'", []).unwrap();
        assert_eq!(csv_entries(&conn, "Generative AI").unwrap()[0].state, SENT);
    }

    #[test]
    fn a_batch_is_marked_submitted_only_when_everything_is_uploaded() {
        let (dir, conn) = setup();
        build_csv(&conn, 1, dir.path(), "Generative AI").unwrap();
        let refused = mark_submitted(&conn, 1).unwrap();
        assert_eq!(refused.waiting, vec![("needs_metadata".to_string(), 1), ("pending".to_string(), 1)]);
        assert_eq!(refused.count, 0);

        conn.execute("UPDATE files SET status = 'uploaded' WHERE filename = 'b.jpg'", []).unwrap();
        conn.execute("UPDATE files SET status = 'invalid' WHERE filename = 'c.jpg'", []).unwrap();
        let done = mark_submitted(&conn, 1).unwrap();
        assert_eq!((done.count, done.warnings.len()), (2, 0));
        let message: String = conn.query_row("SELECT message FROM events ORDER BY id DESC LIMIT 1", [], |r| r.get(0)).unwrap();
        assert_eq!(message, "batch 1: 2 files marked submitted");
        assert_eq!(dict_repr(&[("pending".into(), 2), ("review".into(), 1)]), "{'pending': 2, 'review': 1}");
    }
}
