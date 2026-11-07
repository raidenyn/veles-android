# veles-bridge-installer (OTP-25)

Standalone, signed, per-user installers for the plain-Rust Veles native-messaging host
(Windows NSIS + Rust engine, macOS SwiftPM app + Rust engine). No Tauri, no Node, no
elevation, no service. User and developer instructions: `docs/otp-25-installers.md`.
Design/plan: `docs/superpowers/{specs,plans}/2026-11-06-otp25-installers*.md`.

## Layout

| Path | What |
|---|---|
| `src/`, `tests/` | engine crate `veles-bridge-installer` (transactions, journal, policy, cleanup) |
| `windows/installer.nsi`, `macos/` | native frontends |
| `config/` | `release.schema.json`, fixture configs, `tools.lock.json`; no approved production config is tracked |
| `scripts/` | unsigned input assembly, correspondence, signing gate, dev registration, **evidence validator** |
| `acceptance/cases.json`, `acceptance/matrix.json` | acceptance case definitions and the recorded matrix |

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
{"schema": 1, "platforms": {"windows": {"source_commit": "<40 hex>", "config_sha256": "…",
  "descriptor_sha256": "…", "tools_sha256": "…", "unsigned_sha256": "…",
  "package_sha256": "…", "signer": "<publisher or Developer ID identity>"}, "macos": {"…": "…"}}}
```

Fill it from the release gate's authenticated output, not from the test operator's notes.
Never record a digest or fingerprint that was not read from real output.

### Run bundle

* `run.json`: `schema`, `run_id`, `platform`, `operator.id`, `binding` (the anchor fields plus
  `captures.{package_digest,signature[,gatekeeper]}`), `environment.{os_version,arch,chrome_version}`,
  `clocks.{source,start_utc,end_utc,host_offset_seconds,offset_capture}`, `accounts[]`
  (`id`, `kind`, `fresh`, `identity_capture`, `fresh_evidence`), `captures[]` (`path`, `sha256`,
  `bytes` — every file under `captures/`), `publication.raw_captures`
  (`withheld` | `published` + `privacy_review`).
* `ledger.jsonl`: one JSON object per line; `seq` from 1, `prev` = sha256 of the previous raw
  line (first: 64 zeros); `attempt_id`, `case`, `account`, `started`, `ended`, `outcome`,
  `actions[]` (`id`, `at`, `step`, `capture`, `returns`, `elevated:false`), `observations[]`
  (`id`, `at`, `capture`), `reason` (required unless PASS), `resolves[]` (a PASS lists earlier FAILs).
* `adjudication.json`: `adjudicator.{id,independent_of[]}`, `run_json_sha256`, `ledger_head_sha256`,
  `adjudicated_at`, `verdicts[]` (`attempt_id`, `outcome`, `rationale`).

Unit tests (`scripts/test_evidence.py`) use synthetic bundles in temp directories only; none is
physical evidence.
