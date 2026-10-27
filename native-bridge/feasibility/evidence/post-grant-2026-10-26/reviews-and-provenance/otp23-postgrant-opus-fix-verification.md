# OTP-23 post-grant review: fix verification (read-only)

- Reviewer: Claude Code session `ceb6e424-1e26-4ea7-972b-efa4ba2dc633` (the same session as the final review) on model `claude-opus-5-5`. The authoritative model metadata is the `modelUsage` block in this CLI run's JSON result.
- Mode: read-only. No repository edits, devices, subagents, commits or pushes. The only file written is this report.
- Inputs: `/tmp/opencode/otp23-postgrant-opus-review.md`, `/tmp/opencode/otp23-postgrant-review-fixes-progress.md`, and the actual working-tree diff against `db5d4db` plus the untracked evidence and scripts.
- Date: 2026-10-27.

## Verdict

- **IMPORTANT-1: resolved** (option b, as chosen).
- **IMPORTANT-2: resolved.**
- All seven MINOR items have been dispositioned.
- **No CRITICAL or IMPORTANT findings remain.** Four MINOR findings (two new, two follow-ups) and one NIT are listed below. None blocks the commit.
- No row was promoted. No acceptance text was rewritten: `requirement`, `expected`, `source`, `group`, `human_gated` and `waiver` are unchanged for every row against HEAD. No failure was discarded. The waiver still covers only W-01, M-01 and M-02.
- Matrix: 15 PASS / 14 BLOCKED / 3 NOT RUN. **Decision: NO-GO.**

## IMPORTANT-1: evaluator provenance and reproducibility — resolved

**Code (`strict_background_finalize.py`)**
- `evaluator_provenance()` records the git revision (`rev-parse HEAD`), or `null` when git or a checkout is unavailable; it never guesses.
- The dirty flag is scoped to the three evaluator files (`status --porcelain --untracked-files=all -- <files>`).
- It records the SHA256 of `strict_background_core.py`, `strict_phone_validation.py` and `strict_background_finalize.py`.
- Every `adjudicate()` output now carries this record.
- Error handling: `OSError` and `SubprocessError` give `null`, a non-zero exit gives `null`, and a 30 s timeout is set. That is acceptable on Windows and outside a checkout.
- No legacy bypass was added: `_foreground_check` is unchanged, and a test asserts the bypass is absent.

**Tests (`test_strict_background_finalize.py`, 28 pass)**
- The hashes equal those of the loaded files.
- A non-checkout gives `null` revision and dirty flag but still computes hashes.
- In a temporary repo, the dirty flag is scoped: an unrelated file stays clean, and an edited evaluator file is reported as dirty.
- A real-raw regression test re-runs the HEAD evaluator on the committed Mac raws and phone files, using the original source hashes, collection mtimes and recorded clock. It gets BLOCKED with ST-15 BLOCKED and ST-01..14 PASS, while the originals stay PASS with exactly 14 checks and no provenance field.
- Line endings won't break these byte-hash tests on Windows CI: `native-bridge/feasibility/.gitattributes` sets `* text=auto eol=lf`, and `git check-attr` confirms `eol: lf` on the raws.

**New evidence (`phone-evidence/mac/reevaluation-at-head/`)**
- Both re-evaluations are BLOCKED, and only ST-15 is non-PASS.
- Provenance recorded: `git_revision db5d4db…`, `evaluator_files_dirty: true`, modified file `…/strict_background_finalize.py`.
- The recorded hashes `54b5f448…`, `93057ec4…` and `15e4d152…` **equal the current working-tree files**, so they describe the code that will be committed.
- The raw hashes `883a3100…` / `2c462434…` are bound.

**Raw evidence unchanged**
- Raws, phone files, original adjudications, M6 files, recovery2 result and Opus raws are all byte-identical to the `/tmp/opencode` originals (`cmp`).
- `SHA256SUMS`: 182 entries, 0 failures, no unlisted non-AppleDouble file.

