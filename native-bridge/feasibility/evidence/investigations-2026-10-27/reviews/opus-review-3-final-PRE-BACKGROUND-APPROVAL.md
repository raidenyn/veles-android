# OTP-23 investigations — final Opus review

**Reviewer metadata (read from the transcript on disk):** Claude Code session `2fe20385-5ef1-4622-bf94-00b8c0c6477e`.
- Every assistant message is `claude-opus-5-5` (245 at capture). CLI `2.1.311`. 0 subagents.
- Transcript sha256 at capture: `a66bff2df186691359dd93ed1fc7afdf80b5cae17cf13b606ea0ab0049e710ce` (the file keeps growing).
- Read-only. My only writes are this report and ignored `target/` build output from running `cargo clippy`.

## Exact tree evaluated
- Worktree `.claude/worktrees/otp23-investigations`, branch `worktree-otp23-investigations`. HEAD `cab0438` plus **uncommitted** changes: 9 modified tracked files and the untracked docs, scripts, timer probe and evidence.
- Tree fingerprint, computed as `sha256( git diff cab0438 ; sha256sum <every untracked file, sorted> )`:
  **`5b8b56798c7ad17f435f62a712858dd6093a94bc98a8eceffb61c0bf3b379ca0`**. Identical at 14:11:46Z and 14:13:08Z.
- Evidence `investigations-2026-10-27/SHA256SUMS` (sha256 `a6079035…81fbe`): 230/230 OK. The file list matches the files on disk exactly.
- This approval applies only to this fingerprint. Any edit, including the correction below, needs a fingerprint recheck. A wording-only fix needs no new review.

## Independent checks
- Feasibility Python suite: 320 tests OK. `matrix.py check`: OK (NO-GO).
- `cargo fmt --check`: OK. `cargo clippy --target x86_64-pc-windows-msvc --locked --all-targets -D warnings`: OK.
- `src/winrt.rs` sha256 is `d348d615…aed1d1`, matching `diag-binary.txt`.
- Matrix compared against `cab0438`:
  - No status changes. Changed rows: W-07, W-08, M-03, M-04.
  - Only W-01, M-01 and M-02 are waived. Decision: NO-GO.
- I did not rerun the Gradle gates. `gates/r3-*` records BUILD SUCCESSFUL.

## My earlier IMPORTANT findings
- **I-1 (owner decision): resolved.**
  - `owner-approvals/2026-10-27-otp23-background-wording-decision.md` records the relayed words verbatim, labelled operator-captured.
  - §1.5 says the wording is NOT approved and keeps the draft labelled as unapproved.
  - The RFC does not contain the sentence (grep count 0), and a test enforces that.
  - W-11, W-12, M-09 and M-10 are unchanged. The probes are presented as evidence, not as meeting the owner's request.
- **I-2 (source/binary binding): resolved.** `diag-binary.txt` binds three binaries:
  - `6cf72ed0` to the `cab0438` source;
  - `186f920c` to a pre-fmt copy, not claimed as final;
  - `e36328d2` built from the final `winrt.rs` (same hash on both machines).
  The Chrome lifecycle results record `e36328d2`. No row is re-baselined. The trace helper now prints `none_or_unavailable`.
- **I-3 (duplicate section): resolved.** The new section in `otp-23-contract-alignment.md` is now "## 16".

## Architectural and code conclusions, checked against the raw files
1. **The Native Messaging port is not a timer-throttling exemption on Chrome 156, on both macOS and Windows.**
   - Windows probe (`result-540s-hidden-tabs.json`): both tabs were hidden from load; this was checked at +25 s and the driver now aborts otherwise.
     - The port tab and the port-less control tab produced identical gap sequences.
     - Intensive throttling began about 59 s after hiding: the first long gap ended at 78.3 s, then gaps of 60.00 ± 0.01 s.
     - The port tab received 57/57 host events with a maximum lag of 1 ms, and had no disconnects.
   - macOS: same result, with the onset at about 5 minutes.
   - The negative conclusion does not need the control tab, because the port tab itself was throttled.
   - Chrome 155 is covered only by the source files pinned at that tag. Neither machine had 155 installed, and the doc says so.
   - Minor: the Windows onset (~59 s) is earlier than the 5-minute rule the analysis assumes. That doesn't affect the verdict, but the doc should state it.
   - "No DevTools during idle" rests on the driver code (it closes its only browser WebSocket before the idle period). The doc states this limit.
