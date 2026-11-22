//! The SQLite state database: schema, scan registration, batches, event log. Port of `state.py`
//! (only what the pages migrated so far need). The schema text is identical to the Python one.

use std::collections::HashMap;
use std::io::Read;
use std::path::{Path, PathBuf};

use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use sha2::{Digest, Sha256};

use super::metadata_csv::{split_into_batches, Item};
use crate::error::AppResult;

pub const PENDING: &str = "pending";
pub const INVALID: &str = "invalid";
pub const UPLOADED: &str = "uploaded";
pub const SUBMITTED: &str = "submitted";
pub const NEEDS_METADATA: &str = "needs_metadata";
pub const REVIEW: &str = "review";

const HASH_SUFFIX_CHARS: usize = 8;
const HASH_CHUNK_BYTES: usize = 1024 * 1024;

pub const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY,
    folder TEXT NOT NULL,
    part INTEGER NOT NULL,
    csv_path TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (folder, part)
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER REFERENCES batches(id),
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    root TEXT NOT NULL DEFAULT '',
    people_visible INTEGER NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT,
    updated_at TEXT NOT NULL
);
-- Append-only history of what the commands did; never rewritten by other operations.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    command TEXT NOT NULL,
    level TEXT NOT NULL,
    filename TEXT,
    message TEXT NOT NULL
);
-- The SFTP root is flat, so a name may be used by only one live (non-invalid) file.
CREATE UNIQUE INDEX IF NOT EXISTS files_live_filename
    ON files(filename) WHERE status != 'invalid';
";

/// Keywords as Python's `json.dumps(list)` writes them (`["a", "b"]`, non-ASCII as `\uXXXX`), so rows
/// written here are byte-identical to the ones the Python app writes.
pub fn keywords_json(keywords: &[String]) -> String {
    let encoded: Vec<String> = keywords
        .iter()
        .map(|word| {
            let quoted = serde_json::to_string(word).unwrap_or_else(|_| "\"\"".to_string());
            let mut out = String::with_capacity(quoted.len());
            for ch in quoted.chars() {
                if ch.is_ascii() {
                    out.push(ch);
                } else {
                    let mut units = [0u16; 2];
                    for unit in ch.encode_utf16(&mut units) {
                        out.push_str(&format!("\\u{unit:04x}"));
                    }
                }
            }
            out
        })
        .collect();
    format!("[{}]", encoded.join(", "))
}

pub fn parse_keywords(raw: &str) -> Vec<String> {
    serde_json::from_str(raw).unwrap_or_default()
}

/// Same text as Python's `datetime.now(UTC).isoformat(timespec="seconds")`.
pub fn now() -> String {
    Utc::now().format("%Y-%m-%dT%H:%M:%S+00:00").to_string()
}

pub fn open_db(path: &Path) -> AppResult<Connection> {
    let conn = Connection::open(path)?;
    conn.execute_batch(SCHEMA)?;
    add_missing_columns(&conn)?;
    Ok(conn)
}

/// Upgrades databases created before a column existed.
fn add_missing_columns(conn: &Connection) -> AppResult<()> {
    let mut stmt = conn.prepare("SELECT name FROM pragma_table_info('files')")?;
    let columns: Vec<String> = stmt.query_map([], |row| row.get(0))?.collect::<Result<_, _>>()?;
    let additions = [
        ("mtime_ns", "INTEGER NOT NULL DEFAULT 0"),
        ("root", "TEXT NOT NULL DEFAULT ''"),
        ("people_visible", "INTEGER NOT NULL DEFAULT 0"),
        ("content_type", "TEXT NOT NULL DEFAULT ''"),
    ];
    for (name, ddl) in additions {
        if !columns.iter().any(|column| column == name) {
            conn.execute(&format!("ALTER TABLE files ADD COLUMN {name} {ddl}"), [])?;
        }
    }
    Ok(())
}

pub fn sha256_of(path: &Path) -> std::io::Result<String> {
    let mut digest = Sha256::new();
    let mut file = std::fs::File::open(path)?;
    let mut chunk = vec![0u8; HASH_CHUNK_BYTES];
    loop {
        let read = file.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        digest.update(&chunk[..read]);
    }
    Ok(digest.finalize().iter().map(|byte| format!("{byte:02x}")).collect())
}

