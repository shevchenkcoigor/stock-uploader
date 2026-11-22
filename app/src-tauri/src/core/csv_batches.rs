//! What the CSV page lists: the batches that have a CSV, their rows and the state of each CSV
//! (port of `csv_batches.py`).

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use rusqlite::{params, Connection};
use serde::Serialize;

use super::metadata_csv::{adobe_csv_bytes, check_items, missing_category_problems, Item};
use super::state::{self, INVALID, UPLOADED};
use crate::error::AppResult;

pub const BUILT: &str = "built";
pub const SENT: &str = "sent";
pub const ERROR: &str = "error";
/// Files a batch's CSV covers: described ones waiting for upload, uploaded ones, and submitted ones once
/// the batch is sent.
const CSV_FILE_STATUSES: [&str; 3] = [state::PENDING, state::UPLOADED, state::SUBMITTED];

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct CsvEntry {
    pub batch_id: i64,
    pub name: String,
    pub rows: usize,
    pub bad_rows: usize,
    pub state: &'static str,
    /// The CSV file on disk.
    pub path: String,
    pub folder: String,
    pub part: i64,
    /// (status, files) of batch files not on SFTP yet.
    pub waiting: Vec<(String, i64)>,
    /// When the CSV appeared on disk (epoch seconds); 0 when it is missing.
    pub created: f64,
    /// When it was last written; 0 when it is missing.
    pub updated: f64,
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct CsvRow {
    pub filename: String,
    pub path: String,
    pub title: String,
    pub keywords: Vec<String>,
    pub category: String,
    pub status: String,
}

/// The files a batch's CSV covers.
pub fn csv_rows(conn: &Connection, batch_id: i64) -> AppResult<Vec<CsvRow>> {
    let mut stmt = conn.prepare(
        "SELECT filename, path, title, keywords, category, status FROM files
         WHERE batch_id = ? AND status IN (?, ?, ?) ORDER BY id",
    )?;
    let rows = stmt.query_map(params![batch_id, CSV_FILE_STATUSES[0], CSV_FILE_STATUSES[1], CSV_FILE_STATUSES[2]], |row| {
        Ok(CsvRow {
            filename: row.get(0)?,
            path: row.get(1)?,
            title: row.get(2)?,
            keywords: state::parse_keywords(&row.get::<_, String>(3)?),
            category: row.get(4)?,
            status: row.get(5)?,
        })
    })?;
    Ok(rows.collect::<Result<_, _>>()?)
}

/// True when Adobe would not take this row as it is: no title, no keywords or a wrong category.
fn row_has_problem(row: &CsvRow, ai_label: &str) -> bool {
    let item = Item {
        filename: row.filename.clone(),
        title: row.title.clone(),
        keywords: row.keywords.clone(),
        category: row.category.clone(),
    };
    !check_items(std::slice::from_ref(&item), &HashSet::from([row.filename.clone()]), ai_label).is_empty()
        || !missing_category_problems(&[item]).is_empty()
}

/// Files of a batch that are not on SFTP yet (still pending or earlier), by status; empty when the whole batch
/// is uploaded.
/// Invalid files never get into a batch's CSV, so they do not hold the batch back.
pub fn batch_waiting(conn: &Connection, batch_id: i64) -> AppResult<Vec<(String, i64)>> {
    let mut stmt = conn.prepare(
        "SELECT status, COUNT(*) FROM files WHERE batch_id = ? AND status NOT IN (?, ?, ?)
         GROUP BY status ORDER BY status",
    )?;
    let rows = stmt
        .query_map(params![batch_id, UPLOADED, state::SUBMITTED, INVALID], |row| {
            Ok((row.get(0)?, row.get(1)?))
        })?;
    Ok(rows.collect::<Result<_, _>>()?)
}

fn epoch(time: std::io::Result<SystemTime>) -> f64 {
    time.ok()
        .and_then(|moment| moment.duration_since(SystemTime::UNIX_EPOCH).ok())
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

/// (created, updated) of a file in epoch seconds; (0, 0) when it cannot be read.
fn file_times(path: &Path) -> (f64, f64) {
    std::fs::metadata(path).map_or((0.0, 0.0), |meta| (epoch(meta.created()), epoch(meta.modified())))
}

fn last_name(path: &str) -> String {
    Path::new(path).file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
}

/// Batches with a built CSV, newest first.
pub fn csv_entries(conn: &Connection, ai_label: &str) -> AppResult<Vec<CsvEntry>> {
    let mut stmt =
        conn.prepare("SELECT id, csv_path, folder, part FROM batches WHERE csv_path IS NOT NULL ORDER BY id DESC")?;
    let batches = stmt
        .query_map([], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?, row.get::<_, i64>(3)?))
        })?
        .collect::<Result<Vec<_>, _>>()?;
    let mut entries = Vec::new();
    for (batch_id, csv_path, folder, part) in batches {
        let rows = csv_rows(conn, batch_id)?;
        if rows.is_empty() {
            continue;
        }
        let path = PathBuf::from(&csv_path);
        let bad_rows = rows.iter().filter(|row| row_has_problem(row, ai_label)).count();
        let kind = if rows.iter().all(|row| row.status == state::SUBMITTED) {
            SENT
        } else if bad_rows > 0 || !path.exists() {
            ERROR
        } else {
            BUILT
        };
        let (created, updated) = file_times(&path);
        entries.push(CsvEntry {
            batch_id,
            name: last_name(&csv_path),
            rows: rows.len(),
            bad_rows,
            state: kind,
            path: csv_path,
            folder: last_name(&folder),
            part,
            waiting: batch_waiting(conn, batch_id)?,
            created,
            updated,
        });
    }
    Ok(entries)
}

