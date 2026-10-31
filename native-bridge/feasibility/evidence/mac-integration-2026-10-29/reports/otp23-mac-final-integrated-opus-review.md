# OTP-23 Mac M-08/M-11: final independent integrated-tree review (Opus 5.5, 2026-10-29)

## 0. Role envelope (safe metadata only)

- **Reviewer:** Claude Code, model `claude-opus-5-5`. This is a fresh session,
  `faee6a0e-43c5-41d7-9bc4-a2cd3f3de09b` (taken from the local project log file name), with 0
  subagents.
- **Not:**
  - implementer `752e1974-0617-470b-a425-dfbda97ac728` (A1/A2, M-08 retake, curation)
  - integration implementer `9f6f8b0f-01c7-4ccc-8c3a-f5daf806045e`
  - the micro-reviewer `e30e474d-…`
  - the parent controller `gpt-6.1-sol`
- **Not done:**
  - no hardware, Mac, phone, Chrome, TCC, signing or build
  - no Gradle, Rust or Node rerun (their sources are unchanged and already source-bound)
  - no GitHub, staging, commit or push
  - no source, doc or evidence edit
- **Files:**
  - **Written:** this report only.
  - **Scratch:** `/dev/shm/otp23rev-*`, deleted.
- **Side effect, disclosed:** the full Python suite run rewrote 3 Git-ignored
  `scripts/__pycache__/{macos_poc,macos_cdp,macos_install}.cpython-314.pyc` files. The cause is
  `test_macos_poc.py:52`, which deliberately runs `python -m py_compile`, so `-B` cannot prevent it.
  The same effect applies to the implementer's gate 04, so "no new .pyc" in the handoff is only
  approximately true. The files are Git-ignored, so the publication set is not affected.
- **Reviewed tree:**
  - HEAD `9c3ddc54a3721eced84e49f02ed76ef156f0baed` plus the working tree
  - `git diff` sha256 `dec764f4dcd02f62dc4a55c2a3b28d29468520d60ed1ba4d0a615cf91a9ac50d` (recomputed; it equals the handoff and `gates/09`)
  - active integration `SHA256SUMS` `dbada608c3b15528768c37d2e0b4cd11e6829a2a5b875c92a4939cc12a87c89f`

## VERDICT: CHANGES REQUIRED, bounded and non-substantive. Approval follows on the closure in §7

**Substance: approved.** All of the following are verified and truthful:
- the M-08/M-11 promotion
- matrix 25/4/3 NO-GO, with exactly W-10/M-03/M-04/A-01 open and only W-01/M-01/M-02 waived
- the retained history, the A2-only adoption and the precision non-claim
- the M-11 caveats, the TCC supplement scope, M-03/M-04 unchanged, the RFC follow-up, the
  `matrix.py`/`test_matrix.py` changes and the workflow selection

**Not approvable as-is.** Three findings block it:
- one publication-curation finding (**F1**: unrelated names in the new published set)
- two provenance-accuracy corrections (**F2**, **F3**)

None of them needs a source, analysis or acceptance-logic change. §7 authorizes exactly how they are
closed. After that closure, this review counts as **APPROVED for commit/publication pending clean
export and green CI**, with no further integrated-review loop.

## 1. Integrity against the approved micro-review (e30)

**Source hashes, all recomputed and equal to e30 §1, `gates/09` and the handoff:**

| file | sha256 |
|---|---|
| `mac_release_attribution.py` | `ce75a8f1…634e` |
| test | `219c78be…f016` |
| `mac_release_bound.py` | `a49b3450…6560` |
| `test_mac_release_bound.py` | `4b8aa10d…` |
| `scenario_matrix.py` | `f8bbf2d6…` |
| `scenario_matrix_core.py` | `cb512418…` |
| `macos_poc.py` | `e2131ad3…` |
| `mac_clock_corroborate.py` | `8e6b9bbb…` |
| workflow | `df1305d4…` |
| `matrix.py` | `3d45aac1…` |
| `test_matrix.py` | `4a7b106a…` |
| `otp23-matrix.json` | `8a54f571…` |

