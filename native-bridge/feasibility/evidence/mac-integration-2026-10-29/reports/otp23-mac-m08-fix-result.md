# OTP-23 Mac M-08 minimum-fix phase: completion report (implementer continuation, 2026-10-29)

## 0. Role envelope

- **Role:** IMPLEMENTER. Claude Code, model `claude-opus-5-5`, session
  `752e1974-0617-470b-a425-dfbda97ac728`, no subagents. Authorized by
  `/tmp/opencode/otp23-mac-opus-continuation-owner-record.json`. Metadata (transcript-derived):
  `/tmp/opencode/otp23-mac-m08-fix-opus-implementer-metadata.json`.
- **Predecessor:** GLM `ses_e7cde50b5ffezoaQLsnQlF6FlE` (ollama-cloud/glm-5.3-flash) hit an API 429 at
  2026-10-28T18:00:51Z. It stopped before re-running tests after its privacy and M-11 edits, before
  the M-11 README edits were checked, and partway through the Mac upload. Its work is preserved and
  attributed to it below. I didn't take its claims on trust; everything was re-verified on disk.
- **Other roles unchanged:** Kimi K3 wrote the completed code verification. Opus wrote the design
  preflight. The interrupted Opus code verification approved nothing.
- **This is not an independent review.** It is handed back for independent micro-verification by a
  separate fresh Opus session.

## 1. Repository state

HEAD `9c3ddc54a3721eced84e49f02ed76ef156f0baed`, branch `contract/otp23-desktop-alignment`, no
commits, no stash, nothing pushed. `git status` shows 4 modified files (workflow, `macos_poc.py`,
`scenario_matrix.py`, `test_macos_poc.py`), 5 new scripts/tests, and 2 new evidence dirs. Diff on
tracked files: `4 files changed, 377 insertions(+), 13 deletions(-)`.

## 2. Final source inventory (sha256, on disk at 2026-10-29T04:11Z)

| file | sha256 | vs Kimi-reviewed | changed by |
|---|---|---|---|
| `.github/workflows/otp23-feasibility.yml` | `95f78691b9daede65cf7917fa27366c194cab358e02fc622ed04d7aa2afd5808` | was `081924dc…` | GLM added a step; Opus moved it to the non-Windows set (§4.1) |
| `scripts/macos_poc.py` | `e2131ad32a8488cebbdc6646954c968b4f56593d8a9287f78677386ba44734e0` | was `3d2eccae…` | GLM (F-7) |
| `scripts/scenario_matrix.py` | `f8bbf2d6ea158d2bd30bee35b97a655c727a7ca7fc677a5c9a3a53eafe6b7f28` | unchanged | none |
| `scripts/test_macos_poc.py` | `a729dcceb71c2bf8123c2529b59db37f8ede0bf5f9af170bd3b9f2f4f7d3fb3a` | unchanged | none |
| `scripts/mac_release_bound.py` | `a49b3450d6d984c24e87ae60b4ffd00f4ca661beb92ec56b9b5410fc6bc26560` | was `7e779e97…` | GLM (F-1, F-2) |
| `scripts/mac_clock_corroborate.py` | `8e6b9bbbb221b33285a7a6b30e9122ef826a74e6dcbfd450550f4dbaf19c418f` | was `50e2fe2c…` | GLM (fallback +500 ms term) |
| `scripts/test_mac_release_bound.py` | `4b8aa10d1909774016c4b81f8e682f717e7bb03619c1dbc701c01ea78fc7dc22` | was `a66e5ca4…` | GLM (nested fixture + 3 tests); Opus (+1 real-artifact test) |
| `scripts/test_mac_release_gate.py` | `7f61cdeec80bac3e1e1ad748e551614ef442f9b8d0d839847c1e4fd858d0533f` | unchanged | none |
| `scripts/test_mac_clock_corroborate.py` | `e811352823106b57e97a3164b8b0a974c294afa846c46fe406dd799fb2656803` | new | GLM |

v4 pins: `scenario_matrix.py` at HEAD is still `f8284c98b9dc…`, the historical pin source at
`9c3ddc5`. Live differs only by the approved P0-A Mac gate. Nothing was regenerated. The other six
pins (`package1_b5_v2_derive 9cad1a70…`, `scenario_matrix_core cb512418…`,
`package1_phone_burst_telemetry c6fcfd6e…`, `package1_phone_bound 40d8f824…`,
`pc_phone_clock_probe 06f0de1b…`, `chrome_validate 78d53ca9…`) have live == HEAD.
Windows: `release_gate("windows") == 2`, `release_gate("macos") is None`, unknown → None.

