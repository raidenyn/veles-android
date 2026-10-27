# OTP-23 CI portability fix — macOS harness feasibility job (post-Opus rework)

## Failure (CI 40602509979, job 122533903491)
`test_strict_background.py::MacForegroundReaderBehaviour.test_session_state_reads_the_dictionary`
asserted `state() == {"verified": False}` on the **real default probe**, valid only where CoreGraphics
cannot be CDLL'd. On `macos-26` runners the real probe succeeds
(`{'verified': True, 'on_console': True, 'screen_locked': False}`) — environment-dependent test, not a
runtime defect.

## Fix (test-only, 1 file, +5/−2)
`native-bridge/feasibility/scripts/test_strict_background.py` only — no runtime/evaluator/Rust/physical source.
Per Opus review v2: the previous filesystem guard (`Path.exists`) is unsound on macOS — framework
binaries for macOS 10.14+ typically live only in the dyld shared cache, so the on-disk file can be
absent while `ctypes.CDLL` still resolves. The guard is now platform-based, not filesystem-based:

```
if sys.platform != "darwin":
    self.assertEqual(state(), {"verified": False},
                     "the real probe fails closed off-macOS (no CoreGraphics CDLL here)")
```

- Off-macOS (Linux/Windows CI): the real fail-closed default-probe path is executed live, unchanged.
- macOS: no live query in the suite (its outcome is inherently session-environment-dependent);
  coverage there rests on the unchanged deterministic injected tests and the wiring assert.
- Retained additions (both deterministic): injected fail-closed `OSError` case, plus
  `assertIn("(probe or read_cg_session_dict)()", fn_src("mac_session_state"))` — the default probe
  must remain the native ctypes reader (same `fn_src` style as neighboring tests).
- Negative coverage unchanged: NULL dict, junk, non-dict, json-array, injected error. No gate weakened.

## Verification (actual commands + counts, Linux tree)
```
python3 <t>  # per otp23-feasibility.yml harness job; all exit 0
  scripts/test_poc_background.py                  26
  scripts/test_strict_background.py              117   (matches CI run count)
  scripts/test_matrix.py                          32
  scripts/test_strict_background_finalize.py      28
  scripts/test_m6_phone_clock_adjudication.py      5
  scripts/test_scenario_matrix.py                 62
  scripts/test_windows_closejoin_compare.py       24
  scripts/test_macos_permission_recovery.py       28
  scripts/trace-parser.test.py              8 (pass lines)
  scripts/test_macos_poc.py                       36
  scripts/test_macos_background_support.py         9
unittest-style subtotal: 367, +8 trace-parser = 375 Python checks, all pass
node --test connector-api.test.mjs isolated-device.test.mjs → pass 5 fail 0
python3 scripts/matrix.py check → "matrix ok: NO-GO" (policy output, exit 0)
JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck --rerun-tasks   → BUILD SUCCESSFUL (exit 0)
JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck → BUILD SUCCESSFUL (exit 0)
ci.yml lint-check raw guards → GUARD1 OK (android.util.Log), GUARD2 OK (bare Log.<level>)
```
Logs: `/tmp/opencode/ci-portability-01-spotless-rerun.log`, `-02-full-gradle-gates.log`, `-03-log-guards.log`
(existing attested raw logs untouched). In-suite evidence checksum gate
(`WANT = strict_background_core.expected_sha256()` record_sha256 assertions) passed; git diff touches
only the one test file.

## Diff provenance
- Base HEAD dde4945 (`contract/otp23-desktop-alignment`), PR 123; no commit/push (parent Opus re-review).
- Diff source hash (sha256 of `git diff`): a00f9b843fa23b5b049284fe2680f74c14e2de78905fc141f652c1ceecf06280

## Model metadata (from opencode db, not self-report)
`sqlite3 ~/.local/share/opencode/opencode.db` → message row: `modelID="glm-5.3-flash"`,
`providerID="ollama-cloud"`, agent `build`. GLM-5.3-flash only, 0 subagents.

## Status
Awaiting parent Opus re-review of the minimal diff; commit/push afterwards.