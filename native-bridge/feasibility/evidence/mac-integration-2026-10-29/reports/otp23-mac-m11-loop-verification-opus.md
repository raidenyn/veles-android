# OTP-23 M-11 prompt-watch loop fix: independent micro-verification (Opus 5.5, 2026-10-29)

## 0. Role envelope (safe metadata only)

- **Reviewer:** an independent Claude Code session, model `claude-opus-5-5`. This is reviewer
  7b4ee6ca, the author of finding M11-1 in `otp23-mac-m08-fix-verification-opus.md`. It is **not**
  the implementer `752e1974-0617-470b-a425-dfbda97ac728`. No subagents.
- **Route:** `/tmp/opencode/otp23-mac-opus-continuation-owner-record.json`.
- **Inputs (frozen, read-only):**
  - `/tmp/opencode/otp23-mac-m11-loop-fix-result.md`
  - `/tmp/opencode/otp23-m11-prep/`, with the old version in `_versions/m11_dev_capture.py.3e7be057`
    for the diff
- **Not done:** no live Mac, Chrome, device, TCC, registration, package, build or source edits; no
  GitHub, commit or push. The M-08 files were not touched.
- **Scratch:** tmpfs only, deleted afterwards (`TMPDIR` for the tests, the mutation runner, the smoke
  run). Run with `python3 -I -B`, so no bytecode was written. The only file written is this report.

**Frozen hashes, re-confirmed after all runs:**

| file | sha256 |
|---|---|
| `m11_dev_capture.py` | `976f61cf038a3d46543bf03a80e8ae3b55a83b9516519d631e31e50f7a40e02d` |
| `test_m11_prompt_watch.py` | `50aa32eba131962738627c198572721c3daa70a4702954df6f5ef0aa38776478` |
| `README-M11-PREP.md` | `93514f0b5ebb2eb8dc4f9beb3c22e8b800a4906632243ac5b6fbe2213606722c` |
| `package-dev.sh` (unchanged) | `fa6ba4e8…` |
| `extension.tgz` (unchanged) | `cb656d2f…` |
| prep doc | `93b26cbc…` |

## VERDICT: APPROVED for owner-coordinated M-11 execution

M11-1 is closed, and so are N1, N2 and N3. The remaining items (§4) are non-blocking. Two of them
are **operator conditions** for the live step, and none needs a code change before M-11.

## 1. M11-1 closure: each required property verified

| required property | verified by |
|---|---|
| **Pre-init trace lines are observations, not terminal** | `watch_prompt` (`m11_dev_capture.py:231-282`) records lines matching `AUTH_TRACE_MARKERS` (deduplicated, with `t_s`) and never breaks on them; there is a final sweep after the loop. **Mutation M1** (break on first observation) fails **7/8** tests. |
| **Waits for the owner's fresh-nonce answer AND an actual scan reply, or a bounded deadline** | The only non-deadline exit needs `answer is not None` plus either a `started` reply or a post-answer reply (`:263-269`). Otherwise it runs to `--prompt-wait-s` (720 s). The host/port stays alive throughout: no `stopScan` or `shutdown` happens inside the loop. **M2** (answer not required) fails **5/8**. |
| **The host's 10 s budget is pending, not denial** | `classify` maps the `permission_pending` stage to `pending_at_host_budget_not_denial`. No rescan happens before the answer (test 4 asserts 0). I confirmed the host's own mapping in the **cd971f0 source the dev counterpart is built from**: `ADAPTER_WAIT = 10 s` and `NotDetermined → permission_pending`, `Denied/Restricted → access_denied` (`src/macos_map.rs:113-115`). The connector **always resolves**, never rejects, so error replies do land in `window.__scan` (`extension/connector.js:134-153`). **M4** (pending → denial) fails 2/8. |
| **At most 2 post-answer rescans; classification from the actual result** | `max_rescans=2`, with one rescan in flight at a time (`awaiting`). The state comes from `final_reply`. **M3** (unbounded) fails 1/8. |
| **No dialog → OWNER report, never inferred** | `read_answer` accepts only `dialog_observed: true` (with a verbatim non-blank `prompt_name_shown` and `choice ∈ {allow, deny}`) or `dialog_observed: false`, plus `answered_by: "owner"`. I probed it directly: missing `dialog_observed`, a string value, a blank name, a bad choice and `answered_by: parent` are **all rejected**. Nothing is read from Settings or the trace. |
| **Wrong nonce ignored and recorded** | The answer stays `None`, the problem goes into `answer_problems`, and the file is re-read every second, so a corrected file is still accepted. **M5** (no nonce check) fails 1/8. |
| **End-to-end through the real `main()`** | Real `main()` + real `watch_prompt`, with stub Chrome `Popen`, stub CDP, scripted trace and fake time. Shutdown comes **after** the answer (t ≥ 30 s); the answer is nonce-bound in `m11-capture.json` with the verbatim name; there are exactly 2 scans; the started handle `h1` goes into `scans_stopped_after_capture` before `otp23.shutdown()`; the user-scope dev manifest is removed byte-verified and is gone from the fake HOME; `rc 0`. **M7** (no stop) and **M10** (no unlink) each fail that test. |

**Realistic-trace replay (mine).** I used the real host sequence: pre-init `not_determined` and
`adapter init: … authorization=not_determined` at about 0.1 s, `adapter wait timed out: … ->
permission_pending` at 10.2 s, the owner's answer at 30 s, then 1 rescan.
- Allow → `authorized_started`.
- Deny → `access_denied`.
- In both cases it waited 31 s, did 1 rescan, and ended by `answer_and_scan_reply`.