## 3. Reviewer findings: status after this session

| finding | status | verification |
|---|---|---|
| F-1 gate reads `detail.teardown.trace_exit` | DONE (GLM), verified | Code at `mac_release_bound.py:151-152`. The hand-computed test now uses the real nested shape and asserts `target_evidence`. **Added (Opus):** `test_real_driver_pass_detail_is_credited` feeds the verbatim `detail` of the committed real PASS artifact `evidence/post-grant-2026-10-26/windows-final/post-grant-2026-10-27/windows-lifecycle-scan-active/shutdown.json` (trace_exit only nested) and asserts `target_evidence`. Mutation: restoring the old top-level gate fails 2 tests. |
| F-2 anchor problems always propagated | DONE (GLM), verified | Propagation moved out of the mapped branch. Mutation: moving it back fails `test_empty_anchor_intersection_reason_is_retained`. |
| F-3 FINDING pin prose | DONE (GLM), verified | `otp23-mac-m08-code-result.md:174-176` now cites `2518c1637fb20ff765c5df88579ecbbd23007f2d0077b6468ff81dd5fd63e5d1` and notes the earlier error. |
| F-4 0-byte `SHA256SUMS.tmp` | DONE (GLM), verified | Codephase dir = `.gitattributes`, `SHA256SUMS`, `dry-probe-lsof-classifier.txt` (`beaffc58…`); `sha256sum -c` OK. |
| F-5, F-6 | not changed (optional, by design) | none |
| F-7 comma-bounded `mounted by` | DONE (GLM), verified | `macos_poc.py:494,514,517`; 4 targeted tests. |
| §9 nit: clock fallback uncertainty | DONE (GLM), verified | `summarise()` adds 500 ms when any `whole_seconds_centred` stamp is used; 2 tests. |
| F-M11-1 | DONE (GLM partial + Opus completion), staged | §6 |

## 4. New issues found and fixed in this session

