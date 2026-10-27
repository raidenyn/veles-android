# OTP-23 Windows awaited-close fix + close-begin-matched physical comparison — focused result (GLM-5.3-flash session, 2026-10-27)

Scope: this file and this folder only. The global RFC/matrix/docs (`docs/otp-23-investigations.md`,
`docs/otp-23-strict-background.md`, macOS strict-background harness files) are NOT edited here;
W-07/W-08 stay BLOCKED and no matrix row is promoted, no OTP-24 work is proposed, and nothing here
weakens any requirement. The parent session performs review, commits and the Gradle gates.

## 1. What was fixed (source, one binary, both arms)

`native-bridge/feasibility` at HEAD `dc5d127` + uncommitted changes. Source bindings (identical on
the Linux worktree and the PC copy `C:\Users\yuriy\otp23-probe\cleanup-closejoin-1`, compared):

| File | sha256 |
|---|---|
| `src/winrt.rs` | `2ef50ec18fd96907c945136d82dda0796989d5e74fdecdb005d47ee027f61e6a` |
| `src/closejoin.rs` (new) | `25e44f6db140941cda514b0e5e798785361ec822c6c7e778e0cd40656cc47b5c` |
| `src/lib.rs` | `79bbf21fdd28b28b8f60667be6d3d1f60e060633f8edc29a7fb06828f58b0265` |
| driver `scripts/windows_closejoin_compare.py` (as run) | `86781a91b2714bcfddb56e5ca46dbb4acad9ee77831093c2a491e1d0b33445fe` |
| host binary `otp23-host.exe` | `8521de4d58bdba0858e717e3dca3bcc0850b272ba3c1544bda158afb9e60ae33` |

The full source-file manifest (all 20 `src/*.rs`) is recorded inside `compare-2/result.json`
(`source_sha256`), taken from the PC copy that actually built the binary — `winrt.rs`, `closejoin.rs`
and `lib.rs` match the Linux worktree hashes above; `core.rs`, `runtime.rs` etc. are the HEAD files.

Defect S-1 (confirmed in source by the GLM analysis and the independent Opus review, and in
traces by 37/42 retained linked-host runs exiting without `released`): on every exit path
`release_all` → per-conn `disconnect()` removes the LinkSlot, so the bounded join in `shutdown()`
always iterated an EMPTY map and `Link::close()` ran on detached workers against
`std::process::exit`. The fix retires every disconnecting worker's `JoinHandle` into a `closing`
sink that `shutdown_join` joins under ONE bounded budget (SHUTDOWN_JOIN_BUDGET kept at 2s); the
historical detached close is reproducible ONLY behind `VELES_OTP23_DETACHED_CLOSE=1` in the same
binary. Also in scope (per the fix preflight I-2/I-3/I-4): `reap_finished` bounds the sink between
shutdowns (no unbounded growth during churn); every close step is traced per-conn with elapsed ms
and raw result (ok or `err hresult=0x…` via `windows::core::Error::code()`) plus
`conn N close begin`; `conn N disconnect requested epoch_ms=…` is traced in BOTH arms as the
common absolute anchor; `conn N connect worker started epoch_ms=…` anchors the second host's
connect start; `released` now reads `released (objects closed; link-down unconfirmed)`.
Deliberately NOT changed: `core.rs` ordering, no CCCD unset (H-3 left as a later separate step),
no link-down polling, no retries, no added waits, `macos.rs` same-class pattern untouched (recorded
for OTP-24 scope; Mac teardown must not be called "awaited" in docs).

## 2. Regression evidence (all on Windows, logs in `gates/`)

1. RED vs HEAD logic — `gates/red-vs-head-scratch.log`: a scratch copy with the HEAD-equivalent
   detach makes the REAL-path test `shutdown_joins_a_worker_retired_by_disconnect` FAIL
   (disconnect → shutdown with a 200 ms worker: the close is still running when shutdown returns).
2. GREEN on the fix — `gates/windows-cargo-test-green.log`: full `cargo test --lib` 13/13 pass
   (incl. the real-path red/green pair, the legacy-unawaited pair, churn-bounded tests on both
   Linux and the Windows-only module).
