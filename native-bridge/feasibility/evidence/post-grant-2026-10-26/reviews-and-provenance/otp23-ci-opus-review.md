# OTP-23 CI-fix review (focused, read-only)

- **Reviewer:** Claude Code session `ceb6e424-1e26-4ea7-972b-efa4ba2dc633` running on `claude-opus-5-5`. The authoritative model metadata is the `modelUsage` field of this run's CLI JSON result. This is the same session as the earlier final review and fix verification.
- **Mode:** read-only. No repository edits, devices, subagents, commits or pushes. The only file written is this report. Scratch scripts ran from `mktemp` directories, which were deleted. Gradle wrote only its usual git-ignored `build/` outputs.
- **Concrete reviewed commit:** `78f1784753ba0274e493ed35a92e27d6311b467a` ("OTP-23: Make lifecycle test hosts portable on Windows").
- **Range:** `66519dd..78f1784`, which is that single commit. The working tree was clean when I reviewed it.
- **Date:** 2026-10-27.

## Verdict: PASS

- No CRITICAL or IMPORTANT findings.
- Two MINOR findings and one NIT, all nonblocking.
- The CI fixes are sound and can be attested as reviewed NO-GO work.
- The decision stays **NO-GO for OTP-24**.

## Scope of the change

`git diff --stat 66519dd..HEAD` touches only three files:

1. `app/src/test/.../FragmentFixturesTest.kt`: a two-line reformat (+2/−1).
2. `native-bridge/feasibility/scripts/test_scenario_matrix.py`: +17 lines, test-only.
3. `docs/protocol/reviews/otp-23-contract-alignment-opus.md`: an appended disposition section (+13).

The commit does **not** touch:
- production code (`scenario_matrix.py` / `DirectHost` and the app's `main/` are unchanged);
- the matrix or its acceptance text;
- the evidence tree, raw bytes or `SHA256SUMS`;
- signing or provenance metadata.

No device or re-signing work was involved.

## Checks performed

1. **Raw CI failures.**
   - `otp23-ci-66519dd-lint.log`: `:spotlessKotlinCheck FAILED` with a format violation in `FragmentFixturesTest.kt`.
   - `otp23-ci-66519dd-windows.log`: three `DirectStdin` failures, all `OSError: [WinError 193]`, i.e. the shebang fake host can't be executed on Windows.
   - Both causes match the fixes.
2. **Formatting fix.**
   - It is a pure line split of the same boolean expression (`a && b && c`), so behaviour is identical.
   - `./gradlew spotlessCheck --rerun-tasks` at HEAD exits 0. Forcing the rerun makes it a real formatter run, not an UP-TO-DATE result.
   - Disclosure: the doc says honestly that the local cache-miss cause was **not determined**. That is acceptable. Forced reruns (`--rerun-tasks`) prevent a repeat.
3. **Windows launcher fix.**
   - `DirectHost` builds `[binary, origin]` (`scenario_matrix.py:584`) and calls `subprocess.Popen` (`:498`).
   - The test wrapper rewrites only an argv whose `argv[0]` equals the fake script, turning it into `[sys.executable, script, origin]`. It forwards all keyword arguments, so `stdin`, `stdout`, `stderr`, `env` and `creationflags` keep their real pipes.
   - It is still a real child process. EOF arrives through real pipe closure, and the exit code and cleanup assertions are unchanged.
   - No test is skipped and none is gated on the OS.
   - Production semantics are unchanged.
4. **Wrapper effectiveness (Windows-like conditions on Linux).** In a scratch harness, I subclassed `DirectStdin` and made the fake host non-executable (`0644`) with a broken shebang (`#!/nonexistent/interp`).
   - With the wrapper, all three previously failing tests pass.
   - In the control without the wrapper, all three fail.
   - So the wrapper is what makes them pass; the Linux shebang does not mask it.
5. **Tests and checks.**
   - `test_scenario_matrix.py`: 59 tests OK; the 5 `DirectStdin` tests pass verbosely.
   - `matrix.py check`: `matrix ok: NO-GO`.
   - `git diff --check 66519dd..HEAD`: clean.
   - Parent's gate log `/tmp/opencode/otp23-ci-fixes-gates.log`: BUILD SUCCESSFUL, `exit=0`.
6. **Disclosure accuracy.** The appended doc section states that the Windows fix is verified on Linux only and that the next Windows CI run is the evidence. It also says no fresh review was claimed at that time and the matrix counts are unchanged. All of that is true.

## Findings

- **MINOR-1: stale wording in the appended doc.**
  - The section in `otp-23-contract-alignment-opus.md` says the two test changes are "new, uncommitted and not reviewed by Opus". They are now committed in `78f1784`, and this review covers them.
  - Correction: in the planned evidence/docs-only follow-up, record that they were committed in `78f1784` and reviewed here. Do not rewrite the earlier text; append instead.
- **MINOR-2: no explicit regression assertion that the wrapper was used.**
  - Real coverage is the Windows CI job: the tests run unskipped on `windows-2025`, so a regression would fail there.
  - On Linux, a future change that stopped `argv[0]` from matching would go unnoticed.
  - Optional fix: count rewrites in the wrapper and assert it rewrote at least once per launch, or launch a non-executable script as my probe did.
- **NIT: the patch is process-global.**
  - `patch.object(sm.subprocess, "Popen", …)` patches the global `subprocess.Popen` for the duration of each test. It passes through any argv it doesn't target and is undone by `addCleanup`, so it is harmless.

## Residuals, stated explicitly

- The Windows behaviour is proven on Linux only, by the probe above. The next `windows-2025` CI run is the authoritative check.
- The earlier deferred items MINOR-B, MINOR-C and MINOR-D from the fix verification remain open and nonblocking. They were not touched here.

## Attestation

- No CRITICAL or IMPORTANT finding remains.
- **PASS may be attested** for `78f1784753ba0274e493ed35a92e27d6311b467a` as reviewed NO-GO CI/test work, extending the R-01 coverage (previously bound to `d72c177` / `66519dd`) to this commit.
- The attestation should be an evidence/docs-only follow-up commit that:
  - cites this raw result's session and `modelUsage`;
  - binds the reviewed hash above;
  - fixes MINOR-1 by appending to the doc;
  - contains no functional change.
- Before pushing, rerun the mandatory gates (`spotlessCheck detekt lintDebug` and the raw Log guard) on that commit. Treat the Windows CI result as the evidence for the WinError 193 fix.
- This is not OTP-23 acceptance and not an OTP-24 GO. The matrix stays 16 PASS / 13 BLOCKED / 3 NOT RUN (waived), and the decision is **NO-GO**.
