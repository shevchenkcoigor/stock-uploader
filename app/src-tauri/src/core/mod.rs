//! The pipeline core: scanning, captioning, preparing copies with embedded metadata, uploading, and the
//! SQLite state they all share.

pub mod backup;
pub mod caption;
pub mod caption_run;
pub mod categories;
pub mod describe;
#[cfg(all(test, unix))]
mod e2e;
pub mod embed;
pub mod export;
pub mod imaging;
pub mod metadata;
pub mod scan;
pub mod sftp;
pub mod state;
pub mod submit;
pub mod upload;
pub mod validate;

/// Python's `repr()` of a string, so messages read exactly like the ones the Python app stored.
pub fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::py_repr;

    #[test]
    fn repr_matches_python_quoting() {
        assert_eq!(py_repr("abc"), "'abc'");
        assert_eq!(py_repr("it's"), "\"it's\"");
        assert_eq!(py_repr(""), "''");
    }
}