3. Flag behaviour proof — `gates/windows-cargo-test-env-flag-red.log`: with
   `VELES_OTP23_DETACHED_CLOSE=1` the same binary makes
   `disconnect_queues_live_workers_for_the_shutdown_join` FAIL (closing.len() 0 != 2), i.e. the
   flag truly reactivates the legacy branch in the real `disconnect_link` path.
   (`disconnect_link_mode(…, detached)` is tested with the explicit mode parameter, so the default
   arm does not depend on the environment.)
4. `cargo check --locked --target x86_64-pc-windows-msvc --all-targets` and Windows-target clippy
   `-D warnings` are clean on the Linux worktree; `cargo fmt --check` clean; Linux `cargo test
   --locked` green (all suites). Build on PC: `cargo build --locked --release --bin otp23-host`
   (`gates/windows-binary-build.log`), 14.16 s.

## 3. Physical comparison (6 runs, `compare-2/`; the invalid first attempt retained in `compare-1/`)

Protocol (`scripts/windows_closejoin_compare.py`, `scripts-used/` copy identical to the repo
driver): direct launch (no Chrome), FIRST host connects BOTH phones and exits via stdin EOF,
SECOND host connects both after a gap gated to anchor+1500 ms. One binary, arms selected per run by
the env switch. Order: awaited, legacy, legacy, awaited, awaited, legacy. Declared quiescent
precondition before every run: no `otp23-host.exe` process alive and ≥12 s since the previous
run's exit (recorded per run). Phones: the two approved fixtures (2be02e41 OnePlus 6T, 864a3db0
NE2215/10 Pro), apps at the recorded SHA `063ac7ea…`/test `8ca89381…` verified on-device BEFORE
starting (no install, no wipe). First attempt `compare-1` is RETAINED: the driver resolved the
`all` token to no devices (token-conversion lost in the rewrite) so no links were ever created —
invalid as comparison data, kept as a failure record; no retry logic, the defect was fixed and
`compare-2` ran cleanly.

Anchor and gap (one PC clock, epoch ms, traced by the host itself — exists in BOTH arms):

- anchor = max `conn N disconnect requested epoch_ms=` of the FIRST host
- gap = min `conn N connect worker started epoch_ms=` of the SECOND host − anchor

| run | arm | gap ms | close durations (per conn) | 2nd-process result |
|---|---|---|---|---|
| 1 | awaited | 1500 | 2 ms / 2 ms | both phones `connected` |
| 2 | legacy | 1500 | process died before close (see below) | both phones `connect_failed` `access_denied` (fatal) |
| 3 | legacy | 1500 | same | both phones `connect_failed` `access_denied` (fatal) |
| 4 | awaited | 1501 | 1 ms / 2 ms | both phones `connected` |
| 5 | awaited | 1501 | 1 ms / 1 ms | both phones `connected` |
| 6 | legacy | 1500 | same | both phones `connect_failed` `access_denied` (fatal) |

**Result: awaited 3/3 clean, legacy 3/3 denied, at matched gaps (1500/1501 ms) with close
durations of 0–2 ms** — the awaited close adds only milliseconds; delay cannot explain the split.

Detailed invariants per the fix preflight validity rules:

- Awaited runs: both conns' six `close step` lines all `ok` (per-conn, attributable), 
  `conn N released (objects closed; link-down unconfirmed)` BEFORE the `exit code` line,
  `shutdown joined 2/2 within ≤10 ms`, no budget excess. Second host: `services
  GattCommunicationStatus=0`, uncached characteristics `=0`, subscription completes.
- Legacy runs: `conn N closing worker detached (legacy detached close)` for both conns. The exit
  truncation point is now precise from the traces: the worker survives ~1 ms — `close begin` and
  the two `Remove*` handler steps appear, then the process exits BEFORE any of `service_close`,
  `maintain_false`, `session_close`, `device_close`; no `released` lines. The truncated four steps
  are exactly the object-closing calls that release the GATT session/device host-side.
- Denials: both phones, stage `access_denied`, retryable=false, at the first UNCACHED
  characteristic enumeration, `GattCommunicationStatus=3 protocol_error=none_or_unavailable` on
  `write characteristics` after `services GattCommunicationStatus=0` — the same capture shape as
  the retained K-series controls.
