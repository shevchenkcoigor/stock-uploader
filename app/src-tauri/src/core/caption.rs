//! Generate Adobe Stock titles and keywords with headless Claude Code (no API key needed).
//!
//! Each call receives several downscaled images and returns JSON. The call is slimmed down (no MCP
//! servers, no skills, no user settings, short system prompt) so it uses a few thousand tokens instead of
//! the ~150k a default Claude Code session loads.

use std::collections::HashMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use image::ExtendedColorType;
use image::codecs::jpeg::JpegEncoder;
use serde_json::{Value, json};

use super::categories::{CATEGORIES, category_menu, is_category_code};
use super::imaging;
use super::metadata::{AI_TERMS, MAX_KEYWORDS, MAX_TITLE_CHARS, keyword_problem};

pub const DEFAULT_MODEL: &str = "claude-haiku-5-5";
/// (model id, short note shown in the picker); extend this list when new models appear.
pub const MODELS: [(&str, &str); 4] = [
    ("claude-haiku-5-5", "fastest and lightest on usage limits (default, good for bulk)"),
    ("claude-sonnet-5-5", "more accurate, uses limits faster"),
    ("claude-opus-5-5", "stronger still, much heavier on limits"),
    ("claude-fable-5-2", "most capable, heaviest on limits"),
];
const CONTENT_TYPES: [&str; 2] = ["photo", "illustration"];
const PREVIEW_MAX_SIDE: u32 = 1568;
const PREVIEW_QUALITY: u8 = 85;
const CALL_TIMEOUT: Duration = Duration::from_secs(300);
const POLL_INTERVAL: Duration = Duration::from_millis(100);
const ERROR_TAIL_CHARS: usize = 300;
pub const CLAUDE_NOT_FOUND: &str =
    "Claude Code (`claude`) was not found. Install it and log in, or set STOCK_CLAUDE_PATH.";

/// A short name for a picker: claude-sonnet-5-5 -> Sonnet 5.5.
pub fn model_title(model_id: &str) -> String {
    let parts: Vec<&str> = model_id.strip_prefix("claude-").unwrap_or(model_id).split('-').collect();
    let is_number = |part: &&str| !part.is_empty() && part.chars().all(|c| c.is_ascii_digit());
    let versions: Vec<&str> = parts.iter().copied().filter(is_number).collect();
    let mut words: Vec<String> = parts
        .iter()
        .filter(|part| !is_number(part))
        .map(|part| {
            let mut chars = part.chars();
            chars.next().map_or_else(String::new, |first| {
                first.to_uppercase().collect::<String>() + &chars.as_str().to_lowercase()
            })
        })
        .collect();
    words.push(versions.join("."));
    words.join(" ").trim().to_string()
}

pub fn system_prompt() -> String {
    [
        "You write metadata for Adobe Stock. Open each listed image with the Read tool and answer only with the requested JSON.\n",
        "For every image:\n",
        "- title: a factual English description, 6-15 words, under 150 characters. Describe only what is visible. No brand names, trademarks, people's names or quotation marks. Never mention that the image is AI-made or name AI tools: the words 'AI', 'generative', 'generated', 'artificial intelligence', 'Midjourney', 'DALL-E', 'Stable Diffusion', 'ChatGPT' and 'Firefly' must not appear in the title or in the keywords; the tool adds the required 'Generative AI' label to the title itself.\n",
        "- keywords: 25-35 English keywords (single words or short phrases), most relevant first, covering subject, setting, colors, mood, composition and likely uses. Only things visible or clearly implied. No brands, trademarks, artist names, and no words like 'AI' or 'generated'. Each keyword is ONE concept: a single word, or a short phrase only when it names one thing (like 'arctic fox'); never join several concepts into one keyword and never put a comma or semicolon inside a keyword. Put the main subject first and the individual words and concepts of your title within the first 10 keywords. No numbers and no file or camera information. If a keyword names a city, state or province, add the country as a separate keyword. Use each keyword once.\n",
        "- category: the single best-fitting category code from this list: ",
        &category_menu(),
        ".\n",
        "- people_visible: true if any person or human-like figure is visible.\n",
        "- content_type: 'photo' if it looks camera-captured and photo-realistic, otherwise 'illustration'.\n",
        "- review_reason: Adobe's generative-AI policy forbids names of artists, real people or fictional characters, references to copyrighted works, government agencies, third-party intellectual property (brands, logos, trademarks) and anything implying a real newsworthy event. Never put any of these in the title or keywords; describe such things generically. If the image itself shows or strongly resembles any of them, or a recognizable real person, explain briefly in review_reason; otherwise use an empty string.",
    ]
    .concat()
}

