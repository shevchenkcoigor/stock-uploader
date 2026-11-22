//! IPTC-IIM datasets and the Photoshop "8BIM" resource that carries them inside a JPEG APP13 segment.

use super::EmbedMeta;
use crate::error::{AppError, AppResult};

const MARKER: u8 = 0x1C;
pub(super) const PHOTOSHOP_HEADER: &[u8] = b"Photoshop 3.0\0";
const RESOURCE_SIGNATURE: &[u8] = b"8BIM";
const IPTC_RESOURCE_ID: u16 = 0x0404;
/// Declares the text below as UTF-8 (ESC % G), as Lightroom does.
const UTF8_ESCAPE: [u8; 3] = [0x1B, 0x25, 0x47];
pub(super) const MAX_OBJECT_NAME: usize = 64;
const MAX_HEADLINE: usize = 256;
const MAX_KEYWORD: usize = 64;
const MAX_CAPTION: usize = 2000;

// (record, dataset) numbers of the IIM fields we write.
const CODED_CHARSET: (u8, u8) = (1, 90);
const RECORD_VERSION: (u8, u8) = (2, 0);
const OBJECT_NAME: (u8, u8) = (2, 5);
const KEYWORDS: (u8, u8) = (2, 25);
const HEADLINE: (u8, u8) = (2, 105);
const CAPTION: (u8, u8) = (2, 120);

/// What we read back out of an IPTC block.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct IptcFields {
    pub object_name: Option<String>,
    pub headline: Option<String>,
    pub caption: Option<String>,
    pub keywords: Vec<String>,
}

fn push_dataset(out: &mut Vec<u8>, (record, dataset): (u8, u8), data: &[u8]) {
    let length = u16::try_from(data.len()).expect("dataset sizes are checked before pushing");
    out.extend_from_slice(&[MARKER, record, dataset]);
    out.extend_from_slice(&length.to_be_bytes());
    out.extend_from_slice(data);
}

fn check_len(name: &str, text: &str, max: usize) -> AppResult<()> {
    if text.len() > max {
        return Err(AppError::Invalid(format!(
            "{name} слишком длинный для IPTC: {} байт при максимуме {max}",
            text.len()
        )));
    }
    Ok(())
}

/// The IPTC datasets for `meta`. The object name (a 64-byte field) is left out for a longer
/// title instead of being cut, so the stored text never silently differs from the database.
pub fn build_iim(meta: &EmbedMeta) -> AppResult<Vec<u8>> {
    check_len("Заголовок", &meta.title, MAX_HEADLINE)?;
    check_len("Описание", &meta.description, MAX_CAPTION)?;
    for keyword in &meta.keywords {
        check_len(&format!("Ключевое слово {keyword:?}"), keyword, MAX_KEYWORD)?;
    }
    let mut out = Vec::new();
    push_dataset(&mut out, CODED_CHARSET, &UTF8_ESCAPE);
    push_dataset(&mut out, RECORD_VERSION, &4u16.to_be_bytes());
    if meta.title.len() <= MAX_OBJECT_NAME {
        push_dataset(&mut out, OBJECT_NAME, meta.title.as_bytes());
    }
    for keyword in &meta.keywords {
        push_dataset(&mut out, KEYWORDS, keyword.as_bytes());
    }
    push_dataset(&mut out, HEADLINE, meta.title.as_bytes());
    push_dataset(&mut out, CAPTION, meta.description.as_bytes());
    Ok(out)
}

pub fn parse_iim(iim: &[u8]) -> IptcFields {
    let mut fields = IptcFields::default();
    let mut at = 0;
    while at + 5 <= iim.len() && iim[at] == MARKER {
        let key = (iim[at + 1], iim[at + 2]);
        let length = usize::from(u16::from_be_bytes([iim[at + 3], iim[at + 4]]));
        let Some(data) = iim.get(at + 5..at + 5 + length) else { break };
        let text = || String::from_utf8_lossy(data).into_owned();
        match key {
            OBJECT_NAME => fields.object_name = Some(text()),
            HEADLINE => fields.headline = Some(text()),
            CAPTION => fields.caption = Some(text()),
            KEYWORDS => fields.keywords.push(text()),
            _ => {}
        }
        at += 5 + length;
    }
    fields
}

/// One "8BIM" image resource: its id, its whole raw bytes and just its data.
struct Resource<'a> {
    id: u16,
    raw: &'a [u8],
    data: &'a [u8],
}

fn padded(length: usize) -> usize {
    length + length % 2
}

fn resources(app13: &[u8]) -> Option<Vec<Resource<'_>>> {
    let mut at = app13.strip_prefix(PHOTOSHOP_HEADER).map(|rest| app13.len() - rest.len())?;
    let mut found = Vec::new();
    while app13.get(at..at + 4) == Some(RESOURCE_SIGNATURE) {
        let start = at;
        let id = u16::from_be_bytes([*app13.get(at + 4)?, *app13.get(at + 5)?]);
        let name_len = usize::from(*app13.get(at + 6)?);
        let size_at = at + 6 + padded(1 + name_len);
        let size = u32::from_be_bytes(app13.get(size_at..size_at + 4)?.try_into().ok()?) as usize;
        let data = app13.get(size_at + 4..size_at + 4 + size)?;
        at = size_at + 4 + padded(size);
        found.push(Resource { id, raw: app13.get(start..at.min(app13.len()))?, data });
    }
    Some(found)
}