- Phone-side raw history: `phones/dumpsys-2be02e41-RAW.txt` and
  `phones/dumpsys-864a3db0-RAW.txt` (unfiltered `dumpsys bluetooth_manager` of both phones,
  captured right after the runs; 22 CONNECTED/DISCONNECTED history lines per phone covering the
  6-run window). Host pids and scan states are recorded per phase in `compare-2/result.json`.

## 4. What this establishes — and its limits

- The S-1 defect is fixed and regression-backed (red vs HEAD logic, green on the fix, flag
  reproduces legacy in the same binary).
- Per the parent's decision rules: fixed arm 3/3 clean while the legacy arm denies at MATCHED gaps
  (1.5 s from the common anchor) **supports the causal-fix claim**: under this protocol (both
  phones, direct launch, this PC, this binary, 1.5 s gap), whether the second process is denied at
  the 1.5 s point was decided by the host's close discipline, not by OS/peripheral teardown
  latency. With n = 3 no threshold shape is claimed; the 1.5 s point is between the previously
  established 4 s denial bound and the 10 s clean bound, i.e. INSIDE the previously denied window.
- NOT claimed here: that every observed AccessDenied across all platforms/configurations has one
  cause; any threshold (the 4–10 s bracket is untested at n=3); a fix for the OS/peripheral
  accounts (H-2/H-3/H-4 remain as possible further contributors elsewhere); CCCD (S-2) as involved
  — the awaited close fixed the window WITHOUT any CCCD change, so the measured causality at this
  point does not require it.
- Consequences that do NOT follow: no blanket relabelling of permission denials, no retry policy
  change. The existing distinct retryable classification and the bounded reconnect discussions
  stay in #87/#88 where they are. W-07/W-08 remain BLOCKED and would need their own acceptance
  rerun; the matrix adjudicates; no OTP-24 work is proposed. `formal_otp23_acceptance` semantics
  unchanged. A retry/delay remains "not a fix" per §2.5 of the investigations doc.
- The comparison used direct launch (no Chrome): the current "Chrome-only" instruction is satisfied
  because no Chrome 155 test exists and no Chrome-version claim is made — this is a direct
  host-to-host lifecycle comparison on the OTP-23 diagnostic host identity (like prior ctl-K/direct
  evidence), not an acceptance run.

## 5. Provenance, hygiene and retained failures

- Model: actual GLM-5.3-flash (`ollama-cloud/glm-5.3-flash`); no subagents; parent captures
  opencodedb metadata separately. This is not self-attested here — the parent records the
  session provenance.
- PC state after work: no `otp23-host.exe` processes; no registration was created or removed
  (direct launch throughout); the diagnostic source tree `C:\Users\yuriy\otp23-probe\cleanup-closejoin-1`
  and the retained RED scratch `C:\Users\yuriy\otp23-probe\red-scratch-1` are left in place.
  `compare-1` (driver-defect attempt, linkless), `discover-1` (scan-only validation), and this
  folder's raw files are preserved byte-as-produced.
- Fixture hygiene (recorded, not hidden): fixtures were started for this comparison at
  17:03–17:05Z after verifying the installed APK hashes on-device (no install/wipe). The 10 Pro
  round finished cleanly (`OK (1 test)`, `phones/fixture-10Pro.instrument.log`). The 6 T round
  produced NO JUnit result (`fixture-6T.instrument.log` ends after the bare class-name line,
  with no `Time:`/`OK` trailer); the verbatim record shows a `me.nagaev.veles.otp23.test`
  process (uid `u0_a221`) still ALIVE after the FIRST `am force-stop` and gone only after BOTH
  force-stops; that record's "before force-stop" section is empty; no advertising state was
  captured beyond the feature flags listed there. The cause of the missing JUnit result is
  UNPROVEN. POST-REVIEW CORRECTION (I-B, Opus windows-final review): the earlier wording that
  the stop marker raced with FOUR stale instrumentation sessions was OPERATOR-INFERRED from
  stale LOCAL adb client processes (Oct 26 16:57Z, Oct 27 00:11Z, 01:53Z, 13:40Z — killed
  locally) and does NOT prove any number of remote instrumentations; those local-client counts
  are retained here only as that inference's raw basis. The earlier "app-data uid anomaly"
  wording (data dir `u0_a220` vs live process `u0_a221`) is RETRACTED as un-evidenced: no
  data-directory ownership output was preserved, and a test APK legitimately runs under its OWN
  uid, so differing uids may well be normal. The same 6 T produced `OK (1 test)` in the earlier
  macOS prewarm round (2026-10-27), which CONTRADICTS any standing data-directory defect on that
  phone. This session performed NO install, NO wipe, NO key change. All verbatim:
  `phones/fixture-6T-forcestop-record.txt`, `phones/fixture-*.instrument.log`,
  `phones/fixture-start-utc.txt`, `phones/fixture-stop-utc.txt`. The comparison's validity does
  not depend on this: both advertisers were live and every first host linked both phones (the
  duplicated connection-history lines appear on BOTH phones' dumps, and the 10 Pro had a clean
  JUnit run).
