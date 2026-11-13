# veles-ble-host (OTP-24 native-messaging BLE bridge)

Production Native Messaging host for the Veles Android OTP app: a headless stdio process
that Chrome launches to relay BLE scan/connect/GATT-send requests to the phone. The crate
is the portable control plane only — no Tauri, no platform BLE stack, no simulator switch.

- `protocol/docs/veles-v1.md` — wire protocol (framing, envelopes, exit-code contract
  OTP2-BRD-032).
- `native-bridge/host/tests/process.rs` — the process-contract acceptance suite below.
- `docs/superpowers/plans/2026-10-31-otp24-production-bridge.md` — task plan; platform
  adapters (WinRT / CoreBluetooth) arrive with Tasks 6/7.

## Layout

| Path | Role |
|---|---|
| `src/lib.rs` | Portable library: `protocol`, `framing`, `strict_json`, `ingress`, `outqueue`, `core`, `fragment`, `buffer`, `runtime`, `host`. |
| `src/runtime.rs` | Headless process runtime: one reader, one writer, the core loop, bounded five-second exit (release observation + ≤2 s stdout drain). |
| `src/host.rs` | Process entry: strict Chrome launch validation, build-time origin allowlist, platform-gated dispatch, bounded typed stderr diagnostics, raw stdio ownership. |
| `src/bin/veles-native-host.rs` | Production binary — runs `host::run_process`, then `process::exit`. No simulator flag exists. |
| `examples/host_test_driver.rs` | Test-only driver (bin `host-test-driver`, gated by the non-default `test-driver` feature, enabled for `cargo test` only via a self dev-dependency): fake backend, deterministic stuck/freeze/block/delay modes. Never built by a default or release build; never a production entry. |
| `tests/` | Protocol, queues, core, fragmentation, runtime and process suites. |

## Build and test

```bash
CARGO_TARGET_DIR=build/native-bridge/host/target \
  cargo test --manifest-path native-bridge/host/Cargo.toml --locked --test process
CARGO_TARGET_DIR=build/native-bridge/host/target \
  cargo test --manifest-path native-bridge/host/Cargo.toml --locked
cargo fmt --all -- --check
cargo clippy --all-targets --locked -- -D warnings
```

Every process test spawns real children over real pipes and bounds each child with a
parent watchdog (`try_wait` deadline loop, kill on overrun). The narrow `--test process`
selection builds the bin targets the tests spawn; a full `cargo test` builds everything.

### Gradle entry points and outputs

From the repository root (no Node/npm/Tauri involved; see `docs/reproducible-builds.md`):

```bash
./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest
./gradlew bridgeHostBuild
```

`bridgeHostBuild` runs `cargo build --locked --release --bin veles-native-host` and writes
`build/native-bridge/host/veles-native-host[.exe]` plus `SHA256SUMS`; cargo's own target directory is
`build/native-bridge/host-target`. `native-bridge/host/target` and `native-bridge/host/build` are
never valid outputs (`./gradlew clean` removes them and `rust/scripts/assert-clean.sh` asserts it).
CI artifacts are `native-host-windows` (executable + `SHA256SUMS`) and `native-host-macos` (a mode-preserving tar of the executable + `SHA256SUMS`, plus an archive checksum; upload would drop a bare exec bit), all unsigned; the
older Tauri scaffold installers keep their `native-windows` / `native-macos` artifacts.

## Property, fuzz and adversarial verification (Task 9; verification-only)

Nothing here ships: `tests/properties.rs` is a test target and `fuzz/` is a **separate Cargo
workspace** (own `Cargo.lock`, `[workspace]`, never a member of or dependency of
`veles-ble-host`). `cargo tree --locked -e normal` on the host never lists `veles-ble-host-fuzz`
or `libfuzzer-sys`; the production binary is built with `cargo build --locked --release --bin
veles-native-host` and does not read `fuzz/`.

One shared model, `tests/support/adversarial.rs`, drives only public seams (`parse_strict`,
`parse_request`, `read_frame`, `fragment`/`Reassembler`, `Core` + recording fake backend + real
ingress/outqueue) and is included by both the properties and the fuzz crate, so they check the
same invariants.

