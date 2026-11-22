//! Writing the title, description and keywords into a copy of a JPEG (XMP + IPTC), the way
//! Lightroom does, so Adobe Stock picks them up on upload without a CSV.

mod iptc;
mod xmp;

use std::path::Path;

use img_parts::Bytes;
use img_parts::jpeg::{Jpeg, JpegSegment, markers};

use crate::error::{AppError, AppResult};

const XMP_HEADER: &[u8] = b"http://ns.adobe.com/xap/1.0/\0";
const XMP_EXTENSION_HEADER: &[u8] = b"http://ns.adobe.com/xmp/extension/\0";
const EXIF_HEADER: &[u8] = b"Exif\0\0";
/// A JPEG segment stores its length in 16 bits, two of which count the length field itself.
const MAX_SEGMENT_PAYLOAD: usize = 65_533;

/// The text embedded in a picture.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EmbedMeta {
    pub title: String,
    pub description: String,
    pub keywords: Vec<String>,
}

/// The metadata found in a file, as the two standards store it.
#[derive(Debug, PartialEq, Eq)]
pub struct Embedded {
    pub xmp: xmp::XmpFields,
    pub iptc: iptc::IptcFields,
}

fn read_jpeg(path: &Path) -> AppResult<Jpeg> {
    let bytes = std::fs::read(path)?;
    Jpeg::from_bytes(Bytes::from(bytes))
        .map_err(|error| AppError::Invalid(format!("{} не читается как JPEG: {error}", path.display())))
}

fn is_xmp(segment: &JpegSegment) -> bool {
    segment.marker() == markers::APP1
        && (segment.contents().starts_with(XMP_HEADER) || segment.contents().starts_with(XMP_EXTENSION_HEADER))
}

fn is_photoshop(segment: &JpegSegment) -> bool {
    segment.marker() == markers::APP13 && segment.contents().starts_with(iptc::PHOTOSHOP_HEADER)
}

/// Where our segments go: after the JFIF and Exif headers a decoder expects first.
fn insert_position(segments: &[JpegSegment]) -> usize {
    segments
        .iter()
        .take_while(|segment| {
            segment.marker() == markers::APP0
                || (segment.marker() == markers::APP1 && segment.contents().starts_with(EXIF_HEADER))
        })
        .count()
}

fn checked_segment(marker: u8, payload: Vec<u8>, what: &str) -> AppResult<JpegSegment> {
    if payload.len() > MAX_SEGMENT_PAYLOAD {
        return Err(AppError::Invalid(format!("{what} не помещается в один блок JPEG ({} байт)", payload.len())));
    }
    Ok(JpegSegment::new_with_contents(marker, Bytes::from(payload)))
}

fn same_file(first: &Path, second: &Path) -> bool {
    match (first.canonicalize(), second.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => first == second,
    }
}

fn embedded_jpeg(src: &Path, meta: &EmbedMeta) -> AppResult<Jpeg> {
    let mut jpeg = read_jpeg(src)?;
    let existing_app13 = jpeg.segments().iter().find(|segment| is_photoshop(segment)).map(|segment| segment.contents().to_vec());
    jpeg.segments_mut().retain(|segment| !is_xmp(segment) && !is_photoshop(segment));
    let iim = iptc::build_iim(meta)?;
    let app13 = checked_segment(markers::APP13, iptc::merge_app13(existing_app13.as_deref(), &iim), "IPTC")?;
    let mut xmp_payload = XMP_HEADER.to_vec();
    xmp_payload.extend_from_slice(xmp::build_packet(meta).as_bytes());
    let xmp_segment = checked_segment(markers::APP1, xmp_payload, "XMP")?;
    let at = insert_position(jpeg.segments());
    jpeg.segments_mut().insert(at, xmp_segment);
    jpeg.segments_mut().insert(at + 1, app13);
    Ok(jpeg)
}