pub fn response_schema() -> Value {
    let codes: Vec<u32> = CATEGORIES.iter().map(|(code, _)| *code).collect();
    json!({
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "title": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "category": {"type": "integer", "enum": codes},
                "people_visible": {"type": "boolean"},
                "content_type": {"type": "string", "enum": CONTENT_TYPES},
                "review_reason": {"type": "string"},
            },
            "required": ["file", "title", "keywords", "category", "people_visible", "content_type", "review_reason"],
        }}},
        "required": ["items"],
    })
}

/// The claude process failed as a whole (limits, auth, timeout), not for one image.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClaudeCallError(pub String);

impl std::fmt::Display for ClaudeCallError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CaptionResult {
    pub path: PathBuf,
    pub title: String,
    pub keywords: Vec<String>,
    pub category: String,
    pub people_visible: bool,
    pub content_type: String,
    /// Set when the image may break Adobe's AI policy; needs a human.
    pub review_reason: String,
    pub error: Option<String>,
}

impl CaptionResult {
    fn failed(path: &Path, error: impl Into<String>) -> Self {
        Self { path: path.to_path_buf(), error: Some(error.into()), ..Self::default() }
    }
}

/// Writes a small JPEG copy; originals can be 100 MP, far more than needed to describe them.
pub fn make_preview(source: &Path, destination: &Path) -> Result<(), String> {
    let picture = imaging::decode_rgb(source, PREVIEW_MAX_SIDE).ok_or_else(|| "cannot read image".to_string())?;
    let small = imaging::fit_within(picture, PREVIEW_MAX_SIDE);
    let file = std::fs::File::create(destination).map_err(|error| error.to_string())?;
    JpegEncoder::new_with_quality(std::io::BufWriter::new(file), PREVIEW_QUALITY)
        .encode(small.as_raw(), small.width(), small.height(), ExtendedColorType::Rgb8)
        .map_err(|error| error.to_string())
}

pub fn clean_title(raw: &str) -> String {
    let without_quotes = raw.replace('"', "");
    let scrubbed = AI_TERMS.replace_all(&without_quotes, " ");
    let joined = scrubbed.split_whitespace().collect::<Vec<_>>().join(" ");
    let title = joined.trim_matches(&[' ', ',', ';', ':', '-'][..]);
    if title.chars().count() <= MAX_TITLE_CHARS {
        return title.to_string();
    }
    let cut: String = title.chars().take(MAX_TITLE_CHARS).collect();
    cut.rsplit_once(' ').map_or(cut.clone(), |(head, _)| head.to_string())
}

/// Normalizes Claude's keywords: split on commas, drop duplicates, AI wording, numbers, file info.
pub fn clean_keywords(raw: &[String]) -> Vec<String> {
    let mut kept: Vec<String> = Vec::new();
    for entry in raw {
        for keyword in entry.replace(';', ",").split(',') {
            let normalized = keyword.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase();
            if normalized.is_empty() || kept.contains(&normalized) || AI_TERMS.is_match(&normalized) {
                continue;
            }
            if keyword_problem(&normalized).is_some() {
                continue;
            }
            kept.push(normalized);
        }
    }
    kept.truncate(MAX_KEYWORDS);
    kept
}

/// The category as an Adobe category code string; anything outside Adobe's list is dropped (optional field).
fn category_code(raw: Option<&Value>) -> String {
    raw.and_then(Value::as_i64).filter(|code| is_category_code(*code)).map_or_else(String::new, |code| code.to_string())
}

