# Verification Report: split-android-and-granular-ci

Verified at `cd36a47` (origin/master after PR 3 merged). All four implementation PRs are merged:
#143 `5ca8bfa`, #146 `8de11f6`, #147 `146b054`, #148 `cd36a47`.

## Summary

| Dimension | Status |
| --- | --- |
| Completeness | 55/57 tasks; 43/43 ADDED/MODIFIED requirements mapped (0 removals, 0 renames) |
| Correctness | 43/43 requirements implemented; 146/148 scenarios covered; 2 not verified (release-certificate parity, D5 script fallback) |
| Coherence | Design followed (D1-D8); 2 owner-approved deviations recorded; no contradictions |

Task tracking is configured; no unavailable tracking files. Delta specs contain 43 ADDED/MODIFIED
requirements across four capabilities and 148 scenarios. No REMOVED or RENAMED requirements.

## Completeness

### Task completion

55 of 57 tasks complete. The two remaining tracked tasks are procedural, not implementation gaps:

- **7.1** — "Verify the change (`/opsx:verify`) and archive it after PRs 1, 1b, 2 and 3 have merged."
  This verification is that step's first half; it completes when `verify.md` is recorded and the
  archive runs.
- **7.2** — "Follow-up, not a prerequisite for archive: when no in-flight PR needs them and the next
  Android release tag contains the migration, remove the inert `legacy-release` and `retired-paths`
  entries in a cleanup change." The plan explicitly makes this non-blocking; the condition (a new
  Android tag containing the move) is not met yet.

### Spec coverage

All 43 ADDED/MODIFIED requirements have implementation evidence:

| Capability | Requirements | Evidence |
| --- | --- | --- |
| `pr-last-green-selection` | 14 | `scripts/last_green.py` (`candidate_heads`, `run_belongs_to_pr`, `node_is_green`, `find_bases`, `main`, `MAX_CANDIDATES`/`MAX_API_CALLS`), `.github/workflows/ci.yml` (`changes` permissions, `--all` on push, kill switch, summary), `scripts/components.py` (`selection_spec`, `--node-bases`, `--evidence-notes`, `--summary-out`), `scripts/tests/test_last_green.py` (42 tests), `verify/test/workflow-contracts.test.mjs` (job derivation, permissions, push selection, `ci-ok`). |
| `component-versioning` | 9 | `.github/components.json` (`retired-paths`, `legacy-release`, `global-overrides`, root build files on non-Android nodes), `scripts/components.py` (`owner_nodes`, `release_paths`, `parse_map`), `scripts/release_plan.py` (`legacy_commits`, `legacy_skip_args`, merged passes), `scripts/tests/test_components.py` (63 tests), `scripts/tests/test_release_plan.py` (40 tests), `protocol/checks/spec-location.test.mjs`, `protocol/docs/`. |
| `ci-pipeline` | 4 | `.github/workflows/ci.yml` (`changes`, `ci-ok`), `release-build.yml` selection unchanged, `.github/components.json` jobs/overrides, `verify/test/workflow-contracts.test.mjs` (55 tests) and `native-bridge/installer/scripts/test_build_contract.py` (62 tests). |
| `android-standalone-build` | 16 | `android/settings.gradle.kts`, `android/build.gradle.kts`, root `build.gradle.kts` aliases and `includeBuild`, `rust/build.gradle.kts`, `rust/ndk-version`, `android/gradlew`/`gradlew.bat`, `.githooks/install-hooks.gradle.kts`, `verify/check-android-layout.sh`, `verify/check-root-aliases.sh`, `scripts/check-mac-comparisons.mjs`, the relocated workflows, `rust/scripts/test-ndk-lookup.sh`, `scripts/tests/test_components.py`, the APK identity comparison, `CLAUDE.md`/`BUILD.md`/`docs/ci.md`, and the clean contract. |

## Correctness

### Requirement implementation mapping

All 43 requirements are implemented. Representative checks:

- Last-green selection: `scripts/last_green.py:134-313`; eligibility, ancestry, map compatibility,
  budget/error split, kill switch and output files are covered by `scripts/tests/test_last_green.py`.
- Per-node selection and output/summary contract: `scripts/components.py:110-355`; integration tests
  drive `last_green.main` output into `components.py changed` and assert the summary reasons.
- Ownership and release paths: `scripts/components.py:70-187`; `retired-paths` accounted, overrides
  select only listed nodes, `release-paths` never selects, legacy/retired never release inputs.
- Legacy release attribution: `scripts/release_plan.py:176-320`; per-commit `android/` epoch test,
  `--skip-commit` for new-epoch commits, dedupe by id, merged reasons/notes and highest bump; the
  planner tests cover squash, revert/reapply, both-pass matches, inertness, untagged components and
  `config/detekt/` gaining no attribution.
- CI wiring: `.github/workflows/ci.yml:32-96` (permissions, `last_green` fallback, `--node-bases`,
  summary, push `--all`, validation ordering); contracts pin the shapes.
- Android build boundary: `android/settings.gradle.kts`, `android/build.gradle.kts`, root aliases;
  `verify/check-android-layout.sh` renames the root build files in a scratch copy, proves the Android
  build still succeeds, runs each shared Rust task once, and resolves every alias.
