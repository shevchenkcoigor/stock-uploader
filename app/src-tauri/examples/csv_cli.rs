//! Developer tool: `cargo run --example csv_cli -- <db> <csv_dir> <batch>` builds a batch's CSV, then marks it submitted.

use std::path::Path;

use app_lib::core::{csv_build, state};

fn main() {
    let mut args = std::env::args().skip(1);
    let db = args.next().expect("db");
    let dir = args.next().expect("csv dir");
    let batch: i64 = args.next().expect("batch").parse().expect("number");
    let conn = state::open_db(Path::new(&db)).expect("open");
    println!("{:?}", csv_build::build_csv(&conn, batch, Path::new(&dir), "Generative AI").expect("build"));
    println!("{:?}", csv_build::mark_submitted(&conn, batch).expect("submit"));
}