- **Fragments** run in lockstep with an independent per-peer reference model (`RefModel`, written from
  OTP2-GAT-011/012 and the Android semantics, with its own live partials, byte totals and conflict
  window). After every frame the production outcome — including the exact bytes of every completed
  record — must equal the model's, as must expiry counts, `next_expiry`, per-peer partial counts and the
  global reserved count/bytes; per-peer/global caps (2 transactions, 8 KiB, 16 transactions, 64 KiB) are
  asserted from the model's independent sums. The model encodes the owner-approved conflict policy (OTP2-GAT-011:
  a start for a live id erases that partial and releases its budget and is itself rejected; later nonzero
  continuations are ignored; a fresh index 0 may start; the second conflicting start within 60 s disconnects).
  The censuses assert a minimum of conflict erasures. Mixed-source completions
  remain possible by design only for a bare next-index continuation injected without a conflicting start (the
  transport cannot authenticate fragments; what higher layers do with such bytes is not tested here); a conflicting start can no longer
  enable one; any change to the policy needs the same owner decision across Android, the reference and fixtures.
- **Core**: one terminal per request id, no handle/link crossing, no write to a closing, released or
  capacity-broken link, no `Connect` to a target whose earlier link is unreleased, frozen fragment sizing,
  no event after exit, reservation/data budgets conserved and returned; branch censuses (connects, sends,
  messages, busy fence, oversize, capacity breaks, flood overflow, live-duplicate exits, exit codes) are
  asserted so random programs cannot silently stop reaching them.
- **Allocation guard** (narrow claim): for ONE `parse_request` call on the calling thread, the largest
  single allocation, the gross requested bytes and the peak live bytes are bounded by documented multiples
  of the input length. It is not a whole-process memory proof.

```bash
# deterministic properties (fixed SplitMix64 seeds recorded in the file; no clock, no entropy)
CARGO_TARGET_DIR=build/native-bridge/host/target \
  cargo test --manifest-path native-bridge/host/Cargo.toml --locked --test properties

# bounded smoke (60 s/target) + property run + identity/hash/exit records
native-bridge/host/fuzz/run-smoke.sh 60        # evidence: build/native-bridge/host/verification/
```

| Target (corpus ceiling) | Seed | Corpus |
|---|---|---|
| `strict_envelope` (65,536 B = frame ceiling) | 24001 | `fuzz/corpus/strict_envelope/` |
| `fragment_stream` (16 KiB program; embedded frames ≤ 600 B) | 24002 | `fuzz/corpus/fragment_stream/` |
| `core_sequence` (4 KiB; ≤ 512 ops) | 24003 | `fuzz/corpus/core_sequence/` (the five Review Focus cases + a census seed) |

`fragment_stream` seeds are found, not hand-assembled (first seeded programs reaching conflict-erase / cap-refusal /
disconnect / expiry / the 16-transaction cap; see `smoke --write-seeds`), because the program layout
(nine peers sharing one global budget, pooled records, ops mod 20) is defined by the target.

The corpus is regenerated deterministically with
`fuzz/target/…/smoke --write-seeds native-bridge/host/fuzz/corpus` (build with
`cd native-bridge/host/fuzz && CARGO_TARGET_DIR=../../../build/native-bridge/host/fuzz-target
cargo build --locked --release --bin smoke`). Reproduce a saved finding with
`smoke --target T --replay build/native-bridge/host/verification/crashers/T/crash-*.bin`.

Two drivers run the same target functions (`veles_fuzz::run`):

- **`smoke`** (stable, pinned toolchain `1.98.0`, no extra dependency): a seeded mutator over the
  corpus with a coarse-behavior signature as the keep/discard rule. It is **not** coverage guided
  and **not** libFuzzer, and its JSON says so (`kind`). This is what the recorded 60 s runs use.
  `run-smoke.sh` exits non-zero if the property run OR any smoke target fails, while still collecting
  all evidence (`VELES_PROPERTIES_CMD` / `VELES_VERIFICATION_OUT` exist only for its negative test).
