//! Uploading the pending files of a batch (port of `services.upload_batch`): the status is written after
//! each file, so an interrupted run resumes where it stopped.

use std::collections::HashMap;
use std::path::PathBuf;

use rusqlite::Connection;

use super::sftp::Uploader;
use super::state;
use crate::error::AppResult;

#[derive(Debug, Default, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UploadReport {
    pub batch: i64,
    pub total: usize,
    pub uploaded: usize,
    pub failed: Vec<String>,
    /// The whole run could not start or was cut off (DNS, login, ...).
    pub error: Option<String>,
    pub stopped: bool,
}

/// One step of a run: a file finished, or a chunk of the current one went out.
#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UploadProgress {
    pub done: usize,
    pub total: usize,
    pub message: String,
    pub file: String,
    pub sent: u64,
    pub size: u64,
}

/// Uploads the pending files of `batch`. `connect` is called only when there is something to send.
pub fn upload_batch(
    conn: &Connection,
    batch: i64,
    connect: impl FnOnce() -> Result<Box<dyn Uploader>, String>,
    on_progress: &mut dyn FnMut(UploadProgress),
    should_stop: &dyn Fn() -> bool,
) -> AppResult<UploadReport> {
    let files = state::pending_files(conn, batch)?;
    let total = files.len();
    let mut report = UploadReport { batch, total, ..UploadReport::default() };
    if files.is_empty() {
        return Ok(report);
    }
    state::log_event(conn, "upload", &format!("batch {batch}: started, {total} files"), "info", None)?;
    let mut uploader = match connect() {
        Ok(uploader) => uploader,
        Err(error) => return fail_run(conn, report, batch, &error),
    };
    let mut errors: HashMap<String, String> = HashMap::new();
    let mut done = 0;
    for (path, name) in &files {
        if should_stop() {
            report.stopped = true;
            break;
        }
        let size = std::fs::metadata(path).map_or(0, |meta| meta.len());
        let progress = |message: String, sent: u64, done: usize| UploadProgress {
            done,
            total,
            message,
            file: name.clone(),
            sent,
            size,
        };
        on_progress(progress(format!("uploading {name}"), 0, done));
        let outcome = uploader.put(path, name, &mut |sent| on_progress(progress(format!("uploading {name}"), sent, done)));
        done += 1;
        match outcome {
            Ok(()) => {
                state::mark_uploaded(conn, path)?;
                state::log_event(conn, "upload", &format!("uploaded as {name}"), "info", Some(&file_name(path)))?;
                report.uploaded += 1;
                on_progress(progress(format!("uploaded {name}"), size, done));
            }
            Err(error) => {
                state::record_error(conn, path, &error)?;
                errors.insert(name.clone(), error.clone());
                report.failed.push(name.clone());
                on_progress(progress(format!("FAILED {name}: {error}"), 0, done));
            }
        }
    }
    for name in &report.failed {
        let text = errors.get(name).map_or("upload failed", String::as_str);
        state::log_event(conn, "upload", text, "error", Some(name))?;
    }
    let level = if report.failed.is_empty() { "info" } else { "error" };
    state::log_event(
        conn,
        "upload",
        &format!(
            "batch {batch}: {}, {} uploaded, {} failed",
            if report.stopped { "cancelled" } else { "finished" },
            report.uploaded,
            report.failed.len()
        ),
        level,
        None,
    )?;
    Ok(report)
}

fn file_name(path: &PathBuf) -> String {
    path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
}

