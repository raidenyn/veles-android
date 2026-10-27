# OTP-23 CI portability fix (test_strict_background.py only) — Opus review

**Reviewer metadata (read from the transcript on disk):** Claude Code session `2fe20385-5ef1-4622-bf94-00b8c0c6477e`, every assistant message `claude-opus-5-5` (427 at capture), CLI `2.1.311`/`2.1.312`, 0 subagents.
- Read-only: no edits, no hardware, no GitHub.

**Tree (2026-10-27T23:37:01Z):** HEAD `dde4945`. The only change is `native-bridge/feasibility/scripts/test_strict_background.py` (+5/−2). `git diff` sha256 `746dd020…a3dd`; file sha256 `c34029b252179e5a…`.
- Runtime and evaluator are unchanged: `strict_background.py` `478b836c…`, `strict_background_core.py` `e44f616c…`.
- No source, evidence, matrix or RFC change. Acceptance is unaffected.

**CI failure (macos-26, job 122533903491):** `test_session_state_reads_the_dictionary` asserted that the **real** probe returns `{"verified": False}`. On the runner the native probe correctly returned `{'verified': True, 'on_console': True, 'screen_locked': False}`.

## Verdict: NOT YET — the guard is very likely ineffective on macOS
The fix skips the real-probe assertion only when
`pathlib.Path("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics").exists()` is true.

Since macOS 11 (Big Sur), system dynamic libraries live only in the dyld shared cache. Apple's Big Sur 11.0.1 release notes say "copies of dynamic libraries are no longer present on the filesystem. Code that attempts to check for dynamic library presence by looking for a file at a path or enumerating a directory will fail."

The framework's `CoreGraphics` entry is a symlink to a binary that is absent on disk. `ctypes.CDLL` on that path still works, because `dlopen` consults the cache; that is why the runtime reader is fine.

On macOS 26, `Path.exists()` (which follows symlinks) is therefore expected to be **False**, so:
1. the test still calls the real probe on macOS;
2. that probe returns verified True on the runner;
3. the macOS CI job would fail exactly as before.

I could not confirm this on a Mac (read-only, no hardware). The commit has not been demonstrated on a macOS runner, and the Linux run does not exercise the branch: there, both the old and new code take the off-macOS path.

## Minimum fix (test-only)
Gate on the platform, not on a filesystem path. Keep the existing mocked positive, negative and error parser coverage, plus the wiring assertion:

```python
if sys.platform == "darwin":
    real = state()   # environment-dependent on a Mac: assert only the fail-closed shape
    self.assertIn(real.get("verified"), (True, False))
    if real["verified"]:
        self.assertIsInstance(real["on_console"], bool)
        self.assertIsInstance(real["screen_locked"], bool)
    else:
        self.assertEqual(real, {"verified": False})
else:
    self.assertEqual(state(), {"verified": False},
                     "the real probe fails closed off-macOS (no CoreGraphics)")
```

`sys` is already imported. Do not rely on a path probe. If presence must be checked at all, use `ctypes.util.find_library("CoreGraphics")`, which is shared-cache aware on macOS 11+, but the platform check is simpler and sufficient.

## What is already correct
- The change is test-only; the wiring assertion `(probe or read_cg_session_dict)()` is reasonable.
- The mocked parser cases are unchanged.
- On Linux the targeted class (6 tests) and the full `test_strict_background.py` pass. Windows takes the same off-macOS branch.
- No runtime, evaluator, physical-source or acceptance change; NO-GO unchanged.

## Required evidence before re-attesting
- The macos-26 CI job, plus the Linux and Windows jobs, pass on the corrected commit.
- Optionally, before pushing, run the test once on the Mac in the existing context.
- No broader re-review is needed: the test-only scope is confirmed.
