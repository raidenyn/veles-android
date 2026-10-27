# OTP-23 follow-up — final integrated-tree review (Opus)

**Reviewer metadata (read from the transcript on disk):** Claude Code session `2fe20385-5ef1-4622-bf94-00b8c0c6477e`, every assistant message `claude-opus-5-5` (411 at capture), CLI `2.1.311`/`2.1.312`, 0 subagents.
- Read-only: no edits, hardware or GitHub. The only file written is this report.
- The one command I executed beyond reading was the integration's own verbatim Log-guard script, run from the repo root.

## Exact tree reviewed (2026-10-27T23:21:56Z)
- `/home/dev/repos/veles-android`, branch `contract/otp23-desktop-alignment`, HEAD `dc5d127`, all changes uncommitted.
- `git diff` sha256: **`f86ddf36afc369a339cab06ab32b9f5d9b8830509911df0890a8f280a46fa9c6`** (matches the ready report).
- Full fingerprint `sha256(git diff ; sha256sum of every untracked file, sorted)`: **`e69ae2a50eb0aa2dab060b814fb86f300a272d5faea5380ca1c0b23b63521cf5`**. Identical at 23:21:06Z and 23:21:56Z.

**Approved sources are unchanged against my prior reviews:**

| File | sha256 (prefix) |
|---|---|
| `winrt.rs` | `2ef50ec1…` |
| `closejoin.rs` | `25e44f6d…` |
| `lib.rs` | `79bbf21f…` |
| `strict_background_core.py` | `e44f616c…` |
| `strict_background_finalize.py` | `5a163880…` |
| `strict_background.py` | `478b836c…` |
| As-run Windows driver copy | `86781a91…` |

The physical producer (Rust) and the evaluator are unchanged, and no raw byte changed.

## Verified
- **Evidence manifests.** The active `SHA256SUMS` passes everywhere: strict-mac 37/37, windows-cleanup 49/49, post-grant 196/196, investigations 257/257, integration 23/23. The strict-mac and windows file lists equal the files on disk. `SHA256SUMS-v2` still passes 34/34. v1 is kept as `SHA256SUMS-before-review.original` (`3487728c…`), its non-active status explained in `SHA256SUMS-NOTES.md`.
- **Windows I-A.** The driver now has a pure `resolve_wanted` and a strict `run_validity`, which requires:
  - exactly 2 first-phase connections;
  - both second-phase attempts;
  - the gap within ±500 ms of the target.
  
  `test_windows_closejoin_compare.py` passes 24/24, including the compare-1 red/green shape. The derived `VALIDITY-RECHECK-20261027.json` records compare-1 **6/6 INVALID** and compare-2 **6/6 VALID**, with the driver hash and input `result.json` hashes. No raw was rewritten and nothing was re-run physically. The claim scope is unchanged.
- **Windows I-B.** FINDING.md §5 has been corrected:
  - **observed:** no JUnit result, and a test process outlived the first force-stop;
  - **"four stale sessions":** now labelled operator-inferred from local adb clients only;
  - **uid "anomaly":** retracted;
  - the earlier 6T JUnit OK is noted.
  
  Alignment doc §17 matches. The phone raws are untouched.
- **Matrix.** `matrix.py check`: NO-GO.
  - Counts: **18 PASS / 11 BLOCKED / 3 NOT RUN (waived W-01, M-01, M-02)**.
  - Open rows: W-02, W-07, W-08, W-10, W-11, W-12, M-03, M-04, M-08, A-01, M-11.
  - M-09 and M-10 are PASS, `account: admin`, with an empty blocker. They cite `adjudicated-v2`, the approved host `e7c716c5`, Chrome 156.0.8078.48 and `formal_otp23_acceptance` as script policy, plus the residuals (operator-inferred context, operator binding, identical payload, linear drift, partial 10 Pro log, poll-1 gate).
  - The rendered document is regenerated.
- **Tests and gates.**
  - Python suite: 373 OK; I reran it.
  - Gradle: BUILD SUCCESSFUL in both the full-gate and spotless-rerun logs.
  - Rust fmt, host test and Windows-target clippy logs are present.
  - The CI Log-guard script is verbatim; I reran it from the root and it exits 0.

## IMPORTANT (one focused fix before publication)
**CI does not run the new regression test.**
- `.github/workflows/otp23-feasibility.yml` lists Python test files explicitly and has no step for `scripts/test_windows_closejoin_compare.py`.
- That file holds the regression for the established compare-1 driver defect (I-A). Without a step, CI never guards it.
- **Fix:** add `- run: python scripts/test_windows_closejoin_compare.py`. It runs as a script with no hardware, is OS-neutral, and passes 24/24 locally.
- Optionally also add `python scripts/test_timer_probe_analysis.py`, a pre-existing omission from the investigation commit.
- The Rust regressions are already covered by the existing `cargo test` step on all three runners.

## MINOR (do not expand scope)
- `integration-2026-10-27/gates/ci-log-guards.log` is **0 bytes**. The "exit 0, fail=0" claim is therefore not in the saved log. My independent rerun passes. Next time, record the exit marker; no action is needed for this review.
- The `f2-macos-host-bundle-codesign.txt` registration-time wording ("as registered 16:10:56Z" against the captured 16:04:16Z event) survives only as a note in the copied Opus report under `integration-2026-10-27/reports/`. A single cross-reference sentence in `SOURCE-SCOPES.md` or the strict-mac README would make it discoverable. The raw stays unchanged either way.

## Verdicts (separate)
- **Publishable: YES** at fingerprint `e69ae2a5…21cf5` (diff `f86ddf36…`), once the single CI step above is added.
  - That change touches only the workflow file. Re-run the CLAUDE.md gates only if anything else changes.
  - After the commit, export-verify the evidence manifests.
  - No CRITICAL findings, and no other IMPORTANT findings.
- **OTP-23 acceptance: NOT satisfied. Decision stays NO-GO.**
  - 11 rows remain BLOCKED. M-09 and M-10 are the only promotions, and each is justified by its own verified evidence.
  - W-11/W-12 and W-07/W-08 are undecided; this review promotes neither.
  - **OTP-24 is not authorised.**
- **R-01 (new phase):** this report is the final independent Opus review for the parent's ledger entry. Bind it to the commit that contains exactly this tree plus the CI step.
