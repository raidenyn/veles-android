# OTP-23 M-11 private loop fix (M11-1 + N1/N2/N3): result (2026-10-29, code only; M-11 NOT executed)

## 0. Role envelope

- **Implementer:** Claude Code, `claude-opus-5-5`, session
  `752e1974-0617-470b-a425-dfbda97ac728`, no subagents.
- **Scope:** the finding M11-1 and nits N1–N3 from the independent micro-verification
  `otp23-mac-m08-fix-verification-opus.md` (fresh Opus 7b4ee6ca).
- **Done after M-08 was frozen:** this ran after `otp23-mac-m08-retake-result.md` was written. It
  doesn't touch any M-08 source; repo sources are unchanged.
- **Not done:**
  - no Chrome, dev host, registration, CoreBluetooth or TCC action
  - no packaging, building or signing
  - no M-04 edits, commit, push or GitHub

## 1. What was wrong (reviewer M11-1, confirmed by mutation)

The prompt-watch loop treated the host's first trace line,
`bluetooth authorization before adapter init: …`, as a terminal outcome. That line is emitted before
CoreBluetooth can show any dialog. The result:
- the run was misclassified as `authorizing_via_dialog_pending_reply`
- the 720 s wait was dead, and the host was shut down within about 2 s
- the owner's answer was never bound

Reinserting that behaviour into the fixed code ("break on first observation") fails 7 of 8 new tests.

## 2. Fix (`/tmp/opencode/otp23-m11-prep/m11_dev_capture.py`)

New sha `976f61cf038a3d46543bf03a80e8ae3b55a83b9516519d631e31e50f7a40e02d`; previous `3e7be057…`
is kept in `_versions/` and on the Mac.

**`watch_prompt()`** is a pure function with an injectable clock, sleep, trace, reply and rescan:
- **Observations, not events:** new trace lines with `bluetooth authorization before adapter init:`,
  `adapter init:` or `adapter wait timed out:` are recorded with their time and never end the loop.
  There is a final sweep after the loop, so lines written just before the final reply are kept.
- **Replies:** each scan reply is taken and cleared (`window.__scan`), recorded with `after_answer`.
- **End conditions:** the loop ends **only** when the owner's nonce-bound answer exists AND either
  an authorized started scan or a post-answer reply has been observed. Otherwise it runs to the
  deadline (default 720 s). The host and port stay alive the whole time.
- **The 10 s host budget:** a `permission_pending` reply from it is recorded as pending, never as a
  denial.
- **Bounded rescans:** once the answer exists, at most **2** rescans are issued to observe the
  actual post-answer authorization/scan result.

**`read_answer()`** accepts only two shapes:
- `{"nonce": <this>, "answered_by": "owner", "dialog_observed": true, "prompt_name_shown": "<verbatim>", "choice": "allow"|"deny"}`
- `{"nonce": <this>, "answered_by": "owner", "dialog_observed": false}`, where "no prompt" is the
  owner's own observation.

A wrong nonce or a malformed answer is ignored and recorded in `answer_problems`. Nothing is
inferred from Settings or from the trace.

**`classify(watch)`** works from the final reply plus the **post-init** `authorization=` value. It
returns one of `authorized_started`, `pending_at_host_budget_not_denial`, `access_denied`,
`scan_error:…` or `no_scan_reply`, together with `owner_dialog_observed` and `ended_by`. A deadline
without a valid answer and reply adds a `problems` entry.

**Other changes:**
- Every started scan handle is stopped before `otp23.shutdown()`.
- The request's `answer_format` now carries the nonce and `dialog_observed`.
- **N1:** refusals (plist/label guard, fixture drift, pre-existing dev manifest) are appended to
  `problems` as `refused: …` and to `record["refused"]`. The record is written and the exit code
  stays non-zero.
- **N3:** a missing or unreadable approved app in `finally` is recorded as `unavailable`, and the
  capture is still written.

**Unchanged:**
- `--user-scope-manifest` is required.
- The dev ID/name/display guard (`me.nagaev.veles.bridge.dev` / `VelesOtp23Dev`) runs before the
  profile, the manifest write and the launch.
- The `APPROVED_APP` `Path` fix is in place.
- `package-dev.sh` (`fa6ba4e8…`) is untouched and not run. No codesign, no raw build.

