# veles-bridge-installer (OTP-25)

Standalone, signed, per-user installers for the plain-Rust Veles native-messaging host
(Windows NSIS + Rust engine, macOS SwiftPM app + Rust engine). No Tauri, no Node, no
elevation, no service. User and developer instructions: `docs/otp-25-installers.md`.
Design/plan: `docs/superpowers/{specs,plans}/2026-11-06-otp25-installers*.md`.

## Layout

| Path                                              | What                                                                                                |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `src/`, `tests/`                                  | engine crate `veles-bridge-installer` (transactions, journal, policy, cleanup)                      |
| `windows/installer.nsi`, `macos/`                 | native frontends                                                                                    |
| `config/`                                         | `release.schema.json`, fixture configs, `tools.lock.json`; no approved production config is tracked |
| `scripts/`                                        | unsigned input assembly, correspondence, signing gate, dev registration, **evidence validator**     |
| `acceptance/cases.json`, `acceptance/matrix.json` | acceptance case definitions and the recorded matrix                                                 |

## Tests

```bash
cd native-bridge/installer && cargo test --locked
python3 -m unittest discover -s native-bridge/installer/scripts -p 'test_*.py'   # from repo root
```

Native steps (NSIS, `signtool`, Swift/Xcode, `codesign`, notarization, the physical matrix) do
not run on Linux; they are reported as NOT RUN / BLOCKED, never as passes.

## Acceptance matrix and evidence validator

```bash
python3 -I native-bridge/installer/scripts/evidence.py check-matrix \
  --cases native-bridge/installer/acceptance/cases.json \
  --matrix native-bridge/installer/acceptance/matrix.json
python3 -I native-bridge/installer/scripts/evidence.py validate --run <run dir> \
  --cases native-bridge/installer/acceptance/cases.json --anchor <external anchor.json> [--previous <older run dir>]
```

Exit `0` structurally valid, `1` invalid evidence, `2` missing input/environment.
**Exit 0 is never acceptance.** The initial matrix is 0 PASS / 0 FAIL / 32 BLOCKED / 0 NOT_RUN:
no device, account, credential or native runner has been available, so no physical case was
observed. Do not edit a row to PASS by hand: `check-matrix` rejects it. A real PASS needs a
standard-user run bound to an externally supplied anchor, raw captures with checksums,
an append-only hash-chained ledger and a separate independent `adjudication.json`;
publishing raw captures needs an approved privacy review. Physical obligations are tracked
under #89 and coordinated with #125/#127; this directory makes no BLE, protected-delivery
or Gate B/C/D claim.

### Anchor file (supplied from outside the run)

```json
{
    "schema": 1,
    "platforms": {
        "windows": {
            "source_commit": "<40 hex>",
            "config_sha256": "…",
            "descriptor_sha256": "…",
            "tools_sha256": "…",
            "unsigned_sha256": "…",
            "package_sha256": "…",
            "signer": "sha256:<leaf DER digest> or Developer ID identity"
        },
        "macos": { "…": "…" }
    }
}
```

Fill it from the release gate's authenticated output, not from the test operator's notes.
Never record a digest or fingerprint that was not read from real output. The Windows `signer`
is the leaf-certificate fingerprint `sha256:<64 lowercase hex of the DER>`, the same identity
`src/platform/windows.rs` authorizes at runtime; a certificate display name is never an
authorization identifier.

### Run bundle

- `run.json`: `schema`, `run_id`, `platform`, `operator.id`, `binding` (the anchor fields plus
  `captures.{package_digest,signature[,gatekeeper]}`), `environment.{os_version,arch,chrome_version}`,
  `clocks.{source,start_utc,end_utc,host_offset_seconds,offset_capture}`, `accounts[]`
  (`id`, `kind`, `fresh`, `identity_capture`, `fresh_evidence`), `captures[]` (`path`, `sha256`,
  `bytes` — every file under `captures/`), `publication.raw_captures`
  (`withheld` | `published` + `privacy_review`).
- `ledger.jsonl`: one JSON object per line; `seq` from 1, `prev` = sha256 of the previous raw
  line (first: 64 zeros); `attempt_id`, `case`, `account`, `started`, `ended`, `outcome`,
  `actions[]` (`id`, `at`, `step`, `capture`, `returns`, `elevated:false`), `observations[]`
  (`id`, `at`, `capture`), `reason` (required unless PASS), `resolves[]` (a PASS lists earlier FAILs).
- `adjudication.json`: `adjudicator.{id,independent_of[]}`, `run_json_sha256`, `ledger_head_sha256`,
  `adjudicated_at`, `verdicts[]` (`attempt_id`, `outcome`, `rationale`).

Unit tests (`scripts/test_evidence.py`) use synthetic bundles in temp directories only; none is
physical evidence.

## Build-tool identity (F8)

`scripts/tool_observation.py observe` reads the real `rustc`/`rustup`/`xcodebuild` (macOS) or `cl`/`link`/the
Visual Studio environment (Windows) on the build machine; `check` holds that inventory to `config/tools.lock.json`.
`bridgeInstallerUnsigned` depends on the check, so a production build cannot start on an unlocked toolchain;
`bridgeInstallerPackageFixture` only records the observation. `finalize` binds the observation into
`provenance.json`, and `release_gate.py` re-validates it in `verify-inputs` (before any key is used) and again in
`validate` (eligibility). The Windows MSVC and SDK identities were observed on the approved `windows-2025`
runner and are pinned by version only (owner decision 2026-11-08); the toolset/SDK digest definitions are
deferred and this pin should be strengthened later. The observation never invents a digest. macOS builds
select the SDK (`SDKROOT`, `swift build --sdk`) and the deployment target (`MACOSX_DEPLOYMENT_TARGET`, read
from the lock) explicitly. None of this has run on a native runner: the parsers follow documented tool
output and are unverified against real output.