**Documentation**
- M-09/M-10, INTEGRATION.md and alignment §15 state that the historical PASS came from the pre-`db5d4db` evaluator. They mark this as operator-inferred from timestamps and content, say it is not reproducible at HEAD, and say there is no legacy bypass.

## IMPORTANT-2: M-11 — resolved

- M-11 `actual` and `blocker` now say:
  - not executed;
  - no unsigned dev-registered host was built or run on the existing account;
  - the grant covers only the signed `me.nagaev.veles.bridge` bundle;
  - no result exists;
  - **not waived**.
- The status stays BLOCKED.
- `test_matrix.py` rejects "cannot approve" in any blocker, and in any `actual` unless it is labelled PRE-GRANT.

## MINOR dispositions verified

- **MINOR-3 (M6):**
  - `scripts/m6_phone_clock_adjudication.py` documents its algorithm.
  - Running it on the committed phone file reproduces the committed output byte for byte (my run, `diff` empty): 16 pairs / 1505 ms, 100 records per peer, 71 / 639. It binds `input_sha256 6515a400…`.
  - It matches my independent recomputation from the final review.
  - The tests check the committed output against a fresh run, plus synthetic disjoint, interleaved and survivor cases.
  - The cross-run central-prefix corroboration is stated as "not authentication".
  - The machine FAIL is retained.
- **MINOR-4 (DEX and JUnit):**
  - `inspect_release_dex.py` and its output check that the release APK hash `5d770ede…` matches the approved hash, then count 0 hits for all nine needles. This matches my own check.
  - `junit-summary-334.txt`: 42 classes, 334 tests, 0/0/0 failures/errors/skipped.
  - Both are honestly labelled as re-reads, not the original inspection.
  - This is valid for 3dbe3db because `git diff 3dbe3db -- app` is empty and the working tree has no changes under `app/`.
- **MINOR-5:** the fragment bounds are marked as derived, and the sweep APK identity stays "inferred".
- **MINOR-6:** the Windows run → phone-file table matches the hashes I checked in the final review. The PASS duplicates are labelled, and the missing prewarm-fg-1 phone files are disclosed.
- **MINOR-7:** the residuals (operator `SERIAL=PATH` binding, operator-identified harness revision, macOS page-only focus, unrecorded fix-wave metadata) are listed in the rows and in INTEGRATION.md. No provenance was invented.
- **MINOR-8:** the Gradle, log-guard and Python logs are now separate. Results without a log are explicitly not claimed as logged.
- **MINOR-9:** reworded.
- **R-01:** BLOCKED/PENDING. It cites the final review session `ceb6e424…` correctly; the copied raw shows `modelUsage` `claude-opus-5-5`, 0 subagents, `is_error` false, and the review `.md` is byte-identical to `/tmp`.

## Remaining findings

**MINOR-A (new): one leftover "current strong validator" wording in M-09**
- Where: M-09 `actual`, `acceptance/otp23-matrix.json:539`.
- Problem: it still says "finalized it OFFLINE on Linux with the current strong validator". The same text later says that evaluator was pre-`db5d4db` and is not reproducible at HEAD. M-10 and alignment §15 already use the corrected phrasing.
- Correction: change it to "the then-current (pre-`db5d4db`) strong validator" and re-render. This is wording only; no status change.

**MINOR-B (new): `evaluator_provenance()` could report a wrong revision in one edge case**
- Problem: `git -C <scripts dir> rev-parse HEAD` can succeed inside an *unrelated* enclosing repository, for example a copied harness under a home directory that is itself a git repo. The function would then report that repo's revision.
  - If the evaluator files are untracked there, `evaluator_files_dirty` becomes true, so the result is misleading but flagged.
  - If they are git-ignored there, it would report `dirty: false` with a foreign revision.
- Impact: the SHA256 values remain the authoritative binding, and the committed evidence was produced inside this repository.
- Correction (future): also check that the files are tracked at that revision, e.g. compare `git rev-parse HEAD:<repo-path>` with `git hash-object`, or record `git rev-parse --show-toplevel`.

