//! Marking the uploaded files as submitted: the portal steps (category, AI and fictional-people ticks,
//! send for review) are manual, so the user tells the app when they are done.

use rusqlite::Connection;
use serde::Serialize;

use super::state;
use crate::error::AppResult;

#[derive(Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SubmittedReport {
    pub count: usize,
}

/// Records that everything uploaded so far was submitted in the portal (a manual step). Files that are
/// not on SFTP yet are left alone: they cannot have been submitted.
pub fn mark_submitted(conn: &Connection) -> AppResult<SubmittedReport> {
    let count = state::mark_all_submitted(conn)?;
    state::log_event(conn, "submitted", &format!("{count} files marked submitted"), "info", None)?;
    Ok(SubmittedReport { count })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_uploaded_files_are_marked_submitted() {
        let dir = tempfile::tempdir().unwrap();
        let conn = state::open_db(&dir.path().join("state.db")).unwrap();
        for (name, status) in [("a.jpg", "uploaded"), ("b.jpg", "pending"), ("c.jpg", "needs_metadata")] {
            conn.execute(
                "INSERT INTO files (path, filename, size, sha256, root, status, updated_at)
                 VALUES (?, ?, 5, ?, '/r', ?, 't')",
                rusqlite::params![format!("/r/{name}"), name, name, status],
            )
            .unwrap();
        }
        let done = mark_submitted(&conn).unwrap();
        assert_eq!(done.count, 1);
        let statuses: Vec<String> =
            conn.prepare("SELECT status FROM files ORDER BY id").unwrap().query_map([], |r| r.get(0)).unwrap().map(|r| r.unwrap()).collect();
        assert_eq!(statuses, ["submitted", "pending", "needs_metadata"]);
        let message: String = conn.query_row("SELECT message FROM events ORDER BY id DESC LIMIT 1", [], |r| r.get(0)).unwrap();
        assert_eq!(message, "1 files marked submitted");
    }
}
