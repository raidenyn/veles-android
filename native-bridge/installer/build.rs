//! Embed the authenticated release carrier that release packaging supplies.
//!
//! The producer sets `VELES_EMBEDDED_CARRIER` to the carrier document it assembled and
//! verified; this script embeds those exact bytes into the engine. The producer also sets
//! `VELES_BUILD_CHANNEL`, which makes a production build fail closed when the carrier is
//! absent (instead of shipping an engine that refuses every install) and refuses a release
//! carrier in a development build. When neither is set the engine embeds nothing: that is the
//! honest state of an unreleased build, and every package operation that needs a release
//! identity refuses with `policy-denied`. The generated constant is the only build-time input
//! the binary reads.

#[path = "build_support/embedded_carrier.rs"]
mod embedded_carrier;

use std::fs;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-env-changed={}", embedded_carrier::ENV);
    println!(
        "cargo:rerun-if-env-changed={}",
        embedded_carrier::CHANNEL_ENV
    );
    println!("cargo:rerun-if-changed=build_support/embedded_carrier.rs");

    let value = std::env::var(embedded_carrier::ENV).ok();
    if let Some(path) = value.as_deref().filter(|value| !value.is_empty()) {
        println!("cargo:rerun-if-changed={path}");
    }
    let channel = std::env::var(embedded_carrier::CHANNEL_ENV).ok();

    // The debug fixture trust adapter is a compile-time choice that only the debug producer may
    // make. There is no runtime switch: refuse the feature in a release profile or for the
    // production channel before any code is generated, so a release binary can never contain it.
    if std::env::var_os("CARGO_FEATURE_DEBUG_FIXTURE_TRUST").is_some() {
        let profile = std::env::var("PROFILE").unwrap_or_default();
        if profile != "debug" {
            eprintln!(
                "veles-bridge-installer: the debug-fixture-trust feature is a debug-profile \
                 installer capability and must not be enabled in a release build (PROFILE={profile:?})"
            );
            std::process::exit(1);
        }
        if channel.as_deref().map(str::trim) == Some("production") {
            eprintln!(
                "veles-bridge-installer: the debug-fixture-trust feature must never be enabled \
                 for the production channel ({}={:?})",
                embedded_carrier::CHANNEL_ENV,
                channel.as_deref()
            );
            std::process::exit(1);
        }
    }

    let bytes = match embedded_carrier::resolve(value.as_deref(), channel.as_deref()) {
        Ok(bytes) => bytes,
        Err(error) => {
            eprintln!("veles-bridge-installer: {error}");
            std::process::exit(1);
        }
    };

    let out = PathBuf::from(std::env::var_os("OUT_DIR").expect("cargo sets OUT_DIR"));
    let generated = match bytes {
        None => concat!(
            "/// No release identity is compiled in: this build cannot authenticate a\n",
            "/// package against a release descriptor.\n",
            "pub const EMBEDDED_CARRIER: Option<&[u8]> = None;\n",
        )
        .to_owned(),
        Some(bytes) => {
            let binary = out.join("embedded_carrier.bin");
            fs::write(&binary, &bytes).expect("the embedded carrier is writable");
            format!(
                concat!(
                    "/// The authenticated release carrier embedded by release packaging.\n",
                    "pub const EMBEDDED_CARRIER: Option<&[u8]> = Some(include_bytes!({:?}));\n",
                ),
                binary.to_string_lossy(),
            )
        }
    };
    fs::write(out.join("embedded_carrier.rs"), generated)
        .expect("the generated embedded-carrier module is writable");
}
