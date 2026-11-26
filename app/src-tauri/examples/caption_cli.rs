//! Developer tool: `cargo run --example caption_cli -- <db> <limit>` captions new files with the real `claude`.

use std::path::Path;

use app_lib::core::caption_run::{CaptionOptions, caption_files};
use app_lib::core::state;

fn main() {
    let mut args = std::env::args().skip(1);
    let db = args.next().expect("db path");
    let limit: usize = args.next().expect("limit").parse().expect("number");
    let claude = std::env::var("ADOBE_CLAUDE_PATH").unwrap_or_else(|_| "claude".to_string());
    let conn = state::open_db(Path::new(&db)).expect("open");
    let options = CaptionOptions {
        model: "claude-haiku-5-5",
        claude: &claude,
        limit,
        per_call: 2,
        workers: 1,
        names: None,
        redo: false,
        ai_label: "Generative AI",
    };
    let report = caption_files(&conn, &options, &mut |done, total, msg| println!("{done}/{total} {msg}"), &|| false)
        .expect("run");
    println!("{report:#?}");
}
