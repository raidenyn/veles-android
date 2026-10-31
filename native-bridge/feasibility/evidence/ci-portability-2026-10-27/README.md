# OTP-23 CI portability evidence (2026-10-27)

Scope: the hermetic test-only fix for the macOS harness failure in
`MacForegroundReaderBehaviour.test_session_state_reads_the_dictionary`, gated evidence only.
No runtime, evaluator, Rust or physical source change; acceptance/NO-GO matrix and RFCs untouched.

- CI reference: run 40602509979, job 122533903491 (macos-26, `otp23-feasibility.yml` harness).
- Failure: the test executed the real default probe and asserted `{"verified": False}`, valid only
  where CoreGraphics cannot be CDLL'd; on the macos-26 runner the probe correctly returned
  `{'verified': True, 'on_console': True, 'screen_locked': False}`. Environment-dependent assertion,
  not a runtime defect.
- Solution (test-only, `native-bridge/feasibility/scripts/test_strict_background.py`, +5/−2,
  diff sha256 a00f9b843fa23b5b049284fe2680f74c14e2de78905fc141f652c1ceecf06280): default wiring is
  asserted deterministic (`(probe or read_cg_session_dict)()` native reader) and the live
  fail-closed real-probe assertion runs only `if sys.platform != "darwin"`. On macOS the suite relies
  on the unchanged injected positive/negative/error coverage incl. NULL and a raised `OSError`.
  A filesystem-path guard was rejected in review r1 (dyld shared cache: file can be absent while
  CDLL still resolves).

Contents (all byte-exact copies; `SHA256SUMS` covers every file except itself):
- `ci-job-40602509979.log` — complete raw CI job log of run 40602509979 (ends at job failure and cleanup).
- `opus-review-r1-not-yet.md` — review r1, verdict NOT YET (path-exists guard unsound on macOS).
- `opus-review-r2-approved.md` — review r2, verdict APPROVED (sys.platform guard).
- `fix-report.md` — GLM fix report with gates, actual counts and model metadata.
- `gate-01-spotless-rerun.log`, `gate-02-full-gradle.log`, `gate-03-log-guards.log` — local gate logs.
- `metadata-glm-opencode-session.json` — canonical opencode.db values for the GLM fix session.
- `metadata-opus-review-transcript.json` — Claude Code transcript metadata for the Opus reviews
  (metadata only; the personal transcript itself is not copied).

Status note: the corrected commit is NOT yet claimed green on all three CI platforms — the macos-26,
ubuntu and windows-2025 jobs must be observed passing on the pushed branch before any such claim.
Local Linux verification of the same source: suite 117/117 OK, full harness suites OK, forced
`spotlessCheck --rerun-tasks` and `spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck`
BUILD SUCCESSFUL, both raw log guards OK (see fix-report.md and gate logs).

Model provenance (canonical): GLM fix executed by `glm-5.3-flash` (provider `ollama-cloud`,
opencode session `ses_e804a0cc7ffeqKBdBiLFpOIxSn`); reviews by `claude-opus-5-5` (Claude Code session
`2fe20385-5ef1-4622-bf94-00b8c0c6477e`). Zero subagents in both.