//! Developer tool for parity checks against the Python app: `cargo run --example scan_cli -- <db> <folder>`.

use std::path::Path;

use app_lib::core::{backup, scan, state};

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (db, folder) = (Path::new(&args[1]), Path::new(&args[2]).canonicalize().expect("folder"));
    backup::protected(db, || {
        let mut conn = state::open_db(db)?;
        let images = scan::find_images(&folder, true);
        let report = scan::scan(&mut conn, &folder, &images, 1000, &mut |_, _, _| {}, &|| false)?;
        println!(
            "found {} added {} need_metadata {} unchanged {} invalid {} batches {}",
            report.found,
            report.result.added,
            report.result.needs_metadata,
            report.result.unchanged,
            report.result.invalid,
            report.result.batches_created
        );
        Ok(())
    })
    .expect("scan");
}