2. **The Windows access_denied refusal is per device and depends on recency; its cause is not localised.**
   - Captured: `GattCommunicationStatus=3` with no protocol error on the first characteristic query, for a phone linked moments earlier by a previous host process.
   - It is now reproduced through Chrome-launched `port.disconnect`:
     - R1b: the previously linked phone was denied while the new phone connected.
     - R2b: both phones denied.
     - Direct-launch repeats F1b and F2b agree.
   - The measured gap from the first host exiting to the second connect was about 1.6–2.4 s; the label "1 s" is the configured pause.
   - Chrome LC-port-disconnect C1–C3 passed with two linked phones. C2's connect came about 9–10 s after C1's host exited, consistent with the recency window.
   - Correctly not claimed: Windows versus phone localisation, the cause, and a threshold between 4 and 10 s. Every control is n=1. The 6T was never the repeated phone in a one-phone control.
   - Carry-forward to #87/#88 (a distinct retryable classification, a bounded reconnect policy, a physical retest) is right. No retry is presented as a fix.
3. **Harness `reply()` fix:** correct and covered by regression tests. The out-of-order replies were confirmed earlier in the raw evidence.
4. **Signing amendment:** matches the relayed approval exactly.
   - OTP-23 only: the existing Apple Development final-ID fixture, now pinned in the RFC by Team, executable hash and CDHash.
   - CI and distribution signing are deferred to OTP-25, whose carry-over note covers the designated-requirement change and revalidation.
   - M-03 stays BLOCKED on the prompt owner. M-04, M-11 and the background gates are untouched. OTP-24 is not approved.
   - The approval is recorded as operator-captured, not as a GitHub approval.

## Remaining corrections (wording only; none blocks publication)
- **C-1 (should fix before publishing).** The `blocker` fields of W-07 and W-08 still describe only the old retained-binary failures. W-08's says "no Chrome-provenance stdin-EOF-with-links proof", which is now out of date: a Chrome-provenance pass exists (C1–C3) on the non-adopted identity `e36328d2`. Proposed W-08 blocker: "Chrome-provenance port-closure with links passed only on non-adopted host identity e36328d2; awaiting the decision to retest with retained 6cf72ed0 or adopt the new identity; rapid relaunch within ~1–4 s yields per-device access_denied (carried to #87/#88)." W-07's blocker needs the same update. Status stays BLOCKED.
- **C-2 (minor).** §2.3 "Not shown … Chrome-launched path … interactive session" should be scoped to "in the direct-launch runs (see §2.6 for Chrome-launched)". Also state the Windows probe's ~59 s onset in §1.6.
- **C-3 (minor).** The R1b and R2b result files do not record the host hash. The binding rests on `diag-binary.txt` and the registered copy; say so.

## Verdict
- **The investigation package is publishable** as NO-GO investigation and evidence work. C-1 should be applied first; it is a text-only matrix edit, followed by a re-render and a checksum update if anything under evidence changes. Then rerun the CLAUDE.md gates on the exact committed tree before pushing.
- **This is not OTP-23 acceptance. NO-GO remains correct.** 13 rows stay BLOCKED, including:
  - M-03 and M-04 (prompt owner not observed);
  - M-11 (unsigned host not run);
  - W-07 and W-08 (host-identity decision or retained-binary retest pending);
  - W-11, W-12, M-09 and M-10 (background wording not approved; macOS foreground check ST-15);
  - W-02 and M-08 (connected-phase windows), W-10 and A-01.
- OTP-24 is not authorised. No deferred backlog is added by this review.
