//! VERIFICATION ONLY. Shared targets of the OTP-24 fuzz workspace. The target bodies live in
//! `tests/support/adversarial.rs` (included by path) so `tests/properties.rs` and every fuzz
//! driver exercise literally the same checkers.

#[path = "../../tests/support/mod.rs"]
#[allow(
    unused_imports,
    clippy::duplicated_attributes,
    clippy::new_without_default
)]
pub mod support;

pub use support::adversarial::{
    FragCensus, MAX_OPS, Op, PARSER_INPUT_MAX, Rng, Summary, core_sequence, encode_ops,
    fragment_stream, seed_programs, strict_envelope,
};

/// Corpus/input ceilings per target. `strict_envelope`: the Native Messaging frame ceiling
/// (65,536). `fragment_stream`: 16 KiB of program (each embedded frame is further capped at 600
/// bytes by the interpreter, above the 512-byte ingress fragment ceiling). `core_sequence`: 4 KiB
/// (at most `MAX_OPS` = 512 ops are interpreted).
pub const MAX_LEN_STRICT_ENVELOPE: usize = PARSER_INPUT_MAX;
pub const MAX_LEN_FRAGMENT_STREAM: usize = 16 * 1024;
pub const MAX_LEN_CORE_SEQUENCE: usize = 4 * 1024;

pub fn max_len(target: &str) -> Option<usize> {
    match target {
        "strict_envelope" => Some(MAX_LEN_STRICT_ENVELOPE),
        "fragment_stream" => Some(MAX_LEN_FRAGMENT_STREAM),
        "core_sequence" => Some(MAX_LEN_CORE_SEQUENCE),
        _ => None,
    }
}

/// Runs one input against `target`; returns a coarse behavior signature (used only by the smoke
/// driver to decide which mutants are worth keeping — it is not coverage).
pub fn run(target: &str, data: &[u8]) -> u64 {
    let data = &data[..data.len().min(max_len(target).unwrap_or(0))];
    match target {
        "strict_envelope" => u64::from(strict_envelope(data)),
        "fragment_stream" => {
            let c = fragment_stream(data);
            (c.complete.min(15) as u64)
                | ((c.conflict_erased.min(15) as u64) << 4)
                | ((c.disconnect.min(3) as u64) << 8)
                | ((c.expired.min(7) as u64) << 10)
                | ((c.ignored.min(15) as u64) << 13)
        }
        "core_sequence" => {
            let s = core_sequence(data);
            (s.connected.min(15) as u64)
                | ((s.sent.min(15) as u64) << 4)
                | ((s.errors.min(15) as u64) << 8)
                | ((s.exit.map_or(9, |c| c as u64)) << 12)
        }
        other => panic!("unknown target {other}"),
    }
}