**Copied report.** The `reports/otp23-mac-safe-attribution-verification-opus.md` copy is
byte-identical to `/tmp/opencode/…` (`cmp`; `40cb23df…`).

**Manifests:**
- I verified every evidence `SHA256SUMS` myself: **24 manifests, 1,304 listed entries, all OK**.
- **Disk set equals the manifest** for every new Mac folder, counting the nested `curation/SHA256SUMS`
  files, which are listed by their parents. The "disk ≠ manifest" results in the tracked older folders
  are their pre-existing nested-manifest layout and are unchanged.
- **Frozen digests unchanged:**

| folder | digest |
|---|---|
| retake | `f952c123` |
| A2 safe | `c05174f4` |
| A1 | `dd05dac4` |
| source-history | `2a550c1c` |
| m11-dev | `071d14f1` |
| resume | `9e8ed077` |
| codephase | `339505b6` |
| tcc | `a1e4d80b` |

- **No files changed since e30.** None of the frozen folders has a file newer than the e30 report,
  apart from `mac-m11-dev`. That folder's 6 curation-phase files (11:19–11:23) are inside the pinned
  `071d14f1` and were reviewed here (§4).
- **Earlier integration entries are byte-unchanged.** All 26 entries in
  `history/SHA256SUMS.curation-as-of-…` (`7bb6af16…`) are still listed in the active manifest with
  identical digests.

**Git:**
- `git check-ignore` matches none of the 216 new evidence/script files.
- Every new folder has `.gitattributes` `* -text -whitespace`.
- The root has `* text=auto eol=lf`, so `.py` self-hash pins also hold on Windows checkouts.

## 2. Matrix, renderer and tests

**Rows changed versus HEAD (JSON diff):** only M-03, M-04, M-08, M-11, D-02 and R-01, plus two
**new** source keys. No source key was removed or changed.
- **`actual` and `evidence` are append-only** (old text is a prefix, old lists a prefix). The one
  exception is M-11 `actual`, where the old text is kept verbatim behind
  `PRE-INTEGRATION RECORD (…preserved verbatim)`.
- **The M-08/M-11 `blocker` fields were emptied.** That is the convention for PASS rows: no HEAD PASS
  row has a blocker.
  - **M-08:** the substance of its old blocker is either resolved in the appended text (connected-phase
    18/18 samples; Chrome-owned `stdin_closed`) or still present in the preserved `actual`
    (deferral, `loadUnpacked`, preservation gap).
  - **M-11:** its old blocker text is preserved in the PRE-INTEGRATION record.
- **Command results:**
  - `matrix.py check` → `matrix ok: NO-GO` (exit 0)
  - `decision` → `NO-GO: 4 row(s) open: W-10, M-03, M-04, A-01`, waived `W-01, M-01, M-02`, exit 1.
    That is **the expected NO-GO, not a hidden failure.**
  - Counts are PASS 25 / BLOCKED 4 / NOT RUN 3 / FAIL 0.
  - The rendered doc equals `render(load())` (`True`).
- **`matrix.py` diff.** It adds one dated render paragraph and qualifies "23/6/3" as "as of 2026-10-28
  (superseded …)". The `validate` and `decision` logic is unchanged. Wording is truthful: no gate
  change, no waiver, OTP-24 not approved.
- **`test_matrix.py` diff:**
  - `OPEN_ROWS` is now exact equality (stricter, not looser).
  - M-08/M-11 were added to the promoted list.
  - The hardware-PASS evidence-base allow-list gained the two frozen Mac folders.
  - The M-11 history test checks that the `PRE-INTEGRATION RECORD` is preserved.
  - The signing-amendment test requires M-11's own 2026-10-29 source.
  - The new invariant test pins 6 frozen manifest digests and re-hashes every listed file. It checks:
    - A1 is not cited
    - A2 is PASS/creditable/unique
    - the hull is PASS/FAIL/FAIL
    - all 19 checks are true
    - the M-11 raws keep their null answers, deadline classifications and problems
    - exactly 1 `AUTHREQ_PROMPTING` record, for `.dev` / `95540.2`
    - M-03/M-04 stay BLOCKED
    - D-02/R-01 strings, the render paragraph and the RFC
  - These are sound and non-circular. `test_matrix.py` → **Ran 35, OK**.