- **libFuzzer targets** (`fuzz/fuzz_targets/*.rs`, feature `libfuzzer`, `libfuzzer-sys =0.4.13`):
  need `cargo-fuzz` and a nightly toolchain, neither pinned or installed by this task. The targets
  type-check on `1.98.0` (`cargo check --locked --features libfuzzer --bins`) but have not been run.
  Intended invocation once a reviewed nightly + `cargo-fuzz` revision is pinned:
  `cd native-bridge/host/fuzz && cargo +<pinned-nightly> fuzz run strict_envelope fuzz/corpus/strict_envelope -- -seed=24001 -max_len=65536 -max_total_time=60`
  (`fragment_stream`: `-seed=24002 -max_len=16384`; `core_sequence`: `-seed=24003 -max_len=4096`).

Bounded runs are regression evidence, never proof of absence. Pinned "interesting" lengths: every
record 1..=4,096 round-trips base64/envelope/fragments/core at MTU 185, 244, 517 and write 512;
0, 4,097, 5,120 and 5,121 are checked at the envelope/core split.

## Ingress staging bounds (user-approved 2026-11-01)

Unconsumed OS notifications wait in a bounded ingress before the deterministic core drains them:
**48 events / 16 KiB per link, 1,536 events / 512 KiB global** (exactly 32 links x the per-link
ceilings, so every link can sit at its cap at once), fragments at most 512 bytes. The event and
byte ceilings are independent (512-byte fragments reach 16 KiB at 32 events). These are *ingress
staging* ceilings only: the frozen reassembly caps (2 transactions / 8 KiB per peer, 16 / 64 KiB
global), send storage and delivery caps are unchanged. At the 185-byte MTU floor one maximum
4,096-byte record is 24 fragments, so a stalled core can hold exactly two of them per link
(48 fragments, 8,736 bytes); at larger MTUs two records need fewer fragments. A link that exceeds
its bound fails closed (one `LinkLost`, never a gap) and other links are unaffected. Memory is
fixed-capacity plus the accounted payload bytes: measured by `tests/ingress_memory.rs`
(about 188 KB ingress lane array + 196 KB for 34 gate lane arrays + 524,288 B payload + about
164 KB queue bookkeeping with all 32 links at cap, ~1.07 MiB). No hardware guarantee is claimed:
the vendored macOS bluest peripheral event channel keeps its own, independent 16-slot
overflow-dropping buffer (shared by all of that peripheral's events; an overflow ends the link
fail-closed). It bounds only the *link worker thread's* poll latency — the worker forwards into
the gate and ingress without waiting for the core — whereas the 48-event ingress bound covers
*core-drain* latency. They guard different stalls rather than combining into one margin, and the
16-slot buffer is the smaller one for worker descheduling (unmeasured on hardware). Windows burst
behaviour is unmeasured. See the design spec and `protocol/v1/limits.json` (`bridge_data_events_*`,
`bridge_gate_staged_*`, `bridge_deferred_controls_max`).

## Scan release accounting (Opus I3/I4 fix)

- A scan is counted against the 2-scan ceiling while it is **pending, active or closing**.
  `stop_scan`, the 300 s cap, an OS-ended scan and a failed start answer the protocol
  immediately (ordering unchanged) but leave the scan in the core table, with its ingress
  reservation, until the backend reports `BackendEvent::ScanReleased { scan }` — emitted by
  the scan worker only after its teardown was observed **clean** (`closejoin::complete_scan`).
  Repeated `start_scan`/`stop_scan` against a slow or wedged stop therefore stays at two
  workers and answers `cap_exceeded`; link handling is unaffected.
- A scan whose cleanup failed never emits `ScanReleased`: it stays counted (like a closing
  link whose release is never observed; restart clears it). The adapters' `ScanLedger`
  latches such a failure when it prunes the finished worker, and `shutdown` hands the
  coordinator an already failed completion, so `ShutdownComplete` is withheld and the
  runtime watchdog fails the exit closed.
- Late callbacks of a closing scan are staged by the ingress (bounded, reservation kept so
  the release can be delivered) and never disclosed. Downstream: any new backend must emit
  `ScanReleased` once per scan (also after a failed start, including a worker that could not
  be spawned), or the scan slot is leaked.

## Launch contract

Chrome starts the host with exactly:

