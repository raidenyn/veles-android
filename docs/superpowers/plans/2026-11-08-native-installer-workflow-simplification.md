# Native Installer Workflow Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce approved, labeled PR debug installers and owner-triggered signed master installers without manual digest approval or cross-run release ceremony.

**Architecture:** Separate PR and release caller workflows reuse explicit platform builders. Release artifacts are built, hashed, signed and verified in the same run; `native-release` approval and master/owner restrictions authorize signing. Debug packages use an isolated fixture identity and compile-time debug-only trust, never release secrets.

**Tech Stack:** GitHub Actions, Gradle Kotlin DSL, Python unittest, Rust/Cargo, NSIS, SwiftPM, Windows Authenticode and macOS codesign.

**Spec:** `docs/superpowers/specs/2026-11-08-native-installer-workflow-simplification-design.md` (proposed, awaiting owner approval).

## Global Constraints

- Planning only: no implementation, merge, release run or settings mutation is authorized yet.
- Windows 10+, static CRT; existing macOS arm64 / macOS 26.0 floor remains.
- Labels: `windows-installer-build`, `mac-installer-build`.
- Only `raidenyn` may successfully initiate/rerun native release signing; dispatch ref must be `refs/heads/master`.
- No release secrets in PR jobs; debug trust must be absent from release binaries.
- Release builds retain A/B, inventory, provenance, signature and correspondence verification.
- Missing release keys or selected-platform blockers fail closed; no unsigned release fallback.
- No automatic publication, Store identity migration or acceptance PASS claims.

## Review Focus

- A new PR commit, removed label or rerun must not reuse approval for different code (Tasks 2, 5).
- Release owner/ref guards must cover reruns and every secret-bearing route (Tasks 3, 4).
- Debug installer and cleanup helper must never authorize production paths or identities (Tasks 1, 5).
- A missing macOS key or failed A/B check must not silently succeed or block Windows-only (Tasks 3, 5).
- Same-run unsigned bytes must remain the signed input; no rebuild/substitution after verification (Tasks 3, 4).

## File Map

- Modify `build.gradle.kts`: explicit native debug/release profile/channel build tasks.
- Modify `.github/workflows/build-native-{windows,macos}.yml`: explicit build kind inputs and finished debug artifacts.
- Create `.github/workflows/build-pr-installers.yml`: PR label routing and pre-build debug approval.
- Modify `.github/workflows/release-native-installers.yml`: owner/master dispatcher and same-run release graph.
- Modify `.github/workflows/release-build.yml`: remove native coupling; preserve unrelated Android/web/Rust behavior.
- Modify `native-bridge/installer/{Cargo.toml,build.rs,src/platform.rs,src/platform/windows.rs,src/platform/macos.rs}` and relevant helper trust entry points: compile-time fixture debug trust boundary; locate exact helper calls before edits.
- Modify `native-bridge/installer/scripts/{assemble_unsigned.py,release_gate.py,sign-windows.ps1,sign-macos.sh}` as needed to reuse package assembly and same-run authorization.
- Modify existing installer script tests and `verify/test/workflow-contracts.test.mjs`; add native debug separation tests under `native-bridge/installer/tests/`.
- Modify `docs/otp-25-installers.md`, `docs/reproducible-builds.md`, `docs/decisions/2026-11-08-self-signed-completion-identity.md`, and OTP-25 progress/evidence documents to supersede orchestration rules without altering historical test results.

## Task 1: Produce genuine isolated debug installers

**Interfaces:** reusable builder input `build-kind: debug|release` is required, with no config-file-existence fallback. Debug selects fixture identity/debug profile; release selects production identity/release profile. Preserve `commit-sha` and `artifact-name` inputs. Debug output includes final setup/DMG plus metadata `{source_commit, build_kind, channel, platform, workflow_run_id}` and separate symbols.

