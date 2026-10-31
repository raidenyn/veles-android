# OTP-23 package 1 — final integrated read-only review (Opus 5.5)

## Identity, role and scope

- **Role:** independent final integrated reviewer of the whole intended delta from HEAD
  `8f365fce0d31f975610c235085a95b9136ac25e2`, read-only. The only file I wrote is this report. My scratch
  probes are in `/tmp/opus-v4-probe/`. No subagents, hardware, Chrome, Mac, adb, GitHub, staging, commit
  or push.
- **Actual metadata,** read from envelope fields only (no message content, tool results, headers or API
  errors) of this Claude Code session's log
  `~/.claude/projects/-home-dev-repos-veles-android/1b447229-ceaa-4cee-a5be-2181dfdd8838.jsonl`:
  - Claude Code `2.1.312`.
  - Assistant records: `claude-opus-5-5` × 157 and `<synthetic>` × 1. The synthetic one is the harness
    placeholder for an interrupted turn, not model output.
  - Recorded `effort` is `high` on all 157 real records, so the "high" label in the integrated text is
    backed by real metadata.
  - First and last assistant records: `2026-10-28T13:49:33.902Z` and `2026-10-28T14:13:26Z`. Captured
    at `2026-10-28T14:13:27Z`.
- **Same session as the focused v4 review:** this review continues it. The published
  `opus-review-model-metadata-final.json` (69 entries) is therefore a correct snapshot as of the end of
  the v4 review, not of this review. The counts above supersede it for this report.
- **Scope:** I reuse the completed independent reviews without reopening them:
  - GPT observer-code final (`33e8233e…`);
  - GPT P2/B5 (`15a28c80…`);
  - GPT Windows strict (`520c2edd…`);
  - my v4 report (`d85d4f57…`).

  I did not reopen the numeric/schema fuzz backlog and require no hardware reruns.

## Verdict

**APPROVED for commit and publication, pending clean Git-export checks and green CI.** No finding below
blocks this. Each is publication curation or wording hygiene, and none changes a row status, a
measured value, a pin or an owner decision.

## What I independently verified

**Canonical state**
- `matrix.py check` → `matrix ok: NO-GO`. `matrix.py decision` lists exactly 6 open rows:
  W-10, M-03, M-04, M-08, A-01, M-11. Waived (NOT RUN, not PASS): exactly W-01, M-01, M-02.
