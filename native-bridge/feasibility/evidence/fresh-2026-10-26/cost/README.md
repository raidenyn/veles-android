# Current dependency-linked Tauri comparison

These artifacts describe source snapshot `4e9c685855d68c0c97eacc21108b7565c49bc186`.
They are separate from the historical `1a34191` cost experiment and from the
binary used by the fresh Chrome lifecycle run.

- Windows: the committed `scripts/cost_report.py` was run with only the
  `baseline` and `tauri-linked` entries in its in-memory `VARIANTS` map. Both
  used clean release target directories. The Tauri event-loop variant was not
  run. `windows-cost.json` preserves the tool's original field names: its
  `rustc` field actually contains **cargo**'s version, so use `../identity.json`
  for the independently measured compiler version.
- macOS: the native plain executable from the target checks was compared with
  `cargo build --release --locked --features tauri-linked --bin otp23-host
  --target-dir target-cost-linked`; no Tauri event loop was started. Native
  build output, byte sizes and hashes are retained.
- The macOS-target CycloneDX files were generated **on Linux**, with pinned
  `cargo-cyclonedx 0.5.9`, against the same `Cargo.lock` and filtered target
  `aarch64-apple-darwin`. They describe dependencies, not an independently
  inspected signed macOS artifact or a new native execution result.

## Measured sizes and SBOM components

| Platform / variant | Native executable bytes | Target SBOM components |
|---|---:|---:|
| Windows plain | 717312 | 22 |
| Windows dependency-linked Tauri | 758272 | 276 |
| macOS plain | 1721088 | 46 |
| macOS dependency-linked Tauri | 1744368 | 275 |

Windows cost-run executables have their own hashes in `windows/windows-cost.json`;
they are not the installed Chrome-run binary in `../identity.json`. No PE byte
reproducibility claim is made. `windows/driver.log` is the retained incomplete
offline attempt; `windows/driver-online.log`, `exit.txt` (0), and
`windows-cost.json` describe the completed comparison. The post-run lockfile hash
matches the source snapshot. Only the completed comparison supplies the table.

Linking Tauri without starting it is a cost proxy, not proof that Tauri's
bundler can package the chosen plain-Rust production executable correctly. No
installer was implemented and packaging-only retention is not approved by
these measurements.
