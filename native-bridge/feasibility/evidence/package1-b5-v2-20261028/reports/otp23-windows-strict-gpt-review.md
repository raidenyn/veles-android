# OTP-23 latest-Windows strict background acceptance review

## Review identity and boundary

- Independent reviewer: actual strict-review session `ses_e7ef53f61ffesIPmRxLtwZ1efE`, OpenCode `1.19.4`, agent `build`, provider/model `openai/gpt-5.6-sol`, variant `high`. Safe database metadata captured at `2026-10-28T06:01:57.705Z`: 64 messages (4 user, 60 assistant), 452 parts, and 0 child sessions/subagents; all 60 assistant messages identify only `openai/gpt-5.6-sol` variant `high`. The earlier session `ses_e7fbe5353ffeWU4VTjn6lgYjIL` is the separate required preflight session and is not metadata for this review. No API error headers, cookies, turn-state tokens, or transcript contents were read or used.
- Reviewed repository: `/home/dev/repos/veles-android`, HEAD `8f365fce0d31f975610c235085a95b9136ac25e2`, with existing uncommitted package-1 work preserved.
- This was read-only except for this report. No source, hardware, Chrome, adb/homepc, GitHub, commit, matrix, or evidence-row operation was performed; no subagent was used.
- Scope is only the completed latest-Windows W-11 COLD and W-12 PREWARM evidence. The connected-observer B-1..B-5/I-1..I-6 findings, W-02/W-07/W-08, M-08, and final CI hygiene are separate unless actually used here.

## Verdict

**Recommend PASS for W-11 and W-12 under the owner-approved 2026-10-27 background criterion, with no new physical rerun.** This is a matrix recommendation, not a row edit or an OTP-24 authorization. `formal_otp23_acceptance=false` is correct script policy: the matrix is the adjudicator, so that field is not a permanent-false blocker.

| Row | Recommendation | Decisive result |
|---|---|---|
| W-11 COLD | **PASS** | Separate full COLD run, Chrome `156.0.8078.73`, 719.988 s closed-CDP interval, no send/warmup, two exact unsolicited 4096-byte records hidden/unfocused, all ST-01..ST-15 PASS |
| W-12 PREWARM | **PASS** | Separate full PREWARM run, same Chrome build, exactly one exact 4096-byte exchange per phone before idle (seeds 41/53), 717.822 s closed-CDP interval, two exact unsolicited records hidden/unfocused, all ST-01..ST-15 PASS |

Current `strict_background_core.evaluate(..., platform="windows")` independently recomputes both raws as `PASS`, 15 checks each, with no status mismatch. The focused strict suites report **145 tests OK**.

## Runtime proof

