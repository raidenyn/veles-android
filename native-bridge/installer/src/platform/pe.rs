//! Bounded PE carrier-resource reader used by the Windows trust adapter.
//!
//! Authenticode hashes the PE headers and the raw data of every section; the
//! checksum, the security-directory entry and the certificate table are excluded.
//! [`extract_carrier`] therefore accepts the embedded policy carrier only when
//!
//! * the file is a well-formed PE32/PE32+ image with a non-empty certificate
//!   table that lies outside every section and ends exactly at end-of-file (an
//!   unauthenticated trailing overlay is rejected),
//! * the resource directory (type `RT_RCDATA`, named carrier resource) is inside
//!   a section's raw data, and
//! * the carrier bytes themselves map completely into one section's raw data.
//!
//! The caller has already had WinVerifyTrust accept this exact file handle, so
//! bytes meeting these conditions are signature-covered. Parsing is deliberately
//! independent of the Windows API so its coverage rules are unit-tested on every
//! host instead of only on Windows.

/// `RT_RCDATA` resource type ordinal.
pub(crate) const RESOURCE_TYPE_RCDATA: u32 = 10;

const MAX_SECTIONS: usize = 256;
const MAX_RESOURCE_ENTRIES: usize = 4096;
const MAX_RESOURCE_DEPTH: usize = 3;
const MAX_RESOURCE_NAME_CHARS: usize = 256;
const SECTION_HEADER_LEN: usize = 40;

/// Why a PE carrier resource was not accepted.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PeError {
    /// The bytes are not a structurally valid PE image.
    Malformed,
    /// No matching resource exists.
    Missing,
    /// The resource is not completely inside one initialized section.
    OutsideImage,
    /// Bytes are not covered by the embedded Authenticode signature.
    #[cfg(any(windows, test))]
    UnauthenticatedOverlay,
    /// The resource is larger than the caller's bound.
    TooLarge,
}

#[derive(Debug, Clone, Copy)]
struct Section {
    virtual_address: usize,
    size_of_raw_data: usize,
    pointer_to_raw_data: usize,
}

impl Section {
    fn raw_end(self) -> Option<usize> {
        self.pointer_to_raw_data.checked_add(self.size_of_raw_data)
    }

    fn contains_rva(self, rva: usize, length: usize) -> Option<usize> {
        if self.size_of_raw_data == 0 {
            return None;
        }
        let start = rva.checked_sub(self.virtual_address)?;
        let end = start.checked_add(length)?;
        if end > self.size_of_raw_data {
            return None;
        }
        self.pointer_to_raw_data.checked_add(start)
    }
}

/// Extract the named `RT_RCDATA` resource if Authenticode covers its bytes.
///
/// Compiled only where it is used: the Windows release adapter and the unit tests. The debug
/// fixture adapter always uses `extract_unsigned_carrier`, so the release coverage rules cannot
/// be relaxed by the debug build.
#[cfg(any(windows, test))]
pub(crate) fn extract_carrier<'a>(
    image: &'a [u8],
    resource_name: &str,
    max_bytes: usize,
) -> Result<&'a [u8], PeError> {
    let parsed = parse_image(image)?;
    extract_resource(image, &parsed.sections, resource_name, max_bytes)
}

/// Extract the named `RT_RCDATA` resource from a **genuinely unsigned** image.
///
/// The Windows debug producer embeds the fixture carrier in a `carrier.exe` it never signs, so the
/// certificate directory is empty and there is no signature that could cover the resource. The
/// debug fixture trust adapter is the only caller, and it is compiled only with the
/// `debug-fixture-trust` feature; the release adapter keeps using `extract_carrier` and its
/// certificate/coverage requirements unchanged.
///
/// Only the signature requirement is dropped. Everything else is the same bounded reader: the
/// image must be a structurally valid, untruncated PE whose resource directory and named resource
/// lie completely inside one section's raw data, and the resource is refused when it is empty or
/// larger than `max_bytes`.
#[cfg(feature = "debug-fixture-trust")]
pub(crate) fn extract_unsigned_carrier<'a>(
    image: &'a [u8],
    resource_name: &str,
    max_bytes: usize,
) -> Result<&'a [u8], PeError> {
    let parsed = parse_sections(image)?;
    extract_resource(image, &parsed.sections, resource_name, max_bytes)
}

