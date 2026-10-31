# OTP-23 package 1 observer code fixes — final GPT-5.6-Sol verification

## Scope and identity

- Narrow, read-only verification of only the two minimum fixes requested by `/tmp/opencode/otp23-package1-observer-code-gpt-verification.md`: B-4 observer provenance fail-closed validation and I-1 `lsof` rc=0 diagnostic-stderr handling.
- OpenCode session `ses_e7fbe5353ffeWU4VTjn6lgYjIL`; provider `openai`; model `gpt-5.6-sol`; variant `high`; OpenCode `1.19.4`; session agent `build`.
- Safe metadata read from `/home/dev/.local/share/opencode/opencode.db` at `2026-10-28T06:32:48Z`: 88 assistant messages at capture, comprising 85 `build` and 3 `compaction` records, all under the session's `(openai, gpt-5.6-sol, high)` model identity. No API headers, credentials, quota bodies, cookies, or state tokens were read or recorded.
- Repository HEAD remained `8f365fce0d31f975610c235085a95b9136ac25e2`; the existing dirty worktree and evidence tree were not changed.
- No source edits, subagents, hardware/Chrome/adb/homepc activity, GitHub activity, commits, evidence rewrites, adjudication, or matrix-row changes. Only this report was authored.

## Verdict

**APPROVED at the code-to-hardware boundary for the limited pending physical work. Both requested minimum fixes are closed.**

The malformed empty observer-digest map is now refused while a valid complete Windows map is accepted. `lsof` rc=0 with any nonempty diagnostic stderr now returns unavailable (`None`), both with empty stdout and with endpoint rows. No concrete issue remains in either targeted fix.

This is code-boundary approval only. It does not establish B-5, execute M-08, promote any matrix row, complete package 1, authorize OTP-24, or add a waiver. The inherited package state and overall NO-GO remain unchanged pending the separately declared physical work and adjudication.

## Fix 1: B-4 observer provenance — closed

- `scenario_matrix_core.source_hashes()` now records a loaded but unreadable/unhashable helper in `observer_modules_missing` and does not put a `None` digest in `platform_observer_sha256` (`scenario_matrix_core.py:133-156`).
- `scenario_matrix_core.observer_pin_problems()` rejects an unknown platform, non-document metadata, absent/non-dictionary digest map, missing required keys, unexpected keys, and values that are not 64-character hexadecimal SHA-256 strings. It independently requires `observer_modules_missing` to be an empty list (`scenario_matrix_core.py:159-195`).
- `scenario_matrix.require_observer_pinned()` delegates to that validator and exits before measurement on any problem (`scenario_matrix.py:836-845`). It no longer trusts an empty missing-list assertion by itself.
- Targeted tests cover the exact empty-map probe, omitted metadata/map, malformed digests, incomplete/extra/wrong-platform maps, valid complete Windows/macOS maps, a lying missing list, and unreadable helper handling (`test_scenario_matrix.py:537-639`).

Corrected direct probes:

```text
empty_observer_digest_map=REFUSED
valid_complete_observer_pin=ACCEPTED
```

## Fix 2: I-1 rc=0 stderr — closed

- `macos_poc.lsof_endpoints()` returns `None` for any nonempty stderr after accepting only rc 0/1, before parsing stdout (`macos_poc.py:424-444`). A warning or partial-output diagnostic can no longer become an endpoint-absence result.
- Existing intended behavior remains: rc=1 with empty stderr/no records may return `[]`, subject to the separately enforced live-pid requirement; unparsable records and command failures remain unavailable.
- Targeted tests cover rc=0 with warning and empty stdout, and rc=0 with rows plus warning, alongside the existing return-code/error matrix (`test_macos_poc.py:259-287`).

Corrected direct probes:

```text
rc0_stderr_empty_stdout= None
rc0_stderr_with_rows= None
```

## Verification

- `python3 -m unittest test_scenario_matrix test_macos_poc`: **149 tests OK, 5 skipped**.
- `python3 -m unittest discover -s . -p 'test*.py'`: **424 tests OK, 5 skipped**.
- `python3 -m py_compile` on the five changed target/test files: PASS.
- Scoped `git diff --check` on those files: PASS.
- The five portable-suite skips remain platform-specific work, including the Windows-leg smoke execution; they were not converted into passes on Linux and must run on their declared platform.
- Two initial ad hoc shell probe invocations had Python `-c` quoting syntax errors and did not exercise product code. They were discarded and replaced by the corrected successful probes reported above.
- No live observer, OS, browser, phone, Bluetooth, or fixture execution was performed.

## Reviewed hashes

| file | sha256 |
|---|---|
| `chrome_validate.py` | `78d53ca98c839e78bccf404beab5fb978f421f485f7945e5578d09a8b71a62f6` |
| `macos_poc.py` | `c2a6a20c738720da3bd683c2a1eaf77839d410266f067dd52ee29ba1d3a35cd7` |
| `scenario_matrix.py` | `f8284c98b9dc200bf8ec83e09b4d040643a725e4ca637d5854b09494fc31ffc1` |
| `scenario_matrix_core.py` | `7212bfbb7a7fc230dad190d075f350b483c754b857fc1f7f8d484d8442791f7f` |
| `test_scenario_matrix.py` | `5a34232b845c2dd4e26f4c9163b1acc0b236348c994d14ddc9442a4ad220ace8` |
| `test_macos_poc.py` | `3ac96dd4af0359903efae8578bdbd4fe24d237004a35105da258cb2a4994aac1` |

Supporting document hashes:

| document | sha256 |
|---|---|
| `otp23-package1-observer-code-fixes.md` | `9ee9bb8baddf10db07887b661ba3692c47e555ec75673d57f2b8f358a4772f3c` |
| `otp23-package1-observer-code-gpt-verification.md` | `1a14ebd10bad9df349d8295a80d47b1055e2266b10fbe1e180039035c9843dbc` |
| `otp23-package1-owner-approval.json` | `89d255383b21272590f7c3b3cc3370e7b8f1e688b2445a65dba022d529f23fd4` |

## Hardware boundary

- The code is suitable to proceed with the narrow W-02 connected-census and W-07 literal-port-disconnect retakes. W-02 may use only the exact approved clarification in `/tmp/opencode/otp23-package1-owner-approval.json`: the plain host owns no top-level window or network endpoint; Chrome's default Windows launcher may own one never-visible console window; any visible window or host/wrapper network endpoint fails.
- B-5 remains pending: fresh pre/post phone captures must prove each phone's own CONNECTED-to-DISCONNECTED association within the predeclared 30-second bound. This report supplies no such evidence.
- The code is suitable to begin M-08 with the existing approved signed fixture and preserved grant. M-08 remains unrun and blocked from any result claim until physical evidence exists.
- Scope remains latest-installed Chrome only. No older-Chrome requirement, new acceptance criterion, new waiver, raw-record rewrite, package-completion claim, matrix promotion, or OTP-24 authorization is introduced.
