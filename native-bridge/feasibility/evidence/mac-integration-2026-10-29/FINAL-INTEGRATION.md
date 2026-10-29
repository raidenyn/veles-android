# OTP-23 Mac final integration (2026-10-29): M-08 and M-11 promoted — 25 PASS / 4 BLOCKED / 3 waived, NO-GO

**What this is.** This folder began as curation only. `README.md`, `curation-map.json`,
`path-map.json` and `model-metadata.json` are the curation record as of 2026-10-29 ~11:25Z and stay
byte-unchanged. That record states "promotes no matrix row" and "A2 review pending", and both
statements were true at the time. This file records what happened next.

**Superseded statements:**
- **A2 review.** The independent micro-review approved A2.
- **Row promotion.** The canonical integration promoted **M-08 and M-11 only**.

**Manifests:**
- **Curation-as-of manifest** (26 entries, `7bb6af16…`): kept as
  `history/SHA256SUMS.curation-as-of-2026-10-29T11-25Z`.
- **`SHA256SUMS`:** refreshed to cover every file in this folder, including the gate logs.

**Roles:**

| role | who |
|---|---|
| Implementer of this integration | Claude Code `claude-opus-5-5`, session `9f6f8b0f-01c7-4ccc-8c3a-f5daf806045e`, 0 subagents. Its first turn hit API 429 before any canonical edit; it was resumed in the same session (`model-metadata-final.json`). |
| Independent approval | fresh Opus `e30e474d-65cc-44c2-99c7-d0195cfc4631`, `reports/otp23-mac-safe-attribution-verification-opus.md` (byte-identical copy): **APPROVED normal M-08/M-11 integration** |
| M-11 physical operator | parent controller `gpt-6.1-sol` (not Opus) |
| TCC supplement operator | parent controller `gpt-6.1-sol`, read-only queries |
| M-08 retake operator | Opus `752e1974` |
| Final independent integrated-tree review | **NOT done here.** The parent performs it after this integration, before commit and publication. |

## Added in this step

| path | what |
|---|---|
| `adjudications/m08-final-adjudication.md` | final M-08 adjudication (owner completion of the deferral, retake, A2 only, precision correction, retained history) |
| `adjudications/m11-final-adjudication.md` | final M-11 manual adjudication with every micro-review §5 caveat; supersedes the frozen, kept `../mac-m11-dev-2026-10-29/adjudication-draft.md` |
| `reports/otp23-mac-safe-attribution-verification-opus.md` | independent micro-review, byte-identical (`40cb23df…`) |
| `reports/otp23-mac-publication-curation-result.public.md` | the curation report, **redacted** (the name-sweep list of unrelated apps → placeholder, plus one leading comment). It is not byte-identical; the private original `bb2b6cff…` is mapped in `final-integration-map.json`. |
| `model-metadata-final.json` | whitelisted model and role metadata (curation, micro-review, this session) |
| `final-integration-map.json` | curation of the added files and resolution of every new `/tmp`, `/home` or `/dev/shm` citation |
| `history/SHA256SUMS.curation-as-of-2026-10-29T11-25Z` | the earlier 26-entry manifest |
| `gates/` | verbatim gate logs, exit codes, manifest verification and the source-hash inventory |

**New sibling folder:** `../mac-tcc-supplement-2026-10-29/`, the read-only post-run TCC log supplement
(6 entries). It corroborates the developer prompt only and found no original signed prompt.

## Disposition

| row | result | basis |
|---|---|---|
| M-08 | **PASS** (admin) | owner completed the deferred Chrome update (not a waiver); frozen `../mac-retake-2026-10-29/` (79, `f952c123…`); A2 `../mac-release-attributed-safe-2026-10-29/` (6, `c05174f4…`), widths 235–282 ms, the ≤ 250 ms instrument target not claimed |
| M-11 | **PASS** (scoped manual, admin) | frozen `../mac-m11-dev-2026-10-29/` (25, `071d14f1…`) plus the account record `a2bdc40c…`; the raw timeouts and nulls are kept |
| M-03 | BLOCKED | original signed prompt owner unobserved; nothing inferred |
| M-04 | BLOCKED | no OFF→ON approved or done; exact guidance not implemented |
| W-10, A-01 | BLOCKED | unchanged |

**Account waiver:** exactly W-01, M-01 and M-02. **Decision: NO-GO.** OTP-24 is not authorized.

**Unchanged frozen inputs:**
- `../mac-release-attributed-2026-10-29/` (A1, `dd05dac4…`)
- `../mac-release-attribution-source-history-2026-10-29/` (`2a550c1c…`)
- `../mac-resume-2026-10-28/` (`9e8ed077…`)
- `../mac-m08-codephase-2026-10-28/` (`339505b6…`)

## Gates (run after the last source and doc edit; verbatim logs in `gates/`)