- **Windows portability of the new test.** It uses `read_text()` without an explicit encoding, which
  is acceptable: all 12 JSON/ndjson files it decodes are pure ASCII (0 bytes ≥ 0x80), and the hashing
  is byte-level. The RFC is read as UTF-8, as is the established style.

## 3. Substantive claims (spot-verified against raw data; full code and physical reviews not redone)

**M-08:**
- **Data.** Values match e30 §3 and the physical review §1:
  - 18 samples with 0 windows at all layers and `tcp4 []`, with positive controls
  - `shutdown_complete` last for shutdown and `stdin_closed` for port-disconnect/tab-close
  - exit 0, drained; host gone in 0.004 / 0.004 / 0.113 s
  - widths 235–282 ms, margins +54…+93 ms
  - hull PASS/FAIL/FAIL; A1 not cited
- **Global-last, exit+30 s and stop guards.** These were retained. The 250 ms target is explicitly
  "NOT an RFC gate … NOT claimed as met".
- **Deferral chronology:**
  - deferral `recorded_utc 2026-10-28T08:48:33Z` (`ace5e31e…`, verified)
  - then the resume owner record, "Updated and relaunched", 156.0.8078.74
- **W-08 reading (independently checked here).** W-08 expects "Same cleanup after the extension stdin
  pipe closes without a shutdown request". In the real topology the extension's port disconnect or tab
  close makes Chrome close the host's stdin. The retake shows `stdin_closed` with no shutdown frames,
  exit 0, drained and gone, which is the literal clause. The Windows W-08 precedent was independently
  approved earlier. I **concur** that the direct-SSH EOF correctly stays a `permission_pending`
  diagnostic, neither PASS nor waived. See F2 for the attribution error.

**M-11:**
- **Raw captures.** Attempt 1 is a setup FAIL (`port_closed`, synthesized). Attempts 2 and 3 have
  `human_prompt_answer null` and `ended_by deadline`, with states `pending_at_host_budget_not_denial`
  and `authorized_started`, and non-empty problems.
- **Account record.** `a2bdc40c…` is labelled post-run.
- **Operator and code roles.**
  - **Operator:** `gpt-6.1-sol`, explicitly "not an Opus operator".
  - **Code:** implemented by Opus `752e1974`, reviewed by Opus `7b4ee6ca`.
  - **`c401a87c`:** a 429 before any tool (`model-metadata.json`: `api_error usage_limit_reached`, 429).
- **No retroactive binding and no during-run NoDialog claim.** No scan-enumeration or discovery claim
  is made.

**TCC supplement (raw recomputed):**
- `AUTHREQ_PROMPTING` msgID `95540.2` at 12:29:58.733307+0700 = 05:29:58Z, inside attempt 2
  (05:29:57–05:42:00Z), service `kTCCServiceBluetoothAlways`, `Sub:{me.nagaev.veles.bridge.dev}`
- `AUTHREQ_RESULT` at 13:17:25.367351+0700 = 06:17:25Z, `authValue=2`, then `REPLY`
- This is 35 min after the deadline and about 1 min before the owner's answer file (06:18:28Z),
  consistent with Allow.
- The scoped history has exactly one PROMPTING record. The signed-subject records are only
  `AUTHREQ_SUBJECT` and begin 2026-10-28 22:44 +0700.
- The captured nulls are not replaced. The original signed prompt owner is not inferred, and the
  current signed subject is explicitly "not the original prompt". M-03/M-04 stay BLOCKED, with no
  off/on toggle approved or done.

**RFC and docs:**
- **RFC.** The dated 2026-10-29 follow-up sits in the amendment list. The "M-08 Mac deferral
  follow-up" paragraph comes after the untouched deferral paragraph, and "Cannot restart Chrome now"
  is retained. No requirement is amended and no waiver is added.