1. **Windows CI regression (introduced by GLM's workflow edit).** `test_mac_clock_corroborate.py` sat
   in the portable block that also runs on `windows-2025`. Its F-7 tests call `import pwd`, and so
   does `macos_poc.foreign_nullfs_mounts()` (outside its `try`). Simulating a platform without `pwd`
   (`sys.modules["pwd"]=None`) gives 4 of 6 tests erroring with `ModuleNotFoundError`. Fix: that one
   step moved next to `test_macos_poc.py` under `if: runner.os != 'Windows'`.
   `test_mac_release_gate.py` and `test_mac_release_bound.py` stay portable, and both pass with
   `pwd` blocked (4 OK, 13 OK). Source unchanged.
2. **M-11 capture script couldn't run (pre-existing in the Kimi-reviewed `833a0f52`).** See §6.

## 5. Test commands and actual results (final bytes)

```
cd native-bridge/feasibility/scripts
python3 test_mac_release_bound.py      # Ran 13, OK
python3 test_mac_release_gate.py       # Ran 4, OK
python3 test_mac_clock_corroborate.py  # Ran 6, OK
python3 test_macos_poc.py              # Ran 58, OK
python3 test_scenario_matrix.py        # Ran 136, OK (skipped=5)
python3 -m unittest discover -p "test_*.py"   # Ran 494, OK (skipped=5)
python3 matrix.py check                # matrix ok: NO-GO
python3 -m py_compile scenario_matrix.py macos_poc.py mac_release_bound.py mac_clock_corroborate.py \
  test_mac_release_bound.py test_mac_release_gate.py test_mac_clock_corroborate.py test_macos_poc.py   # OK
git diff --check                       # clean
```

- **Delta:** Kimi saw 484. GLM reported 493 (+6 clock/parser, +3 release-bound), but measured
  before its privacy/M-11 edits. 494 = 493 + my 1 real-artifact test.
- **Skips:** all 5 are `WindowsMachineSmoke` ("exercises the real Windows APIs; part of the Windows
  CI leg").
- **Matrix:** 32 rows = 23 PASS / 6 BLOCKED / 3 NOT RUN (the 3 waived rows), NO-GO.
  `acceptance/otp23-matrix.json` and `docs/otp-23-requirements-matrix.md` untouched.
- **Lint gate:** PyYAML is not installed here, so the workflow was checked by diff only (two moved
  step lines, consistent indentation). The CLAUDE.md gradle lint gate applies before PRs; no PR or
  commit was made, and it was not run.

## 6. Privacy curation (`evidence/mac-resume-2026-10-28/`)

**State on disk:**
- `FINDING.md` is the redacted narrative `821ce855efd12c9509fa3c2b331e768e10d64f2f8ee199d0a4b35327a78aeedf`.
- `curation/curation-map.json` is `75caee81a8f39256bfa312fe7f416038d60a42d0df77ea6c509cb2c8ccdd0a03`,
  with no `original_text` field and no private names.
- `curation/FINDING.public.md` was removed (identical bytes are now `FINDING.md`).
- The active `SHA256SUMS` (`9e8ed07767a0ed6f6da4094685bd6669d7f9a081f6975d9b8ecafed1917af022`, 35
  entries) lists exactly the files on disk. `sha256sum -c` passes, as does `curation/SHA256SUMS`
  (`4933dce9…`).

**Raw bytes:** the archived original manifest has 32 lines over **31 distinct paths**:
`.gitattributes` is listed twice, as `./.gitattributes` and `.gitattributes`, with the same hash
`e97602ab…`. Of those, the 30 non-narrative paths (`.gitattributes` + 29 measurement/helper/stage
files) are **byte-identical** in the active manifest. Only `FINDING.md` differs, and it's excluded
and private. Verified by my own hash comparison.

**Historical mapping (not retconned):** `manifest-history/pre-curation-archive.json` (now
`60354f853f6970a78e6e0abf2fd1ca3b592a42477e7994c5e4d8eedc08718494`) records the superseded manifest
`0bb704d5…` as historical/not active, plus the private archive hashes. I corrected two inaccuracies
in GLM's version: `curation/FINDING.public.md` and `curation/curation-map.json` were never covered by
the old manifest, and the record had no exact line/path accounting (`exact_accounting` added).

**Private archive:** `/tmp/approved-otp23-privacy/` sits outside the workspace. The tgz is
`6564c38e…`, and its members match `PRIVATE-ARCHIVE-CONTENTS.sha256` (raw FINDING `2518c163…`, old
manifest `0bb704d5…`, original map `50831d4d…`, public copy `821ce855…`). It was world-readable;
I set it to `700`/`600` (bytes unchanged). **Caveat:** it lives in `/tmp`, so the parent should move
it to durable private storage.

**Name sweep:** the two redacted tokens are absent from both new evidence dirs (including inside
`stage/stage.tgz`) and from all changed scripts. **Pre-existing exposure (not fixed, out of scope,
parent decision):** the other local user's account name already appears in committed history.
Commit `76a70e4` ("Verify latest-Chrome macOS strict background delivery") contains captured `who`
output in 4 files under `evidence/post-grant-2026-10-26/strict-mac-20261027/`
(`operator-cal-output-1-aqua-153840Z.txt`, `operator-cal-output-2-aqua-155151Z.txt`,
`calibration-cal-ssh-20261027T155808Z.jsonl`, `calibration-preflight2-snapshot.jsonl`). So the
FINDING curation does not make the name absent from the repo. Removing it would need a new commit
or a history rewrite, neither of which is allowed in this session.

## 7. M-11 prep (local `/tmp/opencode/otp23-m11-prep/` and the Mac stage)

**`m11_dev_capture.py` → `3e7be057a2b87351b30f9f4f79c8d1339bdecb24623651054e2e7766760b6b3b`**
- **GLM:** made `--user-scope-manifest` `required=True`, so argparse rejects a run without it
  before anything is created. Docstring corrected.
- **Opus fix 1, `APPROVED_APP` TypeError (pre-existing, Kimi-missed).** `APPROVED_APP` is a `str`, but
  it was used as `APPROVED_APP / "Contents/MacOS/otp23-host"`. That raised `TypeError` at the first
  precondition, and again in `finally`, so the record was never written. It failed closed, but M-11
  could never have run. Fixed with `pathlib.Path(APPROVED_APP) / …` at both sites.
- **Opus fix 2, pre-launch dev identity/label guard.** The dev `Info.plist` must read
  `CFBundleIdentifier=me.nagaev.veles.bridge.dev` and `CFBundleName=VelesOtp23Dev`, with no differing
  `CFBundleDisplayName`. Otherwise the script exits before any manifest write or launch, and the
  values are recorded. The read-only Mac check showed the approved fixture has no
  `CFBundleDisplayName` and gets its label `VelesOtp23PoC` from `CFBundleName`, so
  `package-dev.sh` (`fa6ba4e8…`, unchanged) already produces the matching `VelesOtp23Dev` delta.
- **Offline smoke on Linux.** Scratch harness `/tmp/opencode/otp23-m08-opus-impl-scratch/m11-smoke/smoke.py`
  runs with a fake stage and home, stubbed codesign, and Chrome `Popen` blocked:
  - (A) no flag → exit 2, nothing created
  - (B) good plist → exact single-origin `me.nagaev.veles.otp23.dev` user-scope manifest written,
    one blocked launch attempt, byte-verified own-file removal, fixture re-check OK, record written
  - (C) wrong `CFBundleName` → refused, no manifest, no launch
  - (D) wrong `CFBundleDisplayName` → refused, no manifest, no launch
  - (E) pre-existing dev manifest → refused, foreign file left untouched

**`README-M11-PREP.md` → `3942a5963ab25f454026626949b2f7b45f2d1204533d4c67b3fe17babf1aa2c7`**
- GLM's §3 replacement had **silently not matched**: the text still claimed the profile-local
  default was "the same verified mechanism". GLM checked only one of its replacements.
- §3 is rewritten (only the user-scope layer is relied upon; the profile copy is inert), as are the
  §5 flow text and the §7 baseline/cleanup bullets. The GLM-added "ideally one signed probe" is
  re-worded as optional and parent-scheduled only. The 21+3 source-count prose is fixed.

**Prep doc** `/tmp/opencode/otp23-mac-m11-preparation.md`: §1–§9 kept verbatim, §10 appended with the
superseding command, the stage table and the history (`18777918…` → `ac7f62de…`).

**Superseding run command** (NOT executed; only after the three fresh signed M-08 retake scenarios):
`python3 m11_dev_capture.py --stage . --out-dir capture/m11-attempt-1 --user-scope-manifest`

### 7.1 Mac staging (`/Users/raiden/homepc/work/otp23-m11-dev`)

**Connectivity:** one read-only check at 2026-10-29T04:07:46Z; the Mac was awake.

**Findings from that check:**
- GLM's interrupted upload had put its intermediate `m11_dev_capture.py` (`8b804fd9…`) loose in
  `~/homepc/work/`. Its remote install script never ran, so the stage was still the Kimi-verified
  pre-fix set.
- No veles/otp23 manifest in the user or system NM dir; `target-dev/` absent.
- The approved fixture `e7c716c5…` passes `codesign --verify --strict`: `Identifier=me.nagaev.veles.bridge`,
  `TeamIdentifier=7T39M26Z99`.
- The raw counterpart `c36894dc…` has an ad-hoc signature, `Identifier=otp23_host-b86638eeb61c91b4`.
- Two owner `Google Chrome` processes were running. Observed only, not touched.

**What staging did:**
- Uploaded the two files into a new `_incoming-2026-10-29/` and confirmed the Mac-side hashes
  matched local, guarded by the expected pre-fix stage hashes.
- Copied the pre-fix `m11_dev_capture.py 833a0f52…`, `README-M11-PREP.md f7316eb9…` and
  `stage-manifest.txt 111b3947f2c121d9290b7da742b2ac7ea78c62f35df01f20ef512193b869ed01` into
  `_pre-fix-2026-10-28/`, with its own `SHA256SUMS`.
- Moved GLM's stray file there as `glm-interrupted-partial-upload-2026-10-28-m11_dev_capture.py`
  (hash-checked first).
- Installed the new files and regenerated `stage-manifest.txt`
  (`d3c2523255253afde1bfcbe1bcff068a12283b0fe7d2759cb940248909d34b1d`).
- Appended both generations to `stage-manifest-history.txt`.

**Post-checks:** `ast.parse` OK and `bash -n package-dev.sh` OK, with no `__pycache__` written on
the Mac. No veles/otp23 manifests, no otp23 processes, and the approved fixture is still `e7c716c5…`
with strict verify OK.

**Not done:** no Chrome launch, no dev host, no registration, no CoreBluetooth init, no packaging, no
signing, no build, no TCC action, no phone fixtures, and no personal Chrome or mount action.

## 8. Explicitly not done

- No commits, push, GitHub, or canonical promotion.
- No matrix-row change, and no v4 artifact regeneration.
- No broader math or schema hardening: F-5 and F-6 are left as the reviewer's optional items.
- No physical retake.

Scratch kept for the reviewer under `/tmp/opencode/otp23-m08-opus-impl-scratch/`: `mut/` holds the
mutation copies (a removal was blocked by a safety check, so it was left in place) and `m11-smoke/`
the harness. The parent may delete both.

## 9. Asks for the independent micro-verifier

1. Re-run the §5 commands and the §2/§6/§7 hashes.
2. Re-run both mutation checks and the M-11 smoke.
3. Confirm the Windows-CI gating move.
4. Decide on the pre-existing committed account-name exposure (§6).
5. Move the private archive out of `/tmp`.

STOPPED here: awaiting the parent's independent micro-verification. The physical retake is not
started.