- No Chrome was launched anywhere; no Mac evidence was touched (the parallel macOS finalization
  session confirms winrt.rs/closejoin.rs/lib.rs/windows_* untouched by it in
  `/tmp/opencode/otp23-mac-final-adjudication-result.md`).

## 6. Post-review integration (2026-10-27, GLM-5.3-flash follow-up session)

The independent Opus windows-final review (`/tmp/opencode/otp23-windows-final-opus-review.md`,
byte-identical copy in `evidence/integration-2026-10-27/reports/`) required two minimum fixes
before commit; both are applied here WITHOUT any physical rerun (the review verified that
`compare-2` already satisfies the stricter conditions):

- **I-A (driver protocol hardening).** `scripts/windows_closejoin_compare.py` gained: a pure
  `resolve_wanted` (token selection, regression-covered for the compare-1 zero-link defect: the
  pre-fix behaviour kept the literal token and selected nothing); a strict `run_validity`
  requiring EXACTLY two first-phase connections, BOTH second-phone attempts with nothing
  unresolved, and the MEASURED gap (common anchor -> second-host connect epoch) within
  +/-500 ms of the target (default, `--gap-tolerance-ms`); and an offline `--recheck-dir` mode.
  Pure tests: `scripts/test_windows_closejoin_compare.py` (24 tests, all green on this tree).
  The as-run driver copy `scripts-used/windows_closejoin_compare.py` is UNCHANGED
  (`86781a91…445fe`) — the strengthened live script is a NEW revision of the review protocol,
  not the physical producer.
- **I-B (fixture-account story corrected).** §5 above was rewritten post-review: the observed
  raws state a missing JUnit result, a test process outliving the first force-stop and ending
  after both force-stops, cause UNPROVEN; the stale-local-adb-client inference ("four stale
  instrumentation sessions") is now labelled operator-inferred only, and the un-evidenced
  app-data uid claim is retracted (a test APK may legitimately run under its own uid; the same
  6 T passed JUnit cleanly in the earlier macOS prewarm round).
- **Derived re-evaluation (no raw rewritten, nothing re-run):**
  `VALIDITY-RECHECK-20261027.json` re-judges the saved raws with the stricter rule —
  `compare-1`: 6/6 INVALID (zero first-phase links), `compare-2`: 6/6 VALID, awaited 3/3 clean
  vs legacy 3/3 denied at measured gaps 1500/1501 ms inside tolerance; stored parse and stored
  gap match the disk re-parse for every run. Command provenance is inside the JSON (driver sha,
  result.json sha, tolerance).
- Claim scope unchanged: supported at the tested 1.5 s anchor-matched point only (this PC,
  account, two phones, n=3); no threshold, no retry/delay claim; W-07/W-08 stay BLOCKED and are
  NOT automatically promoted; the crash/kill-restart window remains a product concern with the
  distinct retryable classification and bounded reconnect policy in #87/#88. No OTP-24 work.
- `SHA256SUMS` in this folder is regenerated after these edits and covers the current files; all
  raw files (compare-1, compare-2, discover-1, gates/, phones/, scripts-used/) are unchanged and
  their checksums still match the entries preserved above the additions.