/// Writes a copy of the JPEG `src` at `dst` that carries `meta`, replacing any XMP and IPTC the
/// source had. `src` is never modified; the copy is read back and checked before it is kept.
pub fn write_embedded(src: &Path, dst: &Path, meta: &EmbedMeta) -> AppResult<()> {
    if same_file(src, dst) {
        return Err(AppError::Invalid(format!("{} нельзя перезаписать: метаданные пишутся только в копию", src.display())));
    }
    let jpeg = embedded_jpeg(src, meta)?;
    if let Some(parent) = dst.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut partial = dst.as_os_str().to_owned();
    partial.push(".part");
    let partial = std::path::PathBuf::from(partial);
    std::fs::write(&partial, jpeg.encoder().bytes())?;
    if let Err(error) = verify_embedded(&partial, meta) {
        let _ = std::fs::remove_file(&partial);
        return Err(error);
    }
    std::fs::rename(&partial, dst)?;
    Ok(())
}

pub fn read_embedded(path: &Path) -> AppResult<Embedded> {
    let jpeg = read_jpeg(path)?;
    let xmp = jpeg
        .segments()
        .iter()
        .find(|segment| segment.contents().starts_with(XMP_HEADER) && segment.marker() == markers::APP1)
        .map(|segment| xmp::parse_packet(&String::from_utf8_lossy(&segment.contents()[XMP_HEADER.len()..])))
        .unwrap_or_default();
    let iptc = jpeg
        .segments()
        .iter()
        .filter(|segment| is_photoshop(segment))
        .find_map(|segment| iptc::extract_iim(segment.contents()))
        .map(|iim| iptc::parse_iim(&iim))
        .unwrap_or_default();
    Ok(Embedded { xmp, iptc })
}