/// Locate the named resource and map its complete byte range into section raw data.
fn extract_resource<'a>(
    image: &'a [u8],
    sections: &[Section],
    resource_name: &str,
    max_bytes: usize,
) -> Result<&'a [u8], PeError> {
    let resource_rva = resource_directory_rva(image)?;
    let base = map_rva(sections, resource_rva, 1).ok_or(PeError::OutsideImage)?;
    // Directory entries may only be read from the resource directory's own
    // section raw data, never from the unauthenticated certificate table.
    let limit = sections
        .iter()
        .filter(|section| section.contains_rva(resource_rva, 1).is_some())
        .filter_map(|section| section.raw_end())
        .next()
        .unwrap_or(image.len())
        .min(image.len());

    let mut reader = ResourceReader {
        image,
        base,
        limit,
        visited: 0,
    };
    let (data_rva, data_size) = reader.find(resource_name).ok_or(PeError::Missing)?;
    if data_size == 0 || data_size > max_bytes {
        return Err(PeError::TooLarge);
    }
    let start = map_rva(sections, data_rva, data_size).ok_or(PeError::OutsideImage)?;
    let end = start.checked_add(data_size).ok_or(PeError::OutsideImage)?;
    image.get(start..end).ok_or(PeError::OutsideImage)
}

/// The structural parts of a parsed image the resource readers need.
///
/// The Authenticode fields are read only by the release coverage rule, which is compiled on
/// Windows and in unit tests; the debug build keeps the same parser without them.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
struct ParsedImage {
    sections: Vec<Section>,
    optional_header: usize,
    magic: u16,
    number_of_directories: usize,
}

#[cfg(any(windows, test))]
fn parse_image(image: &[u8]) -> Result<ParsedImage, PeError> {
    let parsed = parse_sections(image)?;
    require_authenticode_coverage(image, &parsed)?;
    Ok(parsed)
}

fn parse_sections(image: &[u8]) -> Result<ParsedImage, PeError> {
    if image.len() < 0x40 || &image[..2] != b"MZ" {
        return Err(PeError::Malformed);
    }
    let pe_offset = u32_at(image, 0x3C)? as usize;
    if image.get(pe_offset..pe_offset.saturating_add(4)) != Some(&b"PE\0\0"[..]) {
        return Err(PeError::Malformed);
    }
    let coff = pe_offset.checked_add(4).ok_or(PeError::Malformed)?;
    let number_of_sections = u16_at(image, coff + 2)? as usize;
    let size_of_optional_header = u16_at(image, coff + 16)? as usize;
    if number_of_sections == 0 || number_of_sections > MAX_SECTIONS {
        return Err(PeError::Malformed);
    }
    let optional = coff.checked_add(20).ok_or(PeError::Malformed)?;
    let section_table = optional
        .checked_add(size_of_optional_header)
        .ok_or(PeError::Malformed)?;

    let magic = u16_at(image, optional)?;
    let number_of_directories = match magic {
        0x10b => u32_at(image, optional + 92)? as usize,
        0x20b => u32_at(image, optional + 108)? as usize,
        _ => return Err(PeError::Malformed),
    };

    let mut sections = Vec::with_capacity(number_of_sections);
    for index in 0..number_of_sections {
        let header = section_table
            .checked_add(
                index
                    .checked_mul(SECTION_HEADER_LEN)
                    .ok_or(PeError::Malformed)?,
            )
            .ok_or(PeError::Malformed)?;
        let section = Section {
            virtual_address: u32_at(image, header + 12)? as usize,
            size_of_raw_data: u32_at(image, header + 16)? as usize,
            pointer_to_raw_data: u32_at(image, header + 20)? as usize,
        };
        if let Some(end) = section.raw_end()
            && section.size_of_raw_data > 0
            && end > image.len()
        {
            return Err(PeError::Malformed);
        }
        sections.push(section);
    }

    Ok(ParsedImage {
        sections,
        optional_header: optional,
        magic,
        number_of_directories,
    })
}