fn iptc_resource(iim: &[u8]) -> Vec<u8> {
    let mut out = RESOURCE_SIGNATURE.to_vec();
    out.extend_from_slice(&IPTC_RESOURCE_ID.to_be_bytes());
    out.extend_from_slice(&[0, 0]); // empty Pascal name, padded to even length
    out.extend_from_slice(&u32::try_from(iim.len()).unwrap_or(u32::MAX).to_be_bytes());
    out.extend_from_slice(iim);
    if iim.len() % 2 == 1 {
        out.push(0);
    }
    out
}

/// The APP13 payload: `existing` with its IPTC resource replaced by `iim`, other resources kept.
pub fn merge_app13(existing: Option<&[u8]>, iim: &[u8]) -> Vec<u8> {
    let mut out = PHOTOSHOP_HEADER.to_vec();
    out.extend(iptc_resource(iim));
    let kept = existing.and_then(resources).unwrap_or_default();
    for resource in kept.iter().filter(|resource| resource.id != IPTC_RESOURCE_ID) {
        out.extend_from_slice(resource.raw);
    }
    out
}

/// The IPTC-IIM bytes inside an APP13 payload, if it has the IPTC resource.
pub fn extract_iim(app13: &[u8]) -> Option<Vec<u8>> {
    resources(app13)?.into_iter().find(|resource| resource.id == IPTC_RESOURCE_ID).map(|resource| resource.data.to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn meta(title: &str, keywords: &[&str]) -> EmbedMeta {
        EmbedMeta {
            title: title.to_string(),
            description: title.to_string(),
            keywords: keywords.iter().map(|k| k.to_string()).collect(),
        }
    }

    #[test]
    fn round_trips_title_description_and_keywords() {
        let iim = build_iim(&meta("Blue wave", &["sea", "surf"])).unwrap();
        let fields = parse_iim(&iim);
        assert_eq!(fields.object_name.as_deref(), Some("Blue wave"));
        assert_eq!(fields.headline.as_deref(), Some("Blue wave"));
        assert_eq!(fields.caption.as_deref(), Some("Blue wave"));
        assert_eq!(fields.keywords, vec!["sea", "surf"]);
    }

    #[test]
    fn declares_utf8_and_keeps_non_ascii_text() {
        let iim = build_iim(&meta("Café wave", &["mer"])).unwrap();
        assert!(iim.windows(5).any(|w| w == [0x1C, 0x01, 0x5A, 0x00, 0x03]));
        assert_eq!(parse_iim(&iim).object_name.as_deref(), Some("Café wave"));
    }

    #[test]
    fn a_title_over_64_bytes_skips_object_name_but_keeps_the_headline() {
        let title = "A".repeat(150);
        let fields = parse_iim(&build_iim(&meta(&title, &["sea"])).unwrap());
        assert_eq!(fields.object_name, None);
        assert_eq!(fields.headline.as_deref(), Some(title.as_str()));
    }

    #[test]
    fn rejects_a_keyword_longer_than_64_bytes() {
        assert!(build_iim(&meta("Title", &[&"k".repeat(65)])).is_err());
    }

    #[test]
    fn rejects_a_headline_longer_than_256_bytes() {
        assert!(build_iim(&meta(&"t".repeat(257), &["sea"])).is_err());
    }

    #[test]
    fn merge_replaces_the_iptc_resource_and_keeps_other_resources() {
        let mut existing = b"Photoshop 3.0\0".to_vec();
        existing.extend_from_slice(b"8BIM\x04\x04\x00\x00\x00\x00\x00\x04OLD!");
        existing.extend_from_slice(b"8BIM\x03\xED\x00\x00\x00\x00\x00\x02ab");
        let iim = build_iim(&meta("New", &["x"])).unwrap();
        let merged = merge_app13(Some(&existing), &iim);
        assert!(merged.starts_with(b"Photoshop 3.0\0"));
        assert!(!merged.windows(4).any(|w| w == b"OLD!"));
        assert!(merged.windows(10).any(|w| w == b"8BIM\x03\xED\x00\x00\x00\x00"));
        assert_eq!(parse_iim(&extract_iim(&merged).unwrap()).object_name.as_deref(), Some("New"));
    }

    #[test]
    fn merge_without_an_existing_segment_makes_a_fresh_one() {
        let iim = build_iim(&meta("New", &["x"])).unwrap();
        assert_eq!(extract_iim(&merge_app13(None, &iim)).unwrap(), iim);
    }

    #[test]
    fn extract_returns_none_for_foreign_data() {
        assert_eq!(extract_iim(b"not photoshop"), None);
    }
}
