# OTP-23 Mac M-08 minimum-fix: independent micro-verification (Opus 5.5, 2026-10-29)

## 0. Role envelope (safe metadata only)

- **Role:** a fresh, independent micro-verifier. I am not the implementer (`752e1974-0617-470b-a425-dfbda97ac728`).
  Claude Code, model `claude-opus-5-5`, no subagents.
- **Authorization:** `/tmp/opencode/otp23-mac-opus-continuation-owner-record.json`, with owner answer
  "Use Opus 5.5 via Claude code".
- **Inputs read:**
  - `otp23-mac-m08-fix-result.md` (implementer completion report)
  - `otp23-mac-m08-code-verification-kimi.md` (Kimi K3 completed review: F-1..F-7, F-M11-1)
  - `otp23-mac-m08-preflight-opus.md` (consulted only as needed)
- **Scope:** only the bounded remaining conditions. This is not a redo of the full earlier review.
  Kimi's approval of the unchanged code is relied upon.
- **Not done:** no source edits, no hardware, Mac, Chrome, CoreBluetooth, TCC, registration,
  package or build, no Gradle, no GitHub, no commit, no push.
- **Transient scratch:** all of it lived on `/dev/shm` and was deleted afterwards:
  - a token file for the name sweep
  - the archive extraction
  - the smoke `TMPDIR`

  Mutation checks ran **in memory**: the mutated module source was exec'd into `sys.modules`, with no
  file copies. `PYTHONDONTWRITEBYTECODE=1` was set throughout. The only file I wrote is this report.
- **Environment:** local Python 3.14.4 (CI uses 3.12).

## VERDICT

**APPROVED for the Mac listener-selftest, followed by the 3 fresh Chrome-owned M-08 scenarios**
(shutdown / port-disconnect / tab-close, signed approved fixture).

**Later M-11: the reviewed fixes are VERIFIED, but M-11 is NOT approved to execute as-is.** Finding
**M11-1** (below) must be fixed and micro-re-verified first. It is pre-existing loop logic that both
the Kimi review and the implementer missed. It has no effect on M-08.

## 1. Prioritized findings

### M11-1 (M-11-only blocker; must fix before M-11 runs; does not gate M-08)

**The prompt-watch loop ends on the host's pre-init trace line, before any dialog answer or scan reply.**

**Where:** `/tmp/opencode/otp23-m11-prep/m11_dev_capture.py:347-363` (sha `3e7be057…`).

**What the loop does:**
- It runs `while time.monotonic() < deadline and outcome is None`.
- Any host-trace line containing `"authorization"` or `"adapter init"` sets
  `outcome = {"kind": "trace_auth"}`.
- The `allowed_always` branch's `pass  # keep polling: allow the reply to land` does **not** keep
  polling, because `outcome` is already non-None and the `while` exits after the 1 s sleep.

**Why it fires immediately:** the host's first Bluetooth trace line is
`bluetooth authorization before adapter init: <state>` (`native-bridge/feasibility/src/macos.rs:87-90`).
It is emitted before `Adapter::default()` creates the CBCentralManager, so before any TCC dialog can
even appear.

**Consequence in a real M-11 run (by code reading):**
- **Misclassification:** iteration 1 checks `window.__scan` first, finds it still null, then matches
  that pre-init line. The run is then classified `authorizing_via_dialog_pending_reply`, whatever the
  real authorization is (allowed included).
- **The 720 s `--prompt-wait-s` is effectively dead.**
- **The host is shut down within about 1–2 s.** After `js("window.__scan", timeout=10)` the script
  calls `otp23.shutdown()`. That is likely while the owner is still reading the dialog, or before
  the dialog appears.