/// Require that an embedded Authenticode signature covers the image content.
#[cfg(any(windows, test))]
fn require_authenticode_coverage(image: &[u8], parsed: &ParsedImage) -> Result<(), PeError> {
    // An embedded Authenticode signature must exist, must not overlap section
    // raw data, and must end exactly at end-of-file so no unauthenticated overlay
    // follows it.
    if parsed.number_of_directories <= 4 {
        return Err(PeError::UnauthenticatedOverlay);
    }
    let directory_base = match parsed.magic {
        0x10b => parsed.optional_header + 96,
        _ => parsed.optional_header + 112,
    };
    let certificate_offset = u32_at(image, directory_base + 32)? as usize;
    let certificate_size = u32_at(image, directory_base + 36)? as usize;
    if certificate_size == 0 || certificate_offset < 8 {
        return Err(PeError::UnauthenticatedOverlay);
    }
    let certificate_end = certificate_offset
        .checked_add(certificate_size)
        .ok_or(PeError::UnauthenticatedOverlay)?;
    if certificate_end != image.len() {
        return Err(PeError::UnauthenticatedOverlay);
    }
    for section in &parsed.sections {
        if let Some(end) = section.raw_end()
            && section.size_of_raw_data > 0
            && end > certificate_offset
        {
            return Err(PeError::UnauthenticatedOverlay);
        }
    }
    Ok(())
}

fn resource_directory_rva(image: &[u8]) -> Result<usize, PeError> {
    let pe_offset = u32_at(image, 0x3C)? as usize;
    let coff = pe_offset.checked_add(4).ok_or(PeError::Malformed)?;
    let optional = coff.checked_add(20).ok_or(PeError::Malformed)?;
    let magic = u16_at(image, optional)?;
    let number_of_directories = match magic {
        0x10b => u32_at(image, optional + 92)? as usize,
        0x20b => u32_at(image, optional + 108)? as usize,
        _ => return Err(PeError::Malformed),
    };
    if number_of_directories <= 2 {
        return Err(PeError::Missing);
    }
    let directory_base = match magic {
        0x10b => optional + 96,
        _ => optional + 112,
    };
    let resource_rva = u32_at(image, directory_base + 16)? as usize;
    let resource_size = u32_at(image, directory_base + 20)? as usize;
    if resource_rva == 0 || resource_size == 0 {
        return Err(PeError::Missing);
    }
    Ok(resource_rva)
}

fn map_rva(sections: &[Section], rva: usize, length: usize) -> Option<usize> {
    sections
        .iter()
        .find_map(|section| section.contains_rva(rva, length))
}

struct ResourceReader<'a> {
    image: &'a [u8],
    base: usize,
    limit: usize,
    visited: usize,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Stage {
    Type,
    Name,
    Language,
}

impl ResourceReader<'_> {
    fn find(&mut self, resource_name: &str) -> Option<(usize, usize)> {
        self.walk(0, Stage::Type, resource_name, 0)
    }

    fn walk(
        &mut self,
        directory_offset: usize,
        stage: Stage,
        resource_name: &str,
        depth: usize,
    ) -> Option<(usize, usize)> {
        if depth > MAX_RESOURCE_DEPTH {
            return None;
        }
        let header = self.base.checked_add(directory_offset)?;
        if header.checked_add(16)? > self.limit {
            return None;
        }
        let named = u16_at(self.image, header + 12).ok()? as usize;
        let ids = u16_at(self.image, header + 14).ok()? as usize;
        let count = named.checked_add(ids)?;
        if count > MAX_RESOURCE_ENTRIES {
            return None;
        }
        for index in 0..count {
            self.visited += 1;
            if self.visited > MAX_RESOURCE_ENTRIES {
                return None;
            }
            let entry = header.checked_add(16 + index.checked_mul(8)?)?;
            if entry.checked_add(8)? > self.limit {
                return None;
            }
            let name_field = u32_at(self.image, entry).ok()?;
            let offset_field = u32_at(self.image, entry + 4).ok()?;
            let matched = match stage {
                Stage::Type => name_field & 0x8000_0000 == 0 && name_field == RESOURCE_TYPE_RCDATA,
                Stage::Name => {
                    name_field & 0x8000_0000 != 0
                        && self
                            .read_name((name_field & 0x7fff_ffff) as usize)
                            .is_some_and(|name| name.eq_ignore_ascii_case(resource_name))
                }
                Stage::Language => true,
            };
            if !matched {
                continue;
            }
            let is_directory = offset_field & 0x8000_0000 != 0;
            let offset = (offset_field & 0x7fff_ffff) as usize;
            match stage {
                Stage::Type if is_directory => {
                    if let Some(found) = self.walk(offset, Stage::Name, resource_name, depth + 1) {
                        return Some(found);
                    }
                }
                Stage::Name if is_directory => {
                    if let Some(found) =
                        self.walk(offset, Stage::Language, resource_name, depth + 1)
                    {
                        return Some(found);
                    }
                }
                Stage::Name if !is_directory => {
                    if let Some(found) = self.read_data_entry(offset) {
                        return Some(found);
                    }
                }
                Stage::Language if !is_directory => {
                    if let Some(found) = self.read_data_entry(offset) {
                        return Some(found);
                    }
                }
                _ => {}
            }
        }
        None
    }

    fn read_name(&self, offset: usize) -> Option<String> {
        let start = self.base.checked_add(offset)?;
        if start.checked_add(2)? > self.limit {
            return None;
        }
        let length = u16_at(self.image, start).ok()? as usize;
        if length == 0 || length > MAX_RESOURCE_NAME_CHARS {
            return None;
        }
        if start.checked_add(2 + length * 2)? > self.limit {
            return None;
        }
        let mut units = Vec::with_capacity(length);
        for index in 0..length {
            units.push(u16_at(self.image, start + 2 + index * 2).ok()?);
        }
        String::from_utf16(&units).ok()
    }

    fn read_data_entry(&self, offset: usize) -> Option<(usize, usize)> {
        let entry = self.base.checked_add(offset)?;
        if entry.checked_add(16)? > self.limit {
            return None;
        }
        let data_rva = u32_at(self.image, entry).ok()? as usize;
        let data_size = u32_at(self.image, entry + 4).ok()? as usize;
        if data_rva == 0 || data_size == 0 {
            return None;
        }
        // The data entry only identifies an RVA; the section table (in
        // `extract_carrier`) decides whether the byte range is image content.
        Some((data_rva, data_size))
    }
}