pub fn log_event(conn: &Connection, command: &str, message: &str, level: &str, filename: Option<&str>) -> AppResult<()> {
    conn.execute(
        "INSERT INTO events (at, command, level, filename, message) VALUES (?, ?, ?, ?, ?)",
        params![now(), command, level, filename, message],
    )?;
    Ok(())
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct ScanResult {
    pub added: usize,
    pub unchanged: usize,
    pub invalid: usize,
    pub needs_metadata: usize,
    pub changed_after_upload: Vec<String>,
    pub batches_created: usize,
}

/// What the scan needs to know about a file: size and modification time in nanoseconds.
struct Stat {
    size: i64,
    mtime_ns: i64,
}

fn stat_of(path: &Path) -> std::io::Result<Stat> {
    let meta = std::fs::metadata(path)?;
    let size = i64::try_from(meta.len()).unwrap_or(i64::MAX);
    #[cfg(unix)]
    let mtime_ns = {
        use std::os::unix::fs::MetadataExt;
        meta.mtime() * 1_000_000_000 + meta.mtime_nsec()
    };
    #[cfg(not(unix))]
    let mtime_ns = meta
        .modified()?
        .duration_since(std::time::UNIX_EPOCH)
        .map(|since| i64::try_from(since.as_nanos()).unwrap_or(i64::MAX))
        .unwrap_or(0);
    Ok(Stat { size, mtime_ns })
}

struct KnownFile {
    id: i64,
    status: String,
    size: i64,
    mtime_ns: i64,
    sha256: String,
}

fn known_file(conn: &Connection, image: &Path) -> AppResult<Option<KnownFile>> {
    let row = conn
        .query_row(
            "SELECT id, status, size, mtime_ns, sha256 FROM files WHERE path = ?",
            [image.to_string_lossy()],
            |row| {
                Ok(KnownFile {
                    id: row.get(0)?,
                    status: row.get(1)?,
                    size: row.get(2)?,
                    mtime_ns: row.get(3)?,
                    sha256: row.get(4)?,
                })
            },
        )
        .optional()?;
    Ok(row.filter(|file| file.status != INVALID))
}

fn file_name(path: &Path) -> String {
    path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
}

fn problems_of(conn: &Connection, image: &Path, digest: &str, check_image: &dyn Fn(&Path) -> Vec<String>) -> AppResult<Vec<String>> {
    let mut problems = check_image(image);
    let duplicate: Option<String> = conn
        .query_row(
            "SELECT path FROM files WHERE sha256 = ? AND path != ? AND status != ? LIMIT 1",
            params![digest, image.to_string_lossy(), INVALID],
            |row| row.get(0),
        )
        .optional()?;
    if let Some(path) = duplicate {
        problems.push(format!("same content as {path}"));
    }
    Ok(problems)
}

/// The flat SFTP/CSV name; a content-hash suffix keeps names of different files apart.
fn remote_name(conn: &Connection, image: &Path, digest: &str) -> AppResult<String> {
    let name = file_name(image);
    let taken = conn
        .query_row(
            "SELECT 1 FROM files WHERE filename = ? AND path != ? AND status != ?",
            params![name, image.to_string_lossy(), INVALID],
            |_| Ok(()),
        )
        .optional()?
        .is_some();
    if !taken {
        return Ok(name);
    }
    let stem = image.file_stem().map(|stem| stem.to_string_lossy().into_owned()).unwrap_or_default();
    let suffix = image.extension().map(|ext| format!(".{}", ext.to_string_lossy())).unwrap_or_default();
    Ok(format!("{stem}_{}{suffix}", &digest[..HASH_SUFFIX_CHARS]))
}

struct Registration<'a> {
    image: &'a Path,
    filename: &'a str,
    digest: &'a str,
    stat: &'a Stat,
    item: Option<&'a Item>,
    status: &'a str,
    error: Option<&'a str>,
    root: &'a Path,
}

