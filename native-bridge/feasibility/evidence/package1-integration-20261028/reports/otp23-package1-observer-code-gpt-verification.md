# OTP-23 package 1 observer code-fix verification — GPT-5.6-Sol

## Scope and identity

- Independent read-only re-review of the six current observer/lifecycle files against the prior GPT preflight B-1..B-5 and I-1..I-6.
- OpenCode session `ses_e7fbe5353ffeWU4VTjn6lgYjIL`; provider `openai`; model `gpt-5.6-sol`; variant `high`; OpenCode `1.19.4`; session agent `build`.
- Safe metadata read from `/home/dev/.local/share/opencode/opencode.db` at `2026-10-28T06:20:34Z`: 72 assistant messages at capture, all `(openai, gpt-5.6-sol, high)`; agent/mode values include this session's `build` and `compaction` records.
- No subagents, source edits, hardware/Chrome/adb/homepc activity, GitHub activity, commits, row promotions, or evidence rewrites. Only this report was authored.

## Verdict

**NOT YET APPROVED for the hardware boundary. Two small fail-closed fixes remain.**

B-1, B-2, B-3, I-2, I-3, and I-4 are correctly implemented and tested. The post-run PC producer snapshot materially closes the old evidence provenance gap for the successful Windows runs. Current B-4 future-run enforcement is incomplete, and I-1 still has one permission/partial-output false-zero path. Fix the two findings below and rerun the same portable suites before Windows retakes or M-08.

B-5 phone pre/post capture and minimum retakes are deliberately not done. M-08 was not run. Package 1 is not complete, no matrix row is promoted, and OTP-24 remains unauthorized.

## Minimum fixes

### 1. BLOCKING: `require_observer_pinned()` trusts the missing-list assertion without validating any digest

`scenario_matrix.py:836-847` checks only that `observer_modules_missing` is an empty list. It does not check `platform_observer_sha256`, required key coverage, or digest validity. Therefore this malformed metadata is accepted:

```python
{"observer_modules_missing": [], "platform_observer_sha256": {}}
```

The review reproduced `empty_observer_digest_map=ACCEPTED`. The unit test at `test_scenario_matrix.py:540-549` explicitly treats `{"observer_modules_missing": []}` as complete, so it preserves the gap.

There is a second path to the same failure: `source_hashes()` inserts `file_sha256(path)` into the pinned map without checking for `None` (`scenario_matrix_core.py:145-154`). An unreadable loaded helper can produce `observer_modules_missing=[]`, a `None` digest, and measurement still proceeds.

Minimum fix:

- Require `platform_observer_sha256` to be a dictionary whose key set includes exactly every required `<module>.py` entry for the platform.
- Require each required value to be a 64-character hexadecimal SHA-256 string; `None`, missing keys, malformed values, and unexpected duplicate/missing declarations must refuse measurement.
- Prefer having `source_hashes()` also add unreadable helper files to `observer_modules_missing`.
- Add tests for empty map, omitted map, `None`, short/non-hex digest, missing one macOS helper, and a valid complete map.

The new PC snapshot independently binds the old Windows producer bytes; this finding concerns the claimed fail-closed guard for future measurements.

### 2. IMPORTANT for M-08: `lsof` return code 0 with diagnostic stderr can still become a false zero

`macos_poc.lsof_endpoints()` rejects stderr only when `returncode == 1` (`macos_poc.py:424-444`). A command returning 0 with a permission/partial-result warning and empty stdout returns `[]`, which can support an endpoint-absence claim. The review reproduced:

```text
rc0_stderr_result= []
```

For an acceptance absence census, any nonempty `lsof` stderr makes completeness uncertain regardless of return code.

Minimum fix:

- Return `None` for any nonempty stderr before parsing stdout.
- Add `rc=0 + permission/warning stderr` and `rc=0 + rows + warning stderr` tests; both must be unavailable.
- Keep the current `rc=1, empty stderr, empty stdout -> []` behavior only with the separate live-pid requirement already enforced by `DEFAULT_MACOS_HOST_FIELDS`.

Do not run M-08 until this is fixed.

## Findings closed

