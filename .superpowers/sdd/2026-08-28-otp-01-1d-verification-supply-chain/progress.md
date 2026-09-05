# SDD ledger — plan: docs/superpowers/plans/2026-08-28-otp-01-1d-verification-supply-chain.md

## Environment

- Baseline Gradle commands require `JAVA_HOME=$JAVA_ROOT/zulu21` and its `bin` directory on `PATH`; the inherited shell does not otherwise expose Java.

## Preflight Interface Scan

| Tasks | Shared file or interface | Finding |
|---|---|---|
| 1 -> 2 | canonical standard manifest writer/parser | Task 2 consumes Task 1 and owns web output only; ordered correctly. |
| 1 -> 3 | canonical standard manifest writer/parser | Task 3 consumes Task 1 and owns Rust output only; ordered correctly. |
| 1 -> 6 | canonical standard manifest writer | Task 6 consumes Task 1 for native package output; ordered correctly. |
| 1 -> 7 | native manifest/metadata APIs | Task 7 consumes Task 1 for transport comparison; ordered correctly. |
| 2 -> 9 | web package tree and manifest | Task 9 aggregates Task 2 output; ordered correctly. |
| 2 -> 10 | web verifier and package tree | Task 10 owns its CI caller; ordered correctly. |
| 2 -> 13 | web output cleanup/documentation | Task 13 consumes public output contract; ordered correctly. |
| 3 -> 4 | `verify/package.json` and `build.gradle.kts` | Task 4 extends verification dependencies and Gradle tooling after Task 3; changes are additive. |
| 3 -> 8 | `build.gradle.kts` | Task 8 forwards isolated native cache variables only; Task 3 owns `rustPackage`; ordered correctly. |
| 3 -> 9 | Rust package tree and manifest | Task 9 aggregates Task 3 output; ordered correctly. |
| 3 -> 10 | `rust-jni-wasm` and Rust verifier | Task 10 owns artifact publication; ordered correctly. |
| 3 -> 13 | Rust output cleanup/documentation | Task 13 consumes public output contract; ordered correctly. |
| 4 -> 5 | pinned verification npm/Cargo tools and policies | Task 5 executes Task 4 policies/tools; ordered correctly. |
| 4 -> 13 | `build.gradle.kts` verification tool output | Task 13 extends cleanup around Task 4 paths; ordered correctly. |
| 5 -> 9 | supply-chain reports | Task 9 orchestrates Task 5 verifier but excludes reports from aggregate; ordered correctly. |
| 5 -> 10 | supply-chain verifier/artifact | Task 10 owns CI execution/upload; ordered correctly. |
| 5 -> 13 | supply-chain paths/documentation | Task 13 consumes public output contract; ordered correctly. |
| 6 -> 7 | native package trees | Task 7 transports and compares Task 6 output; ordered correctly. |
| 6 -> 8 | native package runtime/cache contract | Task 8 provisions the target-native package environment; ordered correctly. |
| 7 -> 8 | native run preparation wrappers | Task 8 invokes/extends Task 7 wrappers; ordered correctly. |
| 7 -> 9 | native verified tree | Task 9 aggregates verified native evidence; ordered correctly. |
| 7 -> 11 | native transport/comparison commands | Task 11 owns reusable native callers; ordered correctly. |
| 8 -> 11 | platform provisioning/offline wrappers | Task 11 invokes Task 8 platform wrappers; ordered correctly. |
| 8 -> 13 | `build.gradle.kts` native cache contract | Task 13 cleans/documents resulting output; ordered correctly. |
| 9 -> 10 | aggregate/verifier command conventions | Task 10 calls individual verifiers only as required; ordered correctly. |
| 9 -> 11 | aggregate component command | Task 11 invokes aggregate component directly; ordered correctly. |
| 10 -> 11 | workflow contract test | Task 11 extends same test after Task 10; ordered correctly. |
| 10 -> 12 | reusable Linux workflows | Task 12 composes Task 10 workflows and pins actions; ordered correctly. |
| 11 -> 12 | reusable native/aggregate workflows | Task 12 composes Task 11 workflows and pins actions; ordered correctly. |
| 12 -> 13 | workflow ownership/commands | Task 13 documents final graph; ordered correctly. |
| 13 -> 14 | all public commands and acceptance state | Task 14 verifies the completed contract; ordered correctly. |