- **The answer is never bound to the record.** The answer file is read only inside that loop, so the
  owner's nonce-bound answer is never written into `m11-capture.json`. This contradicts
  `README-M11-PREP.md` §5 ("polls (default 12 min) for the scan reply / … / the written answer
  file") and §6 ("the script picks it up and binds it to the nonce in `m11-capture.json`").

**Context for the fix:**
- The host's own `ADAPTER_WAIT` is 10 s (`macos.rs:49,181`). An unanswered dialog therefore yields a
  timeout-classified scan error from the host after about 10 s.
- That error must be recorded as "pending at host budget". It must not be treated as a denial.
- The script must keep running until the owner's answer file exists.

**Minimum fix:**
1. Record trace authorization lines as observations, not as terminal events.
2. End the loop only on a scan reply **and** (if a dialog was reported) the nonce answer file, or on
   the deadline.
3. Classify from the scan reply plus the `adapter init: … authorization=` line, not from the pre-init
   line.
4. Extend the offline smoke with a stubbed CDP and trace. Pre-init line first, then the reply: the
   loop must not terminate and the answer must be bound.

**Not covered by the smoke:** the implementer's smoke blocks the Chrome `Popen`, so this loop is
never reached in cases A–E.

### Non-blocking nits (fold into the M11-1 edit if convenient)

- **N1:** refused runs (smoke cases C, D and E) still write `m11-capture.json` with
  `"problems": []`. The refusal reason exists only in the `SystemExit` text. Append the refusal to
  `problems` so that the record alone cannot read as clean.
- **N2:** `README-M11-PREP.md` §5 says the script "builds the disposable profile → checks the dev
  bundle's Info.plist". The code checks the plist **before** `mkdtemp` (lines 255-267 vs 267). The
  code is the safer order, so the doc order is just inaccurate.
- **N3:** if `APPROVED_APP` is missing, the precondition `FileNotFoundError` is re-raised by the
  `sha256` call in `finally`, so no record is written. This fails closed (nothing launched or
  registered). It is only a diagnosability gap.

## 2. M-08 conditions: verified

| condition | result |
|---|---|
| **Source bytes** | All 9 inventory hashes match the report exactly: workflow `95f78691…`, `macos_poc.py e2131ad3…`, `scenario_matrix.py f8bbf2d6…`, `test_macos_poc.py a729dcce…`, `mac_release_bound.py a49b3450…`, `mac_clock_corroborate.py 8e6b9bbb…`, `test_mac_release_bound.py 4b8aa10d…`, `test_mac_release_gate.py 7f61cdee…`, `test_mac_clock_corroborate.py e8113528…`. HEAD is `9c3ddc54…`, with no stash and no commits. `git diff --check` is clean. |
| **F-1** (nested `detail.teardown.trace_exit`) | `mac_release_bound.py:150-152` reads `detail.get("teardown")`. The driver's real shape is confirmed: `verify_gone` returns `trace_exit` (`scenario_matrix.py:459`), and the Chrome scenarios store it as `detail["teardown"]` (`:604,608,612,626`). `test_real_driver_pass_detail_is_credited` uses the verbatim `detail` of the committed, unmodified real PASS artifact `…/windows-lifecycle-scan-active/shutdown.json`, where trace_exit is only nested. It asserts `target_evidence`. **Mutations (mine, in memory):** a top-level gate gives 2 failures (`hand_computed…`, `real_driver_pass…`), and dropping the status gate gives 1 failure (`failed_run_with_nested_teardown…`). The retained ABORTED replay stays `diagnostic_only_not_creditable`. So after the fix the pipeline can label a real Chrome PASS `target_evidence`, while ABORTED runs stay diagnostic. |
| **F-2** (anchor errors always propagate) | `:219-222` propagates `anchor_problems` outside the mapped branch. **Mutation:** gating it on `dis_lo is not None` makes `test_empty_anchor_intersection_reason_is_retained` fail. |
| **F-7** (comma-bounded owner) | `macos_poc.py:494` (classifier) and `:514` (sanitizer) both use `mounted by ([^,]+)`. **Mutation:** a greedy `(.+)` at both capture sites makes `test_self_owned_with_trailing_option_is_not_foreign` fail, so a self-owned mount with a trailing flag stays non-foreign. |
| **Clock fallback** | `mac_clock_corroborate.py:88-98` adds 500 ms when any `whole_seconds_centred` stamp is used. **Mutation:** `centring = 0` makes `test_whole_seconds_fallback_adds_the_500ms_centring_term` fail. This is corroboration-only output. |
| **Windows CI gating** | `test_mac_clock_corroborate.py` is now under `if: runner.os != 'Windows'`, next to `test_macos_poc.py`. `test_mac_release_gate.py` and `test_mac_release_bound.py` stay in the portable set. With `sys.modules['pwd']=None`: release_gate passes 4/0/0, release_bound passes 13/0/0, and clock_corroborate has **4 errors**, which confirms the move was necessary and correct. The new evidence dirs carry `* -text`, and `check-attr` shows the real PASS artifact is `text: unset`. YAML could not be machine-parsed here (no PyYAML); I checked it by diff only (4 added lines, consistent indentation, `working-directory: native-bridge/feasibility`). |
| **Tests** | release_bound 13 OK, release_gate 4 OK, clock 6 OK, macos_poc 58 OK, scenario_matrix 136 OK (5 skipped). Full `discover`: **494 OK, 5 skipped**, and all 5 skips are `WindowsMachineSmoke` ("exercises the real Windows APIs; part of the Windows CI leg"). In-memory `compile()` of the 8 sources is OK. |
| **Matrix** | `matrix.py check` reports `matrix ok: NO-GO`. The JSON has 32 rows: 23 PASS / 6 BLOCKED / 3 NOT RUN. `otp23-matrix.json` and `docs/otp-23-requirements-matrix.md` are unchanged vs HEAD. |
| **Gate and v4 pins** | `release_gate`: windows → 2, macos → None, linux → None. HEAD `scenario_matrix.py` = `f8284c98b9dc…`, the immutable historical v4 source. The live diff is only the P0-A `release_gate` and its 5 call sites. The other six pins (`9cad1a70`, `cb512418`, `c6fcfd6e`, `40d8f824`, `06f0de1b`, `78d53ca9`) are live == HEAD. Nothing was regenerated. |
| **F-3 / F-4** | The code-result prose cites `2518c163…` and labels `9a7ffbab…` as an earlier error. The codephase dir holds exactly `.gitattributes`, `SHA256SUMS` and `dry-probe-lsof-classifier.txt` (`beaffc58…`), and `sha256sum -c` passes. |

## 3. Privacy curation: verified (bounded; no history scrub needed)

**Active manifest and curation hashes**
- `SHA256SUMS` `9e8ed077…` has 35 entries and lists exactly the 35 files on disk, with no extras
  either way. `sha256sum -c` passes.
- `FINDING.md` `821ce855…`, `curation/curation-map.json` `75caee81…` (no `original_text`),
  `curation/SHA256SUMS` `4933dce9…` (passes), `manifest-history/pre-curation-archive.json` `60354f85…`.

**Historical manifest accounting** (archived `0bb704d5…`)
- It has 32 lines over **31 distinct paths**. The only duplicate is `.gitattributes`, listed as both
  `./.gitattributes` and `.gitattributes` with the same hash, so the record is truthful.
- **30 of the 31 are byte-identical** in the active manifest. Only `FINDING.md` differs (raw
  `2518c163…` → redacted).
- The active manifest's extra paths are exactly `curation/.gitattributes`, `curation/SHA256SUMS`,
  `curation/curation-map.json` and `manifest-history/pre-curation-archive.json`.
- The `exact_accounting` block matches my count.

**Private archive**
- The tgz is `6564c38e…`, and its members match `PRIVATE-ARCHIVE-CONTENTS.sha256`.
- The directory is `700` and the files are `600`.
- It sits in `/tmp`, so moving it to durable private storage is a parent action. It is **not** a
  hardware gate.

**Name sweep**
- I took the two redacted tokens from the raw-vs-public FINDING diff of the mount sentence. Positive
  control: both appear in the raw original. The tokens were never printed or written.
- Neither token appears in either new evidence dir, including inside `stage/stage.tgz`, in any
  changed or new script, in the workflow, or in the M-11 prep dir.
- In committed HEAD they appear in exactly the 4 `strict-mac-20261027/` files, all added by
  `76a70e4`, as the implementer disclosed.
- That pre-existing exposure stays frozen and out of scope. **No sweeping privacy-absence claim is
  made or warranted.**

## 4. M-11 fixes: verified (execution still gated by M11-1)

**Required flag**
- `--user-scope-manifest` is `required=True`. A bare run exits 2 with
  "the following arguments are required: --user-scope-manifest".
- The docstring, README §3/§5/§7 and prep doc §10 (`ac7f62de…`) correctly withdraw the
  "profile-local / same verified mechanism" claim. The profile copy is described as inert.
- README `3942a596…`, script `3e7be057…`.

**`APPROVED_APP` TypeError:** both sites now use `pathlib.Path(APPROVED_APP) / …` (lines 236 and 408).

**Identity guard**
- Lines 255-264 check `CFBundleIdentifier == me.nagaev.veles.bridge.dev`,
  `CFBundleName == VelesOtp23Dev`, and `CFBundleDisplayName ∈ {None, VelesOtp23Dev}`.
- The check runs **before** the profile, the manifest write and the Chrome `Popen`.
- `package-dev.sh` `fa6ba4e8…` (unchanged) writes exactly those keys and no DisplayName. It pins raw
  `c36894dc…` and runs no codesign. The script pins approved `e7c716c5…`.

**Offline smoke** (the implementer's harness, which I reviewed: it stubs codesign, fakes HOME, and
blocks the Chrome/otp23 `Popen`; it ran in a private tmpfs `TMPDIR` that was deleted afterwards):

| case | result |
|---|---|
| (A) no flag | exit 2, nothing created |
| (B) good plist | single-origin `me.nagaev.veles.otp23.dev` user manifest written, 1 blocked launch, own file removed, fixture re-check OK, record written |
| (C) wrong `CFBundleName` | refused; no manifest, no launch |
| (D) wrong `CFBundleDisplayName` | refused; no manifest, no launch |
| (E) pre-existing dev manifest | refused; the foreign file is left untouched |

All five match the report.

**Owner and ordering**
- A distinct M-11 owner record exists (`otp23-mac-permission-owner-record.json`: `m11_approved: true`,
  `m04_permission_toggles_authorized: false`, not a TCC-reset approval).
- The docs sequence M-11 after the 3 fresh signed M-08 scenarios, with the owner's read-only Settings
  re-check afterwards.
- The script never clicks; the owner chooses, bound to a fresh nonce. Its current inability to
  *wait* for that choice is M11-1.
- I could not check the Mac-side stage or fixture state (no hardware access). I rely on the
  implementer's read-only Mac check for: raw `c36894dc…` present, approved `e7c716c5…` with strict
  verify OK, stage-manifest `d3c25232…`.

## 5. Approved next steps

1. **Mac listener-selftest**, then the **3 fresh Chrome-owned M-08 scenarios** on the signed approved
   fixture.
2. Each needs `mac_release_bound.py` phone-side proof with `creditability: target_evidence`. Release
   gate P0-A applies (Windows-only trace release count).
3. **M-11 only after:** (a) the 3 fresh M-08 baselines pass, and (b) the M11-1 fix plus a micro
   re-verification, with the owner's live prompt nonce and their own name/choice. Nothing is
   automatic.
