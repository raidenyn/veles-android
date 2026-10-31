# Focused independent OTP-23 evidence adjudication — Kimi K3.1 Pro

**Reviewer:** actual `kimi-code-plan-global/k3.1-pro`  
**Scope:** independent evidence adjudication only. No delegation, repository edits, hardware operations, code approval, commit, push, or OTP-24 authorization.  
**Overall evidence decision:** **APPROVED** for M-04, A-01 and W-10.  
**Program disposition after adoption:** **28 PASS / 1 BLOCKED (R-01) / 3 waived NOT RUN — NO-GO.**

## Per-row decisions

### M-04 — APPROVED (supports PASS)

The unchanged criterion is met by the new denial/recovery run plus the transparently referenced, already approved M-03 prompt observation:

- `m04/result.json` and `m04/state/host-trace.log` record a real native `scan_failed / access_denied / retryable` result after macOS reported Bluetooth authorization `denied`.
- The connector readback records the exact required Settings text, including System Settings → Privacy & Security → Bluetooth, `VelesOtp23PoC`, return/retry, and no reinstall/reset. It was visible in a visible, focused document, not merely a driver constant. The owner then answered “Saw guidance; switched ON”.
- Baseline, denied and recovered phases retain Chrome PID 25216, the disposable profile, extension ID, sentinel, signed binary `e7c716c5…eb91c`, Team `7T39M26Z99`, and one extension load. Recovery produced scan start and one discovery, cleared guidance, stopped scanning and completed shutdown.
- The M-04 run records no TCC reset, extension reload, re-signing or reinstall. Its same-state recovery does not borrow or widen the separately approved M-03 reset.
- The raw M-04 prompt remains honestly `unknown`, with popup owner `not inferred`. The prompt clause is satisfied by the separately reviewed same-identity existing-account observation in `native-bridge/feasibility/evidence/mac-m03-prompt-2026-10-30/ADJUDICATION.md` and its `owner-record.json`; no new popup observation or exact owner spelling is fabricated.
- Profile removal, host termination and registration removal are recorded in `m04/result.json` and `m04/wrapper-cleanup.json`; the binary digest is unchanged.

**Informational residual:** M-04 itself contains no new prompt observation. The M-03 operator-captured spelling/screenshot residuals remain as already adjudicated; they are not rewritten or waived here.

### A-01 — APPROVED (supports PASS)

All three clauses are supported:

1. **Duplicate connected callback / healthy link.** `windows-first/capture/a01-m3.json`, `controller/a01-m3-phone-ack.json` and `controller/a01-m3-server-final.json` bind the same run and nonce. The instrumentation explicitly injected the real callback with the already admitted real `BluetoothDevice` on a live physical link; this was not a mock and is not represented as a natural OS repeat. Before/after acknowledgement preserves peer, ready/subscribed state and the original admission timestamp. The same native handle exchanged byte-exact 16-byte and 4096-byte records before injection and a different byte-exact 4096-byte record afterward. The server retained exactly three received records and one labelled injection.
2. **Normal MTU-before-CCCD order.** The fresh Windows M3 trace records `mtu=517` before `subscribed`. For macOS, both immutable phone sources named by `mac-phone-order-corroboration.json` matched their declared SHA-256 and the accepted run ID `mac-m08-shutdown-20261029T043432Z`; on both, actual Android `mtu` code 517 is sequence 2 and `subscribed` is sequence 3. This is phone-side event evidence, not an MTU inferred from CoreBluetooth max-write.
3. **CCCD-before-MTU rejection and gated I/O.** The matching retake3 client/server records show real radio CCCD status 6 before any MTU callback. The early valid framed write produced no notification during the explicit 300-ms negative observation and the server had zero delivered records while not ready/unsubscribed. MTU 517 then succeeded, the next CCCD succeeded, and the valid record received the expected 8-byte reply; the server delivered only one record. The causal test section confirms the early frame, 300-ms poll and subsequent valid exchange.

The first SCAN failure and second ordinary-permission/locked-phone failure remain retained as FAIL captures and receive no credit. The passing fixture used the external opt-in SCAN manifest and ordinary runtime permission flow. Install/restoration records show the same certificate `2081d17d…8506e` for old and new APKs, only isolated `.otp23` packages, passing main/test digests `0e32c572…57649b` / `5a8e9e30…bd1b41`, and restoration of original main/test digests `063ac7ea…677a5e` / `8ca89381…ede1c` on both phones using same-key `install -r -t`, without uninstall or data clearing.

**Informational residuals:** M3 proves controlled duplicate-callback handling, not natural Android duplication. The M4 negative observation is bounded to 300 ms, not a universal no-delivery claim. Mac normal-order corroboration intentionally reuses immutable accepted M-08 evidence rather than a new Mac run.

