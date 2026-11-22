//! End-to-end run of the whole pipeline against a simulated SFTP server and a stand-in `claude`:
//! scan → caption → approve → prepare copies → upload (with a dropped connection and a retry) → submitted.
//! Nothing leaves the machine; the only fakes are the two process/network boundaries.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use image::{codecs::jpeg::JpegEncoder, ExtendedColorType};
use rusqlite::Connection;

use super::caption::tests::fake_claude;
use super::caption_run::{caption_files, CaptionOptions};
use super::embed::{read_embedded, verify_embedded, EmbedMeta};
use super::export::prepare_pending;
use super::scan::{find_images, scan};
use super::sftp::Uploader;
use super::state;
use super::submit::mark_submitted;
use super::upload::upload_pending;

const SIDE: u32 = 2100; // 4.4 MP, above Adobe's 4 MP minimum
const AI_LABEL: &str = "Generative AI";

/// Remote account root held in memory; `drop_on_put` makes the n-th upload (1-based) fail like a dropped link.
#[derive(Default)]
struct Server {
    files: BTreeMap<String, Vec<u8>>,
    puts: Vec<String>,
    drop_on_put: Option<usize>,
}

struct MemorySftp(Arc<Mutex<Server>>);

impl Uploader for MemorySftp {
    fn put(&mut self, local: &Path, remote: &str, on_bytes: &mut dyn FnMut(u64)) -> Result<(), String> {
        let mut server = self.0.lock().unwrap();
        server.puts.push(remote.to_string());
        if server.drop_on_put == Some(server.puts.len()) {
            return Err("Connection reset by peer".to_string());
        }
        let bytes = std::fs::read(local).map_err(|error| error.to_string())?;
        on_bytes(bytes.len() as u64);
        server.files.insert(remote.to_string(), bytes);
        Ok(())
    }
}

fn write_photo(path: &Path, width: u32, height: u32, shade: u8) {
    let pixels: Vec<u8> = (0..width * height * 3).map(|i| shade.wrapping_add((i % 251) as u8)).collect();
    let file = std::fs::File::create(path).unwrap();
    JpegEncoder::new(std::io::BufWriter::new(file)).encode(&pixels, width, height, ExtendedColorType::Rgb8).unwrap();
}

/// What the stand-in `claude` answers: three pictures, the last one held back for a person to look at.
const CLAUDE_REPLY: &str = r#"{"structured_output":{"items":[
    {"file":"img001.jpg","title":"Red fox walking through fresh snow","keywords":["fox","snow","winter"],"category":1,"people_visible":false,"content_type":"photo","review_reason":""},
    {"file":"img002.jpg","title":"Calm lake at sunrise with mist","keywords":["lake","sunrise","mist"],"category":8,"people_visible":false,"content_type":"photo","review_reason":""},
    {"file":"img003.jpg","title":"Old stone bridge over a river","keywords":["bridge","river","stone"],"category":8,"people_visible":false,"content_type":"photo","review_reason":"check the landmark"}
]}}"#;

struct Env {
    _dir: tempfile::TempDir,
    conn: Connection,
    export_dir: PathBuf,
    sources: Vec<PathBuf>,
}

fn status_of(conn: &Connection) -> Vec<(String, String)> {
    let mut stmt = conn.prepare("SELECT filename, status FROM files ORDER BY filename").unwrap();
    stmt.query_map([], |row| Ok((row.get(0)?, row.get(1)?))).unwrap().collect::<Result<_, _>>().unwrap()
}

fn statuses(conn: &Connection, wanted: &str) -> usize {
    status_of(conn).iter().filter(|(_, status)| status == wanted).count()
}

/// Scans a folder of three good pictures and one too small, then captions them with the stand-in `claude`.
fn scanned_and_captioned() -> Env {
    let dir = tempfile::tempdir().unwrap();
    let photos = dir.path().join("photos");
    std::fs::create_dir(&photos).unwrap();
    let sources: Vec<PathBuf> = (1..=3).map(|n| photos.join(format!("shot{n}.jpg"))).collect();
    for (index, path) in sources.iter().enumerate() {
        write_photo(path, SIDE, SIDE, 40 * index as u8);
    }
    write_photo(&photos.join("tiny.jpg"), 100, 100, 7);

    let mut conn = state::open_db(&dir.path().join("state.db")).unwrap();
    let images = find_images(&photos, true);
    let scanned = scan(&mut conn, &photos, &images, &mut |_, _, _| {}, &|| false).unwrap();
    assert_eq!((scanned.found, scanned.result.needs_metadata, scanned.result.invalid), (4, 3, 1));
    assert_eq!(statuses(&conn, "needs_metadata"), 3);

    let claude = fake_claude(dir.path(), CLAUDE_REPLY, 0);
    let options = CaptionOptions {
        model: "claude-haiku-5-5",
        claude: &claude,
        limit: 100,
        per_call: 3,
        workers: 1,
        names: None,
        redo: false,
        ai_label: AI_LABEL,
    };
    let captioned = caption_files(&conn, &options, &mut |_, _, _| {}, &|| false).unwrap();
    assert_eq!((captioned.captioned, captioned.flagged.len(), captioned.errors.len()), (2, 1, 0));
    Env { export_dir: dir.path().join("export"), _dir: dir, conn, sources }
}

fn connect_to(server: &Arc<Mutex<Server>>) -> impl FnOnce() -> Result<Box<dyn Uploader>, String> {
    let server = Arc::clone(server);
    move || Ok(Box::new(MemorySftp(server)) as Box<dyn Uploader>)
}

