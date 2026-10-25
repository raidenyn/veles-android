# OTP-23 feasibility harness (disposable)

**Working Windows PoC:** see [`docs/windows-ble-poc.md`](../../../docs/windows-ble-poc.md)
for the opt-in capped-fragment Android build, plain-Rust host, tested physical
matrix and handoff. Historical no-go results below describe the original frozen
profile; they remain valid for normal builds, not for the opt-in PoC rule.

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
| `src/fragment.rs` | OTP-06 GATT fragmentation / reassembly (mirrors the Android receiver) |
| `src/core.rs` | Host state machine: negotiation, request ids, handles, bounded scans/links/queues, ordering, shutdown |
| `src/outqueue.rs`, `src/runtime.rs` | Bounded output queue, single stdout writer, reader/writer/core threads |
| `src/winrt.rs`, `src/winrt_map.rs` | WinRT BLE central (watcher, GATT link workers) and its platform-neutral failure mapping |
| `src/sim.rs` | Simulated phones for process-level tests (`--backend sim`) |
| `src/bin/otp23_host.rs` | Plain-Rust host (baseline) |
| `src/bin/otp23_host_tauri.rs` | Windowless Tauri 2.6.0 shell around the same host (`--features tauri-runtime`) |
| `extension/` | Exact-origin unpacked MV3 connector with a fixed key/id |
| `scripts/` | Per-user HKCU registration, CDP/Chrome driver, 10-minute background run, cost/SBOM report |
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
