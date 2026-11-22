//! Description-page operations that also log and batch (port of `services.set_status` / `approve`).

use rusqlite::Connection;

use super::state;
use crate::error::AppResult;

#[derive(Debug, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ApproveReport {
    pub count: usize,
    pub new_batches: usize,
}

/// Moves files to `status` by hand; files that become "described" are put into batches as usual.
pub fn set_status(conn: &Connection, names: &[String], status: &str, batch_size: usize) -> AppResult<ApproveReport> {
    let count = state::set_status(conn, names, status)?;
    let new_batches = state::assign_batches(conn, Some(batch_size))?;
    state::log_event(
        conn,
        "status",
        &format!("{count} of {} files moved to {status}, new batches {new_batches}", names.len()),
        "info",
        None,
    )?;
    Ok(ApproveReport { count, new_batches })
}

/// Releases reviewed files into the upload queue; `None` approves all of them.
pub fn approve(conn: &Connection, names: Option<&[String]>, batch_size: usize) -> AppResult<ApproveReport> {
    let count = state::approve_reviewed(conn, names)?;
    let new_batches = state::assign_batches(conn, Some(batch_size))?;
    let scope = names.map_or_else(|| "all".to_string(), |list| list.join(", "));
    state::log_event(conn, "approve", &format!("approved {count} files ({scope}), new batches {new_batches}"), "info", None)?;
    Ok(ApproveReport { count, new_batches })
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;

    fn db_with(files: &[(&str, &str, &str, &[&str])]) -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(state::SCHEMA).unwrap();
        for (index, (name, status, title, keywords)) in files.iter().enumerate() {
            let words: Vec<String> = keywords.iter().map(|word| word.to_string()).collect();
            conn.execute(
                "INSERT INTO files (path, filename, sha256, size, root, title, keywords, status, updated_at)
                 VALUES (?, ?, ?, 1, '/r', ?, ?, ?, 'x')",
                params![format!("/r/{name}"), name, format!("h{index}"), title, state::keywords_json(&words), status],
            )
            .unwrap();
        }
        conn
    }

    fn status_of(conn: &Connection, name: &str) -> String {
        conn.query_row("SELECT status FROM files WHERE filename = ?", [name], |row| row.get(0)).unwrap()
    }

    fn names(list: &[&str]) -> Vec<String> {
        list.iter().map(|name| name.to_string()).collect()
    }

    #[test]
    fn keywords_are_written_like_python_does() {
        assert_eq!(state::keywords_json(&names(&["a", "b"])), r#"["a", "b"]"#);
        assert_eq!(state::keywords_json(&[]), "[]");
        assert_eq!(state::keywords_json(&names(&["кот"])), "[\"\\u043a\\u043e\\u0442\"]");
        assert_eq!(state::keywords_json(&names(&["😀"])), "[\"\\ud83d\\ude00\"]");
        assert_eq!(state::keywords_json(&names(&["a\"b"])), r#"["a\"b"]"#);
    }

    #[test]
    fn an_edit_makes_a_new_file_pending_only_with_title_and_keywords() {
        let conn = db_with(&[("a.jpg", "needs_metadata", "", &[])]);
        assert!(state::update_metadata(&conn, "a.jpg", "Fox", &[], "").unwrap());
        assert_eq!(status_of(&conn, "a.jpg"), "needs_metadata");
        state::update_metadata(&conn, "a.jpg", "Fox", &names(&["fox"]), "1").unwrap();
        assert_eq!(status_of(&conn, "a.jpg"), "pending");
    }

    #[test]
    fn submitted_and_invalid_files_are_not_edited() {
        let conn = db_with(&[("s.jpg", "submitted", "T", &["k"]), ("i.jpg", "invalid", "", &[])]);
        assert!(!state::update_metadata(&conn, "s.jpg", "New", &names(&["k"]), "").unwrap());
        assert!(!state::update_metadata(&conn, "i.jpg", "New", &names(&["k"]), "").unwrap());
    }

    #[test]
    fn bulk_edit_appends_the_tag_once_and_sets_the_category() {
        let conn = db_with(&[("a.jpg", "pending", "T", &["fox"]), ("b.jpg", "pending", "T", &["tag"]), ("c.jpg", "submitted", "T", &["x"])]);
        let touched = state::bulk_edit(&conn, &names(&["a.jpg", "b.jpg", "c.jpg"]), "tag", Some("7")).unwrap();
        assert_eq!(touched, 2);
        let rows = state::files_to_describe(&conn).unwrap();
        assert_eq!(rows[0].keywords, names(&["fox", "tag"]));
        assert_eq!(rows[1].keywords, names(&["tag"]));
        assert!(rows.iter().all(|row| row.category == "7"));
        assert_eq!(state::bulk_edit(&conn, &names(&["a.jpg"]), "", None).unwrap(), 0);
    }

    #[test]
    fn status_moves_need_text_except_back_to_new() {
        let conn = db_with(&[("a.jpg", "pending", "T", &["k"]), ("b.jpg", "needs_metadata", "", &[]), ("s.jpg", "submitted", "T", &["k"])]);
        let report = set_status(&conn, &names(&["a.jpg", "b.jpg", "s.jpg"]), "review", 1000).unwrap();
        assert_eq!(report.count, 1, "b has no text and s is final");
        assert_eq!(status_of(&conn, "a.jpg"), "review");
        let error: Option<String> = conn.query_row("SELECT error FROM files WHERE filename = 'a.jpg'", [], |r| r.get(0)).unwrap();
        assert_eq!(error.as_deref(), Some(state::MANUAL_REVIEW_REASON));
        set_status(&conn, &names(&["a.jpg"]), "needs_metadata", 1000).unwrap();
        assert_eq!(status_of(&conn, "a.jpg"), "needs_metadata");
        assert!(state::set_status(&conn, &names(&["a.jpg"]), "invalid").is_err());
    }

    #[test]
    fn approving_releases_flagged_files_into_batches_and_logs() {
        let conn = db_with(&[("a.jpg", "review", "T", &["k"]), ("b.jpg", "review", "T", &["k"])]);
        let report = approve(&conn, Some(&names(&["a.jpg"])), 1000).unwrap();
        assert_eq!((report.count, report.new_batches), (1, 1));
        assert_eq!(status_of(&conn, "a.jpg"), "pending");
        assert_eq!(status_of(&conn, "b.jpg"), "review");
        assert_eq!(approve(&conn, None, 1000).unwrap().count, 1);
        let message: String = conn.query_row("SELECT message FROM events ORDER BY id LIMIT 1", [], |r| r.get(0)).unwrap();
        assert_eq!(message, "approved 1 files (a.jpg), new batches 1");
    }
}