fn upsert(conn: &Connection, entry: &Registration) -> AppResult<()> {
    let keywords = entry.item.map(|item| keywords_json(&item.keywords)).unwrap_or_else(|| "[]".to_string());
    conn.execute(
        "INSERT INTO files (path, filename, sha256, size, mtime_ns, root, title, keywords, category,
                            status, error, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(path) DO UPDATE SET
            filename=excluded.filename, sha256=excluded.sha256, size=excluded.size,
            mtime_ns=excluded.mtime_ns, root=excluded.root, title=excluded.title,
            keywords=excluded.keywords,
            category=excluded.category, status=excluded.status, error=excluded.error,
            batch_id=NULL, updated_at=excluded.updated_at",
        params![
            entry.image.to_string_lossy(),
            entry.filename,
            entry.digest,
            entry.stat.size,
            entry.stat.mtime_ns,
            entry.root.to_string_lossy(),
            entry.item.map(|item| item.title.as_str()).unwrap_or(""),
            keywords,
            entry.item.map(|item| item.category.as_str()).unwrap_or(""),
            entry.status,
            entry.error,
            now(),
        ],
    )?;
    Ok(())
}

pub type CheckImage<'a> = &'a dyn Fn(&Path) -> Vec<String>;
pub type ProgressFn<'a> = &'a mut dyn FnMut(usize, usize, &str);

/// Registers new files, skips known ones and groups new valid files into batches.
///
/// `on_progress(done, total, path)` runs before each file; `should_stop()` ends the scan early (what
/// was registered so far is kept). `check_image` runs only for new or changed files, so a rescan of a
/// big tree stays cheap: known files are recognised by size + mtime without hashing.
pub fn scan_folder(
    conn: &mut Connection,
    root: &Path,
    images: &[PathBuf],
    metadata: &HashMap<PathBuf, Item>,
    check_image: CheckImage,
    batch_size: Option<usize>,
    on_progress: ProgressFn,
    should_stop: &dyn Fn() -> bool,
) -> AppResult<ScanResult> {
    let tx = conn.transaction()?;
    let mut result = ScanResult::default();
    for (position, image) in images.iter().enumerate() {
        if should_stop() {
            break;
        }
        on_progress(position, images.len(), &image.to_string_lossy());
        let stat = stat_of(image)?;
        let mut known = known_file(&tx, image)?;
        // a metadata row appeared since the last scan
        if known.as_ref().is_some_and(|file| file.status == NEEDS_METADATA) && metadata.contains_key(image) {
            known = None;
        }
        if known.as_ref().is_some_and(|file| file.size == stat.size && file.mtime_ns == stat.mtime_ns) {
            result.unchanged += 1;
            continue;
        }
        let digest = sha256_of(image)?;
        if let Some(file) = known.as_ref().filter(|file| file.sha256 == digest) {
            tx.execute("UPDATE files SET mtime_ns = ? WHERE id = ?", params![stat.mtime_ns, file.id])?;
            result.unchanged += 1;
            continue;
        }
        if known.as_ref().is_some_and(|file| file.status == UPLOADED || file.status == SUBMITTED) {
            result.changed_after_upload.push(file_name(image));
            continue;
        }
        let problems = problems_of(&tx, image, &digest, check_image)?;
        let base = Registration {
            image,
            filename: "",
            digest: &digest,
            stat: &stat,
            item: None,
            status: INVALID,
            error: None,
            root,
        };
        if !problems.is_empty() {
            let name = file_name(image);
            let error = problems.join("; ");
            upsert(&tx, &Registration { filename: &name, error: Some(&error), ..base })?;
            result.invalid += 1;
            continue;
        }
        let name = remote_name(&tx, image, &digest)?;
        match metadata.get(image) {
            Some(found) => {
                let item = Item { filename: name.clone(), ..found.clone() };
                upsert(&tx, &Registration { filename: &name, item: Some(&item), status: PENDING, ..base })?;
                result.added += 1;
            }
            None => {
                upsert(&tx, &Registration { filename: &name, status: NEEDS_METADATA, ..base })?;
                result.needs_metadata += 1;
            }
        }
    }
    result.batches_created = batch_unassigned(&tx, batch_size)?;
    tx.commit()?;
    Ok(result)
}

struct PendingRow {
    path: String,
    root: String,
    item: Item,
}

fn item_of(filename: String, title: String, keywords: &str, category: String) -> Item {
    Item { filename, title, keywords: parse_keywords(keywords), category }
}

