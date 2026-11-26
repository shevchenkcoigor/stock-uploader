//! The XMP packet (Dublin Core title, description and subject) stored in a JPEG APP1 segment.

use std::sync::LazyLock;

use regex::Regex;

use super::EmbedMeta;

/// What we read back out of an XMP packet.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct XmpFields {
    pub title: Option<String>,
    pub description: Option<String>,
    pub keywords: Vec<String>,
}

static ITEM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)<rdf:li[^>]*>(.*?)</rdf:li>").unwrap());

fn escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

fn unescape(text: &str) -> String {
    text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&apos;", "'").replace("&amp;", "&")
}

fn language_alternative(tag: &str, text: &str) -> String {
    format!("<dc:{tag}><rdf:Alt><rdf:li xml:lang=\"x-default\">{}</rdf:li></rdf:Alt></dc:{tag}>", escape(text))
}

pub fn build_packet(meta: &EmbedMeta) -> String {
    let keywords: String =
        meta.keywords.iter().map(|keyword| format!("<rdf:li>{}</rdf:li>", escape(keyword))).collect();
    format!(
        "<?xpacket begin=\"\u{FEFF}\" id=\"W5M0MpCehiHzreSzNTczkc9d\"?>\n\
         <x:xmpmeta xmlns:x=\"adobe:ns:meta/\"><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\
         <rdf:Description rdf:about=\"\" xmlns:dc=\"http://purl.org/dc/elements/1.1/\">{}{}<dc:subject><rdf:Bag>{keywords}</rdf:Bag></dc:subject>\
         </rdf:Description></rdf:RDF></x:xmpmeta>\n<?xpacket end=\"w\"?>",
        language_alternative("title", &meta.title),
        language_alternative("description", &meta.description),
    )
}

fn section<'a>(packet: &'a str, tag: &str) -> Option<&'a str> {
    let open = format!("<dc:{tag}>");
    let start = packet.find(&open)? + open.len();
    let end = start + packet[start..].find(&format!("</dc:{tag}>"))?;
    Some(&packet[start..end])
}

fn first_item(packet: &str, tag: &str) -> Option<String> {
    let found = ITEM.captures(section(packet, tag)?)?;
    Some(unescape(&found[1]))
}

pub fn parse_packet(packet: &str) -> XmpFields {
    let keywords = section(packet, "subject")
        .map(|bag| ITEM.captures_iter(bag).map(|found| unescape(&found[1])).collect())
        .unwrap_or_default();
    XmpFields { title: first_item(packet, "title"), description: first_item(packet, "description"), keywords }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn meta(title: &str, keywords: &[&str]) -> EmbedMeta {
        EmbedMeta {
            title: title.to_string(),
            description: format!("{title} description"),
            keywords: keywords.iter().map(|k| k.to_string()).collect(),
        }
    }

    #[test]
    fn round_trips_title_description_and_keywords() {
        let fields = parse_packet(&build_packet(&meta("Blue wave", &["sea", "surf"])));
        assert_eq!(fields.title.as_deref(), Some("Blue wave"));
        assert_eq!(fields.description.as_deref(), Some("Blue wave description"));
        assert_eq!(fields.keywords, vec!["sea", "surf"]);
    }

    #[test]
    fn escapes_xml_special_characters() {
        let packet = build_packet(&meta("Tom & \"Jerry\" <3", &["a&b"]));
        assert!(!packet.contains("Tom & "));
        let fields = parse_packet(&packet);
        assert_eq!(fields.title.as_deref(), Some("Tom & \"Jerry\" <3"));
        assert_eq!(fields.keywords, vec!["a&b"]);
    }

    #[test]
    fn keeps_unicode() {
        let fields = parse_packet(&build_packet(&meta("Волна", &["море"])));
        assert_eq!(fields.title.as_deref(), Some("Волна"));
        assert_eq!(fields.keywords, vec!["море"]);
    }

    #[test]
    fn is_a_wrapped_xmp_packet() {
        let packet = build_packet(&meta("T", &["k"]));
        assert!(packet.starts_with("<?xpacket begin="));
        assert!(packet.contains("<rdf:Bag>"));
        assert!(packet.trim_end().ends_with("<?xpacket end=\"w\"?>"));
    }

    #[test]
    fn parse_of_foreign_text_is_empty() {
        assert_eq!(parse_packet("hello"), XmpFields::default());
    }
}
