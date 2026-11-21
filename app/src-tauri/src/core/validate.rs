//! Pre-upload image checks for Adobe Stock (JPEG, sRGB, 4-100 MP, <= 45 MB). Port of `validate.py`.

use std::path::Path;

use image::{ImageDecoder, ImageFormat, ImageReader};

use super::py_repr;

const MAX_FILE_BYTES: u64 = 45 * 1024 * 1024;
const MIN_MEGAPIXELS: f64 = 4.0;
const MAX_MEGAPIXELS: f64 = 100.0;
const PIXELS_PER_MEGAPIXEL: f64 = 1_000_000.0;
const BYTES_PER_MB: f64 = 1024.0 * 1024.0;
const ICC_HEADER_BYTES: usize = 128;
const ICC_TAG_BYTES: usize = 12;

fn format_name(format: ImageFormat) -> String {
    match format {
        ImageFormat::Jpeg => "JPEG".into(),
        other => format!("{other:?}").to_uppercase(),
    }
}

fn be_u32(bytes: &[u8], at: usize) -> Option<usize> {
    let raw: [u8; 4] = bytes.get(at..at + 4)?.try_into().ok()?;
    usize::try_from(u32::from_be_bytes(raw)).ok()
}

fn utf16_be(bytes: &[u8]) -> String {
    let units: Vec<u16> = bytes.chunks_exact(2).map(|pair| u16::from_be_bytes([pair[0], pair[1]])).collect();
    String::from_utf16_lossy(&units)
}

/// The text of a `desc` (ICC v2) or `mluc` (ICC v4) tag.
fn description_text(tag: &[u8]) -> Option<String> {
    match tag.get(..4)? {
        b"desc" => {
            let length = be_u32(tag, 8)?;
            let text = tag.get(12..12 + length)?;
            Some(String::from_utf8_lossy(text).trim_end_matches('\0').to_string())
        }
        b"mluc" => {
            let (length, offset) = (be_u32(tag, 20)?, be_u32(tag, 24)?);
            Some(utf16_be(tag.get(offset..offset + length)?))
        }
        _ => None,
    }
}

/// The profile's description, like Pillow's `getProfileDescription` (empty when it has none).
pub fn icc_description(profile: &[u8]) -> Option<String> {
    if profile.len() < ICC_HEADER_BYTES + 4 {
        return None;
    }
    let tag_count = be_u32(profile, ICC_HEADER_BYTES)?;
    for index in 0..tag_count {
        let entry = ICC_HEADER_BYTES + 4 + index * ICC_TAG_BYTES;
        if profile.get(entry..entry + 4)? != b"desc" {
            continue;
        }
        let (offset, size) = (be_u32(profile, entry + 4)?, be_u32(profile, entry + 8)?);
        return description_text(profile.get(offset..offset.checked_add(size)?)?).map(|text| text.trim().to_string());
    }
    Some(String::new())
}

fn inspect_pixels(path: &Path, problems: &mut Vec<String>) -> Result<(), String> {
    let reader = ImageReader::open(path)
        .map_err(|error| error.to_string())?
        .with_guessed_format()
        .map_err(|error| error.to_string())?;
    let format = reader
        .format()
        .ok_or_else(|| format!("cannot identify image file {}", py_repr(&path.display().to_string())))?;
    let mut decoder = reader.into_decoder().map_err(|error| error.to_string())?;
    if format != ImageFormat::Jpeg {
        problems.push(format!("content is {}, not JPEG", format_name(format)));
    }
    let (width, height) = decoder.dimensions();
    let megapixels = f64::from(width) * f64::from(height) / PIXELS_PER_MEGAPIXEL;
    if !(MIN_MEGAPIXELS..=MAX_MEGAPIXELS).contains(&megapixels) {
        problems.push(format!(
            "{megapixels:.1} MP is outside {}-{} MP",
            MIN_MEGAPIXELS as u32, MAX_MEGAPIXELS as u32
        ));
    }
    // No embedded profile is treated as sRGB; a different profile is an error.
    if let Ok(Some(raw)) = decoder.icc_profile() {
        let name = icc_description(&raw).unwrap_or_else(|| "unreadable".into());
        if !name.to_lowercase().contains("srgb") {
            problems.push(format!("color profile is {}, expected sRGB", py_repr(&name)));
        }
    }
    Ok(())
}

/// Human-readable problems; empty means the file is OK.
pub fn validate_image(path: &Path) -> Vec<String> {
    let mut problems = Vec::new();
    let suffix = path.extension().map(|ext| format!(".{}", ext.to_string_lossy())).unwrap_or_default();
    if !matches!(suffix.to_lowercase().as_str(), ".jpg" | ".jpeg") {
        problems.push(format!("extension {} is not .jpg/.jpeg", py_repr(&suffix)));
    }
    if let Ok(meta) = std::fs::metadata(path) {
        if meta.len() > MAX_FILE_BYTES {
            problems.push(format!("file is {:.1} MB, limit is 45 MB", meta.len() as f64 / BYTES_PER_MB));
        }
    }
    if let Err(error) = inspect_pixels(path, &mut problems) {
        problems.push(format!("cannot read image: {error}"));
    }
    problems
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{codecs::jpeg::JpegEncoder, ExtendedColorType};

    pub fn write_jpeg(path: &Path, width: u32, height: u32, shade: u8) {
        let pixels = vec![shade; (width * height * 3) as usize];
        let file = std::fs::File::create(path).unwrap();
        JpegEncoder::new(std::io::BufWriter::new(file))
            .encode(&pixels, width, height, ExtendedColorType::Rgb8)
            .unwrap();
    }

    #[test]
    fn a_four_megapixel_jpeg_is_valid() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("a.jpg");
        write_jpeg(&file, 2000, 2000, 10);
        assert_eq!(validate_image(&file), Vec::<String>::new());
    }

    #[test]
    fn too_small_wrong_extension_and_unreadable_files_are_reported() {
        let dir = tempfile::tempdir().unwrap();
        let small = dir.path().join("small.JPG");
        write_jpeg(&small, 100, 100, 10);
        assert_eq!(validate_image(&small), vec!["0.0 MP is outside 4-100 MP".to_string()]);

        let renamed = dir.path().join("a.png");
        write_jpeg(&renamed, 2000, 2000, 10);
        assert_eq!(validate_image(&renamed), vec!["extension '.png' is not .jpg/.jpeg".to_string()]);

        let broken = dir.path().join("broken.jpg");
        std::fs::write(&broken, b"not an image").unwrap();
        let problems = validate_image(&broken);
        assert!(problems[0].starts_with("cannot read image"), "{problems:?}");
    }

    #[test]
    fn reads_the_description_of_v2_and_v4_profiles() {
        let mut v2 = vec![0u8; 128];
        v2.extend_from_slice(&1u32.to_be_bytes());
        let text = b"sRGB IEC61966-2.1\0";
        let tag_offset = 128 + 4 + 12;
        v2.extend_from_slice(b"desc");
        v2.extend_from_slice(&(tag_offset as u32).to_be_bytes());
        v2.extend_from_slice(&((12 + text.len()) as u32).to_be_bytes());
        v2.extend_from_slice(b"desc\0\0\0\0");
        v2.extend_from_slice(&(text.len() as u32).to_be_bytes());
        v2.extend_from_slice(text);
        assert_eq!(icc_description(&v2).as_deref(), Some("sRGB IEC61966-2.1"));
        assert_eq!(icc_description(&[0u8; 10]), None);
    }
}