/// Puts pending files that have no batch yet into new batches, grouped by scan root.
/// The caller owns the transaction. Returns the number of batches created.
pub fn batch_unassigned(conn: &Connection, batch_size: Option<usize>) -> AppResult<usize> {
    let mut stmt = conn.prepare(
        "SELECT path, root, filename, title, keywords, category FROM files
         WHERE status = ? AND batch_id IS NULL ORDER BY id",
    )?;
    let rows: Vec<PendingRow> = stmt
        .query_map([PENDING], |row| {
            Ok(PendingRow {
                path: row.get(0)?,
                root: row.get(1)?,
                item: item_of(row.get(2)?, row.get(3)?, &row.get::<_, String>(4)?, row.get(5)?),
            })
        })?
        .collect::<Result<_, _>>()?;
    let mut by_root: Vec<(String, Vec<PendingRow>)> = Vec::new();
    for row in rows {
        match by_root.iter_mut().find(|(root, _)| *root == row.root) {
            Some((_, group)) => group.push(row),
            None => by_root.push((row.root.clone(), vec![row])),
        }
    }
    let mut created = 0;
    for (root, group) in by_root {
        let entries: Vec<(String, Item)> = group.into_iter().map(|row| (row.path, row.item)).collect();
        created += assign_batches_for(conn, &root, entries, batch_size)?;
    }
    Ok(created)
}

/// The latest batch of `root` while it can still take files: no CSV yet and every file pending.
fn open_batch(conn: &Connection, root: &str) -> AppResult<Option<(i64, Vec<(String, Item)>)>> {
    let batch: Option<(i64, Option<String>)> = conn
        .query_row(
            "SELECT id, csv_path FROM batches WHERE folder = ? ORDER BY part DESC LIMIT 1",
            [root],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    let Some((id, csv_path)) = batch else { return Ok(None) };
    if csv_path.is_some_and(|path| !path.is_empty()) {
        return Ok(None);
    }
    let mut stmt = conn
        .prepare("SELECT path, filename, title, keywords, category, status FROM files WHERE batch_id = ? ORDER BY id")?;
    let rows: Vec<(String, Item, String)> = stmt
        .query_map([id], |row| {
            let item = item_of(row.get(1)?, row.get(2)?, &row.get::<_, String>(3)?, row.get(4)?);
            Ok((row.get(0)?, item, row.get(5)?))
        })?
        .collect::<Result<_, _>>()?;
    if rows.is_empty() || rows.iter().any(|(_, _, status)| status != PENDING) {
        return Ok(None);
    }
    Ok(Some((id, rows.into_iter().map(|(path, item, _)| (path, item)).collect())))
}

/// Puts `new_valid` into batches of `root`: first into the open batch, the overflow into new ones.
fn assign_batches_for(conn: &Connection, root: &str, new_valid: Vec<(String, Item)>, batch_size: Option<usize>) -> AppResult<usize> {
    if new_valid.is_empty() {
        return Ok(0);
    }
    let (open_id, open_files) = match open_batch(conn, root)? {
        Some((id, files)) => (Some(id), files),
        None => (None, Vec::new()),
    };
    let path_by_name: HashMap<String, String> = new_valid
        .iter()
        .chain(open_files.iter())
        .map(|(path, item)| (item.filename.clone(), path.clone()))
        .collect();
    let items: Vec<Item> = open_files.iter().chain(new_valid.iter()).map(|(_, item)| item.clone()).collect();
    let next_part: i64 =
        conn.query_row("SELECT COALESCE(MAX(part), 0) + 1 FROM batches WHERE folder = ?", [root], |row| row.get(0))?;
    let mut created: usize = 0;
    for (index, group) in split_into_batches(&items, batch_size).iter().enumerate() {
        let batch_id = match open_id.filter(|_| index == 0) {
            Some(id) => id,
            None => {
                let part = next_part + i64::try_from(created).unwrap_or(0);
                conn.execute(
                    "INSERT INTO batches (folder, part, created_at) VALUES (?, ?, ?)",
                    params![root, part, now()],
                )?;
                created += 1;
                conn.last_insert_rowid()
            }
        };
        for item in group {
            conn.execute("UPDATE files SET batch_id = ? WHERE path = ?", params![batch_id, path_by_name[&item.filename]])?;
        }
    }
    Ok(created)
}

/// Invalid files under `root` as (path, error). Compared as paths, not with a LIKE pattern: a pattern
/// breaks on backslashes and treats `_` and `%` in folder names as wildcards.
pub fn invalid_files(conn: &Connection, root: &Path) -> AppResult<Vec<(String, String)>> {
    let mut stmt = conn.prepare("SELECT path, error FROM files WHERE status = ? ORDER BY path")?;
    let rows: Vec<(String, Option<String>)> =
        stmt.query_map([INVALID], |row| Ok((row.get(0)?, row.get(1)?)))?.collect::<Result<_, _>>()?;
    Ok(rows
        .into_iter()
        .filter(|(path, _)| Path::new(path).starts_with(root))
        .map(|(path, error)| (path, error.unwrap_or_default()))
        .collect())
}

// ---------------------------------------------------------------------------- description page

/// What the Description page lists and lets a person edit: new, described, flagged for review, uploaded.
/// Invalid files stay on the scan page; submitted ones are final.
pub const EDITABLE_STATUSES: [&str; 4] = [NEEDS_METADATA, PENDING, REVIEW, UPLOADED];
pub const MANUAL_REVIEW_REASON: &str = "Отмечено на проверку вручную";

#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FileRow {
    pub filename: String,
    pub path: String,
    pub size: i64,
    pub title: String,
    pub keywords: Vec<String>,
    pub category: String,
    pub status: String,
    pub error: Option<String>,
    pub updated_at: String,
}