- NDK: `rust/build.gradle.kts:90-133` resolves `ANDROID_NDK_HOME`, `ANDROID_SDK_ROOT`, `ANDROID_HOME`,
  root `local.properties`, `android/local.properties`, with the specified failure message;
  `rust/scripts/test-ndk-lookup.sh` covers the order. Every CI install step exports the pinned
  `ANDROID_NDK_HOME` (contract-tested) so the runner preset cannot win.
- Identity: debug APKs from `8de11f6` and `2b3aefd` are byte-identical (69,332,310 B,
  sha256 `1311bae5…`), `versionName 1.2.3`, `versionCode 10203`, certificate SHA-256 `9ef3104b…`.
- Guard split: `check-android.yml` runs Gradle from `android/` and the MAC/Log/pairing guards from
  the root with `--scan android/app/src/main`; `check-web-extension.yml` owns the Chrome pairing guard.
- Hooks: one shared `.githooks/install-hooks.gradle.kts` applied by root and Android; the three
  `core.hooksPath` scenarios were verified from both entry points.
- Path audit: the literal `app/` audit leaves zero operational matches; the remaining matches are
  the map's intentional legacy/retired entries, current/archived plans, dated reviews and negative
  test fixtures. A constructed path in `web-extension/test/debug/code-leak-guard.test.ts` was missed
  by the literal audit and caught by CI; it is fixed to `android/app/src/debug`.

### Scenario coverage

146 of 148 scenarios are covered by implementation and tests (the test suites above). Two are not
fully verified:

- **Certificate parity** (android-standalone-build): the debug APKs are byte-identical with the same
  certificate. Release APKs were not built locally; per the owner's instruction, release identity is
  generated only by master CI. The spec records that approved exception.
- **Script fallback** (android-standalone-build): the D5 `Exec` fallback is a contingency for a
  nested-include failure. The nested include succeeded on the real build, so the fallback path was
  not exercised.

## Coherence

### Design adherence

D1-D8 are followed: last-green lookup and components CLI wiring; map schema; protocol spec relocation;
independent `android/` build with root aliases and shims; included `rust/` build with preserved
outputs and NDK lookup; bounded legacy release attribution; node input map; cross-component guards.
Two deviations are explicitly recorded rather than silent:

- The local, git-ignored fixture signing assets live under `android/config/android_keys/` per the
  owner's instruction, and the spec/plan/`CLAUDE.md` were updated accordingly.
- Release identity is CI-only; only the debug identity was compared locally.

The D5 fallback is documented in the design but not needed because the primary approach passed.

### Code pattern consistency

New code follows the existing patterns: stdlib-only Python with `unittest`, Node `node --test` and
Vitest suites, shell acceptance scripts with explicit failure messages, workflow contracts that
derive from the source, and the existing component-map/planner structure. No significant deviations.

## Issues

### CRITICAL

1. **Task 7.1 is incomplete (procedural).** This verification is its first half; record this report and
   archive the change to complete it.
   Recommendation: complete the archive step.
2. **Task 7.2 is incomplete (explicitly non-prerequisite).** The cleanup of the inert
   `legacy-release`/`retired-paths` entries is deferred until no in-flight PR needs them and a new
   Android tag contains the move.
   Recommendation: leave it for the planned cleanup change; it does not block this archive.

No code or spec-implementation critical issues were found.

### WARNING

1. **Release-certificate parity is not verified locally.** Only the debug APK identity was compared;
   release identity is CI-only by owner direction. The debug certificates and versions are identical.
   Recommendation: accept the recorded exception, or verify the CI-signed release on the first
   `android-v*` master release.
2. **The D5 script fallback is unexercised.** The nested include works, so no evidence exists for the
   fallback branch.
   Recommendation: no action needed for this change; keep the fallback as a documented contingency.

### SUGGESTION

1. The literal `app/` audit missed a constructed path (`'app', 'src', 'debug'`); CI caught it.
   Consider adding a constructed-segment check to `verify/check-android-layout.sh` or documenting that
   full suites are the backstop.
2. `.github/workflows/check-tooling.yml` still says any workflow edit runs both Python build-contract
   owners; the Android overrides now qualify that. Update the comment.
3. The planner appends legacy notes as a second rendered changelog (duplicate `### Bug Fixes` heading);
   group/merge the rendered context instead.
4. `--skip-commit` grows with every post-move commit until the next `android-v` tag; add a comment or
   cap for very long ranges.
5. `rust/scripts/test-ndk-lookup.sh` and `verify/check-android-layout.sh` are manual-only; consider
   wiring them into `tooling / unit-tests` if their runtime is acceptable.

## Final Assessment

No critical implementation issues found in the checks that ran. Two tracked tasks remain by plan
design: 7.1 is the verification/archive step being completed now, and 7.2 is an explicit non-blocking
follow-up. Release-certificate parity is not verified locally (owner reserved it for CI) and the D5
fallback is an unexercised contingency; 2 warnings and 5 suggestions are recorded.

Ready to archive once this verification artifact is recorded and the archive step completes.