- [ ] Add failing build-contract tests: debug omits Cargo `--release`, selects Swift `debug`, packages fixture identity, builds one slot, emits finished installer and symbols; release retains release profile/A/B/static CRT.
- [ ] Add failing native tests: debug fixture packages operate only in fixture roots; production identity/carrier is rejected; helper follows the same boundary; release binaries cannot activate debug trust through args/env/descriptor.
- [ ] Run focused script/native tests and confirm new assertions fail for the intended reasons.
- [ ] Parameterize producer tasks, output paths and Gradle task inputs by build kind. Keep canonical `X.Y.Z` version, PR metadata outside the descriptor version.
- [ ] Implement fixture-only compile-time debug trust and finished unsigned Windows/ad-hoc macOS packaging by reusing existing inventory/carrier/NSIS/DMG code. Release compilation must reject the debug feature combination, including cleanup helper builds.
- [ ] Run script contracts, Linux Rust tests, Windows cross-check, then platform-native debug build/package tests. Inspect build commands and final installer contents; do not substitute input trees for installer artifacts.
- [ ] Commit this independently testable debug builder and its tests.

## Task 2: Route PR labels through an owner approval

**Files:** new PR caller, reusable builders, workflow contract tests.
**Interfaces:** PR caller passes event head SHA and `build-kind: debug` to selected platform builder only after a secret-free approval job referencing `native-installer-debug`.

- [ ] Add failing workflow cases for no labels, each label, both labels, opened/reopened/synchronize/ready events, drafts, unrelated labels and label removal. Assert zero release secret expressions/environment references and no `pull_request_target`.
- [ ] Add tests that approval precedes checkout/build, both selected platforms depend on it, token permissions are `contents: read`, and per-PR concurrency cancels stale runs. Verify fork PR behavior explicitly.
- [ ] Run `node --test verify/test/workflow-contracts.test.mjs` and installer Python build-contract tests; observe expected failures.
- [ ] Implement `build-pr-installers.yml` using the spec events/current-label predicates, exact head SHA, approval dependency and debug-only builders. Record full SHA/run ID in every output; do not grant PR write/cache privileges that cross into release builds.
- [ ] Document the approval boundary honestly: a PR changing its own workflow can remove a workflow-local debug approval gate. This is acceptable only under the stated trusted-collaborator, zero-secret debug model; a requirement to resist malicious writers requires a separately approved trusted dispatcher design.
- [ ] Run contract tests; after owner-authorized settings setup, exercise a labeled test PR, new commit and label removal to verify approval/cancellation behavior on GitHub.
- [ ] Commit PR routing and tests; do not create labels/settings until execution is approved.

## Task 3: Replace tag/digest ceremony with owner-only master authorization

**Files:** release caller, `release_gate.py`, release script tests, workflow contracts.
**Interfaces:** dispatch input `platform: windows|macos|both`, default `windows`; expected SHA is `github.sha`. `authorize(env, descriptor_digest, expected_commit, credentialed=True)` retains its API but enforces master dispatch, both actor fields equal `raidenyn`, exact SHA and internally computed digest. Credentialed calls still require `native-release` context. Bundle context records both actors, master ref, run ID and selected targets.

- [ ] Add failing authorization matrix: owner/master dispatch passes; wrong initial actor, wrong rerun actor, missing actors, PR, tag, push, non-master ref or SHA mismatch fails. No manual approved-digest variable is needed; a claimed digest differing from actual descriptor still fails.
- [ ] Add bundle validation tests for selected Windows-only, macOS-only, both, missing selected target, duplicate/unknown target, changed source/run context and cross-run report substitution. Missing unselected macOS must not invalidate Windows.
- [ ] Run focused `test_release_gate.py` and workflow tests and observe intended failures.
- [ ] Replace tag/approved-digest authorization and bundle context checks with the proposed model; remove obsolete CLI inputs/environment variables and tag-only comments. Preserve publisher pins, timestamp, trust-mode/anchor and correspondence checks unchanged.
- [ ] Build selected release-profile A/B unsigned trees using reusable builders in the same run. Verify actual descriptor/provenance/source/run/artifact digests before importing a key. Remove the two external producer-run inputs and duplicated cross-run A/B requirement, not the builder's two slots.
- [ ] Run pipeline fixture tests proving generated reports validate under the new context and tampered context/artifacts fail. Commit authorization and orchestration changes.