pub fn files_to_describe(conn: &Connection) -> AppResult<Vec<FileRow>> {
    let mut stmt = conn.prepare(
        "SELECT filename, path, size, title, keywords, category, status, error, updated_at
         FROM files WHERE status IN (?, ?, ?, ?) ORDER BY id",
    )?;
    let rows = stmt
        .query_map(params![EDITABLE_STATUSES[0], EDITABLE_STATUSES[1], EDITABLE_STATUSES[2], EDITABLE_STATUSES[3]], |row| {
            Ok(FileRow {
                filename: row.get(0)?,
                path: row.get(1)?,
                size: row.get(2)?,
                title: row.get(3)?,
                keywords: parse_keywords(&row.get::<_, String>(4)?),
                category: row.get(5)?,
                status: row.get(6)?,
                error: row.get(7)?,
                updated_at: row.get(8)?,
            })
        })?
        .collect::<Result<_, _>>()?;
    Ok(rows)
}

/// Saves a person's edit. A file waiting for metadata becomes pending once it has a title and keywords.
/// Only the editable statuses change: not invalid ones and not those already submitted.
pub fn update_metadata(conn: &Connection, filename: &str, title: &str, keywords: &[String], category: &str) -> AppResult<bool> {
    let keywords = keywords_json(keywords);
    let changed = conn.execute(
        "UPDATE files SET title = ?, keywords = ?, category = ?, updated_at = ?,
         status = CASE WHEN status = ? AND ? != '' AND ? != '[]' THEN ? ELSE status END
         WHERE filename = ? AND status IN (?, ?, ?, ?)",
        params![
            title, keywords, category, now(), NEEDS_METADATA, title, keywords, PENDING, filename,
            EDITABLE_STATUSES[0], EDITABLE_STATUSES[1], EDITABLE_STATUSES[2], EDITABLE_STATUSES[3]
        ],
    )?;
    Ok(changed > 0)
}