- **Investigations §7 and contract-alignment §20.** These are consistent with the matrix and correctly
  say the final integrated review is **pending**. R-01 stays historical PASS with the PENDING
  current-phase note, and I do not treat that note as this review.
- **Unchanged.** No owner or dependency changes; #86 is described as Blocked; PR122 is not touched by
  this tree.

## 4. Publication set, provenance and privacy

**Metadata and maps:**
- **`model-metadata.json` / `model-metadata-final.json`.** Field paths are whitelisted only:
  - session id, model keys, provider
  - `is_error`, `terminal_reason`, `api_error_status`, `api_error` (enum string only)
  - turns, subagents, role and private envelope sha
  - No result text, usage, cost, headers or bodies.
- **Roles.** Implementer, curator, reviewer, operator and owner roles are kept separate.
- **`final-integration-map.json`.** Its hashes check:
  - the public curation copy `98a3c3a4…`
  - the private original `bb2b6cff…`, which equals `/tmp/opencode/…`
  - the diff from that original is exactly the term-list line plus the leading comment

  Every new `/tmp`, `/dev/shm` and `/home` citation is resolved.
- **The three earlier `.public.md` redactions** diff from their private originals only on the named
  tokens.

**No private archives, events transcripts, API keys or headers, or cost/usage bodies** are in the
216 new files. The two `stage.tgz` files hold only staged scripts, the extension and `identity.rs`,
with 0 hits for the redacted tokens.

**Already-public identifiers.** The LAN IP `192.168.11.116`, the VPN client `10.13.13.1`, the SSH key
*file name* `id_sirius_ed25519`, the `raiden` user, and the Apple Development CN/team appear in new
helpers, provenance and reports. All of these are **already published in committed HEAD** (for
example `strict-mac-20261027/clock-measure.py` and `calibration-*.jsonl`), and no key material
exists anywhere. That is not a new exposure, so it is not a finding.

**Unrelated names: one hit → F1.**

## 5. Gates and CI selection

**Gate logs:**

| gate | result |
|---|---|
| 01 forced Spotless (`--rerun-tasks`) | exit 0 |
| 02 full `spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` | `BUILD SUCCESSFUL` |
| 03 both raw-Log guards | `ci-log-guards.sh` is byte-for-byte the de-indented `ci.yml` block; `guard_exit_code=0` |
| 04 Python | **Ran 512, OK (skipped=5)**; the 5 are `WindowsMachineSmoke` |
| 05 pycompile | 50 files |
| 07 `git diff --check` | 0; I reran it and got 0 |
| 08 manifests | exit 0 |

- **Gate 04 reproduced.** I reran it: 512 OK, skipped=5.
- **Gate 06:** see F3.
- **Gradle, Rust and Node.** I did not rerun them, because their sources are unchanged.

**Portable test selection:**
- **Newly selected on all OSes:** `test_mac_release_gate`, `test_mac_release_bound` and
  `test_mac_release_attribution`. Their import closure is `mac_release_attribution` →
  `mac_release_bound` → `scenario_matrix`/`_core`, with no `pwd` or POSIX-only module at import time.
  The analyzers read with `encoding="utf-8"`, the frozen inputs they replay are ASCII, and
  `eol=lf`/`-text` protect the self-hash and evidence-hash pins.
- **Correctly POSIX-guarded:** `test_mac_clock_corroborate` (it imports `pwd`), plus the existing
  `test_macos_poc`.
- **Still outstanding (a publication follow-up, as stated in the task):** CI Windows execution and a
  clean `git archive` export.

## 6. Findings (most severe first)

### F1 — MEDIUM, publication curation: unrelated applications' names in the new published set

**Location.** `native-bridge/feasibility/evidence/mac-resume-2026-10-28/registration/record.json`
(new in this publication, under the `9e8ed077…` manifest). Its `before_…native_messaging_hosts_dir_listing`
publishes four other applications' Native Messaging manifest file names from the owner's account.

**Conflict with the project's own policy.** The same publication redacts exactly this class of names
from the Kimi report and the curation report:
- `curation-map.json` policy: "unrelated applications' manifest names … published as `*.public.md`
  with placeholders"
