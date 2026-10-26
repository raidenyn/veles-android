# OTP-23 feasibility harness (disposable)

**Desktop handoff:** [Windows and macOS working PoC](../../../docs/desktop-ble-poc.md).

**Current status (2026-10-26):** [`docs/otp-23-contract-alignment.md`](../../../docs/otp-23-contract-alignment.md)
records the revised canonical contract (fragmentation profile revision 2), the plain-Rust
runtime decision and the strict verification harness. Pass/fail bookkeeping lives in
[`docs/otp-23-requirements-matrix.md`](../../../docs/otp-23-requirements-matrix.md)
(generated from `acceptance/otp23-matrix.json`): every fresh row is `NOT RUN` or `BLOCKED`,
decision **NO-GO**; tests needing new accounts carry a user-approved non-blocking waiver
(`NOT RUN`, never PASS).

**Historical working PoCs:** [`docs/windows-ble-poc.md`](../../../docs/windows-ble-poc.md)
and the macOS handoff. Their results, and the no-go results below for `1a34191`, describe the
trees at those commits and are not current acceptance.

Synthetic Native Messaging + BLE harness that answers one question for OTP-23: can **stable Chrome**
drive the required OS BLE behaviour through **one on-demand native host**? Evidence and decisions
live in [`docs/otp-23-windows.md`](../../../docs/otp-23-windows.md).

It is deliberately separate from the production bridge in `../src-tauri` and reuses none of it.
It carries **no Veles cryptography, trust, identity or OTP data** — only opaque synthetic bytes such
as the OTP-06 fixture `56 45 4c 01` → `56 45 4c 02`.

| Path | What |
|---|---|
| `src/framing.rs` | Chrome framing, 1..65536 bytes, caps checked before allocation |
| `src/strict_json.rs`, `src/protocol.rs`, `src/base64.rs` | Envelope v1: duplicate keys, nulls, floats, trailing bytes, canonical base64, exact request/event schema |
| `src/fragment.rs` | OTP2-GAT-010 profile revision 2: `LinkBudget` (one `min(MTU − 3, 512)` formula fed by `LinkCapacity`), fragmentation / reassembly mirroring the Android receiver |
| `src/core.rs` | Host state machine: negotiation, request ids, handles, bounded scans/links/queues, ordering, shutdown |
| `src/outqueue.rs`, `src/runtime.rs` | Bounded output queue, single stdout writer, reader/writer/core threads |
| `src/winrt.rs`, `src/winrt_map.rs` | WinRT BLE central (watcher, GATT link workers) and its platform-neutral failure mapping |
| `src/sim.rs` | Simulated phones for process-level tests (`--backend sim`) |
| `src/bin/otp23_host.rs` | Plain-Rust host (baseline) |
| `src/bin/otp23_host_tauri.rs` | **Historical** windowless Tauri 2.6.0 shell around the same host (`--features tauri-runtime`); kept only for the packaging cost comparison, not a runtime option |
| `extension/` | Exact-origin unpacked MV3 connector with a fixed key/id |
| `scripts/` | Per-user HKCU registration, CDP/Chrome driver, **strict** background run (`strict_background.py`), legacy 10-minute run (`poc_background.py`), requirements matrix (`matrix.py`), historical cost/SBOM report and current `dependency_cost.py` |
| `acceptance/` | `otp23-matrix.json`, the requirements matrix source |
| `evidence/` | Machine-readable results, safe traces, SBOMs, charts |

## Commands (Windows, from this directory)

```powershell
cargo test                                   # 150 tests: framing, schema, fragments, core, process, mappings
cargo test --test hardware_scan -- --ignored --test-threads=1   # needs a BLE radio
cargo build --release                        # plain host
cargo build --release --features tauri-runtime --bin otp23-host-tauri
python scripts\verify-identity.py            # key <-> extension id <-> Rust constants
.\scripts\register-host.ps1 -Flavour plain -ExePath target\release\otp23-host.exe   # HKCU only, no admin
python scripts\chrome_validate.py --flavour plain --out evidence\chrome-plain.json
.\scripts\unregister-host.ps1                # removes every registration and file
```

The toolchain is pinned by `rust-toolchain.toml` (1.98.0, same as the repository) and the
dependency graph by the committed `Cargo.lock`.

## Feasibility result

*(Historical result for `1a34191`; current status is the matrix above.)*

**Windows NO-GO; OTP-24 remains blocked.** Both physical phones connected and
exchanged small records, but the frozen MTU-517 fragmentation rule exceeds
Android's 512-byte attribute limit on larger notifications. The Tauri runtime
also creates a visible helper window in an interactive session. See the evidence
document linked above for failures and pending cases; simulated tests and the
debugger-attached background diagnostic are not acceptance passes.

Run `node --test scripts/connector-api.test.mjs` for the no-device connector API
check. The separate feasibility CI checks Rust on Linux and Windows; production
native-bridge Prettier/ESLint exclude this disposable subtree and raw evidence.
Use `scripts/isolated-device.gradle` and the opt-in Android instrumentation fixture
as described in the evidence document to preserve existing phone installations.

## macOS PoC

The plain host selects a real CoreBluetooth backend on macOS. Build with Rust
1.98.0, then run from this directory:

```sh
cargo build --locked --release --bin otp23-host
bash scripts/macos-package.sh       # GUI/Aqua context; existing Apple Development identity
bash scripts/macos-register.sh
python3 scripts/macos_poc.py --diag-scan-only --out evidence/macos-poc-scan.json
python3 scripts/macos_poc.py --out evidence/macos-poc-physical.json
python3 scripts/poc_background.py --flavour plain --out evidence/macos-poc-background.json
bash scripts/macos-unregister.sh
```

Enable Bluetooth access in macOS Privacy & Security when required, then retry;
the scripts never reset or bypass TCC. Do not use ad-hoc signing for the tested
bundle identity. SSH signing may lack keychain access; the same packaging script
works in the GUI user session. The phones can remain connected to the PC by USB;
only their nearby BLE advertising is needed by the Mac.

CoreBluetooth reports maximum write length, not negotiated ATT MTU. The host's
effective MTU is explicitly inferred from that length; 512-byte writes yield an
effective 515 and the shared 508-byte capped record payload. Exact-origin host
manifests are copied only into the owned disposable Chrome profile for testing.
See the desktop handoff for evidence, provenance and remaining production limits.

## Strict background validation

`scripts/strict_background.py --scenario cold|prewarm` closes every DevTools connection
before the idle interval, sends nothing during it, and relies on an unsolicited phone
notification (instrumentation arg `-e otp23_unsolicited_after_s 690`). Verdict logic and its
27 regression tests are in `strict_background_core.py` / `test_strict_background.py`; commands
and evidence rules are in `docs/otp-23-contract-alignment.md`, section 8 ("Hardware harness commands"). The older
`poc_background.py` evidence is not this test.

```sh
python3 scripts/test_strict_background.py && python3 scripts/test_matrix.py && python3 scripts/matrix.py check
```
