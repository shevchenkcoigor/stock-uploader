//! Item rules, our input CSV and splitting into batches (port of `metadata_csv.py`, without writing).

use std::collections::HashSet;
use std::path::Path;
use std::sync::LazyLock;

use regex::Regex;

use super::py_repr;

pub const HEADERS: [&str; 5] = ["Filename", "Title", "Keywords", "Category", "Releases"];
pub const MAX_ROWS: usize = 20_000;
pub const MAX_CSV_BYTES: usize = 20 * 1024 * 1024;
pub const MAX_KEYWORDS: usize = 49;
pub const MAX_TITLE_CHARS: usize = 200;
const CATEGORY_COUNT: u32 = 21;
pub const DEFAULT_AI_TITLE_LABEL: &str = "Generative AI";
const UTF8_BOM: &[u8] = b"\xef\xbb\xbf";

// Adobe labels generative-AI content itself (portal checkbox), so titles and keywords must not
// announce it or name AI tools.
pub static AI_TERMS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?:generative(?:\s+ai)?|ai[\s-]+generated|artificial\s+intelligence|generated|ai|midjourney|dall[\s-]?e|stable\s+diffusion|chatgpt|firefly)\b",
    )
    .expect("valid regex")
});
static NUMBER_ONLY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^[\d\s.,x*-]+$").expect("valid regex"));
static FILE_INFO: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:jpe?g|png|tiff?|\d+\s*(?:mp|mb|kb|px|dpi)|megapixels?)\b").expect("valid regex")
});

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Item {
    pub filename: String,
    pub title: String,
    pub keywords: Vec<String>,
    pub category: String,
}

/// Reads our input CSV: filename,title,keywords[,category]. Extra columns are ignored.
pub fn read_source(path: &Path) -> Result<Vec<Item>, String> {
    let bytes = std::fs::read(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let content = bytes.strip_prefix(UTF8_BOM).unwrap_or(&bytes);
    let mut reader = csv::ReaderBuilder::new().flexible(true).from_reader(content);
    let headers = reader.headers().map_err(|error| format!("{}: {error}", path.display()))?.clone();
    let column = |name: &str| headers.iter().position(|header| header == name);
    let required = |name: &str| {
        column(name).ok_or_else(|| format!("{}: в metadata.csv нет колонки «{name}»", path.display()))
    };
    let (filename, title, keywords) = (required("filename")?, required("title")?, required("keywords")?);
    let category = column("category");
    let mut items = Vec::new();
    for record in reader.records() {
        let record = record.map_err(|error| format!("{}: {error}", path.display()))?;
        let cell = |index: usize| record.get(index).unwrap_or("").trim().to_string();
        items.push(Item {
            filename: cell(filename),
            title: cell(title),
            keywords: cell(keywords)
                .split(',')
                .map(str::trim)
                .filter(|word| !word.is_empty())
                .map(str::to_string)
                .collect(),
            category: category.map(cell).unwrap_or_default(),
        });
    }
    Ok(items)
}

/// Why Adobe would not accept this single keyword, or `None` if it is fine.
pub fn keyword_problem(keyword: &str) -> Option<&'static str> {
    if keyword.contains(',') || keyword.contains(';') {
        return Some("contains a comma or semicolon (it would be split into several keywords)");
    }
    if NUMBER_ONLY.is_match(keyword) {
        return Some("is only a number");
    }
    if FILE_INFO.is_match(keyword) {
        return Some("looks like file information");
    }
    None
}

/// Ends the title with `label` (e.g. "Generative AI"), keeping it within the title limit.
/// An empty label leaves the title alone; a title that already ends with the label is kept.
pub fn with_ai_label(title: &str, label: &str) -> String {
    let label = label.trim();
    if label.is_empty() || title.to_lowercase().ends_with(&label.to_lowercase()) {
        return title.to_string();
    }
    let room = MAX_TITLE_CHARS.saturating_sub(label.chars().count() + 1);
    let body = if title.chars().count() <= room {
        title.to_string()
    } else {
        let cut: String = title.chars().take(room).collect();
        match cut.rsplit_once(' ') {
            Some((head, _)) if !head.is_empty() => head.to_string(),
            _ => cut,
        }
    };
    format!("{body} {label}")
}

/// The title minus the trailing AI label the tool adds itself, if it has one.
fn without_ai_label(title: &str, label: &str) -> String {
    let label = label.trim();
    let (title_chars, label_chars): (Vec<char>, Vec<char>) = (title.chars().collect(), label.chars().collect());
    let ends_with_label = !label.is_empty()
        && title.to_lowercase().ends_with(&label.to_lowercase())
        && title_chars.len() >= label_chars.len();
    if !ends_with_label {
        return title.to_string();
    }
    let kept: String = title_chars[..title_chars.len() - label_chars.len()].iter().collect();
    kept.trim_end().to_string()
}