| log | command | result |
|---|---|---|
| `01-spotless-rerun.log` | `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck --rerun-tasks` | BUILD SUCCESSFUL, `gradle_exit_code=0` |
| `02-full-gradle-gates.log` | `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` | BUILD SUCCESSFUL, `gradle_exit_code=0` |
| `03-log-guards.log` + `ci-log-guards.sh` | both raw `android.util.Log` guards from `.github/workflows/ci.yml`, verbatim (`bash -v`) | `guard_exit_code=0` |
| `04-python-tests.log` | `(cd native-bridge/feasibility && python3 -B -m unittest discover -s scripts -p 'test_*.py' -v)` | 512 tests OK, 5 Windows-only skips |
| `05-pycompile.log` | compile every `scripts/*.py` (no bytecode written) | 50 files, exit 0 |
| `06-matrix.log` | `matrix.py check` / `decision` / render-vs-doc diff | `matrix ok: NO-GO`; decision `NO-GO: 4 row(s) open: W-10, M-03, M-04, A-01` (exit 1 by design); render diff exit 0 |
| `07-diff-check.log` | `git diff --check` plus a whitespace scan of untracked files outside the evidence folders | both exit 0 |
| `08-evidence-manifests.log` | `sha256sum -c` on every evidence `SHA256SUMS`, plus a disk-set check for the Mac folders | 24 manifests and 1,294 entries OK; `manifests_exit_code=0` |
| `09-source-hash-inventory.txt` | HEAD, the `git diff` sha256 and the sha256 of every changed or untracked source/doc file | inventory |

**Not run:**
- **Node and shell checks.** No Node, shell or extension sources changed. `protocolCheck` above ran
  the protocol Node consumers.
- **Rust, Kotlin and native checks.** No sources in those languages changed.

**Unchanged frozen pins:**

| file | sha256 |
|---|---|
| `mac_release_attribution.py` | `ce75a8f1…` |
| `test_mac_release_attribution.py` | `219c78be…` |
| `mac_release_bound.py` | `a49b3450…` |
| `scenario_matrix.py` | `f8bbf2d6…` |
| `macos_poc.py` | `e2131ad3…` |
| workflow | `df1305d4…` |

`SHA256SUMS` was refreshed last. Its own digest is reported outside this folder, in the parent's
integration result.

## Final review closure (2026-10-29, appended; the statements above stay as the hand-off record)

The **final independent integrated-tree review** (Claude Code `claude-opus-5-5`, session
`faee6a0e-43c5-41d7-9bc4-a2cd3f3de09b`, 0 subagents) is published byte-identically as
`reports/otp23-mac-final-integrated-opus-review.md`. Its whitelisted envelope fields are in
`model-metadata-final.json`.

It reviewed the tree at `git diff` `dec764f4…`, with this folder's manifest at `dbada608…`. Verdict:
**CHANGES REQUIRED → closed by the bounded F1–F3 closure; APPROVED for commit/publication pending clean
export and green CI** (its §7). Session `9f6f8b0f` implemented the closure; the closure is not a new
review.

| finding | closure |
|---|---|
| F1 (MEDIUM, curation) | **Option (a).** In `../mac-resume-2026-10-28/registration/record.json`, the four unrelated applications' Native Messaging manifest file names are replaced in place by `<other application's manifest>`. The array length (4), `veles_manifest_present: false` and every other byte are unchanged. The record went from `a5c405ac…` to `61335dc5…`, and the private original is outside Git (600). A redaction entry with both sha256 values (no names quoted) was appended to `../mac-resume-2026-10-28/curation/curation-map.json`. A sweep of the 235 new or changed files for the names and their stems found 0 hits. |
| F2 (LOW, provenance) | `adjudications/m08-final-adjudication.md`: the W-08 EOF reading is now attributed to the parent preflight decision (`m08_eof_scope`), which chose it, and to this final review, which verified it. A dated erratum line was added. |
| F3 (LOW, provenance) | `gates/06-matrix.log` is kept as written; `gates/PRE-REVIEW-NOTE.md` explains its inaccurate header. `gates/post-review/06-matrix.log` logs the executed commands literally. |

**`mac-resume-2026-10-28` manifest mapping (old → new).** The citations of `9e8ed077…` in this file's
earlier sections and in the copied reports are as-of history.

| manifest | old | new |
|---|---|---|
| `SHA256SUMS` | `9e8ed07767a0ed6f6da4094685bd6669d7f9a081f6975d9b8ecafed1917af022` (35 entries; byte copy at `manifest-history/SHA256SUMS.pre-registration-redaction`) | `e90d3c21a76009c40c7e56c0d06705ef91fc4aef529621672e2796be70212214` (36 entries) |
| `curation/SHA256SUMS` | `4933dce9…` | `60dc1017…` |

The changed entries are `registration/record.json`, `curation/curation-map.json` and
`curation/SHA256SUMS`, plus the added `manifest-history/SHA256SUMS.pre-registration-redaction`.
Earlier statements that "30 non-narrative paths are byte-identical" are true as of 2026-10-29 ~11:25Z;
`registration/record.json` is now a curated (redacted) path. All other frozen folders are unchanged.

**Gates after the closure:** see `gates/post-review/`. The logs `gates/01–09` are pre-review history
(`gates/PRE-REVIEW-NOTE.md`).

**Precision notes from the review:**
- **Bytecode.** The Python runs use `-B`, except `test_macos_poc.py`'s deliberate
  `python -m py_compile`, which rewrites three Git-ignored `scripts/__pycache__` files. The earlier
  "no new .pyc" wording was therefore approximate.
- **Why `test_mac_clock_corroborate.py` skips Windows.** It runs only off Windows because it imports
  the POSIX `pwd` module, not because of a clock dependency.

Counts stay 25/4/3, NO-GO. OTP-24 is not authorized.