pub fn build_command(claude: &str, preview_dir: &Path, names: &[String], model: &str) -> Vec<String> {
    let prompt = format!(
        "Describe each of these images (files in the current directory):\n{}\nReturn one item per image; 'file' must be the exact file name listed.",
        names.join("\n")
    );
    [
        claude,
        "-p",
        &prompt,
        "--model",
        model,
        "--output-format",
        "json",
        "--tools",
        "Read",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--system-prompt",
        &system_prompt(),
        "--add-dir",
        &preview_dir.to_string_lossy(),
        "--json-schema",
        &response_schema().to_string(),
    ]
    .iter()
    .map(|part| (*part).to_string())
    .collect()
}

fn tail(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    chars[chars.len().saturating_sub(ERROR_TAIL_CHARS)..].iter().collect()
}

fn head(text: &str) -> String {
    text.chars().take(ERROR_TAIL_CHARS).collect()
}

fn drain<R: Read + Send + 'static>(mut pipe: R) -> std::thread::JoinHandle<String> {
    std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = pipe.read_to_end(&mut bytes);
        String::from_utf8_lossy(&bytes).into_owned()
    })
}

/// Runs the CLI and returns stdout; errors when the call as a whole failed.
pub fn run_claude(command: &[String], cwd: &Path) -> Result<String, ClaudeCallError> {
    let mut child = Command::new(&command[0])
        .args(&command[1..])
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| match error.kind() {
            std::io::ErrorKind::NotFound => ClaudeCallError(CLAUDE_NOT_FOUND.to_string()),
            _ => ClaudeCallError(format!("claude did not run: {error}")),
        })?;
    let stdout = child.stdout.take().map(drain);
    let stderr = child.stderr.take().map(drain);
    let started = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() >= CALL_TIMEOUT => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(ClaudeCallError(format!(
                    "claude did not run: timed out after {} seconds",
                    CALL_TIMEOUT.as_secs()
                )));
            }
            Ok(None) => std::thread::sleep(POLL_INTERVAL),
            Err(error) => return Err(ClaudeCallError(format!("claude did not run: {error}"))),
        }
    };
    let out = stdout.and_then(|handle| handle.join().ok()).unwrap_or_default();
    let err = stderr.and_then(|handle| handle.join().ok()).unwrap_or_default();
    if !status.success() {
        let shown = if err.is_empty() { &out } else { &err };
        let code = status.code().map_or_else(|| "a signal".to_string(), |code| code.to_string());
        return Err(ClaudeCallError(format!("claude exited with {code}: {}", tail(shown))));
    }
    Ok(out)
}

/// Extracts {file: item} from the CLI's JSON envelope.
pub fn parse_items(stdout: &str) -> Result<HashMap<String, Value>, ClaudeCallError> {
    let envelope: Value =
        serde_json::from_str(stdout).map_err(|_| ClaudeCallError(format!("output is not JSON: {}", head(stdout))))?;
    if envelope.get("is_error").and_then(Value::as_bool) == Some(true) {
        let result = envelope.get("result").map_or_else(
            || "None".to_string(),
            |value| value.as_str().map_or_else(|| value.to_string(), str::to_string),
        );
        return Err(ClaudeCallError(format!("claude reported an error: {}", head(&result))));
    }
    let payload = match envelope.get("structured_output").filter(|value| !value.is_null()) {
        Some(found) => found.clone(),
        None => envelope
            .get("result")
            .and_then(Value::as_str)
            .and_then(|text| serde_json::from_str(text).ok())
            .ok_or_else(|| ClaudeCallError("no structured output in the response".to_string()))?,
    };
    let items = payload
        .get("items")
        .and_then(Value::as_array)
        .ok_or_else(|| ClaudeCallError("response has no 'items' list".to_string()))?;
    Ok(items.iter().filter_map(|item| Some((item.get("file")?.as_str()?.to_string(), item.clone()))).collect())
}

fn text_of(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(text)) => text.clone(),
        Some(other) => other.to_string(),
    }
}