/// Batches that have described or uploaded files but no CSV yet: what «+ CSV» offers.
pub fn batches_without_csv(conn: &Connection) -> AppResult<Vec<state::PendingBatch>> {
    let mut stmt = conn.prepare(
        "SELECT b.id, b.folder, b.part, COUNT(*) FROM batches b
         JOIN files f ON f.batch_id = b.id AND f.status IN (?, ?)
         WHERE b.csv_path IS NULL GROUP BY b.id ORDER BY b.id",
    )?;
    let rows = stmt.query_map([state::PENDING, UPLOADED], |row| {
        Ok(state::PendingBatch { batch_id: row.get(0)?, folder: row.get(1)?, part: row.get(2)?, n: row.get(3)? })
    })?;
    Ok(rows.collect::<Result<_, _>>()?)
}

/// Whether a batch may be uploaded: its CSV must be built and still match the current descriptions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum CsvReadiness {
    Ready,
    /// No CSV was built for the batch (or the file is gone).
    Missing,
    /// The CSV differs from what the database says now: a description changed or files were added.
    Stale,
}

pub fn csv_readiness(conn: &Connection, batch_id: i64) -> AppResult<CsvReadiness> {
    let Some(path) = state::recorded_csv_path(conn, batch_id)? else { return Ok(CsvReadiness::Missing) };
    let Ok(on_disk) = std::fs::read(&path) else { return Ok(CsvReadiness::Missing) };
    // A CSV is recorded only after it passed every check, so the same bytes mean the same, valid content.
    let current = adobe_csv_bytes(&state::batch_items(conn, batch_id)?).ok();
    Ok(if current.as_deref() == Some(on_disk.as_slice()) { CsvReadiness::Ready } else { CsvReadiness::Stale })
}

/// Why the batch cannot be uploaded; empty when it can.
pub fn readiness_text(batch_id: i64, readiness: CsvReadiness) -> String {
    match readiness {
        CsvReadiness::Ready => String::new(),
        CsvReadiness::Missing => {
            format!("У батча {batch_id} нет CSV: сначала соберите его на странице «CSV».")
        }
        CsvReadiness::Stale => format!(
            "CSV батча {batch_id} устарел или содержит ошибки (описания изменились после сборки): пересоберите его на странице «CSV»."
        ),
    }
}