- the curation report's term list is itself redacted

The raw listing therefore defeats those redactions, and it violates the task criterion "no …
unrelated names in new published set".

**Scope:**
- This is the only such locus. A sweep of all 223 new files for foreign NM manifest ids and the
  redacted tokens found only this file.
- The 10-28 resume curation targeted only the other-user/foreign-app tokens. The 10-29 name sweep
  covered only `mac-m11-dev` and `mac-integration`. Neither published claim is false as scoped.
- **The incremental exposure is small.** Committed HEAD (`strict-mac-20261027/f2-provenance-comparison.md`)
  already says "1Password/Claude/Drive entries". Even so, it is an inconsistency that must be
  dispositioned, not left silent.

**No test reads `registration/record.json`.** `test_mac_release_bound` and
`test_mac_release_attribution` replay only `lifecycle-1/` and `phones/` of `mac-resume`. No test,
matrix row or doc pins `9e8ed077`.

### F2 — LOW, provenance accuracy: W-08 reading misattributed to the micro-review

**Location.** `mac-integration-2026-10-29/adjudications/m08-final-adjudication.md`, under "Direct SSH
stdin EOF". It says the extension-owned EOF clause is met "under the W-08 interpretation that the
micro-review verified independently".

**What the record shows:**
- The e30 micro-review and the e30 data review do not mention W-08 or the EOF reading.
- The physical review (`7b4ee6ca`) verified the `stdin_closed` teardown facts but not the
  interpretation.
- The reading came from the parent preflight decision (`m08_eof_scope`), and the Opus preflight asked
  for it to "get independent review".

**Effect.** This review now supplies that independent check (§3), and I concur with the reading. The
attribution sentence is still wrong. The matrix's "independently verified" becomes true once the R-01
approval records this review.

### F3 — LOW, provenance accuracy: gate 06 logs a command that cannot yield its recorded result

**Location.** `gates/06-matrix.log`. It records
`python3 scripts/matrix.py render (stdout) | diff - ../../docs/otp-23-requirements-matrix.md` →
`render_diff_exit_code=0`.

**Why it cannot.** `matrix.py render` without `--write` prints `matrix ok: NO-GO`, not the document.
Run literally, that pipeline exits **1** (reproduced). The invariant itself holds:
`render(load()) == doc` is True, and `matrix.py render`/`check` return 0, which includes the
staleness check. Only the logged command is inaccurate.

### Notes (non-blocking; no action required for approval)

- **N1.** e30's M9 test gap (the per-notify bound-callback check) is still open. It is optional.
- **N2.** The draft PR text says the "clock-dependent test runs off Windows only". The actual reason is
  that `test_mac_clock_corroborate` imports `pwd` (POSIX). Rewording is optional.
- **N3.** The bytecode side effect described in §0. Optional precision for future gate wording:
  "`-B`, except `test_macos_poc`'s deliberate `py_compile`, which rewrites Git-ignored caches".

## 7. Authorized bounded closure (no new integrated-review loop)

This is provenance and curation only. Do **not** touch any script, test logic, matrix status, gate,
waiver, owner, dependency or other frozen folder. Any deviation from these steps, or any other
source or acceptance edit, needs re-review.

**1. F1, choose exactly one option.**

**(a) Default: redact in place,** following the existing `mac-resume` FINDING.md curation precedent.
1. In `registration/record.json`, replace only the four foreign file names with
   `"<other application's manifest>"`. Keep the array length (4) and every other byte, including
   `veles_manifest_present false`.
2. Move the original byte-exact to
   `/tmp/approved-otp23-privacy/mac-resume-2026-10-28/registration-record.json.private-original`
   (600, outside Git).
3. Extend `mac-resume-2026-10-28/curation/curation-map.json` with one redaction entry carrying both
   sha256 values and the scope description. Do not quote the names.
4. Regenerate `curation/SHA256SUMS` and the folder `SHA256SUMS`.
5. Copy the old manifest byte-exact to
   `mac-resume-2026-10-28/manifest-history/SHA256SUMS.pre-registration-redaction` (`9e8ed077…`).
