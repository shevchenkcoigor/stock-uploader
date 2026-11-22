//! Marking a batch as submitted: the portal steps (category, AI and fictional-people ticks, send for review)
//! are manual, so the user tells the app when they are done.

use rusqlite::{params, Connection};
use serde::Serialize;

use super::state::{self, INVALID, SUBMITTED, UPLOADED};
use super::py_repr;
use crate::error::AppResult;

#[derive(Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SubmittedReport {
    pub count: usize,
    pub warnings: Vec<String>,
    /// Set when refused: (status, files) of the batch not uploaded yet.
    pub waiting: Vec<(String, i64)>,
}

/// Files of a batch that are not on SFTP yet (still pending or earlier), by status; empty when the whole batch
/// is uploaded. Invalid files never reach SFTP, so they do not hold the batch back.
pub fn batch_waiting(conn: &Connection, batch_id: i64) -> AppResult<Vec<(String, i64)>> {
    let mut stmt = conn.prepare(
        "SELECT status, COUNT(*) FROM files WHERE batch_id = ? AND status NOT IN (?, ?, ?)
         GROUP BY status ORDER BY status",
    )?;
    let rows = stmt.query_map(params![batch_id, UPLOADED, SUBMITTED, INVALID], |row| Ok((row.get(0)?, row.get(1)?)))?;
    Ok(rows.collect::<Result<_, _>>()?)
}

/// `{'pending': 2, 'review': 1}`, the way the log has always shown the waiting files.
fn dict_repr(waiting: &[(String, i64)]) -> String {
    format!("{{{}}}", waiting.iter().map(|(status, n)| format!("{}: {n}", py_repr(status))).collect::<Vec<_>>().join(", "))
}

/// Records that the batch was submitted in the portal (a manual step). Refused while some files of the batch
/// are not on SFTP yet: they cannot have been submitted.
pub fn mark_submitted(conn: &Connection, batch: i64) -> AppResult<SubmittedReport> {
    let waiting = batch_waiting(conn, batch)?;
    if !waiting.is_empty() {
        let message = format!("batch {batch}: refused, not uploaded yet: {}", dict_repr(&waiting));
        state::log_event(conn, "submitted", &message, "warning", None)?;
        return Ok(SubmittedReport { waiting, ..SubmittedReport::default() });
    }
    let count = state::mark_batch_submitted(conn, batch)?;
    state::log_event(conn, "submitted", &format!("batch {batch}: {count} files marked submitted"), "info", None)?;
    Ok(SubmittedReport { count, warnings: Vec::new(), waiting: Vec::new() })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// a.jpg is uploaded, b.jpg described and waiting, c.jpg not described yet.
    fn setup() -> Connection {
        let dir = tempfile::tempdir().unwrap();
        let conn = state::open_db(&dir.path().join("state.db")).unwrap();
        std::mem::forget(dir);
        conn.execute("INSERT INTO batches (folder, part, created_at) VALUES ('/r/trip', 1, 't')", []).unwrap();
        for (name, status) in [("a.jpg", "uploaded"), ("b.jpg", "pending"), ("c.jpg", "needs_metadata")] {
            conn.execute(
                "INSERT INTO files (path, filename, size, sha256, root, status, batch_id, updated_at)
                 VALUES (?, ?, 5, ?, '/r', ?, 1, 't')",
                rusqlite::params![format!("/r/{name}"), name, name, status],
            )
            .unwrap();
        }
        conn
    }

    #[test]
    fn a_batch_is_marked_submitted_only_when_everything_is_uploaded() {
        let conn = setup();
        let refused = mark_submitted(&conn, 1).unwrap();
        assert_eq!(refused.waiting, vec![("needs_metadata".to_string(), 1), ("pending".to_string(), 1)]);
        assert_eq!(refused.count, 0);
        let refusal: String = conn.query_row("SELECT message FROM events ORDER BY id DESC LIMIT 1", [], |r| r.get(0)).unwrap();
        assert_eq!(refusal, "batch 1: refused, not uploaded yet: {'needs_metadata': 1, 'pending': 1}");

        conn.execute("UPDATE files SET status = 'uploaded' WHERE filename = 'b.jpg'", []).unwrap();
        conn.execute("UPDATE files SET status = 'invalid' WHERE filename = 'c.jpg'", []).unwrap();
        let done = mark_submitted(&conn, 1).unwrap();
        assert_eq!((done.count, done.warnings.len()), (2, 0));
        let message: String = conn.query_row("SELECT message FROM events ORDER BY id DESC LIMIT 1", [], |r| r.get(0)).unwrap();
        assert_eq!(message, "batch 1: 2 files marked submitted");
    }

    #[test]
    fn invalid_files_do_not_hold_a_batch_back() {
        let conn = setup();
        conn.execute("UPDATE files SET status = 'invalid' WHERE filename != 'a.jpg'", []).unwrap();
        assert!(batch_waiting(&conn, 1).unwrap().is_empty());
    }
}