/// Problems found in the metadata, including filename mismatches.
pub fn check_items(items: &[Item], existing_files: &HashSet<String>, ai_label: &str) -> Vec<String> {
    let mut problems = Vec::new();
    if items.len() > MAX_ROWS {
        problems.push(format!("{} rows, limit is {MAX_ROWS}", items.len()));
    }
    let mut seen = HashSet::new();
    for item in items {
        let name = &item.filename;
        if name.is_empty() {
            problems.push("a row has an empty filename".to_string());
        }
        if !seen.insert(name.clone()) {
            problems.push(format!("{name}: duplicate row"));
        }
        if !existing_files.contains(name) {
            problems.push(format!("{name}: no such file among uploads"));
        }
        if item.title.is_empty() {
            problems.push(format!("{name}: empty title"));
        }
        if item.title.chars().count() > MAX_TITLE_CHARS {
            problems.push(format!("{name}: title longer than {MAX_TITLE_CHARS}"));
        }
        if let Some(found) = AI_TERMS.find(&without_ai_label(&item.title, ai_label)) {
            problems.push(format!(
                "{name}: title mentions {}; Adobe labels AI content itself",
                py_repr(found.as_str())
            ));
        }
        if item.keywords.is_empty() {
            problems.push(format!("{name}: no keywords"));
        }
        if item.keywords.len() > MAX_KEYWORDS {
            problems.push(format!("{name}: more than {MAX_KEYWORDS} keywords"));
        }
        if let Some(banned) = item.keywords.iter().find(|keyword| AI_TERMS.is_match(keyword)) {
            problems.push(format!(
                "{name}: keyword {} mentions AI; Adobe labels AI content itself",
                py_repr(banned)
            ));
        }
        for keyword in &item.keywords {
            if let Some(reason) = keyword_problem(keyword) {
                problems.push(format!("{name}: keyword {} {reason}", py_repr(keyword)));
            }
        }
        let valid_category = item.category.parse::<u32>().is_ok_and(|code| (1..=CATEGORY_COUNT).contains(&code));
        if !item.category.is_empty() && !valid_category {
            problems.push(format!(
                "{name}: category must be a code from 1-{CATEGORY_COUNT}, got {}",
                py_repr(&item.category)
            ));
        }
    }
    problems
}

/// Rows without a category. `check_items` lets it stay empty (Claude may find none, our own CSV may omit
/// it), but a CSV that goes to the portal must have one.
pub fn missing_category_problems(items: &[Item]) -> Vec<String> {
    items
        .iter()
        .filter(|item| item.category.is_empty())
        .map(|item| format!("{}: no category", item.filename))
        .collect()
}

/// UTF-8 size of one CSV row (csv.writer, minimal quoting, `\r\n`), to keep batches under the size limit.
fn estimate_row_bytes(item: &Item) -> usize {
    let keywords = item.keywords.join(", ");
    let fields = [item.filename.as_str(), item.title.as_str(), keywords.as_str(), item.category.as_str(), ""];
    let quoted = |field: &str| {
        if field.contains([',', '"', '\r', '\n']) {
            field.len() + 2 + field.matches('"').count()
        } else {
            field.len()
        }
    };
    fields.iter().map(|field| quoted(field)).sum::<usize>() + (fields.len() - 1) + 2
}