## Task 4: Connect protected per-platform signing and decouple other releases

**Files:** release caller, reusable builders, `release-build.yml`, signing shims, workflow contracts and operator docs.
**Interfaces:** one `native-release` signing/packaging job per selected platform consumes verified same-run inputs; nested and outer stages share the existing validation helpers. No input permits alternate source refs, secret names, environment names or arbitrary artifact paths.

- [ ] Add failing contracts: owner guard dominates all release build/sign jobs; signing jobs use `native-release`; secret mappings occur only in signing steps; release failure cannot invoke debug packaging. Assert no native jobs remain in the Android release caller and unrelated publication predicates remain intact.
- [ ] Run affected workflow/build contracts; confirm expected failures before modifying workflows.
- [ ] Wire nested sign -> assemble -> outer sign -> verify in each protected platform job; retain cleanup on exceptions and secret-free build jobs. Update final summary to selected-platform statuses, explicitly separate from acceptance eligibility.
- [ ] Remove native producers/dependencies from the broad `release-build` caller without enabling new Android publication paths. Leave existing Android credentials/behavior unchanged and document that separate scope.
- [ ] Write owner setup instructions: create exact labels; configure secret-free debug environment/sole reviewer; set release branch policy to master only, sole reviewer raidenyn, self-approval allowed, bypass off; audit native signing secrets for repo/org duplicates and restrict admin rights. Delete obsolete approved-descriptor variables only after the new gate is verified and the owner approves removal.
- [ ] Run workflow contracts, installer/host Python suites, format checks. Commit workflow integration and docs.

## Task 5: Verify the simplified paths before declaring merge readiness

**Files:** test/evidence documents and any narrowly scoped regression fixes discovered during verification.

- [ ] Run `python3 -m unittest discover -s native-bridge/installer/scripts -p 'test_*.py'`, equivalent host discovery, `cargo test --locked`, `cargo fmt --check`, and `node --test verify/test/workflow-contracts.test.mjs`. Require exit 0; retain complete logs/counts rather than filtered tail output alone.
- [ ] Native debug Windows 10 standard-user test: clean staging directory; install, repair, uninstall, helper completion, coexistence with production; verify no dependency on leftover CRT DLLs or release trust stores.
- [ ] Native debug macOS test: ad-hoc executable validity, actual installer launch and isolated install/remove on a supported host. Clearly report OS warnings/limitations rather than claiming production signature acceptance.
- [ ] Exercise GitHub label routing and approval on the updated PR. A new SHA requires a new run/approval. Verify no-label case runs no installer builders, each label only its own platform, both labels both, forks no secrets.
- [ ] Obtain fresh GPT-6.1 Sol static review of trust separation, actor/rerun checks, workflow dependency graph and same-run artifact binding; resolve blockers before merging.
- [ ] Only after separately authorized merge: owner dispatches Windows release from master and approves `native-release`; inspect self-signed installer, signature timestamp/pins/correspondence, clean non-admin install/repair/uninstall. Unauthorized identities/non-master dispatches must fail without key import (test guards with fixtures, not by exposing secrets).
- [ ] Select macOS/both only with required signing configuration available; otherwise record concrete blockers. Do not turn missing credentials/reproducibility failures into green release results.
- [ ] Update evidence truthfully: platform build/sign success is not acceptance-matrix completion. Final owner handoff lists artifacts, exact commits, test results and remaining blockers.

## Planning self-review

Requirements are mapped to Tasks 1–5. Owner-only release trigger uses explicit manual
master dispatch; automatic signing on every master push is deliberately not assumed.
No implementation task is marked complete. Debug trust is the main product-code
change and must be reviewed as such, not dismissed as a workflow-only adjustment.
Existing macOS defects remain blockers to macOS release, not to Windows-only success.
Builder review identified two owner-facing assumptions, now explicit: manual master
release dispatch and a convenience (not hostile-writer-resistant) debug approval
gate. Owner approval of this proposed design is required before implementation.