**MINOR-C (follow-up): no test ties the re-evaluation hashes to the committed evaluator files**
- Problem: `test_matrix.py` only checks that `len(evaluator_provenance.sha256) == 3`. Nothing asserts that the re-evaluation hashes equal the committed evaluator files. They do equal them today.
- Risk: any later edit to the three evaluator files would silently break that binding.
- Correction: none needed if, as planned, nothing functional changes after this check. Optionally add the equality assertion or record the commit in the attestation.

**MINOR-D (follow-up): the M6 script trusts some things it doesn't check**
- Problem: `m6_phone_clock_adjudication.py` does not itself check callback and API status codes, unbound/pending counts, or `clean`/`final`. It relies on the file's counters, which the A-02 row cites separately. A missing bound callback would raise `KeyError` instead of a clear error message.
- Impact: none on the committed result.
- Correction (optional): assert `clean`, `final_after_service_stopped`, `not truncated` and all-success codes, and turn a missing callback into a `ValueError`.

**NIT**
- Where: R-01 `blocker`.
- Problem: it still says the post-grant source "has NOT had its final fresh Opus 5.5 review", while R-01 `actual` correctly records that the review ran and found 0 CRITICAL, 2 IMPORTANT and 7 MINOR.
- Correction: the planned attestation commit will replace it anyway; until then, "final review done; fixes verified; attestation pending" would be more accurate.

## Test results (my runs, this working tree)

- **Python feasibility suites:** all OK.
  - m6_phone_clock_adjudication 5
  - macos_background_support 9
  - macos_permission_recovery 28
  - macos_poc 36
  - matrix 29
  - poc_background 26
  - scenario_matrix 59
  - strict_background 90
  - strict_background_finalize 28
- **Other suites:** trace-parser pass; Node connector/isolated suite 5 pass / 0 fail; `test_macos_package_lib.sh` 69 checks pass.
- **Matrix:** `matrix.py check` gives `matrix ok: NO-GO`. `matrix.py decision` lists 14 open rows (W-02, W-07, W-08, W-10, W-11, W-12, M-03, M-04, M-08, M-09, M-10, A-01, M-11, R-01); waived NOT RUN rows: W-01, M-01, M-02.
- **Hygiene:** `git diff --check` clean; no `__pycache__` or `.pyc` files in the untracked set. `sha256sum -c SHA256SUMS` passes all 182 entries.
- **CI:** the workflow adds `test_m6_phone_clock_adjudication.py`; the finalize tests' `git init`/`commit` call uses inline identity config, which is portable to the runners.
- **Not rerun by me:** the Gradle gates. No file under `app/` or Kotlin changed since the parent's successful final Gradle run. Per CLAUDE.md, the parent should still run `./gradlew spotlessCheck detekt lintDebug` and the raw Log guard on the exact commit before pushing.

## Commit and R-01 decision

- **The reviewed work can be committed** as NO-GO verification/harness/evidence work as it stands. Fixing MINOR-A (one sentence plus re-render) before the commit is recommended but not required. The other MINORs can be deferred.
- **After that commit**, R-01 may be attested PASS in an attestation-only metadata/docs commit, provided it:
  - cites this verification's raw CLI result (session `ceb6e424-1e26-4ea7-972b-efa4ba2dc633`, `modelUsage` `claude-opus-5-5`) together with the final review raw;
  - binds both to the concrete reviewed commit hash;
  - changes nothing functional.
- Even with R-01 PASS, the decision stays **NO-GO for OTP-24**: 13 other rows remain BLOCKED (strict background is pending the owner's Native Messaging clarification; M-03, M-11, W-07/W-08/W-10, W-02/M-08, M-04 and A-01 are unmet).
- Nothing here approves OTP-24 implementation or Tauri packaging. PR 122 and PR 123 stay as they are.