#[test]
fn a_folder_goes_from_scan_to_submitted_through_a_flaky_sftp_server() {
    let Env { _dir, conn, export_dir, sources } = scanned_and_captioned();
    let server = Arc::new(Mutex::new(Server::default()));

    // Review: the flagged picture is held back until a person approves it, then it joins the queue.
    assert_eq!((statuses(&conn, "pending"), statuses(&conn, "review")), (2, 1));
    assert_eq!(state::approve_reviewed(&conn, None).unwrap(), 1);
    assert_eq!(statuses(&conn, "pending"), 3);

    // Nothing may be sent before the copies exist.
    let early = upload_pending(&conn, connect_to(&server), &mut |_| {}, &|| false).unwrap();
    assert!(early.blocked.is_some() && early.uploaded == 0);
    assert!(server.lock().unwrap().puts.is_empty());

    // Prepare: copies carry the metadata, originals stay untouched.
    let prepared = prepare_pending(&conn, &export_dir, AI_LABEL, &mut |_, _, _| {}, &|| false).unwrap();
    assert_eq!((prepared.prepared, prepared.problems.len(), prepared.failed.len()), (3, 0, 0));
    for source in &sources {
        let original = read_embedded(source).unwrap();
        assert_eq!(original.xmp.title, None, "{source:?} must stay as it was");
    }

    // First run: the link drops on the second file; the first stays uploaded, the others wait.
    server.lock().unwrap().drop_on_put = Some(2);
    let first = upload_pending(&conn, connect_to(&server), &mut |_| {}, &|| false).unwrap();
    assert_eq!((first.total, first.uploaded, first.failed.clone()), (3, 2, vec!["shot2.jpg".to_string()]));
    assert_eq!((statuses(&conn, "uploaded"), statuses(&conn, "pending")), (2, 1));

    // Retry: only the missing file is sent.
    server.lock().unwrap().drop_on_put = None;
    let second = upload_pending(&conn, connect_to(&server), &mut |_| {}, &|| false).unwrap();
    assert_eq!((second.total, second.uploaded, second.failed.len()), (1, 1, 0));
    assert_eq!(server.lock().unwrap().puts, ["shot1.jpg", "shot2.jpg", "shot3.jpg", "shot2.jpg"]);

    // What arrived is byte-for-byte the prepared copy and carries its title and keywords.
    let remote = server.lock().unwrap();
    assert_eq!(remote.files.keys().collect::<Vec<_>>(), ["shot1.jpg", "shot2.jpg", "shot3.jpg"]);
    let expected = [
        ("shot1.jpg", "Red fox walking through fresh snow Generative AI", vec!["fox", "snow", "winter"]),
        ("shot2.jpg", "Calm lake at sunrise with mist Generative AI", vec!["lake", "sunrise", "mist"]),
        ("shot3.jpg", "Old stone bridge over a river Generative AI", vec!["bridge", "river", "stone"]),
    ];
    for (name, title, keywords) in expected {
        let copy = export_dir.join(name);
        assert_eq!(remote.files[name], std::fs::read(&copy).unwrap(), "{name} differs from its copy");
        let downloaded = tempfile::NamedTempFile::new().unwrap();
        std::fs::write(downloaded.path(), &remote.files[name]).unwrap();
        let meta = EmbedMeta {
            title: title.to_string(),
            description: title.to_string(),
            keywords: keywords.into_iter().map(String::from).collect(),
        };
        verify_embedded(downloaded.path(), &meta).unwrap_or_else(|error| panic!("{name}: {error}"));
    }
    drop(remote);

    // Done in the portal: everything uploaded is submitted; the invalid file never travelled.
    let submitted = mark_submitted(&conn).unwrap();
    assert_eq!(submitted.count, 3);
    assert_eq!(state::queue_files(&conn).unwrap().iter().filter(|file| file.status == "submitted").count(), 3);
    assert!(!server.lock().unwrap().files.contains_key("tiny.jpg"));
    let log: Vec<String> = conn
        .prepare("SELECT message FROM events ORDER BY id")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap();
    assert!(log.iter().any(|line| line.contains("3 files marked submitted")), "{log:?}");
}

#[test]
fn a_title_edited_after_preparing_blocks_the_upload_until_the_copy_is_refreshed() {
    let Env { _dir, conn, export_dir, .. } = scanned_and_captioned();
    let server = Arc::new(Mutex::new(Server::default()));
    state::approve_reviewed(&conn, None).unwrap();
    prepare_pending(&conn, &export_dir, AI_LABEL, &mut |_, _, _| {}, &|| false).unwrap();

    let keywords = ["fox".to_string(), "snow".to_string()];
    state::update_metadata(&conn, "shot1.jpg", "Arctic fox resting on a snowy hill Generative AI", &keywords, "1").unwrap();
    let stale = upload_pending(&conn, connect_to(&server), &mut |_| {}, &|| false).unwrap();
    assert!(stale.blocked.is_some());
    assert!(server.lock().unwrap().puts.is_empty());

    let refreshed = prepare_pending(&conn, &export_dir, AI_LABEL, &mut |_, _, _| {}, &|| false).unwrap();
    assert_eq!((refreshed.prepared, refreshed.current), (1, 2));
    let sent = upload_pending(&conn, connect_to(&server), &mut |_| {}, &|| false).unwrap();
    assert_eq!((sent.uploaded, sent.failed.len()), (3, 0));
    let meta = EmbedMeta {
        title: "Arctic fox resting on a snowy hill Generative AI".to_string(),
        description: "Arctic fox resting on a snowy hill Generative AI".to_string(),
        keywords: keywords.to_vec(),
    };
    verify_embedded(&export_dir.join("shot1.jpg"), &meta).unwrap();
}
