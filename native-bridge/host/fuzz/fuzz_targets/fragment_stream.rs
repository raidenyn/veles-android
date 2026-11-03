//! libFuzzer target (nightly + cargo-fuzz). VERIFICATION ONLY. See ../README.md.
#![no_main]

libfuzzer_sys::fuzz_target!(|data: &[u8]| {
    veles_fuzz::run("fragment_stream", data);
});