## Internal Consistency Scan

| Task | Finding |
|---|---|
| 1 | Tests, interfaces, canonical grammar, and commit scope agree. |
| 2 | Producer replacement, canonical manifest, Docker reference, and verifier tests agree. |
| 3 | `rustPackage` tree, clean contract, Docker reference, and verifier tests agree. |
| 4 | Exact tool pins, policy files, evaluator tests, and Gradle task scope agree. |
| 5 | Three requested SBOMs, policy scanners, reports, and shell entry point agree. |
| 6 | Exact bridge identity preservation, WebView policy, and per-platform manifests agree. |
| 7 | Transport allow-list, identity gate, exit classification, and synthetic tests agree. |
| 8 | Target-native tool/cache provisioning and static contract tests agree. |
| 9 | APK export, aggregate inclusions/exclusions, and local-only orchestration agree. |
| 10 | Component-owned Linux workflows and contract tests agree. |
| 11 | Two native slots plus compare, verified-only outputs, and aggregate workflow agree. |
| 12 | Caller trigger graph, permissions, and immutable action pins agree. |
| 13 | Clean paths, documentation, and in-progress design status agree. |
| 14 | Local and GitHub acceptance gates agree with final implemented status. |

Ruling: The design currently says `Draft pending written-spec review`, while Task 13 explicitly changes it to `Approved; implementation in progress`. Treat this as the plan's intended staged status transition, not a prerequisite blocker, because the plan and the approved task sequence both reserve the status edit for Task 13. Cost if wrong: documentation can briefly understate approval until Task 13.

Task 1: blocked before dispatch — configured coder subagent rejected the request because its weekly usage limit is exhausted. No implementation files changed.

