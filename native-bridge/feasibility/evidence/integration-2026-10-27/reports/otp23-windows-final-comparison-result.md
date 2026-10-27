# OTP-23 Windows cleanup fix + close-begin-matched physical comparison — final result (GLM-5.3-flash)

Executed per the parent task and the Opus fix preflight (`/tmp/opencode/otp23-windows-fix-preflight-opus.md`).
No subagents; model is actual glm-5.3-flash (parent captures opencodedb provenance). No commits,
no pushes, no GitHub, no Mac files touched, no matrix/RFC/promotion edits. Tree:
`/home/dev/repos/veles-android` @ `dc5d127` + uncommitted changes preserved (Mac harness edits and
this fix coexist; `git status` shows exactly: winrt.rs, closejoin.rs (new), lib.rs, the driver
script (new), evidence folder (new) + the pre-existing Mac strict-background edits).

## 1. Fix state (uncommitted)

- Default: every WinRT disconnect retires the worker's `JoinHandle` into a `closing` sink;
  `shutdown_join` (factored from `Backend::shutdown`) waits for ALL of them (in-map + retired)
  under ONE bounded budget (2 s, unchanged). `VELES_OTP23_DETACHED_CLOSE=1` reproduces the legacy
  detached close in the SAME binary (flag proof on Windows: queueing test goes red under it).
- `reap_finished` bounds the sink between shutdowns (churn tests assert boundedness on Linux and
  Windows). Close tracing is per-conn with step results (`ok` / `err hresult=0x…`) and elapsed ms;
  `conn N disconnect requested epoch_ms=…` (BOTH arms, common absolute anchor),
  `conn N connect worker started epoch_ms=…` (second host), `released (objects closed; link-down
  unconfirmed)` semantics noted. `shutdown joined X/Y within Zms` / `budget exceeded:` lines.
- Deliberately unchanged: core.rs ordering, CCCD unset (H-3, later separate step only), no
  link-down polling, no retries, no added waits, macos.rs (same-class, OTP-24 scope), W-07/W-08
  BLOCKED. No requirements weakened.

## 2. Regression (all real-path; logs in evidence/windows-cleanup-2026-10-27/gates/)

1. RED vs HEAD: scratch tree with HEAD-equivalent detach → `shutdown_joins_a_worker_retired_by_disconnect`
   FAILS on Windows (close provably still running when shutdown returns).
2. GREEN: fixed tree `cargo test --lib` 13/13 on Windows; Linux full `cargo test --locked` green;
   `cargo fmt --check`, clippy `-D warnings` host+windows targets clean (logs included).
3. FLAG: `VELES_OTP23_DETACHED_CLOSE=1 cargo test --lib disconnect` → queueing test FAILS
   (closing.len() 0 ≠ 2) — the legacy branch is genuinely reachable in the shipped binary.

## 3. Physical comparison — compare-2 (six runs, all VALID)

Setup: approved fixtures verified on-device (app `063ac7ea…`, test `8ca89381…`, both phones),
started 17:03–17:05Z (`otp23 true`, no unsolicited), direct launch, one binary
`8521de4d…`, driver `86781a91…`, order awaited,legacy,legacy,awaited,awaited,legacy, quiescent
precondition (no host procs + ≥12 s) recorded per run, target gap 1500 ms gated from the common
`disconnect requested` epoch anchor, actual gaps RECORDED (not configured sleeps).

| run | arm | gap ms | first-host close | second host |
|---|---|---|---|---|
| 1 | awaited | 1500 | 6 steps ok per conn (0–2 ms), released-before-exit, shutdown joined 2/2 in 10 ms | both phones `connected` |
| 2 | legacy | 1500 | detached x2; process died ~1 ms after the two Remove\* steps (no service/session/device close, no released) | both phones `access_denied` fatal (GattStatus=3, no protocol error) |
| 3 | legacy | 1500 | same | both phones `access_denied` fatal |
| 4 | awaited | 1501 | as run 1 | both `connected` |
| 5 | awaited | 1501 | as run 1 | both `connected` |
| 6 | legacy | 1500 | same as 2–3 | both `access_denied` fatal |