## 2. N1, N2, N3

**N1: refusals are stored.** The pre-launch smoke A–E (implementer's harness, which I reviewed: it
blocks the Chrome/otp23 `Popen` and fakes HOME) gives:
- A: exit 2, nothing created.
- B: launch blocked, manifest removed.
- C, D and E: records written with `problems: ["refused: …"]` and `record["refused"]`, 0 launches.
- E: the foreign file is left untouched.

The unit refusal test goes through the generic exception path, not `SystemExit`, so N1's evidence
here is the smoke.

**N2: the real order is documented.** README §5 (lines 85-88) now reads plist guard → profile →
manifest → launch, which matches the code (`:367-385`).

**N3: a missing app is still recorded.** The `finally` block catches `OSError` and writes
`approved_fixture_after.unavailable`; the capture is still written with `problems` non-empty and
`rc 1`. **M8** fails the N3 test.

**Unchanged and still as reviewed (verified in the old→new diff):**
- `--user-scope-manifest` is `required=True`.
- The dev ID/name/display guard runs before the profile, the manifest and the launch.
- `pathlib.Path(APPROVED_APP)` at both sites.
- Exact single origin; distinct `me.nagaev.veles.otp23.dev` manifest with byte-verified own-file
  removal.
- Disposable profile.
- `package-dev.sh` and `extension.tgz` are unchanged: no signing, raw `c36894dc…` pinned.

## 3. Test runs

**Results:**
- `python3 -I -B test_m11_prompt_watch.py` (local 3.14, tmpfs `TMPDIR`): **Ran 8, OK**.
- `ast.parse(..., feature_version=(3,9))` passes for both files. No 3.10+ constructs found
  (`match`, `X | None`, `zip(strict=)`, `removeprefix` and similar).
- The Mac 3.9.6 "8 OK" result is the implementer's; I rely on it, since no live Mac was needed.

**Mutation summary (in memory; the frozen files were not modified):**
- M1 fails 7/8, M2 5/8, M3 1/8, M4 2/8, M5 1/8, M7 1/8, M8 1/8, M10 1/8.
- **M6 survives, but it is an equivalent mutant.** It drops the `before adapter init` exclusion in
  `classify`. The pre-init line has no `authorization=` token, so the regex can never take it either
  way.
- **M9 survives: a test gap, not a code defect.** It accepts an answer without `dialog_observed`. The
  code rejects such answers, as my direct probe showed. One test would close it (optional).

## 4. Non-blocking findings and operator conditions

1. **Operator condition: write the answer file as one JSON object.**
   - A non-object JSON answer (e.g. `[1]`) makes `read_answer` raise `AttributeError`. The run would
     fail closed through `except Exception` (teardown and record), but **the attempt would be lost**.
   - Truncated JSON is handled: it is recorded and re-read.
   - **Condition:** the parent writes the answer file atomically (write a temp file in the same dir,
     then rename) as a JSON object with exactly the documented keys.
   - Optional one-line hardening: `if not isinstance(doc, dict): return None, "answer must be a JSON
     object"`.
2. **`post_init_authorization` is the last traced value, not the current state.**
   - With the real host it is usually `not_determined` even when the final state is
     `authorized_started` or `access_denied`. CoreBluetooth inits once, and a post-answer successful
     rescan emits no new authorization line. My realistic replay confirmed this.
   - Test 1 asserts `allowed_always` only because its scripted trace emits `adapter init` at 31 s,
     which is not what the host does.
   - **Adjudication condition:** the state comes from `final_reply` plus the owner's answer, and
     `post_init_authorization` is historical context only.
   - Optional: rename it to `last_traced_authorization`, carry its `t_s`, and make the test trace
     realistic.
3. **Minor:**
   - On fixture drift, the refusal is recorded but `approved_fixture_before` is assigned after the
     check, so the drifted values aren't stored. `approved_fixture_after` still records the current
     values.
   - The prep dir holds an implementer `__pycache__/` (cpython-314). It is not in the 8-entry stage
     manifest; don't copy it.
   - The 72 `/tmp/m11*` test dirs from the implementer can be deleted.

## 5. Execution gate (owner-coordinated)

M-11 may run now. Steps:

1. Package without signing via `package-dev.sh` from the raw `c36894dc…` counterpart.
2. Run `python3 m11_dev_capture.py --stage . --out-dir capture/m11-attempt-1 --user-scope-manifest`,
   using a unique dir.
3. On `PROMPT-WATCH-REQUESTED nonce=…`, the parent relays that nonce to the owner. The owner reads the
   **exact** dialog name first, then makes their own Allow/Deny, or reports "no dialog".
4. The parent writes the answer atomically (§4.1).
5. After the run, re-request the owner's read-only Settings check.

**Baseline:** the three M-08 retake runs (`shutdown-1`, `port-disconnect-1`, `tab-close-1`) are
already frozen authorized scans by the signed fixture `e7c716c5…` (`allowed_always` → `scan_state
started` → `device_found`). They serve as the M-11 safety baseline without waiting for the M-08
attribution work; formal M-08 row adjudication stays a separate, pending matter.

**Authority:**
- the owner's distinct dev-test approval (`otp23-mac-permission-owner-record.json`, `m11_approved:
  true`)
- the Opus route record

**Not authorized:** no M-04 toggle, no TCC reset, no inference about who owned the original
signed-fixture prompt.

**Not checked by me:** the Mac stage state (`stage-manifest.txt b1907348…`, `target-dev/` absent,
approved app strict OK). I rely on the implementer's read-only check; the parent should re-check
`shasum -c` of the stage manifest just before the run.