fn u16_at(image: &[u8], offset: usize) -> Result<u16, PeError> {
    let end = offset.checked_add(2).ok_or(PeError::Malformed)?;
    let bytes = image.get(offset..end).ok_or(PeError::Malformed)?;
    Ok(u16::from_le_bytes([bytes[0], bytes[1]]))
}

fn u32_at(image: &[u8], offset: usize) -> Result<u32, PeError> {
    let end = offset.checked_add(4).ok_or(PeError::Malformed)?;
    let bytes = image.get(offset..end).ok_or(PeError::Malformed)?;
    Ok(u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}

#[cfg(test)]
mod tests {
    use super::*;

    const RESOURCE_NAME: &str = "VELES_POLICY_CARRIER";
    const PE_OFFSET: usize = 0x40;
    const OPTIONAL_LEN: usize = 0xF0;
    const SECTION_TABLE: usize = PE_OFFSET + 4 + 20 + OPTIONAL_LEN;
    const RAW_OFFSET: usize = 0x200;
    const RAW_LEN: usize = 0x400;
    const SECTION_VA: u32 = 0x1000;
    const CARRIER_IN_SECTION: usize = 0xA0;
    const CERTIFICATE_LEN: usize = 16;

    struct Fixture {
        image: Vec<u8>,
        carrier: Vec<u8>,
    }

    fn put_u16(image: &mut [u8], offset: usize, value: u16) {
        image[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
    }

    fn put_u32(image: &mut [u8], offset: usize, value: u32) {
        image[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
    }

    fn put_unit(image: &mut [u8], offset: usize, value: u16) {
        put_u16(image, offset, value);
    }

    /// Build a minimal PE32+ image whose only section holds a synthetic
    /// `RT_RCDATA`/`VELES_POLICY_CARRIER` resource and whose (fake) certificate
    /// table is the terminal region of the file.
    fn fixture(carrier: &[u8], data_rva: u32) -> Fixture {
        build_fixture(carrier, data_rva, true)
    }

    /// The same image without any certificate table: an empty certificate
    /// directory and no trailing bytes, exactly like the unsigned `carrier.exe`
    /// the Windows debug producer ships.
    #[cfg(feature = "debug-fixture-trust")]
    fn unsigned_fixture(carrier: &[u8], data_rva: u32) -> Fixture {
        build_fixture(carrier, data_rva, false)
    }

    fn build_fixture(carrier: &[u8], data_rva: u32, signed: bool) -> Fixture {
        let certificate_offset = RAW_OFFSET + RAW_LEN;
        let certificate_len = if signed { CERTIFICATE_LEN } else { 0 };
        let mut image = vec![0u8; certificate_offset + certificate_len];
        image[..2].copy_from_slice(b"MZ");
        put_u32(&mut image, 0x3C, PE_OFFSET as u32);
        image[PE_OFFSET..PE_OFFSET + 4].copy_from_slice(b"PE\0\0");
        let coff = PE_OFFSET + 4;
        put_u16(&mut image, coff, 0x8664);
        put_u16(&mut image, coff + 2, 1);
        put_u16(&mut image, coff + 16, OPTIONAL_LEN as u16);
        let optional = coff + 20;
        put_u16(&mut image, optional, 0x20b);
        let directory_base = optional + 112;
        put_u32(&mut image, optional + 108, 16);
        // Resource directory (index 2).
        put_u32(&mut image, directory_base + 16, SECTION_VA);
        put_u32(&mut image, directory_base + 20, 0x100);
        // Certificate table (index 4) at end-of-file, when there is one.
        if signed {
            put_u32(&mut image, directory_base + 32, certificate_offset as u32);
            put_u32(&mut image, directory_base + 36, CERTIFICATE_LEN as u32);
        }
        // Section header.
        image[SECTION_TABLE..SECTION_TABLE + 5].copy_from_slice(b".rsrc");
        put_u32(&mut image, SECTION_TABLE + 8, RAW_LEN as u32);
        put_u32(&mut image, SECTION_TABLE + 12, SECTION_VA);
        put_u32(&mut image, SECTION_TABLE + 16, RAW_LEN as u32);
        put_u32(&mut image, SECTION_TABLE + 20, RAW_OFFSET as u32);

        let resource = |offset: usize| RAW_OFFSET + offset;
        // Type directory: one id entry, RT_RCDATA -> subdirectory 0x18.
        put_u16(&mut image, resource(0) + 14, 1);
        put_u32(&mut image, resource(0x10), RESOURCE_TYPE_RCDATA);
        put_u32(&mut image, resource(0x14), 0x8000_0018);
        // Name directory: one named entry for the carrier.
        put_u16(&mut image, resource(0x18) + 12, 1);
        put_u32(&mut image, resource(0x28), 0x8000_0040);
        put_u32(&mut image, resource(0x2C), 0x8000_0070);
        // Name string.
        let name: Vec<u16> = RESOURCE_NAME.encode_utf16().collect();
        put_u16(&mut image, resource(0x40), name.len() as u16);
        for (index, unit) in name.iter().enumerate() {
            put_unit(&mut image, resource(0x42 + index * 2), *unit);
        }
        // Language directory: one data entry at 0x88.
        put_u16(&mut image, resource(0x70) + 14, 1);
        put_u32(&mut image, resource(0x80), 0x409);
        put_u32(&mut image, resource(0x84), 0x88);
        // Data entry and bytes.
        put_u32(&mut image, resource(0x88), data_rva);
        put_u32(&mut image, resource(0x8C), carrier.len() as u32);
        image[resource(CARRIER_IN_SECTION)..resource(CARRIER_IN_SECTION) + carrier.len()]
            .copy_from_slice(carrier);

        Fixture {
            image,
            carrier: carrier.to_vec(),
        }
    }

    #[test]
    fn pe_carrier_resource_is_extracted_from_signed_image_content() {
        let carrier = b"VELESPC1-carrier-bytes";
        let fixture = fixture(carrier, SECTION_VA + CARRIER_IN_SECTION as u32);
        assert_eq!(
            extract_carrier(&fixture.image, RESOURCE_NAME, 4096).expect("carrier resource"),
            fixture.carrier.as_slice()
        );
    }

    #[test]
    fn pe_resource_must_be_inside_section_image_content() {
        let carrier = b"carrier";
        // The data entry points past the section's raw data, so its bytes are not
        // covered by Authenticode.
        let fixture = fixture(carrier, SECTION_VA + RAW_LEN as u32 + 0x100);
        assert_eq!(
            extract_carrier(&fixture.image, RESOURCE_NAME, 4096).expect_err("outside sections"),
            PeError::OutsideImage
        );
    }

    #[test]
    fn pe_unauthenticated_trailing_overlay_is_rejected() {
        let carrier = b"carrier";
        let mut fixture = fixture(carrier, SECTION_VA + CARRIER_IN_SECTION as u32);
        extract_carrier(&fixture.image, RESOURCE_NAME, 4096).expect("baseline accepted");
        fixture.image.extend_from_slice(&[0xAA; 8]);
        assert_eq!(
            extract_carrier(&fixture.image, RESOURCE_NAME, 4096).expect_err("overlay"),
            PeError::UnauthenticatedOverlay
        );
    }

    #[test]
    fn pe_unsigned_image_is_rejected() {
        let carrier = b"carrier";
        let mut fixture = fixture(carrier, SECTION_VA + CARRIER_IN_SECTION as u32);
        let optional = PE_OFFSET + 4 + 20;
        let directory_base = optional + 112;
        put_u32(&mut fixture.image, directory_base + 36, 0);
        assert_eq!(
            extract_carrier(&fixture.image, RESOURCE_NAME, 4096).expect_err("unsigned"),
            PeError::UnauthenticatedOverlay
        );
    }

    #[test]
    fn pe_missing_or_oversized_resource_is_rejected() {
        let carrier = b"carrier";
        let fixture = fixture(carrier, SECTION_VA + CARRIER_IN_SECTION as u32);
        assert_eq!(
            extract_carrier(&fixture.image, "SOME_OTHER_CARRIER", 4096).expect_err("missing"),
            PeError::Missing
        );
        assert_eq!(
            extract_carrier(&fixture.image, RESOURCE_NAME, carrier.len() - 1).expect_err("bound"),
            PeError::TooLarge
        );
        let mut truncated = fixture.image.clone();
        truncated.truncate(0x20);
        assert_eq!(
            extract_carrier(&truncated, RESOURCE_NAME, 4096).expect_err("truncated"),
            PeError::Malformed
        );
    }

    /// The debug producer writes `carrier.exe` without signing it, so its certificate directory is
    /// empty. The release reader must keep refusing that image; only the debug reader may read the
    /// named resource, and then under the same structural and size bounds.
    #[cfg(feature = "debug-fixture-trust")]
    #[test]
    fn unsigned_debug_image_is_read_only_by_the_debug_reader() {
        let carrier = b"VELESPC1-unsigned-debug-carrier";
        let image = unsigned_fixture(carrier, SECTION_VA + CARRIER_IN_SECTION as u32);

        assert_eq!(
            extract_carrier(&image.image, RESOURCE_NAME, 4096)
                .expect_err("the release reader requires a certificate table"),
            PeError::UnauthenticatedOverlay
        );
        assert_eq!(
            extract_unsigned_carrier(&image.image, RESOURCE_NAME, 4096)
                .expect("the debug reader accepts the unsigned image"),
            image.carrier.as_slice()
        );

        // The same bound and the same image-content rule apply to the debug reader.
        assert_eq!(
            extract_unsigned_carrier(&image.image, RESOURCE_NAME, carrier.len() - 1)
                .expect_err("bound"),
            PeError::TooLarge
        );
        assert_eq!(
            extract_unsigned_carrier(&image.image, "SOME_OTHER_CARRIER", 4096)
                .expect_err("missing"),
            PeError::Missing
        );

        // A resource whose bytes are not inside the section's raw data stays refused.
        let outside = unsigned_fixture(carrier, SECTION_VA + RAW_LEN as u32 + 0x100);
        assert_eq!(
            extract_unsigned_carrier(&outside.image, RESOURCE_NAME, 4096)
                .expect_err("outside sections"),
            PeError::OutsideImage
        );

        // Truncated images are malformed, not a shorter resource.
        let mut truncated = image.image.clone();
        truncated.truncate(0x20);
        assert_eq!(
            extract_unsigned_carrier(&truncated, RESOURCE_NAME, 4096).expect_err("truncated"),
            PeError::Malformed
        );
        let mut truncated = image.image.clone();
        truncated.truncate(RAW_OFFSET + 0x80);
        assert_eq!(
            extract_unsigned_carrier(&truncated, RESOURCE_NAME, 4096)
                .expect_err("truncated resource directory"),
            PeError::Malformed
        );

        // A malformed (oversized) resource size is refused by the bound, and an
        // unparsable data entry by the section mapping.
        let mut oversized = image.image.clone();
        let entry = RAW_OFFSET + 0x88;
        oversized[entry + 4..entry + 8].copy_from_slice(&u32::MAX.to_le_bytes());
        assert_eq!(
            extract_unsigned_carrier(&oversized, RESOURCE_NAME, 4096)
                .expect_err("oversized resource"),
            PeError::TooLarge
        );
    }
}