- one required argument — the calling origin, `chrome-extension://<32-char-id>/`, which
  must be on the build-time allowlist (below); and
- at most one documented platform launcher argument, `--parent-window=<handle>`.

Anything else is refused with exit `2` and one bounded line on stderr
(`veles-native-host: stage=launch_arguments` / `stage=launch_origin`): unknown flags (there
is deliberately no simulator/backend switch), extra or repeated arguments, malformed
handles. Refusals never write to stdout.

### Origin allowlist (build-time only)

The valid origins are embedded at compile time; a runtime environment change can never
alter them, and every embedded entry is validated when the program is built (an invalid
override fails the build):

- Default: `DEVELOPMENT_ORIGIN` = `chrome-extension://abcdefghijklmnopabcdefghijklmnop/`
  — the placeholder shared verbatim with `native-bridge/src/manifest.mjs`. It is
  development-only, NOT an established published origin; the published
  origin/registration policy remains OTP-25.
- Override (compile time only, comma-separated, max 8):
  `VELES_ALLOWED_ORIGINS="chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/,chrome-extension://pppppppppppppppppppppppppppppppp/"`. External
  acceptance tooling that derives a fixture origin rebuilds this same production source
  with that recorded allowlist and records the build configuration.

## Exit codes

`0` clean EOF / successful shutdown handshake · `1` host failure (unsupported platform
refusals instead use `2` — see below) · `2` rejected input, fail-closed, no usable
correlation (also every launch refusal, wrong origin, truncated header/payload, zero or
oversize frame, duplicate live request id) · `3` `incompatible_api` (no API-version
overlap) · `4` output-queue overflow. Protocol exits stay silent on stderr; stderr carries
typed stubs only, e.g. `veles-native-host: stage=platform_unsupported`.

## Platform status (this task)

`host::run_process` is platform-gated:

- Linux and other non-Windows/macOS targets refuse before any runtime starts
  (`stage=platform_unsupported`, exit `2`) — they never pretend to be another platform;
- Windows runs the production WinRT adapter (`src/winrt.rs`, Task 6; compiled on Windows
  only; the pure mappings live in `src/winrt_map.rs`, the release bookkeeping in
  `src/closejoin.rs`);
- macOS runs the production CoreBluetooth adapter (`src/macos.rs`, Task 7; compiled on macOS
  only; the pure mappings — hint/UUID identity, the `write < 512` capacity refusal, failure
  classification and the release-settle rule — live in `src/macos_map.rs`). It has been
  type-checked and linted for `aarch64-apple-darwin` only; it has not been linked or executed.
  It links the vendored, hash-pinned `vendor/bluest` (see `vendor/bluest/VELES-PATCH.md`):
  loss-aware notifications and an exact `Disconnected` state read. The portable link logic
  (`src/macos_link.rs`) is shared and tested on any host.
  `scripts/macos-test-bundle.sh` embeds the built host in the one recorded approved OTP-23 test
  identity (fixed bundle id/team/certificate, no identity options, no ad-hoc fallback, no TCC
  reset, unique staging and atomic non-replacing publish) — a test vehicle, not the production bundle;
- portable protocol behavior is exercised through the test driver, never through a
  production simulation path.

## Test driver (test-only)

`examples/host_test_driver.rs` (bin `host-test-driver`, feature `test-driver`; a default
build refuses to build it and the process tests pin that) accepts the production launch
arguments after stripping its test-only modes:

- `--test-stuck-cleanup` — fake backend never reports `ShutdownComplete`; shows the
  bounded five-second fail-closed release observation end-to-end (exit `1`);
- `--test-block-shutdown` — the fake backend blocks forever inside the release submission;
  the independent process-exit watchdog must end the process with exit `1` at the deadline;
- `--test-delayed-release=<ms>` — the release is reported asynchronously that many
  milliseconds after the submission;
- `--test-freeze-stdout` — the stdout writer parks inside its first frame write (blocked
  Chrome pipe); shows bounded exits independent of a blocked writer.

The fake backend resolves normalized targets (case-folded typed identity, unresolved for
empty hints), reports scan/connect/write lifecycle, and emits the process-wide release
synchronously on `shutdown` unless stuck.