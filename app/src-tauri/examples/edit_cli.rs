//! Developer tool for parity checks against the Python app: `cargo run --example edit_cli -- <db>`.
//! Applies a fixed sequence of description-page edits to the first files of the database.

use std::path::Path;

use app_lib::core::{describe, state};

fn names(conn: &rusqlite::Connection) -> Vec<String> {
    let mut stmt = conn.prepare("SELECT filename FROM files ORDER BY id LIMIT 5").expect("prepare");
    stmt.query_map([], |row| row.get(0)).expect("query").collect::<Result<_, _>>().expect("rows")
}

fn words(list: &[&str]) -> Vec<String> {
    list.iter().map(|word| word.to_string()).collect()
}

fn main() {
    let db = std::env::args().nth(1).expect("db path");
    let conn = state::open_db(Path::new(&db)).expect("open");
    let files = names(&conn);
    state::update_metadata(&conn, &files[0], "Title A", &words(&["kw1", "кот"]), "3").unwrap();
    state::update_metadata(&conn, &files[1], "Title B", &words(&["kw1"]), "").unwrap();
    state::bulk_edit(&conn, &files[..3], "extra", Some("7")).unwrap();
    describe::set_status(&conn, &files[..4], "review").unwrap();
    describe::approve(&conn, Some(&files[..1])).unwrap();
    describe::set_status(&conn, &files[1..2], "pending").unwrap();
    describe::set_status(&conn, &files[..1], "needs_metadata").unwrap();
}