/// Groups items so that each group fits `batch_size`, the row limit and the size limit.
pub fn split_into_batches(items: &[Item], batch_size: Option<usize>) -> Vec<Vec<Item>> {
    let max_rows = batch_size.filter(|size| *size > 0).unwrap_or(MAX_ROWS).min(MAX_ROWS);
    let header_bytes = HEADERS.join(",").len() + 2;
    let mut batches: Vec<Vec<Item>> = Vec::new();
    let mut current: Vec<Item> = Vec::new();
    let mut used = header_bytes;
    for item in items {
        let row_bytes = estimate_row_bytes(item);
        if !current.is_empty() && (current.len() >= max_rows || used + row_bytes > MAX_CSV_BYTES) {
            batches.push(std::mem::take(&mut current));
            used = header_bytes;
        }
        current.push(item.clone());
        used += row_bytes;
    }
    if !current.is_empty() {
        batches.push(current);
    }
    batches
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(name: &str, title: &str, keywords: &[&str]) -> Item {
        Item {
            filename: name.into(),
            title: title.into(),
            keywords: keywords.iter().map(|word| word.to_string()).collect(),
            category: String::new(),
        }
    }

    fn names(files: &[&str]) -> HashSet<String> {
        files.iter().map(|name| name.to_string()).collect()
    }

    #[test]
    fn clean_item_has_no_problems() {
        let problems = check_items(&[item("a.jpg", "A red fox", &["fox", "animal"])], &names(&["a.jpg"]), "Generative AI");
        assert!(problems.is_empty(), "{problems:?}");
    }

    #[test]
    fn ai_words_in_title_keyword_and_the_own_label_are_told_apart() {
        let own_label = item("a.jpg", "A red fox Generative AI", &["fox"]);
        assert!(check_items(&[own_label], &names(&["a.jpg"]), "Generative AI").is_empty());
        let bad = item("a.jpg", "Midjourney fox", &["ai art"]);
        let problems = check_items(&[bad], &names(&["a.jpg"]), "Generative AI");
        assert!(problems.iter().any(|p| p.contains("title mentions 'Midjourney'")), "{problems:?}");
        assert!(problems.iter().any(|p| p.contains("keyword 'ai art' mentions AI")), "{problems:?}");
    }

    #[test]
    fn reports_missing_file_empty_fields_and_bad_category() {
        let mut bad = item("x.jpg", "", &[]);
        bad.category = "99".into();
        let problems = check_items(&[bad], &names(&[]), "");
        assert!(problems.contains(&"x.jpg: no such file among uploads".to_string()));
        assert!(problems.contains(&"x.jpg: empty title".to_string()));
        assert!(problems.contains(&"x.jpg: no keywords".to_string()));
        assert!(problems.contains(&"x.jpg: category must be a code from 1-21, got '99'".to_string()));
    }

    #[test]
    fn keyword_rules() {
        assert!(keyword_problem("a, b").is_some());
        assert_eq!(keyword_problem("2024"), Some("is only a number"));
        assert_eq!(keyword_problem("photo.jpg"), Some("looks like file information"));
        assert_eq!(keyword_problem("12 mp"), Some("looks like file information"));
        assert_eq!(keyword_problem("sunset"), None);
    }

    #[test]
    fn batches_respect_the_row_limit() {
        let items: Vec<Item> = (0..5).map(|n| item(&format!("{n}.jpg"), "T", &["k"])).collect();
        let sizes: Vec<usize> = split_into_batches(&items, Some(2)).iter().map(Vec::len).collect();
        assert_eq!(sizes, vec![2, 2, 1]);
        assert_eq!(split_into_batches(&items, None).len(), 1);
    }

    #[test]
    fn row_size_counts_quoting() {
        let plain = item("a.jpg", "T", &["k"]);
        assert_eq!(estimate_row_bytes(&plain), "a.jpg,T,k,,\r\n".len());
        let quoted = item("a.jpg", "T", &["a", "b"]);
        assert_eq!(estimate_row_bytes(&quoted), "a.jpg,T,\"a, b\",,\r\n".len());
    }

    #[test]
    fn reads_our_input_csv_with_bom_and_extra_columns() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("metadata.csv");
        std::fs::write(&file, "\u{feff}filename,title,keywords,note\na.jpg,Fox,\"a, b\",x\n").unwrap();
        let items = read_source(&file).unwrap();
        assert_eq!(items, vec![item("a.jpg", "Fox", &["a", "b"])]);
        std::fs::write(&file, "name,title\n").unwrap();
        assert!(read_source(&file).is_err());
    }
}

fn csv_row(item: &Item) -> [String; 5] {
    // Releases is left empty: AI images normally have no model or property releases.
    [item.filename.clone(), item.title.clone(), item.keywords.join(", "), item.category.clone(), String::new()]
}

/// The Adobe CSV as bytes: UTF-8, Adobe's headers, CRLF line ends like Python's `csv.writer`.
pub fn adobe_csv_bytes(items: &[Item]) -> Result<Vec<u8>, String> {
    if items.len() > MAX_ROWS {
        return Err(format!("{} rows, Adobe limit is {MAX_ROWS}", items.len()));
    }
    let mut writer = csv::WriterBuilder::new().terminator(csv::Terminator::CRLF).from_writer(Vec::new());
    writer.write_record(HEADERS).map_err(|error| error.to_string())?;
    for item in items {
        writer.write_record(csv_row(item)).map_err(|error| error.to_string())?;
    }
    let content = writer.into_inner().map_err(|error| error.to_string())?;
    if content.len() > MAX_CSV_BYTES {
        return Err(format!("CSV is {} bytes, Adobe limit is {MAX_CSV_BYTES}", content.len()));
    }
    Ok(content)
}