- **Latest installed stable:** both raws record `Chrome/156.0.8078.73`; the producer manifest also recorded `.73` at batch start. The actual Chrome argv contains no throttling/backgrounding/freezing flag and no `--enable-features`, `--disable-features`, field-trial, or direct-launch diagnostic override. The loaded-extension scan covers `background.js`, `connector.html`, `connector.js`, and `manifest.json`; it finds no audio, WebRTC, socket, interval, lock, alarm/offscreen, notification, or keepalive API in either run.
- **No DevTools client during idle:** before the interval the connector target session and browser-level websocket were closed and the driver discarded its CDP object. The idle loop only sleeps; both raws record zero CDP calls. The pre-attach HTTP `/json/list` title read created no CDP session. COLD remained closed for 719.988 s; PREWARM for 717.822 s.
- **No host/phone command during silence:** COLD trace has no desktop send at all. PREWARM has exactly two sends and their two replies before CDP close; then no input until shutdown. Each trace's long silent gap ends with exactly two unsolicited host messages. Phone evidence records one queued type-3 4096-byte push per source after its configured 690,000 ms delay.
- **Uninterrupted background state:** both preconfigured histories are non-truncated, token-bound, and have no event inside the measured interval. The connector stays hidden/unfocused and the ordinary tab stays visible/focused. The two tabs are bound to the same Chrome window.
- **Foreground gate:** Win32 `GetForegroundWindow` recorded the run-owned Chrome PID before BLE and again after the ordinary-tab switch. Ordering is strict: COLD gate mono `741499.2211621` before BLE `741499.9156683`; PREWARM `743308.4854335` before `743309.1741173`. Both gates confirmed on poll 1 with the page visible/focused. This proves the gate was already satisfied; it does **not** prove that the requested physical click itself was observed. No synthetic input is claimed.
- **Byte exactness:** unsolicited expected/recomputed SHA-256 is `bacb3ac9a8a06263791fa5d9e33d1493d9d6a60165739b2f4c84ea25356e9ef6`, matching both handles in both runs. PREWARM reply hashes independently recompute to seed 41 `1cef2c355bfed99b4f4edfcf5f41021ca6d45672b07e2e38743cefa284c9c853` and seed 53 `bf32170537fcd1c7453d22941ddf6c7f627bbb3e5b1537932d3245b4eb6eea67`.
- **Phone binding:** source hashes recompute from the explicit serials: 6T `2be02e41` -> `c049779ebb347937c1e811616b0d63268b874b6425fff380cf98e5ee3c88a729`; 10 Pro `864a3db0` -> `dd63603cd0cdb86d8f0b984739b92bb91f01f0b4ee5c48421b46fa77c2ee168a`. The installed app/test APK hashes on both phones were `063ac7ea719b8b2625690a5be5a9d57a7b3a24bf64242c34a1772b510f677a5e` / `8ca89381dcc8573aba500e65ba4746e698ebdfa0bf3793345ddc4645712ede1c`; no install occurred.
- **Clock allowance:** `clock-offset-strict.json` measured phone-minus-PC medians `+724 ms +/-1116` and `+352 ms +/-1668` over nine round trips each, ending `02:07:25Z`. The supplied desktop-minus-phone union `-538 ms +/-1854` is conservative: it is the midpoint of the medians with the larger per-phone uncertainty plus the 186 ms center displacement. It predates COLD by about 2 h 24 m and PREWARM by 2 h 54 m, so stable clock drift is an assumption. It is not outcome-sensitive: the best distinct adjusted fire/receipt deltas are 0.536/1.039 s COLD and 0.550/1.125 s PREWARM; page receipts themselves are on the desktop clock and precede reattach by 29.860-32.311 s.
- **Shutdown:** each trace records both connection objects released, `shutdown joined 2/2 within 10ms`, `shutdown_complete`, and own `exit code=0 stdout_drained=true`; the driver then records Chrome closed and no surviving host.

## Provenance

The post-run read-only producer snapshot under `producer-pc-snapshot-20261028/` closes the earlier omission in `tree-manifest.json`: its producer raws/traces match the reviewed copies exactly, and its actual producer code matches the reviewed runtime bytes.

| Artifact | SHA-256 |
|---|---|
| `strict-windows-cold-2.json` | `27b723f8b9e6d9a6e57ed317e1510130f51ac03a254c21a275211ace4555fd02` |
| `strict-windows-prewarm.json` | `424378114d860bfc9449e63a10175489080341ac5265e34c7ceaf642fdfdf180` |
| COLD / PREWARM trace | `09c38d96416b168f447ace5300c5582f03942dea6eb9d68b1114f40a028eef84` / `35a1c9d5308d90e37d6dd8dc33c5399425712fa6161d48ca7b900acad216ab38` |
| producer `strict_background.py` | `478b836c444fa194da0528a41b25d5fcb962de925554b9b2b8dbf08a413839c7` |
| producer `strict_background_core.py` | `e44f616c04054707beac175c5dd61c03e5741a5bdfa3405361342ebd8f232ce2` |
| producer `strict_phone_validation.py` | `93057ec40c89529437c3ea3f81d6160324f6c1780770903c71f0dcef90fb9e11` |
| producer `chrome_validate.py` (only its ChromeRun/CDP/process helpers are used by strict) | `5b2589240adb8776e8f33438ad7e276e5f38024f2ea215a6a1643fa5a0045318` |
| producer extension `background.js` / `connector.html` / `connector.js` / `manifest.json` | `2d8a048a914e74328869cd0727f29ed26488aa6d45ba0ae7d204b1ca129deb0b` / `d43f1b59c764753d2c9ce4b5fbc47e1ea211420cd83a5d1501fd293e7a42d4aa` / `c1c33331611a24ca95405b305ee80a69beb3904e228056d6c268e9d4f3b7e2ac` / `6cc440fe2a2b58f0e0de31192607636bd7d89ce653f0dd4c7e4435a3e8605c04` |
| producer host binary | `8521de4d58bdba0858e717e3dca3bcc0850b272ba3c1544bda158afb9e60ae33` |
| `clock-offset-strict.json` | `f0932ffc3daa58769df7df3a0df9f238706e3ce7fd8bbbbe47ef2d45171663de` |
| pre-snapshot `SHA256SUMS` | `a896446a8aefaa67b7cd936c4569cf7cbcf35364e47b4cc4f0cf517b43e8d51d` |

