# OTP-23 investigations — Opus re-review

**Reviewer (actual, read from transcript on disk, not self-report):** Claude Code session `2fe20385-5ef1-4622-bf94-00b8c0c6477e`;
every assistant message `claude-opus-5-5` (215 at capture); CLI `2.1.311`; 0 subagents; transcript sha256 at capture
`000f31b753298ae8253a1a0e0259f8fd27dafeb54409c60c01465943ff411efe` (the file keeps growing).
**Mode:** read-only. No code/doc/evidence edits, no hardware, no GitHub. Only side effect: `cargo clippy` wrote ignored build output under `native-bridge/feasibility/target/`.

## Exact tree evaluated
- Worktree `.claude/worktrees/otp23-investigations`, branch `worktree-otp23-investigations`, HEAD `cab0438` + **uncommitted** changes (9 modified tracked files, 146 untracked).
- Fingerprint `sha256(git diff cab0438 ; sha256sum of every untracked file, sorted)` =
  `853f7d1fa09daca822012a93cae30125276850b712c0f350968cfedc773908b5`. Same value at 13:04:34Z and 13:07:13Z (no change while I reviewed).
- Evidence `investigations-2026-10-27/SHA256SUMS` (sha256 `efebd8a5…0ef1`): 136/136 OK. Last evidence write was at 10:25 local.
- **No Windows interactive-Chrome evidence exists in this tree.** Anything Sonnet produces after this fingerprint has not been reviewed.

## Checks I ran independently
- Feasibility Python suite: 320 tests OK. Targeted `DirectHostReplyOrder`, `test_timer_probe_analysis` and `test_matrix`: 39 OK. `matrix.py check`: OK (NO-GO).
- `cargo fmt --check` OK. `cargo clippy --target x86_64-pc-windows-msvc --locked --all-targets -D warnings` OK.
- From the previous pass, still valid: the reply-fix tests fail against `cab0438`'s `reply()` and pass after.
- I did not rerun the Gradle gates. `gates/r2-02` records BUILD SUCCESSFUL on this tree.

## Verdict
No CRITICAL findings. Three IMPORTANT findings remain (all doc or record corrections, none are code defects), plus minor items. NO-GO is correct and unchanged.
Reviewer approval of this investigation package is **contingent** on:
- the IMPORTANT items below;
- review of the final Windows evidence, which does not exist yet.

## What holds up (verified against raw files)
- **Request-history fix.** `reply()` returns an already-seen frame with the matching `request_id` first. Ids are unique per host (`d{n}`), so a frame cannot be replayed to a different request. The tests cover out-of-order replies, error replies and non-replay.
- **One-phone controls K1–K5.** Raw traces and `result.json` agree with the table. Label-hash to model mapping is consistent: K3's reply list gives 6T connected and 10 Pro denied in one process.
  - The diagnostic lines show `services GattCommunicationStatus=0`, then the write-UUID characteristics query `GattCommunicationStatus=3 protocol_error=none`, all from the diagnostic binary `186f920c…0a64`.
  - This rules out a global state and supports a per-device one. The doc correctly leaves Windows-vs-phone unproven.
  - One more inference is worth adding: status 3 with no ATT protocol-error byte means Windows returned AccessDenied rather than a surfaced ATT error response. That favours a Windows-side decision but does not exclude phone-side state as a contributor.
- **Timer probe** (macOS, Chrome 156.0.8078.48). I recomputed the gaps from the raw logs:
  - Both tabs ran about 2 s gaps until a 47 s step ending about 346 s after hiding, then 60.003, 59.996 and 59.999 s.
  - The port tab received 57/57 host events (host log: 57 sends, then `stdin_eof`), max lag 4 ms, no disconnects.
  - The negative conclusion does not depend on the control tab: the port tab itself was intensively throttled. That holds even if both pages share one extension renderer process.
  - On "no DevTools during idle": `run_probe.py` closes the only browser-level WebSocket before the idle sleep and attaches to pages only before (setup check at +25 s) and after. That is supported by the driver code. No external confirmation was taken during idle (e.g. no attached clients), and the doc should say so.
- **Signing amendment.** It matches the relayed approval:
  - OTP-23 only; existing Apple Development final-ID fixture accepted; CI and distribution signing moved to OTP-25.
  - It does not approve OTP-24, and does not waive M-04, M-11 or any background gate. M-03 stays BLOCKED on the prompt owner.
  - It is recorded as operator-captured, not a GitHub approval. The narrowness test exists and passes.
  - The designated-requirement readback confirms a CN-pinned requirement, so the OTP-25 carry-over note on revalidation is correct.
- **Raw phone dumps (privacy).** No accounts, emails, keys or tokens found. MAC addresses are already redacted by Android to their last two bytes.
  - Residual personal metadata: bonded-device names (e.g. "Mac"), the device model names, partial MAC suffixes, app package and uid names.
  - Acceptable for evidence. Note it in README as "unredacted beyond Android's own masking".