/// Writes a UTF-8 CSV with Adobe's headers; refuses to write one over the Adobe limits.
pub fn write_adobe_csv(items: &[Item], destination: &Path) -> Result<(), String> {
    let content = adobe_csv_bytes(items)?;
    std::fs::write(destination, content).map_err(|error| format!("{}: {error}", destination.display()))
}

/// Re-reads a written CSV and checks it against Adobe's rules, as the portal would.
pub fn validate_csv_file(path: &Path, expected_filenames: &HashSet<String>) -> Vec<String> {
    let bytes = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) => return vec![format!("{}: {error}", path.display())],
    };
    if bytes.len() > MAX_CSV_BYTES {
        return vec![format!("file is larger than {MAX_CSV_BYTES} bytes")];
    }
    let Ok(text) = String::from_utf8(bytes) else { return vec!["file is not valid UTF-8".to_string()] };
    let mut reader = csv::ReaderBuilder::new().has_headers(false).flexible(true).from_reader(text.as_bytes());
    let rows: Vec<Vec<String>> =
        reader.records().filter_map(Result::ok).map(|record| record.iter().map(str::to_string).collect()).collect();
    let mut problems = Vec::new();
    let headers_ok = rows.first().is_some_and(|first| first.iter().map(String::as_str).eq(HEADERS));
    if !headers_ok {
        let shown = rows.first().map_or_else(|| "[]".to_string(), |first| py_repr_list(first));
        let wanted = py_repr_list(&HEADERS.map(str::to_string));
        problems.push(format!("headers {shown} differ from {wanted}"));
    }
    let data = rows.get(1..).unwrap_or(&[]);
    if data.len() > MAX_ROWS {
        problems.push(format!("{} rows, limit is {MAX_ROWS}", data.len()));
    }
    if data.iter().any(|row| row.len() != HEADERS.len()) {
        problems.push("some rows do not have exactly 5 columns".to_string());
    }
    if data.iter().any(|row| row.len() > 1 && row[1].chars().count() > MAX_TITLE_CHARS) {
        problems.push(format!("some titles are longer than {MAX_TITLE_CHARS} characters"));
    }
    let names: Vec<&String> = data.iter().filter_map(|row| row.first()).collect();
    if names.iter().collect::<HashSet<_>>().len() != names.len() {
        problems.push("duplicate filenames".to_string());
    }
    let seen: HashSet<String> = names.into_iter().cloned().collect();
    if &seen != expected_filenames {
        problems.push("filenames differ from the files that were uploaded".to_string());
    }
    problems
}

/// Python's `repr()` of a list of strings: `['a', 'b']`.
fn py_repr_list(items: &[String]) -> String {
    format!("[{}]", items.iter().map(|item| py_repr(item)).collect::<Vec<_>>().join(", "))
}

#[cfg(test)]
mod csv_writing_tests {
    use super::*;

    fn item(name: &str, title: &str, keywords: &[&str]) -> Item {
        Item {
            filename: name.into(),
            title: title.into(),
            keywords: keywords.iter().map(|word| word.to_string()).collect(),
            category: "7".into(),
        }
    }

    #[test]
    fn the_csv_is_crlf_utf8_and_quotes_only_when_needed() {
        let bytes = adobe_csv_bytes(&[item("a.jpg", "A \"fox\", red", &["fox", "кот"])]).unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert_eq!(text, "Filename,Title,Keywords,Category,Releases\r\na.jpg,\"A \"\"fox\"\", red\",\"fox, кот\",7,\r\n");
    }

    #[test]
    fn a_written_csv_validates_and_problems_are_found() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("m.csv");
        let items = [item("a.jpg", "A", &["x"]), item("b.jpg", "B", &["y"])];
        write_adobe_csv(&items, &path).unwrap();
        let names: HashSet<String> = ["a.jpg", "b.jpg"].map(String::from).into();
        assert!(validate_csv_file(&path, &names).is_empty());

        let missing: HashSet<String> = ["a.jpg"].map(String::from).into();
        assert_eq!(validate_csv_file(&path, &missing), vec!["filenames differ from the files that were uploaded"]);

        std::fs::write(&path, "Name,Title\r\na.jpg,A\r\na.jpg,B\r\n").unwrap();
        let problems = validate_csv_file(&path, &missing);
        assert!(problems[0].starts_with("headers ['Name', 'Title'] differ from ['Filename'"), "{problems:?}");
        assert!(problems.contains(&"some rows do not have exactly 5 columns".to_string()));
        assert!(problems.contains(&"duplicate filenames".to_string()));
    }
}