**Counts: awaited 3/3 clean, legacy 3/3 denied, gaps matched 1500/1501 ms, close adds only
0–2 ms.** Per the parent's decision rules, this satisfies "fixed 3/3 clean while legacy denies and
matched gaps" ⇒ supports the causal-fix claim at the tested point: under this protocol the
1.5 s-window outcome was decided by the host's close discipline (truncated object-closing steps),
not by OS/peripheral teardown latency. The legacy truncation point is now trace-precise: the two
handler-removal steps ran; `service_close`, `maintain_false`, `session_close`, `device_close` never
ran — exactly the calls that release the GATT session/device host-side.

Phone-side raws: unfiltered `dumpsys bluetooth_manager` for both phones after the runs (22
CONNECTED/DISCONNECTED history lines per phone across the 6-run window). Host pids, device
label-hashes (never labels), scan states and full framed replies are in `compare-2/result.json`;
per-run traces are byte-preserved (`SHA256SUMS`, 48 files).

Honest notes:
- First attempt `compare-1` RETAINED: the driver resolved the `all` token to no devices (rewrite
  bug), so six runs happened with ZERO links — invalid as comparison data, kept as a failure
  record; the defect was then fixed (token conversion restored) and `compare-2` ran once, no other
  retries.
- No 1.5 s-specific threshold claim; n=3; the known 4 s (deny) / 10 s (clean) bracket is unchanged;
  no CCCD arm (awaited close fixed the window without touching CCCD, so H-3 is not required at
  this point); H-2/H-4 not excluded in general — only that they did NOT decide THIS comparison.
- Residual: W-07/W-08 stay BLOCKED (their own acceptance rerun required; nothing promoted);
  no blanket permission relabel; distinct retryable classification + bounded reconnect policy
  remain in #87/#88; a retry/delay is still not a fix; `formal_otp23_acceptance` semantics
  untouched; OTP-24 out of scope. The comparison is direct (no Chrome); current "Chrome-only"
  instruction honored (no 155 tests exist; no Chrome-version claim made).

## 4. Fixture hygiene (verbatim records in phones/)

- 10 Pro round: clean JUnit result `OK (1 test)`.
- 6 T round: stop-marker race against FOUR stale instrumentation sessions from earlier rounds
  (Oct 26 16:57Z / Oct 27 00:11Z / 01:53Z / 13:40Z local clients — killed locally; remote sleepers
  ended/force-stopped); ended via `am force-stop` of both fixture packages; NO JUnit result for
  this round (fixture/hardware failure preserved, not hidden). Pre-existing app-data uid anomaly
  on the 6 T (data dir `u0_a220` vs live process `u0_a221`) recorded; this session performed NO
  install, NO wipe, NO key change.
- Phones left clean: no veles processes on either device; advertising stopped.
- PC left clean: no host processes; no registration created/removed; diagnostics source trees
  retained (`otp23-probe\cleanup-closejoin-1`, `otp23-probe\red-scratch-1`).

## 5. Coordination / next steps for the parent

- Mac finalization completed independently first (`/tmp/opencode/otp23-mac-final-adjudication-result.md`,
  cold+prewarm adjudicated PASS with the Q-1-fixed evaluator; it confirms it did not touch my files).
- Focused finding + evidence: `native-bridge/feasibility/evidence/windows-cleanup-2026-10-27/`
  (FINDING.md, compare-1, compare-2, discover-1, gates/, phones/, scripts-used/, SHA256SUMS,
  .gitattributes).
- Required Gradle gates (Spotless/detekt/lintDebug/log guards) remain the parent's final step
  before commit; Rust/py gates above are green on this exact tree.
- Suggested parent checks: `sha256sum -c SHA256SUMS` in the evidence dir; `git diff` review of
  winrt.rs/closejoin.rs/lib.rs + the new driver/evidence files.