- Direct JSON count: **23 PASS / 6 BLOCKED / 3 NOT RUN**.
- The rendered doc is fresh: `render` and `check` exit 0.
- Every row's evidence path exists.
- Rows changed versus HEAD:
  - W-02, W-07, W-08, W-11, W-12: BLOCKED → PASS, with blocker cleared and `account` set to `admin`.
  - M-08, D-02, R-01: `actual` text only (M-08's blocker text too). `sources` is unchanged.

**The five Windows PASS rows are justified**
- **W-02:**
  - The owner's approved text in `/tmp/opencode/otp23-package1-owner-approval.json` appears **verbatim**
    in four places: the RFC (`docs/rfcs/2026-08-21-otp-external-devices.md:1620-1637`), the matrix W-02
    `expected` (as its ending), the rendered doc, and `w02-adjudication.json`. The curly apostrophe is
    included.
  - The scope stays narrow: W-02 only, no OTP-24, raw `BLOCKED criterion_conflict` records byte-identical.
  - The census is 6/6 usable: zero host windows of all four kinds, zero host endpoints in all four
    families, exactly one never-visible `ConsoleWindowClass` per sample, no wrapper endpoints, positive
    controls present, sessions equal.
  - It does not permit any host window or endpoint, or any visible wrapper window or network endpoint.
- **W-07/W-08:**
  - The values are the v4 values: 2322/2406 ms with [629,4015]/[1482,3330], and 2304/2310 ms with
    [819,3789]/[1388,3232].
  - The adopted identity is `8521de4d…ae33`. I confirmed it equals the sha256 of the published
    `producer-pc-snapshot-20261028/host-binary/otp23-host.exe`, which is a deliberate 726,528-byte file.
  - The non-adopted e36328d2/6cf72ed0 history is kept.
- **W-11/W-12:** latest-Chrome strict cold/prewarm runs with the GPT strict review copy published
  byte-identically (`520c2edd…`). The `formal_otp23_acceptance=false` and ST-13 limits are kept.
- **M-08:**
  - Still BLOCKED and owner-deferred ("Cannot restart Chrome now", record `ace5e31e…`). The private Chrome
    update/restart is recorded as declined.
  - The overwritten first Mac setup capture is recorded as a preservation gap, never recreated.
  - No Chrome crash root cause and no universal bug is claimed. I scanned all added doc/matrix lines and
    every occurrence of "root cause", "universal", "crash", "GO" or "OTP-24" is negated or qualified.
- **Role labels:**
  - Earlier reviews are labeled as actual GPT (`gpt-5.6-sol high`).
  - Implementation is labeled `glm-5.3-flash` with 0 subagents.
  - The final focused review is labeled as user-authorized Opus 5.5. The owner instruction
    "Use Opus 5.5 via Claude Code" is published under `owner-records/` (`9211bbf8…`).
  - No retroactive Opus authorship is claimed.

**Pins and immutability**
- **Code pins:** all frozen sources hash as pinned:
  - `scenario_matrix_core.py` `cb512418…`
  - `test_scenario_matrix.py` `307a7eb1…`
  - `package1_b5_v2_derive.py` `9cad1a70…`
  - `package1_phone_burst_telemetry.py` `c6fcfd6e…`
  - `package1_phone_bound.py` `40d8f824…`
  - `scenario_matrix.py` `f8284c98…`
  - `pc_phone_clock_probe.py` `06f0de1b…`
  - `chrome_validate.py` `78d53ca9…`
- **Canonical file hashes** match the GLM final record: matrix JSON `bbe67839…`, rendered doc `2b6894aa…`,
  `matrix.py` `2ba2cabf…`, `test_matrix.py` `245f696c…`, RFC `d1de4177…`, contract alignment `9a40bb5e…`,
  investigations `40fad9e7…`.
- **Manifests** (verified by recomputing every listed entry, plus a coverage scan of every file):

  | package | manifest sha | entries | coverage notes |
  |---|---|---|---|
  | v2 | `7b2e924f…` | 15/15 | One unlisted addition, the quota-state record, covered by relative path in the integration manifest. |
  | v3 | `3bf5a493…` | 9/9 | Full coverage. |
  | v4 | `dc214fde…` | 9/9 | Full coverage. |
  | P2 raw | `addf32b2…` | 34/34 | Only `.gitattributes` unlisted. |
  | connected | `218da0f6…` | 181/181 | `SHA256SUMS-before-b5-supersede.original` and `SHA256SUMS-NOTES.md` are unlisted manifest-history sidecars, explained in the notes. |
  | producer-PC snapshot | `f08a8ed6…` | 238/238 | Covers the snapshot, `MANIFEST.json`, `PUBLISH-MAP.md` and the host binary. |
  | integration | `46d2f8cb…` | 21/21 | Full coverage. |

- **Raw input bindings:** my v4 report checked all 33 input/output pins and the 8-entry supersedes chain,
  and the bytes are unchanged since then.

**Git export and safety**
- **Untracked files:** all 522 untracked files are not ignored: 517 package1 evidence files plus the 5
  new helpers. None of the package1 paths is git-ignored, so the new content is not missing from Git.
  The only ignored files nearby are pre-existing macOS `._*` AppleDouble files in older folders.
- **Line endings:** every package folder has `* -text -whitespace`, so exported bytes keep their hashes.
- **Secrets:**
  - Scanning for Authorization/Bearer/API key/token/cookie/private-key/rate-limit/request-id patterns
    found only harmless matches: Android dumpsys `mIsWaitingAuthorization: false`, protocol `request_id`
    trace fields, and the published source snapshot.
  - Bluetooth addresses are already redacted (`00:00:00:00:00:00`).
  - The phone serials `2be02e41`/`864a3db0` were already public at HEAD.
- **Quota record:** the failed-request events file (`/tmp/…-v4-events.jsonl`) is not published, by
  policy. The quota-state record keeps safe facts only.
- **Snapshot build safety:** the snapshot's `Cargo.toml`, `.rs` and `.js` copies don't affect builds.
  - `cargo metadata --no-deps --offline` in `native-bridge/feasibility` lists only the root package.
  - `cargo fmt --check` exits 0.
  - Spotless targets only `**/*.kt`, and both Log guards scan only `app/src/main/**/*.kt`.
- **No product source changes:** the delta has no Kotlin, Rust, Node, Gradle or workflow source changes.

**Tests and CI portability**
- **Local suites:** on Python 3.14, `test_scenario_matrix` passes 136 (5 skipped), `test_matrix` 34 and
  `test_macos_poc` 44. Full discovery gave 456 when I ran it in the v4 review; the recorded 457 is that
  plus the one new final-integration invariant. I did not rerun discovery, because the code under test
  is frozen.
- **Python 3.12 (the CI interpreter):** all 13 changed and new Python files parse with
  `ast.parse(..., feature_version=(3,12))`. On uv's Python 3.13, `test_matrix` (34), `test_scenario_matrix`
  (136, 5 skipped) and `test_macos_poc` (44) all pass.
- **Windows default encoding:** with `-X warn_default_encoding`, I found the new default-encoding
  `read_text()` calls. All five newly read evidence files decode strictly as cp1252 and parse, so the
  Windows CI leg will not hit a decode error.
- **CI job selection** in `otp23-feasibility.yml` (ubuntu, windows-2025, macos-26) is unchanged.
  `test_matrix.py`, `test_scenario_matrix.py` and `matrix.py check` run on all three OSes;
  `test_macos_poc.py` runs everywhere except Windows. Path filters cover `native-bridge/feasibility/**`.
- **Recorded gates:** the logs in `package1-integration-20261028/gates/` show:
  - the forced `spotlessCheck --rerun-tasks` → BUILD SUCCESSFUL (3 tasks executed);
  - `spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` → BUILD SUCCESSFUL;
  - `logguards_exit=0`.

  I did not rerun Gradle: no Kotlin, Gradle or Android input changed in the delta.

## Findings (prioritized; none blocking)

1. **Low, publication curation: the observer-code GPT reviews exist only in `/tmp`.**
   `/tmp/opencode/otp23-package1-observer-code-gpt-final.md` (sha256
   `33e8233ee0e26299118afec02fe26b15eb784fab1db7d0caa6eb701c45b53575`) approved the observer-provenance
   (`scenario_matrix_core.source_hashes`/`observer_pin_problems`, `scenario_matrix.require_observer_pinned`)
   and `macos_poc.lsof_endpoints` changes that are part of this delta. Its predecessor
   `…-observer-code-gpt-verification.md` is in the same position. Neither is published or referenced
   anywhere in the tree (`git grep` for the name and sha finds nothing).
   - **Recommendation:** in the later publication curation, copy both byte-identically into
     `package1-integration-20261028/reports/`, add rows to `PROVENANCE-MAP.md` with their GPT session
     metadata (`ses_e7fbe5353ffeWU4VTjn6lgYjIL`, `gpt-5.6-sol high`), and refresh that folder's active
     `SHA256SUMS`. Alternatively, record them as a required later capture.
   - **Why not blocking:** the P2/B5 GPT review (published) also covers the pinned observer bytes.
2. **Low, `/tmp`-only citations to GLM phase records.** These cite `/tmp/opencode/otp23-package1-integration-result.md`:
   - `package1-integration-20261028/gates/SOURCES.md:18-19` (it also cites `/tmp/opencode/gate3-*.log`;
     those logs themselves are published);
   - the published Opus v4 report (`reports/otp23-p2-opus-v4-final.md:128`). That report is byte-frozen
     and must not be edited.

   `…-final-integration-result.md` is not published either. **Recommendation:** publish safe copies of
   both GLM result files (or a PROVENANCE-MAP row stating they are not exported), so those citations
   resolve.
3. **Low, stale count.** `package1-integration-20261028/gates/SOURCES.md:33` says "this folder's own
   18/18". The final active manifest is **21/21**, which I verified. Fix the wording in the same curation
   pass.
4. **Low, stale interim sentence.** The W-11 and W-12 `actual` text in
   `native-bridge/feasibility/acceptance/otp23-matrix.json:351` and `:385` ends with "W-02/W-07/W-08 stay
   BLOCKED pending their own approval". It is accurate as dated history, and the D-02 FINAL postscript
   carries the current truth. **Optional:** a short "FINAL 2026-10-28: superseded by the final
   integration, see D-02" suffix. That needs a re-render, a check, and a `test_matrix` rerun.
5. **Info.** `package1-b5-v2-20261028/provenance/v4-review-attempt-quota-state.json:26` says reviewers
   are "never Opus" and that the only Opus sessions are 2026-10-27 roles. It was true when recorded
   (12:01Z). The record is outside the frozen v2 manifest but is pinned by relative path in the
   integration manifest, so it should not be edited. `PROVENANCE-MAP.md` already supersedes it with the
   correct labels. No action needed.
6. **Info.** The RFC cites the owner records by their `/tmp` capture paths
   (`docs/rfcs/2026-08-21-otp-external-devices.md:1628`, `:1643`) together with their SHA-256. The
   published byte-identical copies (`package1-b5-v2-20261028/owner-approval-w02-2026-10-28.json`,
   `…/mac-deferral-2026-10-28.json`) are resolved by `PROVENANCE-MAP.md`. **Optional** later RFC
   touch-up: add the repository paths.
7. **Info.** The M-08 `evidence` list (`otp23-matrix.json:556` row) does not list the
   `package1-p2-20261028/p2m8-mac-logs/` attempts or the `mac-deferral-2026-10-28.json` record. Its
   `actual` text cites both and both are published. **Optional:** add them for traceability.
8. **CI-pending (accepted residual).** The 5 skipped tests are the new
   `WindowsMachineSmoke` class (`test_scenario_matrix.py:412`, real Windows APIs, Windows CI leg only).
   They were never run here, and the real-Windows transcript was not retained, which the P2/B5 GPT review
   already accepted. The first `windows-2025` CI run of `otp23-feasibility.yml` is their first retained
   execution, and **must be green before publication**.
9. **Info (from my v4 report).** When `package1_b5_v2_derive.py:52` `superseded_chain` writes outside
   the evidence tree, it emits `sha256: null` instead of failing. The committed v4 is correct. This is
   hardening only and outside the frozen scope.

## Exact commands run (repo root unless noted; read-only)

```text
git rev-parse HEAD; git status --porcelain; git diff --stat; git diff HEAD -- <RFC, matrix JSON, test_matrix.py, docs>
sha256sum <13 python files, matrix JSON, rendered doc, RFC, alignment, investigations>
(scripts) python3 matrix.py check; python3 matrix.py decision; python3 matrix.py render   # exit 0, no --write
python3 -I -c <row diff vs HEAD JSON; status counts; evidence-path existence; verbatim owner sentence in RFC/matrix/doc/adjudication>
python3 -I /tmp/opus-v4-probe/coverage.py <each package1 folder>                           # manifests + coverage
git ls-files --others --exclude-standard | wc -l; git ls-files --others --ignored --exclude-standard native-bridge/feasibility/evidence/package1-*
sha256sum .../host-binary/otp23-host.exe                                                    # 8521de4d…ae33, 726528 B
grep -rIohE -i '<secret/header patterns>' <package1 evidence + new helpers> | sort | uniq -c
git grep -l -e 2be02e41 -e 864a3db0 HEAD; grep -oE '<BT address>' <dumpsys sample>; git grep <BT address> HEAD -- evidence
grep -rn observer-code / 33e8233e <docs, acceptance, evidence>                             # no references
python3 -I -c 'ast.parse(..., feature_version=(3,12))' <13 changed/new python files>        # all OK
uv-python3.13 test_matrix.py / test_scenario_matrix.py / test_macos_poc.py                  # 34 OK / 136 OK (5 skip) / 44 OK
python3 -X warn_default_encoding -W always::EncodingWarning -m unittest scripts/test_matrix.py; ... test_scenario_matrix
python3 -I -c <strict cp1252 decode + json.loads of the 5 newly read evidence files>        # all OK
python3 -m unittest -v test_scenario_matrix | grep skip                                     # 5 = WindowsMachineSmoke
(feasibility) cargo metadata --no-deps --offline; cargo fmt --check                          # root package only; exit 0
grep spotless targets (build.gradle.kts); grep Log-guard scope (.github/workflows/ci.yml); cat .github/workflows/otp23-feasibility.yml
cat gates/SOURCES.md; head/tail gates/final-gateF-*.log
python3 -I -c <envelope-field-only extraction of this session's model/effort/version/timestamps>
```

In the v4 review (same session, unchanged frozen bytes since): `test_scenario_matrix` 136 OK (5
skipped), full discovery 456 OK (5 skipped), v4 re-derivation into a temp folder, and all pin and
supersedes audits.

## Boundary

Approval applies to this exact tree state (HEAD `8f365fc` plus the 13 tracked modifications, the 5 new
helpers and the 6 new package1 evidence folders), subject to:
- a clean Git-export check: exported bytes re-verify against every manifest, including the snapshot
  238/238 and the 726,528-byte host binary;
- green CI, including the first Windows `WindowsMachineSmoke` run.

Findings 1–3 are curation items. They can be handled in a follow-up publication-curation pass, or with
the same refreshed integration manifest, without reopening code approval. This report may be published
later with the safe metadata above.

Final state: **23 PASS / 6 BLOCKED / 3 waived**. Open rows are W-10, M-03, M-04, M-08 (owner-deferred),
M-11 and A-01. Overall status is **NO-GO**, and **OTP-24 is not authorized**.
