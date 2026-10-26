# OTP-23 requirements matrix

<!-- Generated from native-bridge/feasibility/acceptance/otp23-matrix.json by scripts/matrix.py render --write. -->

**Decision: NO-GO** — 0 PASS, 0 FAIL, 5 BLOCKED, 21 NOT RUN. Every row must be PASS (or an approved account-waived NOT RUN) before OTP-24 may start.

**User-approved account waiver** (2026-10-26): tests that require NEW accounts are skipped and non-blocking for the OTP-23 gate: W-01, M-01, M-02. They stay `NOT RUN`, are not PASS, and prove nothing about fresh or standard accounts. The waiver changes no other requirement (signing provenance, final identifier, allow/deny/recovery, architecture, strict background). Rows that do run record the account truthfully as `admin` or `standard`.

Fresh rows start `NOT RUN`. Historical PoC results are context in the narrative documents, never a PASS here:
they were taken with different binaries, an administrator account and (for background) a connected browser-level
DevTools client. Human-gated rows stay `BLOCKED` until a person with the account/GUI access records the result.

| ID | Requirement | Expected | Actual | Evidence | Status |
|---|---|---|---|---|---|
| W-01 | Stable Chrome launches the plain host on demand from a STANDARD non-admin Windows account (new-account test) | Host starts via runtime.connectNative with no elevation; account token is not an administrator. (Historical runs used an administrator account at medium integrity.) | (waived, not run) User-approved acceptance scope revision (2026-10-26): tests requiring NEW accounts are skipped and non-blocking for OTP-23. Not PASS; no fresh or standard-account evidence is claimed. | — | **NOT RUN** |
| W-02 | Host creates no window and communicates only through Chrome-owned stdin/stdout | Zero top-level windows and zero network endpoints owned by the host and its wrapper during connect, transfer and shutdown. | — | — | **NOT RUN** |
| W-03 | Extension-owned search receives bounded device events | start_scan yields device_found events within the documented bounds; stop_scan ends them. | — | — | **NOT RUN** |
| W-04 | Two phones independently exchange synthetic complete records (fresh build of this branch) | Byte-exact replies per original handle for both phones; distinct handles; MTU before CCCD. | — | — | **NOT RUN** |
| W-05 | Attribute-budget boundaries at ATT MTU 517 with the revised contract | Byte-exact on both phones for 4, 177/178/179, 507/508/509, 1015/1016/1017, 4095/4096 bytes; no Android exception; fragments <= 512 bytes. | — | — | **NOT RUN** |
| W-06 | Disconnect affects only its handle | Stale handle rejects sends; the surviving phone still exchanges a 4096-byte record. | — | — | **NOT RUN** |
| W-07 | Chrome port closure stops scans, disconnects links, closes stdout and leaves no process | Host and wrapper gone after port.disconnect(); BLE links released. | — | — | **NOT RUN** |
| W-08 | stdin EOF stops scans, disconnects links, closes stdout and leaves no process | Same cleanup after the extension stdin pipe closes without a shutdown request. | — | — | **NOT RUN** |
| W-09 | shutdown stops scans, disconnects links, closes stdout and leaves no process | shutdown_complete is the last message; host gone. | — | — | **NOT RUN** |
| W-10 | Unsupported or denied radio/permission states are classified | Bluetooth off, adapter absent and access-denied each return a classified error with the documented recoverability. | — | — | **NOT RUN** |
| W-11 | Strict background delivery, COLD idle (no prior exchange) | Connector hidden >= 10 min with another ordinary tab focused, NO DevTools client of any kind, no audio/WebRTC/keepalive; both phones' unsolicited 4096-byte records arrive byte-exact while hidden (scripts/strict_background.py --scenario cold). | — | — | **NOT RUN** |
| W-12 | Strict background delivery, PREWARMED link (separate run) | Same as W-11 after one byte-verified exchange per phone (--scenario prewarm). Reported separately; never combined with W-11. | — | — | **NOT RUN** |
| M-01 | Unsigned development-registered host launches from Chrome in a FRESH STANDARD non-admin macOS account (new-account test) | Chrome starts the host; no administrator involvement; a disposable Chrome profile with its own Native Messaging manifest. | (waived, not run) User-approved acceptance scope revision (2026-10-26): tests requiring NEW accounts are skipped and non-blocking for OTP-23. Not PASS; no fresh or standard-account evidence is claimed. | — | **NOT RUN** |
| M-02 | TCC Bluetooth prompt owner (Chrome vs host) in a FRESH account (new-account test) | Records which process the prompt/grant attaches to and whether a prompt fires for the headless child. (Historical: grant owner unknown; not formal attribution.) | (waived, not run) User-approved acceptance scope revision (2026-10-26): tests requiring NEW accounts are skipped and non-blocking for OTP-23. Not PASS; no fresh or standard-account evidence is claimed. | — | **NOT RUN** |
| M-03 | Signed production-shaped host (stable bundle identity, usage descriptions, final identifier me.nagaev.veles.bridge) with signing provenance | Directly signed fixture with the final bundle identifier and recorded signing provenance; prompt owner observed on the existing account. NOT waived. (Historical Apple Development me.nagaev.veles.otp23.poc is not proof of the production identity.) | (Mac has Apple Development only: no Developer ID and no final-identifier fixture exists; needs the final identity/credentials plus a human-observed prompt on the existing account) | — | **BLOCKED** |
| M-04 | DENY result and exact System Settings retry guidance | Deny produces access_denied with exact System Settings guidance; the prompt owner (Chrome vs host) is recorded as observed on the existing account; no TCC reset used. | (requires a human to deny the prompt on the existing account (account type to be recorded truthfully as admin or standard)) | — | **BLOCKED** |
| M-05 | ALLOW result | Allow yields allowed_always and discovery works for the same binary. | (requires a human to allow the prompt on the existing account (account type to be recorded truthfully as admin or standard)) | — | **BLOCKED** |
| M-06 | Recovery after deny -> allowed_always with the SAME extension state, no reinstall | After granting in System Settings, search recovers without reinstalling and without losing extension state. | (requires a human grant on the existing account with the same Chrome profile/extension state across the transition (account type recorded truthfully)) | — | **BLOCKED** |
| M-07 | Two phones independently exchange synthetic complete records incl. attribute-budget boundaries (fresh build) | Byte-exact for the W-05 size list on both phones using the measured max write-without-response (512 -> attribute 512, payload 508; no invented MTU). | — | — | **NOT RUN** |
| M-08 | No window; port closure, stdin EOF and shutdown each leave no process | As W-02, W-07, W-08, W-09 on macOS. | — | — | **NOT RUN** |
| M-09 | Strict background delivery, COLD idle | As W-11 on macOS. | — | — | **NOT RUN** |
| M-10 | Strict background delivery, PREWARMED link (separate run) | As W-12 on macOS. | — | — | **NOT RUN** |
| A-01 | Interop defects found by the physical runs are fixed or recorded (#120 M3 duplicate STATE_CONNECTED, M4 CCCD/MTU ordering) | Duplicate connected callback keeps a healthy link; MTU-before-CCCD observed on Windows and macOS clients; CCCD-before-MTU rejected with record I/O gated. | — | — | **NOT RUN** |
| A-02 | #120 M6: two CENTRALS on ONE phone (independent 4096-byte downstream records, no cross-peer disconnect) | Explicitly different from two phones on one central; not discharged by W-04/M-07. | — | — | **NOT RUN** |
| D-01 | Current Tauri-vs-plain dependency, SBOM and size evidence justifies (or not) retaining Tauri solely for OTP-25 packaging | A recorded current comparison (dependency graph, SBOM, binary size, packaging proof) that justifies the choice. Historical 1a34191 costs are kept separate and are not current evidence. | (dependency graphs measured today (evidence/current-dependency-cost.json) but current Windows/macOS binary sizes, SBOMs and a packaging proof need the coordinating agent's builds; no justification is asserted) | — | **BLOCKED** |
| D-02 | Explicit OTP-23 feasibility decision recorded after all rows are resolved | GO only when every row above is PASS, except NOT RUN rows carrying the user-approved account waiver (non-blocking, never PASS); otherwise NO-GO for OTP-24. | — | — | **NOT RUN** |

## Sources

- **RFC OTP-23 acceptance criteria** — docs/rfcs/2026-08-21-otp-external-devices.md, task OTP-23 acceptance criteria and verification evidence
- **user directive 2026-10-26** — revision request recorded in docs/otp-23-contract-alignment.md (strict background, attribute budget, macOS identity, plain runtime)
- **user steering 2026-10-26** — account waiver: tests requiring new accounts are non-blocking for OTP-23; recorded as an acceptance scope revision in the RFC
