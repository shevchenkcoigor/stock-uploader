use rusqlite::{params_from_iter, types::Value};
use serde::{Deserialize, Serialize};

use crate::db::open_read_only;
use crate::error::AppResult;

#[derive(Serialize)]
pub struct EventRow {
    id: i64,
    at: String,
    command: String,
    level: String,
    filename: Option<String>,
    message: String,
}

#[derive(Serialize)]
pub struct EventsPage {
    rows: Vec<EventRow>,
    total: i64,
}

#[derive(Deserialize, Default)]
#[serde(rename_all = "camelCase", default)]
pub struct EventFilter {
    text: String,
    level: Option<String>,
    command: Option<String>,
    after_id: i64,
    limit: i64,
}

/// `(WHERE clause, params)` for the filters; the text is matched in message and file name.
fn where_clause(filter: &EventFilter) -> (String, Vec<Value>) {
    let mut conditions = vec!["id > ?".to_string()];
    let mut params = vec![Value::Integer(filter.after_id)];
    if !filter.text.is_empty() {
        let escaped = filter
            .text
            .replace('\\', "\\\\")
            .replace('%', "\\%")
            .replace('_', "\\_");
        conditions.push("(message LIKE ? ESCAPE '\\' OR filename LIKE ? ESCAPE '\\')".into());
        let pattern = format!("%{escaped}%");
        params.push(Value::Text(pattern.clone()));
        params.push(Value::Text(pattern));
    }
    if let Some(level) = filter.level.as_deref().filter(|v| !v.is_empty()) {
        conditions.push("level = ?".into());
        params.push(Value::Text(level.into()));
    }
    if let Some(command) = filter.command.as_deref().filter(|v| !v.is_empty()) {
        conditions.push("command = ?".into());
        params.push(Value::Text(command.into()));
    }
    (conditions.join(" AND "), params)
}

#[tauri::command]
pub fn search_events(filter: EventFilter) -> AppResult<EventsPage> {
    let Some(conn) = open_read_only()? else {
        return Ok(EventsPage { rows: vec![], total: 0 });
    };
    let (where_sql, params) = where_clause(&filter);
    let limit = if filter.limit > 0 { filter.limit } else { 300 };
    let total: i64 = conn.query_row(
        &format!("SELECT COUNT(*) FROM events WHERE {where_sql}"),
        params_from_iter(params.iter()),
        |row| row.get(0),
    )?;
    let mut with_limit = params;
    with_limit.push(Value::Integer(limit));
    let mut stmt = conn.prepare(&format!(
        "SELECT id, at, command, level, filename, message FROM events WHERE {where_sql} \
         ORDER BY id DESC LIMIT ?"
    ))?;
    let rows = stmt
        .query_map(params_from_iter(with_limit.iter()), |row| {
            Ok(EventRow {
                id: row.get(0)?,
                at: row.get(1)?,
                command: row.get(2)?,
                level: row.get(3)?,
                filename: row.get(4)?,
                message: row.get(5)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(EventsPage { rows, total })
}

#[tauri::command]
pub fn event_commands() -> AppResult<Vec<String>> {
    let Some(conn) = open_read_only()? else {
        return Ok(vec![]);
    };
    let mut stmt = conn.prepare("SELECT DISTINCT command FROM events ORDER BY command")?;
    let names = stmt
        .query_map([], |row| row.get(0))?
        .collect::<Result<Vec<String>, _>>()?;
    Ok(names)
}
