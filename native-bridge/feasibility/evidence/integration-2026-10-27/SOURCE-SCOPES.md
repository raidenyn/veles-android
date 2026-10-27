# Report copies — exact sources, scope and fingerprints (2026-10-27)

All files in `reports/` are byte-identical copies of the session reports on disk in
`/tmp/opencode/` at capture time (2026-10-27 ~23:1x UTC, this GLM-5.3-flash integration session;
`cmp` verified for each). They are REVIEW RECORDS, not new claims: the review opinions and their
fingerprints belong to their authors and are unchanged here.

| Copy (reports/) | Author session / model | Written at | What it covers |
|---|---|---|---|
| otp23-windows-final-comparison-result.md | GLM-5.3-flash Windows session (ollama-cloud; opencode metadata captured separately in provenance/) | 2026-10-27 ~17:2x UTC | awaited-close fix + compare-2 physical comparison result |
| otp23-windows-final-opus-review.md | Claude Code Opus session `2fe20385-5ef1-4622-bf94-00b8c0c6477e` (`claude-opus-5-5`, CLI 2.1.311, 0 subagents) | fingerprint 2026-10-27T17:27:11Z | independent Windows review; I-A/I-B minimum fixes; W-07/W-08 stay BLOCKED |
| otp23-mac-review-fixes-result.md | GLM-5.3-flash Mac finalize round 2 (`ses_e81bc7103ffe3F21OMCsqIRppL`) | 2026-10-27 ~17:2x UTC | F-1/F-2/F-3 fixes executed; v2 adjudications; provenance capture |
| otp23-mac-final-opus-review.md | same Opus session | fingerprint 2026-10-27T17:13:24Z | independent macOS review; F-1/F-2/F-3 minimum fixes; M-09/M-10 conditional recommendation |
| otp23-mac-final-fix-verification-opus.md | same Opus session | fingerprint 2026-10-27T23:06:59Z | fix verification; FINAL disposition: M-09 (cold), M-10 (prewarm) PASS with residuals (a)-(d); NO-GO unchanged |

## Tree fingerprints at integration time

- Worktree `/home/dev/repos/veles-android`, branch `contract/otp23-desktop-alignment`, HEAD `dc5d127`,
  all work uncommitted (nothing committed/pushed/GitHub by this session).
- `git diff` sha256 at session start (byte-equal to the fingerprint captured by the Mac fix
  verification report and the Windows review): `a4a47935fb8c5dd89c549259e6a00ba421804f62242e41d0bd5af58ae7638e14`.
  This session's own edits (see FINDING.md §6, the matrix, docs and this folder) change the tree
  AFTER that capture point; the pre-edit state is what the reviews verified.

## Scope of the current phase

- INTEGRATED here: Windows I-A (driver tests + stricter run validity + derived recheck artifact)
  and I-B (fixture-account text correction); M-09/M-10 promoted per the Opus fix verification;
  checksum repair (strict-mac-20261027 manifest; windows-cleanup manifest refresh).
- NOT claimed by this folder: no full final review of the integrated tree has happened yet — the
  parent performs the final gate + review before commit/push (R-01's new-phase ledger entry stays
  pending until then). Latest-Chrome-scope: the current investigations used Chrome **156.0.8078.48**
  only (Windows and macOS); no Chrome 155 build exists on any machine and no old-build rerun is
  claimed or planned. Root untracked `.otp23cal` artifact: NOT present in `git status` (absent; the
  prior agent's cleanup state is as recorded).
- Model provenance: `provenance/` holds the opencode-db captures (actual metadata, not self-report):
  this integration session `ses_e8061b402ffeVie5DWcDyfZ48B` = glm-5.3-flash / ollama-cloud; prior GLM
  implementer sessions are copied byte-exact from /tmp/opencode; the reviews are local Claude Code
  Opus (`claude-opus-5-5`) sessions; the coordinating parent session is gpt-6.1-sol and is NOT the
  implementation.
- `.gitattributes` here (and in `reports/`) is `* -text -whitespace`: these copies stay byte-exact.
- Discoverability note (final Opus review MINOR): the raw capture
  `../../post-grant-2026-10-26/strict-mac-20261027/f2-macos-host-bundle-codesign.txt` contains the
  wording "as registered 16:10:56Z" while the parent transcript's captured registration event is
  16:04:16Z — the discrepancy is explained in the retained copies `reports/otp23-mac-final-fix-verification-opus.md`
  (Minor integration note) and `reports/otp23-mac-review-fixes-result.md` (F-2); the RAW file is
  byte-unchanged and is NOT edited.