**README** `93514f0b5ebb2eb8dc4f9beb3c22e8b800a4906632243ac5b6fbe2213606722c` (was `3942a596…`):
- **N2:** §5 now gives the real order: plist guard, then profile, then manifest, then launch.
- §5 also describes the wait/bind/rescan semantics.
- §6 shows the nonce/`dialog_observed` answer shapes.

**Prep doc** `otp23-mac-m11-preparation.md`: §11 appended (`ac7f62de…` → `93b26cbc…`).

## 3. Offline regression: `test_m11_prompt_watch.py` (new)

sha `50aa32eba131962738627c198572721c3daa70a4702954df6f5ef0aa38776478`.

**Fake-clock `watch_prompt` tests:**

| # | timeline | expected result |
|---|---|---|
| 1 | pre-init line at t=0, `permission_pending` at 11 s, owner Allow at 30 s | 1 rescan → `started`; waited ≥ 30 s, answer bound, `authorized_started`, post-init auth `allowed_always`, pre-init line kept as first observation |
| 2 | already authorized: started at 2 s, owner `dialog_observed:false` at 20 s | waits until 20 s, 0 rescans |
| 3 | Deny | 1 rescan → `access_denied` |
| 4 | unanswered pending | deadline, 0 rescans, `pending_at_host_budget_not_denial` |
| 5 | wrong-nonce answer | ignored and recorded |
| 6 | still pending after the answer | rescans bounded at 2 |

**`main()` end-to-end** with a stub Chrome `Popen`, stub CDP, scripted host trace and fake time:
- the answer is bound in `m11-capture.json` with the nonce and the verbatim name
- `shutdown` happens only after the answer
- exactly 2 scans, and the started handle is stopped
- the user-scope dev manifest is removed byte-verified
- `rc 0`

**Refusal path (N1/N3):** with the approved app missing, the record is still written, `problems` is
non-empty and `approved_fixture_after.unavailable` is set.

| run | result |
|---|---|
| `python3 -I test_m11_prompt_watch.py`, local (3.14) | **Ran 8, OK** |
| same, on the Mac stage (3.9.6) | **Ran 8, OK** (test temp dirs removed afterwards) |
| `ast.parse(feature_version=(3,9))` | OK |
| mutation: terminal-on-trace reinserted | 7 failures |
| mutation: answer not required | 4 failures + 1 error |
| earlier pre-launch smoke A–E on the new script | unchanged (A exit 2; B launch blocked, manifest removed; C/D/E refused) |
| N1 check on C/D/E | `problems` now hold `refused: …` |

## 4. Mac stage (`/Users/raiden/homepc/work/otp23-m11-dev`)

**How it was staged:**
- Guarded by the expected `stage-manifest.txt d3c25232…` and all 8 of its entries passing.
- Uploaded into a new `_incoming-m11-1-2026-10-29/` and verified there.
- The prior generation (`m11_dev_capture.py 3e7be057…`, `README 3942a596…`, `stage-manifest.txt
  d3c25232…`) was archived to `_pre-m11-1-2026-10-29/` with `SHA256SUMS`.
- New `stage-manifest.txt` is `b19073483d68d74f4c953f0814f1cd7be7fdcdbc515e971ef69e8e2437aaf53c`
  (8 entries: package-dev, script, README, test, extension ×4), all OK.
- `stage-manifest-history.txt` now has 3 generations.
- `_pre-fix-2026-10-28/` is untouched.

**Post-state:**
- `target-dev/` absent (packager not run), `source-raw/` and `capture/` empty
- no veles/otp23 manifest in the user or system NM dir, no otp23 processes
- approved app `e7c716c5…`, strict verify OK

## 5. Remaining before M-11 may run

1. An independent micro-verification of this fix.
2. The parent coordinates the owner's live prompt step: fresh nonce, exact name read first, the
   owner's own Allow/Deny or "no dialog".
3. Only then: package (no signing) and run, with `--user-scope-manifest`.

The signed baseline now exists: three live, authorized, Chrome-launched scans in the M-08 retake.
M-04 toggles remain unapproved.

**Leases:** I have no lease tool, so I didn't end or renew any.
- The Mac lease was renewed about 04:09Z for 90 min.
- The PC/adb lease can end, since the fixtures were stopped at 04:43Z.

**Local cleanup:** 72 temp dirs (`/tmp/m11watch-*`, `m11main-*`, `m11ref-*`, `m11smoke-*`) from
the offline runs are left in place. They hold only fake fixtures, and you can delete them.

STOPPED for the parent's independent review before M-11.