- **B-1 closed:** `assemble_hygiene_probe()` calls `read_endpoint_table()` exactly once and slices the same table object for Chrome, host, and wrappers (`scenario_matrix_core.py:424-497`; `chrome_validate.py:404-418`). Unavailable families remain unavailable. Tests at `test_scenario_matrix.py:479-535` exercise one-read, per-pid slicing, controls, and output shape.
- **B-2 closed:** a positive macOS `all_layers` count adds an ownership problem (`scenario_matrix_core.py:517-525`). The driver fails on any probe problem even though `usable` continues to mean census/control availability. The positive and zero cases are tested at `test_scenario_matrix.py:304-322`.
- **B-3 closed:** `probe_connected()` applies integer console-session equality only for role `windows`; macOS uses a measured context audit (`scenario_matrix.py:483-502`, `scenario_matrix_core.py:605-627`). The fake full macOS scenario test confirms `session=None` is not fed to the Windows gate (`test_scenario_matrix.py:578-628`). The audit records manager, uid, and SSH-marker presence without inventing a new session criterion.
- **B-4 old-producer gap closed by capture:** `producer-pc-snapshot-20261028/MANIFEST.json` records 236 files captured after the runs. Recomputed snapshot hashes match the prior reviewed producer pins: `chrome_validate.py=5b258924...`, `scenario_matrix.py=feaf3a54...`, `scenario_matrix_core.py=b2f417c8...`; host binary remains `8521de4d...ae33`. This supplements the run metadata that omitted `chrome_validate.py`. Future-run enforcement still needs minimum fix 1.
- **I-1 mostly closed:** final-colon parsing handles bracketed and unbracketed IPv6, parse failures return unavailable, and nonzero/error cases are covered. Only the rc=0 stderr path above remains.
- **I-2 closed:** Windows liveness now checks `GetExitCodeProcess == STILL_ACTIVE` and closes the handle (`chrome_validate.py:363-397`). Three real-Windows tests are correctly platform-skipped here and must run on the Windows leg.
- **I-3 closed:** `check_teardown()` requires at least `min_released` distinct connection ids, so duplicate lines cannot satisfy a two-link gate (`scenario_matrix_core.py:314-325`). It does not introduce the Windows join line as a macOS requirement.
- **I-4 closed:** wrapper pid/session pairs are passed into and checked by the Windows comparator (`scenario_matrix.py:483-492`, `scenario_matrix_core.py:587-602`).
- **I-5 preserved:** no raw `criterion_conflict` record was rewritten. The exact owner approval remains W-02-only and must be applied through separate adjudication; it grants no other waiver or promotion.
- **I-6 pending process work:** live fixture logs still need to be frozen and a final scoped manifest produced. This is not a code-fix completion claim.
- **B-5 pending physical work:** no new two-phone own-burst proof exists for literal W-07 port disconnect. Keep the predeclared 30 s bound and require each phone's own CONNECTED→DISCONNECTED association in the retake.

## Regression and gate assessment

- No accidental Windows-only join gate was added to macOS.
- No unavailable endpoint family becomes an empty list in the new one-snapshot assembler.
- Mac context auditing is an evidence-availability check, not a Windows session-id comparison.
- Positive `all_layers` is an ownership failure; zero remains valid data and unavailable remains fail-closed.
- Distinct-release enforcement matches the existing two-link criterion and the retained successful traces already contain ids 2 and 3.
- Current raw wrapper behavior remains `criterion_conflict`; this is correct for immutable raw records despite the later narrow owner clarification.
- Latest-installed-Chrome-only scope is unchanged; no older Chrome validation is requested or implied.

## Verification performed

- `python3 -m unittest test_scenario_matrix test_macos_poc`: **143 tests OK, 5 skipped**.
- `python3 -m unittest discover -s . -p 'test*.py'`: **418 tests OK, 5 skipped**.
- The three added skips are Windows-only real-API smoke tests; they were not executed on Linux.
- `python3 -m py_compile` on all six reviewed files: PASS.
- Scoped `git diff --check`: PASS.
- Direct semantic probes reproduced both remaining findings.
- No live OS observer, device, Chrome, or CI run was performed.

## Reviewed hashes

| file | sha256 |
|---|---|
| `chrome_validate.py` | `78d53ca98c839e78bccf404beab5fb978f421f485f7945e5578d09a8b71a62f6` |
| `macos_poc.py` | `a1c8f15f9ca71520b8ca7950b84545b09d83be869df8702cf25c43ba5e4fa4d9` |
| `scenario_matrix.py` | `4def363e60323ea9d3228d0cb6c1285349d3cf911cd731d52ffaf371e1e54917` |
| `scenario_matrix_core.py` | `5f702c28462737207a80d88af3f0b6edb2f854e40c5b2f0d92494534527f9c7c` |
| `test_scenario_matrix.py` | `d313252c7b74457e41ba153ec163fca9d930df9e1d572b11e383ad2d3dd4ad48` |
| `test_macos_poc.py` | `f1564a41047d7bd6505d724abb3cf1521130e574e1026e0a0ab3855257d99a47` |

## Hardware boundary after fixes

After the two minimum fixes pass focused/full portable tests and the Windows smoke tests pass on Windows:

- Code is suitable for the narrow W-02 connected-census and W-07 literal-port-disconnect retakes, but B-5 still requires fresh pre/post phone captures and independent adjudication.
- Code is suitable to begin M-08 only with the existing approved signed fixture and preserved grant; M-08 remains unrun until actual evidence exists.
- W-02 uses only the exact approved narrow wrapper clarification. No other waiver, row promotion, package-completion claim, or OTP-24 authorization follows.