## IMPORTANT
**I-1. The owner decision is not recorded; the docs still present it as an open question.**
- The owner chose "REQUIRE MORE EVIDENCE FIRST" on the background wording. `otp-23-investigations.md` §1.5 and "Concrete human blockers" item 2 still ask "accept … or require the Windows/155 probe".
- **Correction:**
  - Record the decision, operator-captured and verbatim, like the signing approval.
  - State that the §1.5 sentence stays unapproved, and that W-11, W-12, M-09 and M-10 are unchanged.
  - Name the evidence still required (see "Rerun required" below), at minimum the same probe on Windows Chrome.
  - Do not describe the macOS/156 probe as satisfying the owner's request.

**I-2. The diagnostic `winrt.rs` change breaks the source/binary story unless it is pinned.**
- The tree now changes the feasibility host source. Matrix rows and docs bind Windows evidence to the retained binary `6cf72ed0…fcda`, built from the unchanged `cd971f0` sources.
- The diagnostic binary `186f920c…` was built from a pre-`cargo fmt` copy, so it is not the committed source. The reflow shifts line numbers, which plausibly changes the bytes.
- **Correction:** pick one.
  - (a) Keep the trace lines. Rebuild the diagnostic binary from the final committed tree and record its hash. State in the docs and matrix sources that `6cf72ed0` corresponds to `cd971f0`/`cab0438`'s `winrt.rs`, not to HEAD.
  - (b) Move the patch into evidence as a `.patch` and leave `src/` unchanged.
- In either case, the pending Windows Chrome W-07/W-08 runs must name which binary they used. For acceptance rows, prefer the retained `6cf72ed0`, or record a new host identity and say which earlier rows it re-baselines.

**I-3. Duplicate section number in `docs/otp-23-contract-alignment.md`.**
- The new "## 11. Signing-provenance amendment…" duplicates the existing "## 11. Review fixes…". Sections already run to 15, and other records cite "alignment section 11".
- **Correction:** renumber the new section to "## 16" and update any reference to it.

## MINOR
1. **`protocol_error_text`** maps every `Err` to `none`. That conflates "no protocol error" (null reference) with an accessor failure. Rename the output to `none_or_unavailable`, or distinguish the two. Diagnostic only.
2. **One-phone controls used only the 10 Pro as the repeated phone.** There is no 6T→6T case. Single-phone denial on the 6T is inferred from the earlier two-phone runs. Each control is n=1. The wording "a phone that the previous process had linked was refused" should say "observed for the 10 Pro (K1, K3, K4); the 6T only in two-phone runs".
3. **Timer probe residuals to state.**
   - The browser pid was not found, so the launch flags were not captured, and the cleanup fallback (pid-scoped kill) could not work. Cleanup relied on `Browser.close`; add an after-run process listing for the profile.
   - Only 4 throttled samples. Both tabs were hidden from creation, so `hidden_start_epoch_ms` is about 0.2 s after the pages hid; this is harmless.
   - Chrome window visibility and occlusion were not recorded.
4. **The narrowness test matches RFC text across a hard line wrap** (`"does not\napprove OTP-24"`). It is brittle against reflow; match on normalised whitespace.
5. **Pin the fixture instance.** The amendment says "the existing … fixture" but does not pin it in the RFC. Add the executable hash `e7c716c5…` and CDHash `25bc7fc2…` (or reference the readback). Any rebuild or re-signing (e.g. a final `CFBundleName` for the M-04 guidance) would then clearly need a new decision.
6. **The matrix W-07 entry still has the missing space ("trigger.No")** if the earlier sentence was kept; check after the rewrite.

## Rerun required / evidence still needed (for final approval; deferred backlog not required)
1. **Windows, interactive session, Chrome-launched W-07 `LC-port-disconnect` and W-08 stdin EOF with two links.**
   - Record Chrome version and channel (155.0.8059.85, or whatever is installed), the console session, the host binary hash and the scenario order with timestamps.
   - If an inter-scenario quiescence gap or phone-side disconnect check is added, declare it as a test precondition with its justification. Do not present it as a fix. The rapid-relaunch denial stays a carried-forward defect for #87/#88.
   - A pass obtained by retrying is not acceptable.
2. **Windows timer probe** on the installed Chrome. This needs a Windows port of `run_probe.py`, since the current one uses `open -na`, `pgrep` and `ps`.
   - Same design: port tab, port-less control tab, all CDP clients closed for ≥ 540 s, raw logs kept.
   - If Windows Chrome has updated past 155, say so; 155 is then covered by the pinned sources only.
3. After 1 and 2, apply I-1 to I-3, recompute `SHA256SUMS`, and rerun the CLAUDE.md gates on the final tree before any commit or PR.
4. I will re-review the final fingerprint. Approval applies only to that exact tree.

## Unchanged and correct
No matrix status changed, NO-GO holds, and only W-01, M-01 and M-02 are waived. M-03, M-04, M-11, W-11, W-12, M-09 and M-10 stay BLOCKED. OTP-24 is not authorised.
