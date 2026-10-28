# Gate-log copies — exact sources (2026-10-28 integration curation)

Per the phase steering, these are byte-identical (`cmp`-verified) copies of the ALREADY-COMPLETED
full-gate logs from the last completed phase (the integration phase earlier on 2026-10-28); no new
gate run is required by this curation and the FINAL gates run after the canonical promotion later.

| Copy (gates/) | Exact source command (root of /home/dev/repos/veles-android) | Exit code at the time |
|---|---|---|
| gate3-spotless-rerun.log | `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck --rerun-tasks` (forced rerun after ALL integration edits) | 0 |
| gate3-gradle-full.log | `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` | 0 (BUILD SUCCESSFUL) |
| gate3-logguards.log | the two verbatim `.github/workflows/ci.yml` android.util.Log guard checks (fully-qualified/wildcard import + bare `Log.[dweiv](` outside LogSink.kt) | logguards_exit=0 |

Full portable harness at the same tree state: `python3 -m unittest discover -s . -p 'test*.py'` =
**456 tests OK, 5 skipped**; `matrix.py check` → `matrix ok: NO-GO` (20 PASS / 9 BLOCKED / 3 waived);
`git diff --check` → 0; all evidence manifests verified (post-grant 196/196, investigations 257/257,
windows-cleanup 49/49, strict-mac 37/37, integration 26/26, ci-portability 11/11, package1-p2 34/34,
package1-connected 181/181, v2 15/15, v3 9/9, v4 9/9, snapshot 238/238). These runs' logs lived in
`/tmp/opencode/gate3-*.log` at copy time; the interim counts and exits are also recorded in
`phase-records/otp23-package1-integration-result.md` in this folder (byte-identical copy; the older
`/tmp` gates logs it cited are themselves published in this folder).
## Final gates (after the authorized 3-row integration + all doc/test edits; the ACTUAL final run)

| Copy (gates/) | Exact source command (root of /home/dev/repos/veles-android) | Exit code |
|---|---|---|
| final-gateF-spotless-rerun.log | `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck --rerun-tasks` | 0 |
| final-gateF-gradle-full.log | `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` | 0 (BUILD SUCCESSFUL) |
| final-gateF-logguards.log | both verbatim ci.yml android.util.Log guard checks | logguards_exit=0 |

At the same final tree state: `py_compile` (matrix.py, test_matrix.py, scenario_matrix_core.py,
test_scenario_matrix.py, package1_b5_v2_derive.py) → OK; full portable harness
`python3 -m unittest discover -s . -p 'test*.py'` → **457 tests OK, 5 skipped**;
`matrix.py render --write` + `check` → `matrix ok: NO-GO`; `matrix.py decision` → NO-GO, 6 open rows
(W-10, M-03, M-04, M-08, A-01, M-11), waived exactly [W-01, M-01, M-02]; `git diff --check` → 0; ALL
13 evidence manifests verified by RECOMPUTING every listed entry plus a coverage scan of each folder
(this folder's ACTIVE manifest covers every published byte here + the relative-path v2 quota
addition — its exact entry count is whatever the coverage-verified manifest says at publication, not
a fixed number, so the count cannot go stale). Rust sources unchanged (no affected check);
web-extension/Node and shell harnesses unchanged (not affected — no binding change; prior records
reused).

## Publication-curation gates (after the approved-review curation edits; the PUBLICATION-INTENT run)

| Copy (gates/) | Exact source command (root of /home/dev/repos/veles-android) | Exit code |
|---|---|---|
| final-gateP-spotless-rerun.log | `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck --rerun-tasks` | 0 |
| final-gateP-gradle-full.log | `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` | 0 (BUILD SUCCESSFUL) |
| final-gateP-logguards.log | both verbatim ci.yml android.util.Log guard checks | logguards_exit=0 |

Same-run state: `test_matrix` 34 OK; full portable harness **457 OK, 5 skipped**; `matrix.py check` →
NO-GO (6 open rows, unchanged statuses); `git diff --check` → 0; the ACTIVE manifest of this folder
was refreshed to cover every newly published byte (integrated Opus review, the two observer-code GPT
reports, the two GLM phase records, the whole-session Opus metadata, the updated SOURCES/FINDING/MAP)
plus the relative-path v2 quota addition, all verified by coverage recompute.