fn result_from(source: &Path, item: &Value) -> CaptionResult {
    let title = clean_title(&text_of(item.get("title")));
    let raw_keywords: Vec<String> = item
        .get("keywords")
        .and_then(Value::as_array)
        .map(|list| list.iter().map(|word| text_of(Some(word))).collect())
        .unwrap_or_default();
    let keywords = clean_keywords(&raw_keywords);
    if title.is_empty() || keywords.is_empty() {
        return CaptionResult::failed(source, "empty title or keywords");
    }
    let content_type = text_of(item.get("content_type"));
    CaptionResult {
        path: source.to_path_buf(),
        title,
        keywords,
        category: category_code(item.get("category")),
        people_visible: item.get("people_visible") == Some(&Value::Bool(true)),
        content_type: if CONTENT_TYPES.contains(&content_type.as_str()) { content_type } else { String::new() },
        review_reason: text_of(item.get("review_reason")).split_whitespace().collect::<Vec<_>>().join(" "),
        error: None,
    }
}

/// Captions several images in one call. Per-image problems are returned as errors.
pub fn caption_group(sources: &[PathBuf], model: &str, claude: &str) -> Result<Vec<CaptionResult>, ClaudeCallError> {
    let tmp = tempfile::Builder::new()
        .prefix("stock_caption_")
        .tempdir()
        .map_err(|error| ClaudeCallError(format!("claude did not run: {error}")))?;
    let mut ready: Vec<(String, &PathBuf)> = Vec::new();
    let mut unreadable: HashMap<&PathBuf, String> = HashMap::new();
    for (index, source) in sources.iter().enumerate() {
        let name = format!("img{:03}.jpg", index + 1);
        match make_preview(source, &tmp.path().join(&name)) {
            Ok(()) => ready.push((name, source)),
            Err(error) => {
                unreadable.insert(source, error.to_string());
            }
        }
    }
    let items = if ready.is_empty() {
        HashMap::new()
    } else {
        let names: Vec<String> = ready.iter().map(|(name, _)| name.clone()).collect();
        parse_items(&run_claude(&build_command(claude, tmp.path(), &names, model), tmp.path())?)?
    };
    let named: HashMap<&PathBuf, &String> = ready.iter().map(|(name, source)| (*source, name)).collect();
    Ok(sources
        .iter()
        .map(|source| match (named.get(source), unreadable.get(source)) {
            (Some(name), _) => match items.get(*name) {
                Some(item) => result_from(source, item),
                None => CaptionResult::failed(source, "model returned nothing for this image"),
            },
            (None, Some(error)) => CaptionResult::failed(source, error.clone()),
            (None, None) => CaptionResult::failed(source, "model returned nothing for this image"),
        })
        .collect())
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    /// A fake `claude`: records its arguments next to itself and prints `output`.
    pub fn fake_claude(dir: &Path, output: &str, exit_code: i32) -> String {
        let script = dir.join("claude");
        let body =
            format!("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$0.args\"\ncat <<'JSON'\n{output}\nJSON\nexit {exit_code}\n");
        std::fs::write(&script, body).unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
        script.to_string_lossy().into_owned()
    }

    pub fn write_jpeg(path: &Path, shade: u8) {
        let pixels = vec![shade; 300 * 200 * 3];
        let file = std::fs::File::create(path).unwrap();
        JpegEncoder::new(std::io::BufWriter::new(file)).encode(&pixels, 300, 200, ExtendedColorType::Rgb8).unwrap();
    }

    const ENVELOPE: &str = r#"{"structured_output":{"items":[
        {"file":"img001.jpg","title":"A red \"fox\" AI in snow","keywords":["Fox","fox","arctic fox; snow","2024","photo.jpg","Midjourney"],"category":13,"people_visible":false,"content_type":"photo","review_reason":""},
        {"file":"img002.jpg","title":"","keywords":[],"category":99,"people_visible":true,"content_type":"weird","review_reason":" needs   review "}
    ]}}"#;

    #[test]
    fn titles_and_keywords_are_cleaned_like_the_python_app_does() {
        assert_eq!(clean_title(" \"A red fox\" generated by AI, "), "A red fox by");
        assert_eq!(clean_title(&"word ".repeat(80)).chars().count(), 199);
        let words =
            ["Fox", "fox", "arctic fox; snow", "2024", "photo.jpg", "Midjourney", " Red   Sky "].map(String::from);
        assert_eq!(clean_keywords(&words), vec!["fox", "arctic fox", "snow", "red sky"]);
        let many: Vec<String> = (0..80).map(|n| format!("kw{}x", n)).collect();
        assert_eq!(clean_keywords(&many).len(), MAX_KEYWORDS);
    }

    #[test]
    fn model_titles_are_short_names() {
        assert_eq!(model_title("claude-sonnet-5-5"), "Sonnet 5.5");
        assert_eq!(model_title("claude-fable-5-2"), "Fable 5.2");
    }

    #[test]
    fn a_group_is_captioned_with_one_call_and_the_right_flags() {
        let dir = tempfile::tempdir().unwrap();
        let claude = fake_claude(dir.path(), ENVELOPE, 0);
        let (first, second) = (dir.path().join("a.jpg"), dir.path().join("b.jpg"));
        write_jpeg(&first, 10);
        write_jpeg(&second, 90);

        let results = caption_group(&[first.clone(), second.clone()], "claude-haiku-5-5", &claude).unwrap();

        assert_eq!(results[0].title, "A red fox in snow");
        assert_eq!(results[0].keywords, vec!["fox", "arctic fox", "snow"]);
        assert_eq!((results[0].category.as_str(), results[0].content_type.as_str()), ("13", "photo"));
        assert_eq!(results[1].error.as_deref(), Some("empty title or keywords"));
        let args = std::fs::read_to_string(format!("{claude}.args")).unwrap();
        for expected in [
            "-p",
            "--model\nclaude-haiku-5-5",
            "--output-format\njson",
            "--tools\nRead",
            "--strict-mcp-config",
            "--add-dir",
            "--json-schema",
            "img001.jpg\nimg002.jpg",
        ] {
            assert!(args.contains(expected), "missing {expected:?} in {args}");
        }
    }

    #[test]
    fn a_whole_call_failure_is_an_error_and_an_unreadable_image_only_its_own() {
        let dir = tempfile::tempdir().unwrap();
        let failing = fake_claude(dir.path(), "usage limit reached", 1);
        let good = dir.path().join("a.jpg");
        write_jpeg(&good, 10);
        let error = caption_group(std::slice::from_ref(&good), "m", &failing).unwrap_err();
        assert!(error.0.starts_with("claude exited with 1: "), "{error}");

        let claude = fake_claude(dir.path(), ENVELOPE, 0);
        let broken = dir.path().join("broken.jpg");
        std::fs::write(&broken, b"nope").unwrap();
        let results = caption_group(&[good, broken], "m", &claude).unwrap();
        assert!(results[0].error.is_none(), "{:?}", results[0]);
        assert_eq!(results[1].error.as_deref(), Some("cannot read image"));
    }

    #[test]
    fn a_missing_claude_is_reported_as_such() {
        let dir = tempfile::tempdir().unwrap();
        let good = dir.path().join("a.jpg");
        write_jpeg(&good, 10);
        let error = caption_group(&[good], "m", "/nonexistent/claude").unwrap_err();
        assert_eq!(error.0, CLAUDE_NOT_FOUND);
    }

    #[test]
    fn responses_without_usable_content_are_errors() {
        assert!(parse_items("not json").unwrap_err().0.starts_with("output is not JSON: "));
        assert!(
            parse_items(r#"{"is_error":true,"result":"limit"}"#)
                .unwrap_err()
                .0
                .contains("claude reported an error: limit")
        );
        assert_eq!(parse_items(r#"{"result":"plain text"}"#).unwrap_err().0, "no structured output in the response");
        assert_eq!(parse_items(r#"{"structured_output":{}}"#).unwrap_err().0, "response has no 'items' list");
        let from_text = parse_items(r#"{"result":"{\"items\":[{\"file\":\"x\"}]}"}"#).unwrap();
        assert!(from_text.contains_key("x"));
    }

    #[test]
    fn the_schema_lists_the_category_codes() {
        let schema = response_schema();
        assert_eq!(
            schema["properties"]["items"]["items"]["properties"]["category"]["enum"].as_array().unwrap().len(),
            21
        );
    }
}
