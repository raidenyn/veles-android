//! Embed the authenticated release carrier that release packaging supplies.
//!
//! The producer sets `VELES_EMBEDDED_CARRIER` to the carrier document it signs
//! and ships; this script embeds those exact bytes into the engine. When the
//! variable is unset or empty the engine embeds nothing and every package
//! operation that needs a release identity refuses with `policy-denied`. The
//! generated constant is the only build-time input the binary reads.

#[path = "build_support/embedded_carrier.rs"]
mod embedded_carrier;

use std::fs;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-env-changed={}", embedded_carrier::ENV);
    println!("cargo:rerun-if-changed=build_support/embedded_carrier.rs");

    let value = std::env::var(embedded_carrier::ENV).ok();
    if let Some(path) = value.as_deref().filter(|value| !value.is_empty()) {
        println!("cargo:rerun-if-changed={path}");
    }
    let bytes = match embedded_carrier::channel_bytes(value.as_deref()) {
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