6. **Mechanical acceptance:**
   - the `diff` against the private original touches only those 4 strings
   - the sweep for those names over all new files gives 0 hits
   - `sha256sum -c` passes and the disk set equals the manifest
   - the replay tests pass unchanged
7. Then update the citations of `9e8ed077` in `FINAL-INTEGRATION.md`, which lists it as "unchanged":
   either update them to the new digest, or append a dated note that maps old → new. The copied
   historical reports stay byte-unchanged; their `9e8ed077` is history.

**(b) Alternative, only with an explicit owner decision:** keep the bytes frozen.
1. Record an operator-captured owner acceptance (verbatim question and answer) stating that these
   four own-account manifest names may be published, and noting the HEAD precedent.
2. Add one disclosure entry in `final-integration-map.json` and in `FINAL-INTEGRATION.md`.

The parent may not choose (b) for the owner.

**2. F2.** In `m08-final-adjudication.md`, replace "that the micro-review verified independently" with
an accurate attribution:
- the parent preflight decision chose the reading
- this final integrated review (`faee6a0e-…`) independently verified it

Alternatively, append a dated erratum line with the same content. Make no other wording changes.

**3. F3.** When rerunning gate 06, log the command that is actually executed. For example, use
`python3 -B scripts/matrix.py render` (exit 0, which includes the staleness check), or an explicit
`render(load()) == doc` comparison, with its true exit code.

**4. Publish this review.**
1. Copy this report byte-identically to
   `mac-integration-2026-10-29/reports/otp23-mac-final-integrated-opus-review.md`.
2. Add whitelisted metadata to `model-metadata-final.json` (or a sibling file): session
   `faee6a0e-43c5-41d7-9bc4-a2cd3f3de09b`, model `claude-opus-5-5`, Claude Code, 0 subagents, role
   "INDEPENDENT final integrated-tree reviewer; not implementer 752e1974/9f6f8b0f, not micro-reviewer
   e30e474d, not parent", and verdict. Use envelope fields only, with no transcript or result body.
3. Map the `/tmp/opencode/otp23-mac-final-integrated-opus-review.md` citation in
   `final-integration-map.json`.

**5. Record the approval.**
1. Append the actual approval to R-01, after the existing PENDING note, which stays. Name this review,
   its session, its verdict ("CHANGES REQUIRED → closed by the bounded F1–F3 closure; APPROVED for
   commit/publication pending clean export and green CI"), and the F1 option used. Include no
   OTP-24/GO wording.
2. Update the "pending" statements in `docs/otp-23-investigations.md` §7,
   `docs/otp-23-contract-alignment.md` §20, `FINAL-INTEGRATION.md` and the PR draft by appending, not
   rewriting.
3. In `test_matrix.py`, you may add only `assertIn` checks for the new R-01 approval string. No other
   test changes.

**6. Regenerate and rerun.**
1. Run `matrix.py render --write`, then `check` and `decision`; expect exit 1, NO-GO, with W-10/M-03/M-04/A-01.
2. Rerun every gate in handoff §4: forced Spotless; full Spotless/detekt/lintDebug/unit/protocolCheck;
   both Log guards; the Python suite (expect 512 or more, OK, 5 Windows skips); pycompile;
   `git diff --check`; all manifests.
3. Refresh **only** the active `mac-integration-2026-10-29/SHA256SUMS`, plus the `mac-resume` manifests
   under F1(a).
4. Confirm 24 manifests, and re-hash every other frozen folder against §1.

**7. Approval criteria.** If every check in step 6 is green and the diff from this reviewed tree is
confined to steps 1–6, this review counts as **APPROVED for commit/publication pending clean export
and green CI**. Those remaining publication follow-ups are still required:
- a clean `git archive` export with all manifests verified, plus `matrix.py check` and `test_matrix.py`
  on the export
- green Linux, macOS and **Windows** feasibility CI and main CI
- durable private storage of `/tmp/approved-otp23-privacy/`
- PR123 body update with #122/#86 unchanged and nothing merged

OTP-24 stays unauthorized and the decision stays NO-GO.