/// Adds one keyword to, and/or sets the category of, several files; returns how many were touched.
/// The keyword is appended (never twice, at most MAX_KEYWORDS), the category replaces the old one.
pub fn bulk_edit(conn: &Connection, filenames: &[String], tag: &str, category: Option<&str>) -> AppResult<usize> {
    let tag = tag.trim();
    if filenames.is_empty() || (tag.is_empty() && category.is_none()) {
        return Ok(0);
    }
    let tx = conn.unchecked_transaction()?;
    let mut changed = 0;
    for filename in filenames {
        let row: Option<(String, String, String)> = tx
            .query_row(
                "SELECT title, keywords, category FROM files WHERE filename = ? AND status IN (?, ?, ?, ?)",
                params![filename, EDITABLE_STATUSES[0], EDITABLE_STATUSES[1], EDITABLE_STATUSES[2], EDITABLE_STATUSES[3]],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .optional()?;
        let Some((title, raw_keywords, old_category)) = row else { continue };
        let mut keywords = parse_keywords(&raw_keywords);
        if !tag.is_empty() && !keywords.iter().any(|word| word == tag) && keywords.len() < super::metadata_csv::MAX_KEYWORDS {
            keywords.push(tag.to_string());
        }
        update_metadata(&tx, filename, &title, &keywords, category.unwrap_or(&old_category))?;
        changed += 1;
    }
    tx.commit()?;
    Ok(changed)
}

/// Moves files between the statuses of the Description page by hand; returns how many were moved.
/// Only files of those statuses move, and a file already in `status` is not counted. Everything but "new"
/// needs a title and keywords: a file without them stays as it is.
pub fn set_status(conn: &Connection, filenames: &[String], status: &str) -> AppResult<usize> {
    if !EDITABLE_STATUSES.contains(&status) {
        return Err(crate::error::AppError::Invalid(format!("not a status of the description page: {status}")));
    }
    if filenames.is_empty() {
        return Ok(0);
    }
    let needs_text = if status == NEEDS_METADATA { "" } else { " AND title != '' AND keywords != '[]'" };
    let reason = (status == REVIEW).then_some(MANUAL_REVIEW_REASON);
    let names = serde_json::to_string(filenames)?;
    let sql = format!(
        "UPDATE files SET status = ?, error = ?, updated_at = ? WHERE status != ? \
         AND status IN (?, ?, ?, ?) AND filename IN (SELECT value FROM json_each(?)){needs_text}"
    );
    Ok(conn.execute(
        &sql,
        params![status, reason, now(), status, EDITABLE_STATUSES[0], EDITABLE_STATUSES[1], EDITABLE_STATUSES[2], EDITABLE_STATUSES[3], names],
    )?)
}

/// Releases flagged files into the pending queue; `None` approves all of them.
pub fn approve_reviewed(conn: &Connection, filenames: Option<&[String]>) -> AppResult<usize> {
    match filenames {
        None => Ok(conn.execute(
            "UPDATE files SET status = ?, error = NULL, updated_at = ? WHERE status = ?",
            params![PENDING, now(), REVIEW],
        )?),
        Some([]) => Ok(0),
        Some(names) => Ok(conn.execute(
            "UPDATE files SET status = ?, error = NULL, updated_at = ? WHERE status = ?
             AND filename IN (SELECT value FROM json_each(?))",
            params![PENDING, now(), REVIEW, serde_json::to_string(names)?],
        )?),
    }
}

pub fn assign_batches(conn: &Connection, batch_size: Option<usize>) -> AppResult<usize> {
    let tx = conn.unchecked_transaction()?;
    let created = batch_unassigned(&tx, batch_size)?;
    tx.commit()?;
    Ok(created)
}

/// Statuses whose files may be captioned again (their text is replaced).
pub const RECAPTIONABLE: [&str; 3] = [NEEDS_METADATA, PENDING, REVIEW];

/// Paths of new files waiting for a title and keywords; `names` narrows it to those files.
/// With `redo` the picked files that already have a description count too.
pub fn files_needing_metadata(conn: &Connection, limit: usize, names: Option<&[String]>, redo: bool) -> AppResult<Vec<PathBuf>> {
    let statuses: &[&str] = if redo && names.is_some() { &RECAPTIONABLE } else { &[NEEDS_METADATA] };
    let marks = vec!["?"; statuses.len()].join(",");
    let mut values: Vec<rusqlite::types::Value> = statuses.iter().map(|status| (*status).to_string().into()).collect();
    let mut sql = format!("SELECT path FROM files WHERE status IN ({marks})");
    if let Some(list) = names {
        sql.push_str(" AND filename IN (SELECT value FROM json_each(?))");
        values.push(serde_json::to_string(list)?.into());
    }
    sql.push_str(" ORDER BY id LIMIT ?");
    values.push(i64::try_from(limit).unwrap_or(i64::MAX).into());
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(rusqlite::params_from_iter(values), |row| row.get::<_, String>(0))?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?.into_iter().map(PathBuf::from).collect())
}

pub struct Generated<'a> {
    pub title: &'a str,
    pub keywords: &'a [String],
    pub category: &'a str,
    pub people_visible: bool,
    pub content_type: &'a str,
    pub review_reason: &'a str,
}

