# Correspondence fixture provenance (OTP-25 Task 9)

These fixtures feed `../test_correspondence.py`. They come in exactly two kinds and the
tests pin which is which. **Nothing here is a real Authenticode or Apple signature, a
notarization ticket or the output of a native signing run.**

## 1. Real toolchain output (the two base images)

| file                                       | origin                                                                                                                                        |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `correspondence/veles-fixture-x64.exe`     | PE32+ (machine `0x8664`), linked by the pinned Rust toolchain with `rust-lld` (`lld-link` flavor) from `src/pe_min.rs`                        |
| `correspondence/veles-fixture-arm64.macho` | arm64 `MH_EXECUTE`, linked by `rust-lld` (`ld64.lld` flavor) from `src/macho_min.rs`; carries the **linker's own ad-hoc** `LC_CODE_SIGNATURE` |

- Toolchain: `rustc 1.98.0 (88d9e12ae 2026-08-18)`, LLVM 22.1.8, pinned by
  `native-bridge/installer/rust-toolchain.toml`.
- Built on **Linux x86_64 (cross-linking)**, by `make-base-fixtures.sh`. They were **not**
  built on the pinned Windows/macOS runners: there is no MSVC `link.exe`, Windows SDK, Apple
  `ld` or Xcode SDK involved.
- Reproducibility check (2026-11-06): re-running `make-base-fixtures.sh` into a clean
  directory reproduced both files byte-for-byte (SHA-256 below).
- Sanitized: neither file contains a host path, user name, `rustc` string or timestamp
  (checked by `test_base_images_are_sanitized` and by hand with `strings`).

## 2. Locally constructed images (derived, deterministic)

`../correspondence_fixtures.py` derives signature-carrying variants from the two base
images using byte layouts it re-implements with `struct` independently of
`correspondence.py` (so a comparator bug cannot hide behind a matching builder bug):

- a terminal WIN_CERTIFICATE table with a recomputed ImageHlp checksum;
- an `LC_CODE_SIGNATURE` command, a SuperBlob with a real CodeDirectory (SHA-256 page
  hashes computed over the signed bytes) and the grown `__LINKEDIT`.

Their **cryptographic content is a placeholder**: a DER `SEQUENCE` wrapper whose body is the
text `LOCALLY-CONSTRUCTED-PLACEHOLDER-NOT-A-REAL-CMS-SIGNATURE`. They prove the comparator's
_structural_ logic only.

## Pinned digests

`test_correspondence.py::test_fixture_digests_match_provenance` fails if any row differs.

| name                                     | sha256                                                             | size  | kind                  |
| ---------------------------------------- | ------------------------------------------------------------------ | ----- | --------------------- |
| pe-x64-unsigned.exe                      | `32107940ab5927d231d9b96d8c9d90e15c188102e5793bc963ed94324333d1ed` | 2048  | real toolchain output |
| pe-x64-signed-constructed.exe            | `87a97f7f2dc86857fdf54d485a83ff192e5d7ed6e820fa29b2e32c79fdf9d6a4` | 2184  | locally constructed   |
| macho-arm64-linker-signed.bin            | `6d591f7022a0be04059d691c7935ec3bac3327b55d8fb1895eba62277c5f78a5` | 16912 | real toolchain output |
| macho-arm64-no-signature-constructed.bin | `9212d0aa59a7321e626bcd7c027c757d46cede856a035696d7ba0f47968a0657` | 16608 | locally constructed   |
| macho-arm64-signed-constructed.bin       | `d5dc5a876bf42d176a99be9ae845a10b029f8c2d04d21e363e71f56eeb56f552` | 17349 | locally constructed   |
| macho-arm64-resigned-constructed.bin     | `072b3daa5557a30f0f99b2472a81d020135900ced2ff66fb17c7f059221227c9` | 17213 | locally constructed   |

## BLOCKED / NOT RUN (native signing)

| step                                | status            | reason                                                                             | command that would produce the real fixture                                                                                                                                    |
| ----------------------------------- | ----------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Authenticode-signed PE              | BLOCKED / NOT RUN | no Windows runner, `signtool` or certificate on the Linux development host         | `signtool sign /fd SHA256 /td SHA256 /tr <timestamp-url> /n <publisher> veles-native-host.exe`                                                                                 |
| Developer ID-signed Mach-O / `.app` | BLOCKED / NOT RUN | no macOS runner, `codesign` or Developer ID identity on the Linux development host | `codesign --force --timestamp --options runtime --entitlements <plist> --sign <Developer ID> Veles.app`                                                                        |
| Notarization + ticket stapling      | BLOCKED / NOT RUN | no `notarytool`/`stapler` or Apple credentials                                     | `xcrun notarytool submit ...` then `xcrun stapler staple Veles.app`                                                                                                            |
| Real-tool allowlist validation      | BLOCKED / NOT RUN | needs the three outputs above                                                      | run `correspondence.py` on each unsigned/signed pair; if the real mutation differs from the allowlist, investigate **before** widening it and record the finding in the ledger |

The comparator's allowlist is therefore derived from the published PE/COFF, Mach-O and
code-signing formats, not yet confirmed against real `signtool`/`codesign` output.
