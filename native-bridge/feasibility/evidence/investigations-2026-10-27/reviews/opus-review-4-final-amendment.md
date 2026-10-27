# OTP-23 final amendment and delta review (Opus)

**Reviewer metadata (read from the transcript on disk):** Claude Code session `2fe20385-5ef1-4622-bf94-00b8c0c6477e`.
- Every assistant message is `claude-opus-5-5` (261 at capture). CLI `2.1.311`. 0 subagents.
- Transcript sha256 at capture: `64a4152a9682b2cf766796c5c1ad358d1219957e49e15edff17f78d9b017fd1e` (the file keeps growing).
- Read-only. The only file I wrote is this report. I used no hardware and made no GitHub changes.

## Exact tree evaluated
- Worktree `.claude/worktrees/otp23-investigations`, HEAD `cab0438` plus uncommitted changes.
- Fingerprint, computed as `sha256( git diff cab0438 ; sha256sum <every untracked file, sorted> )`:
  **`602a2fbf8e2271efff6f306100b8efc739922526a12f2af976271faa0d7992e4`**. I computed it at 14:25:12Z and again at 14:25:57Z, and it matches `/tmp/opencode/otp23-ready-for-commit.md`.
- The delta reviewed is everything since my last fingerprint, `5b8b5679…9ca0`. This verdict covers only `602a2fbf…92e4`.

## Checks run
- Feasibility Python suite: 321 tests OK.
- `matrix.py check`: OK. `matrix.py decision`: NO-GO, with 13 rows open (W-02, W-07, W-08, W-10, W-11, W-12, M-03, M-04, M-08, M-09, M-10, A-01, M-11) and only W-01, M-01 and M-02 account-waived.
- Re-rendering the matrix document into a scratch copy was byte-identical to the committed-to-be document, so the render is a no-op.
- Evidence `SHA256SUMS`: 256/256 OK; the list matches the files on disk. `cargo fmt --check`: OK.
- The three earlier Opus reports under `evidence/.../reviews/` are byte-identical to my originals in `/tmp/opencode/`. Each is labelled with the earlier tree it evaluated.
- Not rerun, per your instruction: Gradle gates, clippy, hardware. The `gates/r5-*` logs record them passing.

## Amendment scope (RFC is authoritative)
- **The background clarification is verbatim and narrow.**
  - The quoted sentence in the owner record appears exactly once in the RFC, character for character, including the typographic apostrophe.
  - The only edit to the acceptance criterion is a pointer, "(as clarified by the 2026-10-27 background-criterion revision below)", in the **OTP-23** criterion (RFC line 1559). The OTP-08 and Gate B wording are untouched.
  - The clarification paragraph says it applies to OTP-23 only and keeps everything else:
    - separate cold-idle and prewarmed runs of at least ten minutes;
    - the strict-run requirements and the foreground evidence; **macOS ST-15 is explicitly not waived**;
    - freeze and discard requirements (owned by OTP-08), and the evaluator-provenance and `formal_otp23_acceptance` rules;
    - the NO-GO rule; it promotes no row and approves nothing about OTP-24.
  - It states plainly that Chrome 155 was inspected in source only, with no fresh 155 probe. This matches the evidence: both probes ran on Chrome 156.0.8078.48, on Windows and on macOS.
- **The chronology is preserved.** `…background-wording-decision.md` ("REQUIRE MORE EVIDENCE FIRST") is kept unchanged. `…background-clarification-approval.md` records the later approval verbatim, says it is later, and is operator-captured, not a GitHub approval. The matrix `sources` carry both entries in order.
- **The signing minimum is retained and pinned.** The Apple Development fixture is pinned by Team `7T39M26Z99`, executable sha256 `e7c716c5…eb91c` and CDHash `25bc7fc2…`. Any rebuild or re-signing counts as a new instance that needs its own decision. CI and distribution signing stay deferred to OTP-25.
- **No automatic promotions.** Compared with `cab0438` there are 0 status changes.
  - Changed rows: W-07, W-08, W-11, W-12, M-03, M-04, M-09, M-10 (text only).
  - The W-11, W-12, M-09 and M-10 blockers now say the criterion text is clarified but the rows are not promoted. A formal re-adjudication is still needed under the existing rules: `formal_otp23_acceptance=false`, ST-13, the operator-bound phone files and evaluator provenance.
  - M-09 and M-10 also keep the missing ST-15 evidence as a blocker, not waived.
- **OTP-24 is not authorised.** Section 4 of the investigations doc is a recommendation only. Its minimum owner action is a single gate decision, with existing owners and no new tasks.

## My earlier corrections C-1 to C-3: applied
- **C-1.** The W-07 and W-08 blockers now say that the Chrome-provenance port closure with two links passed three times **only on the non-adopted identity `e36328d2…`**. The retained `6cf72ed0…` was not run through Chrome, and there is no automatic promotion. The per-device `access_denied` after a reconnect within about 1–4 s is carried to #87/#88. Its cause, Windows versus phone, is still unlocalised. W-08 also notes that a Chrome-owned stdin EOF without a shutdown request was not run separately. The stale wording is gone and a test pins the new wording.
- **C-2.** The scope of the "Not shown" list is fixed. The doc now states the Windows throttling onset (~59 s) and the macOS onset (~5 min), and the measured relaunch gaps (about 2.4 s and 1.6 s against the configured 1 s).
- **C-3.** `diag-binary.txt` and doc §2.6 state the limits of the host-hash binding for C1–C3, R1b and R2b.

## Residual notes (non-blocking; no action needed for publication)
- `strict_background_core.py` still describes the Native Messaging port as possibly an exemption that is "not excluded", and ST-13 is still flags-only. That is consistent with "no promotion". The matrix and RFC explain it, and any re-adjudication (#90) should update that text.
- The preserved earlier decision record says the sentence "is not inserted into the RFC". That is true for its time, and the later record supersedes it explicitly. Keep both as they are.

## Verdicts (separate)
- **Publishable investigation, harness and governance package: YES**, at fingerprint `602a2fbf…92e4`. There are no remaining CRITICAL or IMPORTANT findings.
  - Commit exactly this tree.
  - After committing, verify a clean `git archive` export of the evidence folder against `SHA256SUMS`.
  - Rerun the CLAUDE.md gates on the committed tree.
  - Do not renormalize the evidence folder.
- **OTP-23 acceptance: NOT satisfied. The decision stays NO-GO.** 13 rows remain BLOCKED and none was promoted. Approving the clarification and the signing minimum changes criterion text only, and ST-15 is not waived. **OTP-24 is not authorised.**