/// DNS, network or login problems end the whole run; the files stay pending.
fn fail_run(conn: &Connection, mut report: UploadReport, batch: i64, error: &str) -> AppResult<UploadReport> {
    let message = format!("cannot upload batch {batch}: {error}");
    state::log_event(conn, "upload", &message, "error", None)?;
    report.error = Some(message);
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    struct Fake {
        sent: Vec<String>,
        fail_on: Option<&'static str>,
    }

    impl Uploader for Fake {
        fn put(&mut self, _local: &Path, remote: &str, on_bytes: &mut dyn FnMut(u64)) -> Result<(), String> {
            if self.fail_on == Some(remote) {
                return Err("Permission denied".to_string());
            }
            on_bytes(5);
            self.sent.push(remote.to_string());
            Ok(())
        }
    }

    fn setup(count: usize) -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = state::open_db(&dir.path().join("state.db")).unwrap();
        conn.execute("INSERT INTO batches (folder, part, created_at) VALUES ('/r', 1, 't')", []).unwrap();
        for index in 0..count {
            let path = dir.path().join(format!("p{index}.jpg"));
            std::fs::write(&path, b"12345").unwrap();
            conn.execute(
                "INSERT INTO files (path, filename, size, sha256, root, status, batch_id, updated_at)
                 VALUES (?, ?, 5, ?, '/r', 'pending', 1, 't')",
                rusqlite::params![path.to_string_lossy(), format!("p{index}.jpg"), format!("h{index}")],
            )
            .unwrap();
        }
        (dir, conn)
    }

    fn statuses(conn: &Connection) -> Vec<(String, Option<String>)> {
        let mut stmt = conn.prepare("SELECT status, error FROM files ORDER BY id").unwrap();
        stmt.query_map([], |row| Ok((row.get(0)?, row.get(1)?))).unwrap().collect::<Result<_, _>>().unwrap()
    }

    #[test]
    fn every_file_is_uploaded_and_marked_one_by_one() {
        let (_dir, conn) = setup(3);
        let mut steps = Vec::new();
        let report = upload_batch(&conn, 1, || Ok(Box::new(Fake { sent: vec![], fail_on: None })), &mut |p| steps.push(p.message), &|| false).unwrap();
        assert_eq!((report.total, report.uploaded, report.failed.len(), report.stopped), (3, 3, 0, false));
        assert!(statuses(&conn).iter().all(|(status, error)| status == "uploaded" && error.is_none()));
        assert_eq!(steps.last().unwrap(), "uploaded p2.jpg");
    }

    #[test]
    fn a_failed_file_keeps_its_error_and_stays_pending_while_the_rest_go_on() {
        let (_dir, conn) = setup(3);
        let report = upload_batch(&conn, 1, || Ok(Box::new(Fake { sent: vec![], fail_on: Some("p1.jpg") })), &mut |_| {}, &|| false).unwrap();
        assert_eq!((report.uploaded, report.failed.clone()), (2, vec!["p1.jpg".to_string()]));
        let rows = statuses(&conn);
        assert_eq!(rows[1], ("pending".to_string(), Some("Permission denied".to_string())));
        assert_eq!(rows[2].0, "uploaded");
    }

    #[test]
    fn a_connection_failure_ends_the_run_and_leaves_everything_pending() {
        let (_dir, conn) = setup(2);
        let report = upload_batch(&conn, 1, || Err("вход не выполнен: bad password".to_string()), &mut |_| {}, &|| false).unwrap();
        assert_eq!(report.error.as_deref(), Some("cannot upload batch 1: вход не выполнен: bad password"));
        assert!(statuses(&conn).iter().all(|(status, _)| status == "pending"));
    }

    #[test]
    fn stopping_leaves_the_rest_untouched_and_an_empty_batch_never_connects() {
        let (_dir, conn) = setup(2);
        let report = upload_batch(&conn, 1, || Ok(Box::new(Fake { sent: vec![], fail_on: None })), &mut |_| {}, &|| true).unwrap();
        assert!(report.stopped && report.uploaded == 0);
        assert!(statuses(&conn).iter().all(|(status, error)| status == "pending" && error.is_none()));

        let empty = upload_batch(&conn, 99, || panic!("must not connect"), &mut |_| {}, &|| false).unwrap();
        assert_eq!(empty.total, 0);
    }

    #[test]
    fn the_queue_queries_list_batches_counts_and_files() {
        let (_dir, conn) = setup(2);
        assert_eq!(state::upload_batches(&conn).unwrap()[0].n, 2);
        // an uploaded file keeps its batch in the list; a submitted one lets it go
        conn.execute("UPDATE files SET status = 'uploaded'", []).unwrap();
        assert_eq!(state::upload_batches(&conn).unwrap()[0].n, 2);
        conn.execute("UPDATE files SET status = 'submitted'", []).unwrap();
        assert!(state::upload_batches(&conn).unwrap().is_empty());
        assert_eq!(state::batch_status_counts(&conn).unwrap(), vec![(1, "pending".to_string(), 2)]);
        assert_eq!(state::batch_files(&conn, 1).unwrap()[1].filename, "p1.jpg");
    }
}