Task 1: review requires fix round 1/5 — Windows separator traversal, usage/environment/identity exit classification, and parsed symlink target/digest binding are missing.
Ruling: Use exit 2 for invalid API usage, filesystem/environment failures, and missing/invalid native runner identity; retain exit 1 for malformed standard evidence and a symlink target/digest mismatch. This reconciles Task 1's malformed-evidence direction with the design's explicit usage/environment/missing-identity exit-2 rule. Cost if wrong: callers may classify malformed native identity evidence differently, but this preserves the design's stated safety semantics.
Task 1: fix round 1/5 (2 addressed, 2 open — directory-enumeration I/O and native-header exit classification; plus discovered invalid filename regression; commits cc35c36..7882cb2).
Ruling: Classify all missing/duplicate/unknown/empty native identity headers as exit 2 because they make the required identity invalid, while classify invalid filenames discovered in the artifact tree as exit 1 because they are artifact-contract drift rather than invalid caller input. Cost if wrong: a consumer could prefer all malformed evidence as exit 1, but these distinctions preserve the design's error taxonomy and artifact-policy boundary.
Task 1: fix round 2/5 (2 addressed, 1 open — generic post-header comment incorrectly classified as identity error; commits 7882cb2..6e95d38).
Task 1: fix round 3/5 (1 addressed, 0 open — generic comments preserve mismatch exit classification; commits 6e95d38..97b0414).
Task 1: complete (commits a81dacc..97b0414, review clean)
Task 2: review requires fix round 1/5 — production shell does not normalize tool, git-status, and initialization failures to exit 2. Minor: verifier tests cover test-only abstractions rather than production orchestration. Docker daemon absence remains an environment acceptance gap.
Task 2: fix round 1/5 (0 addressed — candidate mismatch becomes exit 2 and production pin/Docker fixtures were removed; commits c1c34e0..4ed0d6d).
Task 2: fix round 2/5 (candidate mismatch and production Docker fixtures addressed; genuine pin assertions remain open; commits 4ed0d6d..4d1c708).
Task 2: fix round 3/5 (genuine Node/npm production pin assertions addressed; commits 4d1c708..499b9b7).
Task 2: complete (commits 97b0414..499b9b7, review clean; Docker daemon unavailable for reference integration acceptance)
Task 3: review requires fix round 1/5 — reference dependencies are not acquired before offline package production, wrapper prints self-comparison success before reference output exists, and Docker inputs include floating downloads.
Task 3: fix round 1/5 (3 addressed, 0 open; commits 6237334..0432311).
Task 3: complete (commits 499b9b7..0432311, review clean; Docker daemon unavailable for reference integration acceptance)
Ruling: Keep `license-checker-rseidelsohn` pinned at the current upstream package release 5.0.1. Its locked package metadata is authoritative; its `--version` command has an upstream hard-coded stale `4.4.2` banner. Record and exercise the executable, but do not treat that known banner discrepancy as a package-version failure. Cost if wrong: a future upstream CLI-version regression could be masked until the exception is revisited.
Task 4: complete (commits 0432311..3d6e783, review clean; current latest license-checker banner exception recorded)
Task 5: complete (commits 3d6e783..d65c65f, review clean)
Task 6: complete (commits d65c65f..bc42ea4, review clean)
Ruling: Replace macOS host-wide PF manipulation with `sandbox-exec` process-tree outbound-network denial. It preserves the host firewall configuration, proves the package command cannot reach the fixed endpoint, and is accepted despite not constraining unrelated host processes. Cost if wrong: a process outside the package command tree could retain network access during the build.
Task 9: fix round 1/5 (3 addressed, 1 open — raw native aggregate records, bounded native evidence, stale aggregate cleanup; required APK verification pending Docker; commits c43ce65..757d39e).
Task 9: fix round 2/5 (3 addressed, 1 open — required APK verification cannot run because Docker daemon is unavailable; commits 757d39e..26aa20c).
Ruling: Treat Task 9's local APK verifier failure caused by the unavailable `/var/run/docker.sock` as a pending target-environment acceptance gap, not a code blocker, because the user explicitly designated Tasks 2, 3, and 9 Docker reference integrations as pending acceptance. `assembleRelease` and all focused/local Node fixtures passed, and the report captures the exact Docker failure. Cost if wrong: a Docker-enabled environment could reveal a reference-image or APK verification defect before acceptance.
Task 9: complete (commits c43ce65..26aa20c, 1 parked: Docker reference integration pending target environment acceptance)
Task 10: fix round 1/5 (Android secret environment handling, clean-runner cargo-deny provisioning, exact supply-chain reports, and workflow contract coverage addressed; commits 8f5825d..3ac4aa8).
Task 10: fix round 2/5 (unsigned Android output and workflow-contract parser coverage addressed; commits 3ac4aa8..adec480).
Task 10: fix round 3/5 (all workflow upload/permission/secret contract coverage widened; commits adec480..6358dea).
Task 10: fix round 4/5 (quote-aware workflow contract parsing closes action-ref, quoted-permission, and quoted-flow-run bypasses; commits 6358dea..fd0fd51).
Task 10: complete (commits 26aa20c..fd0fd51, review clean; GitHub Actions execution remains Task 14 acceptance)
Task 11: fix round 1/5 (PowerShell parameter blocks, verified artifact validation, and aggregate producer/consumer contracts addressed; commits bb385f4..6227281).
Task 11: fix round 2/5 (validator invocation and split Android aggregate/release evidence artifacts addressed; commits 6227281..81a77ef).
Task 11: fix round 3/5 (signed Android APK/mapping staged before unsigned rebuild; commits 81a77ef..b17e41c).
Task 11: complete (commits fd0fd51..b17e41c, review clean; real Windows/macOS GitHub runner acceptance remains Task 14)
Task 12: fix round 1/5 (forbidden `name` fields removed from reusable-workflow caller jobs and contract test restored to exact permitted set; commits adf835f..b5b805e).
Task 12: complete (commits b17e41c..b5b805e, review clean; real trigger/label/dispatch gating remains Task 14 GitHub acceptance)
Task 13: fix round 1/5 (native two-platform layout code correction, offline trust-boundary docs, exit-2 normalization prerequisites/pin-upgrade/Windows-row docs addressed; commits 2c64a95..20c1fc2).
Task 13: fix round 2/5 (accurate trust boundaries, missing-tool exit 2, staged two-platform comparisons addressed; cargo-deny policy classification and publish atomicity open; commits 20c1fc2..f56399b).
Task 13: fix round 3/5 (cargo-deny licenses-bit 0x4 → exit 1, staged publication addressed; cross-filesystem staging and unguarded mktemp/cleanup open; commits f56399b..0e072af).
Task 13: fix round 4/5 (same-filesystem staging, guarded exits addressed; rollback trap deleting restored output open; commits 0e072af..793b041).
Task 13: fix round 5/5 (restored prior output survives EXIT trap; rollback test reaches real branch; commits 793b041..ac0c0d5).
Task 13: complete (commits b5b805e..ac0c0d5, review clean)
Ruling: Task 13 finding 1 took the code-correction route (verify-native.sh detects two-platform layout and emits per-platform namespaced output) because the plan's binding verify-all.sh synopsis requires the documented layout to be consumable; docs alone could not satisfy it. Cost if wrong: the CI flat per-platform path is preserved and tested, but a divergence between the two layouts would surface at Task 14 GitHub acceptance.
Task 13: minor (deferred): design spec still contains stale pre-fix trust-boundary wording at docs/superpowers/specs/2026-08-28-otp-01-1d-verification-supply-chain-design.md:479-490 (web candidate container network denial, platform-wide native denial) — final review to triage.
Task 14: local acceptance complete with no fix commits (Steps 1-5: 93/93 verify tests, web 8 stages, bridge, Rust, supply-chain, unit tests, assembleRelease, clean all PASS; verify-web.sh/verify-rust.sh/verify.sh Docker checks PENDING target environment per user-acknowledged gap; spec status intentionally NOT changed to Implemented — gated on GitHub acceptance Steps 6-8).
Task 14: minor (deferred): verify-web.sh/verify-rust.sh clean-checkout guard trips over pre-existing untracked local verify/node_modules/ on a Docker host; not an issue on fresh CI checkouts — final review to triage.
Final review: fix wave 1 (commits ac0c0d5..804c920) resolved ImageOS win25/macos26, useLocalToolsDir, .gitignore verify/node_modules, rust-package fixtures, trust-boundary docs; 4 findings adjudicated FIX-NOW after re-review (runner identity exec-2 gaps, archive-root mapping, standalone-manifest fabrication, transport/aggregate contradiction).
Final review: fix wave 2 (commits 804c920..cd53457) — macOS exec-2 behavior-tested, archive layouts mapped, manifest extracted from real archives (zip-reader), transport/aggregate aligned with E2E test; re-review left 3 gaps (Windows param-binding usage exit, 4 missing NSIS files, optional component SHA256SUMS).
Final review: fix wave 3 (commits cd53457..ee60ed9) — all 3 gaps fixed; scoped re-review APPROVED (110/110 tests, no new breakage). Branch review closed clean.
Ruling: Promote the final review's verify/node_modules finding to fix-before-merge and resolve it via .gitignore (directory stays on disk, untracked, excluded from verify-all.sh dirtiness per the user's do-not-remove ruling). Cost if wrong: local verify-all.sh dirtiness semantics shift, but the user ruling and fresh-CI nonissue both hold.
Task 14: deferred-minor triage executed by final review — stale design trust-boundary wording: fix-before-merge (done in wave 1); verify-node_modules: fix-before-merge (done in wave 1); Docker reference integrations (Tasks 2/3/9) and GitHub-hosted native acceptance (Steps 6-8): ride as user-acknowledged pending target environment, spec status stays `Approved; implementation in progress` until those gates pass.

Ruling: Tauri 2.6.0's NSIS/MSI/DMG/.app outputs are non-reproducible producer payloads (embedded timestamps and platform identifiers) with no supported deterministic configuration. Compare only stable raw host/manifest evidence across matched runs; require each slot's native manifest and product SHA256SUMS to validate the installer payload, outer package, and sidecar locally. Cost if wrong: cross-run installer-content drift is not detected as a mismatch, so this exception must be revisited when Tauri or its bundlers gain reproducible output support.