/// Errors unless the file's XMP and IPTC both carry exactly `meta`.
pub fn verify_embedded(path: &Path, meta: &EmbedMeta) -> AppResult<()> {
    let found = read_embedded(path)?;
    let expected_xmp = xmp::XmpFields {
        title: Some(meta.title.clone()),
        description: Some(meta.description.clone()),
        keywords: meta.keywords.clone(),
    };
    let expected_iptc = iptc::IptcFields {
        object_name: (meta.title.len() <= iptc::MAX_OBJECT_NAME).then(|| meta.title.clone()),
        headline: Some(meta.title.clone()),
        caption: Some(meta.description.clone()),
        keywords: meta.keywords.clone(),
    };
    if found.xmp != expected_xmp {
        return Err(AppError::Invalid(format!("XMP в {} не совпадает с записанным", path.display())));
    }
    if found.iptc != expected_iptc {
        return Err(AppError::Invalid(format!("IPTC в {} не совпадает с записанным", path.display())));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{ExtendedColorType, codecs::jpeg::JpegEncoder};
    use img_parts::jpeg::{Jpeg, JpegSegment, markers};
    use img_parts::Bytes;

    fn meta(title: &str) -> EmbedMeta {
        EmbedMeta {
            title: title.to_string(),
            description: title.to_string(),
            keywords: vec!["sea".to_string(), "surf".to_string()],
        }
    }

    fn write_jpeg(path: &Path) {
        let pixels: Vec<u8> = (0..16 * 16 * 3).map(|i| (i % 251) as u8).collect();
        let file = std::fs::File::create(path).unwrap();
        JpegEncoder::new(std::io::BufWriter::new(file))
            .encode(&pixels, 16, 16, ExtendedColorType::Rgb8)
            .unwrap();
    }

    fn segment_count(path: &Path, marker: u8) -> usize {
        let jpeg = Jpeg::from_bytes(std::fs::read(path).unwrap().into()).unwrap();
        jpeg.segments_by_marker(marker).count()
    }

    #[test]
    fn writes_a_copy_with_readable_metadata_and_leaves_the_source_alone() {
        let dir = tempfile::tempdir().unwrap();
        let (src, dst) = (dir.path().join("a.jpg"), dir.path().join("out").join("a.jpg"));
        write_jpeg(&src);
        let before = std::fs::read(&src).unwrap();
        write_embedded(&src, &dst, &meta("Blue wave")).unwrap();
        assert_eq!(std::fs::read(&src).unwrap(), before);
        let found = read_embedded(&dst).unwrap();
        assert_eq!(found.xmp.title.as_deref(), Some("Blue wave"));
        assert_eq!(found.xmp.keywords, vec!["sea", "surf"]);
        assert_eq!(found.iptc.keywords, vec!["sea", "surf"]);
        assert_eq!(found.iptc.caption.as_deref(), Some("Blue wave"));
        verify_embedded(&dst, &meta("Blue wave")).unwrap();
    }

    #[test]
    fn the_pixels_are_untouched() {
        let dir = tempfile::tempdir().unwrap();
        let (src, dst) = (dir.path().join("a.jpg"), dir.path().join("b.jpg"));
        write_jpeg(&src);
        write_embedded(&src, &dst, &meta("T")).unwrap();
        let decode = |path: &Path| image::open(path).unwrap().to_rgb8().into_raw();
        assert_eq!(decode(&src), decode(&dst));
    }

    #[test]
    fn replaces_foreign_xmp_and_keeps_other_segments() {
        let dir = tempfile::tempdir().unwrap();
        let (src, dst) = (dir.path().join("a.jpg"), dir.path().join("b.jpg"));
        write_jpeg(&src);
        let mut jpeg = Jpeg::from_bytes(std::fs::read(&src).unwrap().into()).unwrap();
        let foreign_xmp = b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta>--v 5.2 Job ID: 963d</x:xmpmeta>".to_vec();
        jpeg.segments_mut().insert(0, JpegSegment::new_with_contents(markers::APP1, Bytes::from(foreign_xmp)));
        jpeg.segments_mut().insert(1, JpegSegment::new_with_contents(markers::APP2, Bytes::from_static(b"ICC_PROFILE\0keep")));
        std::fs::write(&src, jpeg.encoder().bytes()).unwrap();
        write_embedded(&src, &dst, &meta("Blue wave")).unwrap();
        let bytes = std::fs::read(&dst).unwrap();
        assert!(!bytes.windows(6).any(|w| w == b"Job ID"));
        assert_eq!(segment_count(&dst, markers::APP2), 1);
        assert_eq!(segment_count(&dst, markers::APP1), 1);
    }

    #[test]
    fn embedding_a_copy_again_replaces_the_old_metadata() {
        let dir = tempfile::tempdir().unwrap();
        let (src, first, second) = (dir.path().join("a.jpg"), dir.path().join("1.jpg"), dir.path().join("2.jpg"));
        write_jpeg(&src);
        write_embedded(&src, &first, &meta("Old title")).unwrap();
        write_embedded(&first, &second, &meta("New title")).unwrap();
        assert_eq!(read_embedded(&second).unwrap().xmp.title.as_deref(), Some("New title"));
        assert_eq!(segment_count(&second, markers::APP1), 1);
        assert_eq!(segment_count(&second, markers::APP13), 1);
    }

    #[test]
    fn refuses_to_overwrite_its_own_source() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("a.jpg");
        write_jpeg(&src);
        assert!(write_embedded(&src, &src, &meta("T")).is_err());
    }

    #[test]
    fn rejects_a_file_that_is_not_a_jpeg() {
        let dir = tempfile::tempdir().unwrap();
        let (src, dst) = (dir.path().join("a.jpg"), dir.path().join("b.jpg"));
        std::fs::write(&src, b"not a picture").unwrap();
        assert!(write_embedded(&src, &dst, &meta("T")).is_err());
        assert!(!dst.exists());
    }

    #[test]
    fn a_long_title_stays_whole_in_xmp_and_headline() {
        let dir = tempfile::tempdir().unwrap();
        let (src, dst) = (dir.path().join("a.jpg"), dir.path().join("b.jpg"));
        write_jpeg(&src);
        let title = "Colorful abstract oil painting of a lone surfer riding a huge curling wave at sunset".repeat(2);
        write_embedded(&src, &dst, &meta(&title)).unwrap();
        let found = read_embedded(&dst).unwrap();
        assert_eq!(found.xmp.title.as_deref(), Some(title.as_str()));
        assert_eq!(found.iptc.headline.as_deref(), Some(title.as_str()));
        assert_eq!(found.iptc.object_name, None);
    }

    #[test]
    fn verify_reports_a_mismatch() {
        let dir = tempfile::tempdir().unwrap();
        let (src, dst) = (dir.path().join("a.jpg"), dir.path().join("b.jpg"));
        write_jpeg(&src);
        write_embedded(&src, &dst, &meta("Blue wave")).unwrap();
        assert!(verify_embedded(&dst, &meta("Other")).is_err());
    }
}
