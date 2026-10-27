# OTP-23 CI portability fix r2 — Opus review

**Reviewer metadata (read from the transcript on disk):** Claude Code session `2fe20385-5ef1-4622-bf94-00b8c0c6477e`, all assistant messages `claude-opus-5-5`, CLI `2.1.311`/`2.1.312`, 0 subagents. Read-only: no edits, no hardware, no GitHub.

**Tree (2026-10-27T23:41:15Z):** HEAD `dde4945`, with only `native-bridge/feasibility/scripts/test_strict_background.py` changed (+5/−2).

| Item | sha256 |
|---|---|
| `git diff` | `a00f9b843fa23b5b049284fe2680f74c14e2de78905fc141f652c1ceecf06280` |
| `test_strict_background.py` | `b4c8edfb62fbf600…` |
| `strict_background.py` (unchanged) | `478b836c…` |
| `strict_background_core.py` (unchanged) | `e44f616c…` |

## Verdict: APPROVED (test-only)
- **The dyld concern is resolved.** The guard is now `sys.platform != "darwin"`, with no filesystem path probe, so the macOS shared-cache layout no longer matters. On macOS the environment-dependent real probe is not asserted in the unit test. On Linux (`linux`) and Windows (`win32`) the off-macOS smoke test still asserts that the real probe fails closed: there `CDLL` of the CoreGraphics path raises, so the result is `{"verified": False}`. `sys` is already imported.
- **Coverage is kept.** All injected positive, negative and error parser cases are unchanged, and a source assertion pins the default wiring `(probe or read_cg_session_dict)()`.
- **Scope.** There is no runtime, evaluator, physical-source, evidence, matrix or RFC change, and no acceptance criterion is weakened: the runtime fail-closed behaviour is untouched. NO-GO is unchanged.
- **Local checks.** The targeted class `MacForegroundReaderBehaviour` and the full `test_strict_background.py` pass (Linux).
- **Evidence still required.** The parent's monitored CI run must pass on all 3 OS jobs (ubuntu, windows-2025, macos-26) at the pushed commit. The real macOS confirmation is that job.