### W-10 — APPROVED (supports PASS)

Each required OS state has a matching classified result and documented recoverability:

- **Bluetooth off:** `state-RadioOff-20261030-033508-601.json` records On → Off before `radio-off.json` returned `scan_failed / radio_off / retryable`. `state-Restore-20261030-033516-385.json` records Off → On after capture.
- **Adapter absent:** after the attended reboot, the disable record shows adapter Error and zero enumerated radios; `restored-persistent-enabled-20261030-050316-677.json` independently records problem code 22. `adapter-absent-v2.json` returned `scan_failed / no_adapter / fatal`. Restoration returned the adapter to OK/radio On.
- **Access denied:** `exclusive-direct.json` records typed SDK service discovery Success, `OpenAsync` Success, SharingMode Exclusive and notification Success for only the synthetic Veles GATT service. Its holder interval contains the native `connect_failed / access_denied / fatal` observation in `access-held.json`. This is an OS GATT exclusive-access denial, not a Windows privacy-policy denial. After holder PID 54712 exited, `access-recovered.json` used the same target digest as `access-baseline-v2.json` and exchanged the same byte-exact 4-byte reply.

The retained pre-reboot `windows-first/capture/adapter-absent.json` actually started a scan and is invalid absent-adapter setup; it receives no credit. Earlier holder-construction ERROR records likewise receive no AccessDenied credit. Final records show registration absent, no owned/holder processes or tasks, adapter ConfigFlags 0, problem code 0 and radio On. `FINDING.md` records that both hardware sessions were ended; this review did not contact hardware to reverify that operational statement.

**Informational residual:** `fatal` is the request classification. Recovery occurred after external holder release and a new request; it is not an automatic-retry promise.

### R-01 — BLOCKED (publication/review hold)

The expected text remains unchanged: a completed `claude-opus-5-5` review of the committed tree, no remaining CRITICAL/IMPORTANT findings, and every earlier finding dispositioned.

The historical committed-tree PASS cannot be silently applied to this candidate. Fresh `git status --porcelain=v1` inspection found 16 worktree entries (13 tracked changes and 3 untracked), and the separate focused Kimi code review is not completed by this evidence review. Earlier Opus reviews do not cover the new connector/driver/instrumentation code. Therefore R-01 must remain **BLOCKED** pending the final committed-tree/publication review and the separate code approval. Preserve the existing expected text, waivers and history; do not invent Opus authorship or convert this evidence decision into OTP-23 acceptance or OTP-24 GO.

## Provenance, integrity and privacy

- Fresh read-only Python verification checked all **154/154** declared provenance copies: source bytes, published bytes, SHA-256 values and byte-identity flags matched; zero failures.
- Both declared M3 redactions were reconstructed exactly by replacing only `connected.platform_hint` in the published bytes with the protected original value. Both original and published SHA-256 values matched, and every other byte was identical. Private values were not printed.
- Both referenced immutable M-08 phone files matched their declared hashes and run binding.
- No old frozen evidence was edited by this review. Protected originals remain local, not an off-host backup. This report contains no private original platform hints, full API errors/headers, raw model transcripts, keys or personal application inventory.

## Defects and verification boundary

**Physical-evidence defects:** none found; consequently there are no CRITICAL/IMPORTANT evidence-defect paths to report.  
**Blocking publication gate:** R-01 at `native-bridge/feasibility/acceptance/otp23-matrix.json`, for the reasons above.

I ran bounded, read-only Python consistency/hash checks over the named evidence. I did not run or claim the parent's Gradle gates, 514 Python/5-skip or 3 Node checks, and I did not perform the separate code review.

## Bounded mechanical integration authorization

**Authorized without another substantive physical-evidence review, conditional on the separate focused Kimi code approval.** Integration may only:

1. Append truthful `actual` / `source` / `evidence` / D02 records for the approved M-04, A-01 and W-10 evidence and set those supported statuses to PASS.
2. Record the honest R-01 BLOCKED hold and resulting **28 PASS / 1 BLOCKED / 3 waived NOT RUN, NO-GO** counts.
3. Preserve all expected text, existing waivers and history; leave old frozen evidence unchanged.
4. Regenerate the existing renderer outputs and affected docs/RFC/report artifacts, include this report with safe actual model metadata and mechanically computed checksums, and run the current renderer tests, raw-integrity invariants and other affected checks.

No new app/proof implementation, expected-text or waiver change, old frozen-evidence edit, commit, push, OTP-23 GO or OTP-24 authorization is included. R-01 still requires its final committed-tree/publication review.