The current clean evaluator metadata is HEAD `8f365fce0d31f975610c235085a95b9136ac25e2`, evaluator files dirty `false`, with core `e44f616c...`, validator `93057ec4...`, and finalizer `5a1638808759a31b3f0020c75a2e7b66e3376d2138d0588c6d4664e7ccf73c47`. The finalizer did not produce these live Windows raws; it is listed only as the current independent re-evaluator. The producer core and validator match it byte-for-byte.

The host binding is consistent end-to-end: the batch manifest records the scoped per-user registration and registered-copy hash `8521de4d...`; connected runs in the same retained-registration interval read the registered manifest path and self-record the same target hash; strict uses `runtime.connectNative` with fixed host `me.nagaev.veles.otp23.plain` and fixed extension id; the post-run producer binary again hashes `8521de4d...`. Registration was removed only after PREWARM. The disposable profile manifest was deleted with the profile and is not reconstructed.

## Retained failure and residuals

- `retained-strict-cold-launch-denied-1.json` remains a real failed setup attempt. Its cause is **unproven**; `PermissionError` at `DevToolsActivePort` is not proof of a Chrome race or proof of a fix-by-retry. It performed no BLE measurement. The later COLD file is a separate complete run with fresh phone timers, not a rewritten result.
- The producer snapshot was captured after the runs, not self-hashed by the raws. It is acceptable here because it preserves the exact producer tree and the task records that no producer update intervened; future runs should self-record all loaded runtime helper hashes before measurement.
- Phone physical identity ultimately rests on the operator's explicit serial binding plus distinct source hashes and two distinct host handles; the payload bytes are intentionally identical.
- The active checksum manifest verified all 181 pre-snapshot entries. Add the completed producer snapshot and this review to a new final manifest rather than rewriting the historical one. This is archival hygiene, not a reason to repeat the timed runs.
- The outer strict scheduled-task action/batch files are not among the retained root launchers. Effective runtime parameters, Chrome argv, clock values, phone count/source hashes, and outputs are recorded in the raws. If the task action can still be captured, add it to the final provenance manifest; absence alone does not create a behavioural contradiction or require a rerun.

## Same-build probe decision

No Chrome `156.0.8078.73` timer-probe rerun is required. The authoritative clarification permits the architecture-required Native Messaging port, retains the opt-out/keepalive/DevTools prohibitions, and defines a port-versus-portless escape as disqualifying. The existing Windows `.48` same-build probe found both tabs intensively throttled (about 60 s median gaps), while the `.73` strict runs directly satisfy all delivery and isolation gates with clean argv. A new `.73` probe becomes necessary only if a concrete `.73` scheduler/feature/policy change or a port/control divergence is identified; the minimum response would be one focused `.73` port-versus-control probe, not another COLD/PREWARM or connected-observer backlog rerun.

## Separation from other findings

The connected-observer defects do not alter this recommendation. Strict imports `chrome_validate.py` only for Chrome/CDP/process launch helpers; it does not call the defective connected endpoint census, macOS layer/session logic, Windows `pid_alive` observer, release-line gate, or W-07 phone-history adjudicator. Those rows still require their own fixes/evidence. This report promotes no other row, changes no waiver, and leaves the overall OTP-23 decision dependent on the remaining matrix rows.