/// Attaches generated metadata. The file becomes pending (ready to be batched), unless a review reason
/// is set: then it goes to `review` and stays out of batches until a person approves it. Only a new file
/// is filled in, so what a person edited meanwhile is never overwritten; with `redo` a described or
/// flagged file is replaced too.
pub fn set_metadata(conn: &Connection, path: &Path, data: &Generated, redo: bool) -> AppResult<()> {
    let status = if data.review_reason.is_empty() { PENDING } else { REVIEW };
    let allowed: &[&str] = if redo { &RECAPTIONABLE } else { &[NEEDS_METADATA] };
    let marks = vec!["?"; allowed.len()].join(",");
    let reason = (!data.review_reason.is_empty()).then_some(data.review_reason);
    let mut values: Vec<rusqlite::types::Value> = vec![
        data.title.to_string().into(),
        keywords_json(data.keywords).into(),
        data.category.to_string().into(),
        i64::from(data.people_visible).into(),
        data.content_type.to_string().into(),
        status.to_string().into(),
        reason.map(str::to_string).into(),
        now().into(),
        path.to_string_lossy().into_owned().into(),
    ];
    values.extend(allowed.iter().map(|value| (*value).to_string().into()));
    conn.execute(
        &format!(
            "UPDATE files SET title = ?, keywords = ?, category = ?, people_visible = ?, content_type = ?, \
             status = ?, error = ?, updated_at = ? WHERE path = ? AND status IN ({marks})"
        ),
        rusqlite::params_from_iter(values),
    )?;
    Ok(())
}

pub fn record_error(conn: &Connection, path: &Path, error: &str) -> AppResult<()> {
    conn.execute(
        "UPDATE files SET error = ?, updated_at = ? WHERE path = ?",
        params![error, now(), path.to_string_lossy()],
    )?;
    Ok(())
}

/// A batch that still has pending files, with how many.
#[derive(Debug, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PendingBatch {
    pub batch_id: i64,
    pub folder: String,
    pub part: i64,
    pub n: i64,
}

/// Batches that still have pending files, oldest first, with their pending counts.
pub fn pending_batches(conn: &Connection) -> AppResult<Vec<PendingBatch>> {
    let mut stmt = conn.prepare(
        "SELECT b.id, b.folder, b.part, COUNT(*) FROM files f JOIN batches b ON b.id = f.batch_id
         WHERE f.status = ? GROUP BY b.id ORDER BY b.id",
    )?;
    let rows = stmt.query_map([PENDING], |row| {
        Ok(PendingBatch { batch_id: row.get(0)?, folder: row.get(1)?, part: row.get(2)?, n: row.get(3)? })
    })?;
    Ok(rows.collect::<Result<_, _>>()?)
}

/// For every batch the number of its files per status: what the batch's own status is made of.
pub fn batch_status_counts(conn: &Connection) -> AppResult<Vec<(i64, String, i64)>> {
    let mut stmt = conn.prepare(
        "SELECT batch_id, status, COUNT(*) FROM files WHERE batch_id IS NOT NULL
         GROUP BY batch_id, status ORDER BY batch_id, status",
    )?;
    let rows = stmt.query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))?;
    Ok(rows.collect::<Result<_, _>>()?)
}

/// A file of a batch as the upload queue shows it.
#[derive(Debug, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QueueFile {
    pub path: String,
    pub filename: String,
    pub status: String,
    pub size: i64,
    pub error: Option<String>,
}

/// Every file of a batch with its status, in upload order.
pub fn batch_files(conn: &Connection, batch_id: i64) -> AppResult<Vec<QueueFile>> {
    let mut stmt =
        conn.prepare("SELECT path, filename, status, size, error FROM files WHERE batch_id = ? ORDER BY id")?;
    let rows = stmt.query_map([batch_id], |row| {
        Ok(QueueFile {
            path: row.get(0)?,
            filename: row.get(1)?,
            status: row.get(2)?,
            size: row.get(3)?,
            error: row.get(4)?,
        })
    })?;
    Ok(rows.collect::<Result<_, _>>()?)
}

/// Pending files of a batch as (local path, remote name).
pub fn pending_files(conn: &Connection, batch_id: i64) -> AppResult<Vec<(PathBuf, String)>> {
    let mut stmt = conn.prepare("SELECT path, filename FROM files WHERE status = ? AND batch_id = ? ORDER BY id")?;
    let rows = stmt.query_map(params![PENDING, batch_id], |row| Ok((PathBuf::from(row.get::<_, String>(0)?), row.get(1)?)))?;
    Ok(rows.collect::<Result<_, _>>()?)
}

pub fn mark_uploaded(conn: &Connection, path: &Path) -> AppResult<()> {
    conn.execute(
        "UPDATE files SET status = ?, error = NULL, updated_at = ? WHERE path = ?",
        params![UPLOADED, now(), path.to_string_lossy()],
    )?;
    Ok(())
}
