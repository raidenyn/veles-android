# Single-build Pipelines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify PR #111 to single-build producers and publication without reproducibility gates, retaining optional rebuild support.

**Architecture:** Publish producer artifacts and their component checksums directly. Keep native compilation and installer packaging on their target runners; leave reference rebuild tools opt-in.

**Tech Stack:** GitHub Actions YAML, Gradle, Node test runner, Bash, PowerShell, Tauri.

**Spec:** `docs/superpowers/specs/2026-10-23-single-build-pipelines-design.md`

## Global Constraints

- No automatic A/B or candidate/reference comparison in producer or release jobs.
- Preserve signed/unsigned Android handling, provenance, checksums, and policy checks.
- Windows uses `windows-2025`; macOS uses `macos-26`; retain current tool pins.
- Keep existing optional rebuild commands; do not claim installer reproducibility.
- Work in `.worktrees/otp-01-1d`; do not commit or push without user request.

## Review Focus

- Signing configured: select the signed APK without building an unsigned reference.
- Signing absent: unsigned APK, mapping, and checksum downloads remain usable.
- Native uploads: archive bytes preserve macOS modes/symlinks without A/B transport.
- Failed producer or policy job: manual publication does not run after failure.
- Tag publication: asset integrity check and provenance survive gate removal.

---

### Task 1: Single-build component workflows

**Files:**
- Modify `.github/workflows/build-{android,web-extension,rust,native-windows,native-macos,supply-chain}.yml`.
- Modify `verify/test/workflow-contracts.test.mjs`.

**Interfaces:**
- Android workflow consumes `commit-sha`, `artifact-name`, optional keystore secrets;
  emits one directory artifact containing actual release APK, mapping, SHA256SUMS.
  Remove `release-evidence-artifact-name` input; update caller in Task 2.
- Other producers retain `commit-sha`/`artifact-name`; native uploads contain
  existing product archive, sidecar, and component SHA256SUMS, not extracted views.

- [x] Update workflow tests to assert one native job/platform, target labels,
  normal packaging command, Windows provisioning/NSIS path, macOS Xcode job env,
  neutral artifact defaults, and absence of automatic verifier calls.
- [x] Add assertions that Android uses one mutually exclusive signed/unsigned
  assembly path, routes JNI validation to the selected APK, and stages mapping
  plus SHA256SUMS without canonical unsigned export or reference rebuild.
- [x] Run `node --test verify/test/workflow-contracts.test.mjs`; confirm new
  assertions fail against the original workflows.
- [x] Implement workflow changes. Preserve existing web test/bundle checks and
  remove only the web/Rust reference-verifier steps. Native jobs invoke
  `bridgePackage` once, provision required tools, and upload packaged files
  directly. Do not call firewall/sandbox wrappers or create run transports.
- [x] Run the targeted contract tests and inspect all changed artifact paths.

### Task 2: Publication and caller graph

**Files:**
- Modify `.github/workflows/release-build.yml`, `.github/workflows/release.yml`.
- Delete `.github/workflows/build-toolchain-manifest.yml`.
- Modify `verify/test/workflow-contracts.test.mjs`.

**Interfaces:**
- Manual publish downloads `android-release` and selects its actual APK according
  to signing configuration; full graph uses neutral component artifact names.
- Tag publish depends on `build` only, retaining draft asset SHA-256 validation.
- Optional `verify/aggregate-checksums.sh` remains a local tool, not a CI dependency.

- [x] Update tests for removed aggregate workflow/dependency and tag verify job;
  assert manual publish explicitly requires success of every full-graph producer
  and supply-chain job, as well as master guard success.
- [x] Assert tag provenance, SHA256SUMS, asset digest comparison, tag/version
  validation, and collision guards remain. Check both signed/unsigned references.
- [x] Run targeted tests to confirm failures, then update workflow caller inputs,
  artifact names, publication conditions and descriptions. Remove verify-only
  APK upload from tag build. Keep optional user-facing rebuild instructions.
- [x] Run workflow contracts and `actionlint` if installed; inspect job graph for
  missing needs, stale artifact names, or publication paths that bypass failures.

### Task 3: Documentation and acceptance

**Files:**
- Modify `README.md`, `CLAUDE.md`, `docs/reproducible-builds.md`.
- Add supersession notes to `docs/superpowers/specs/2026-08-28-otp-01-1d-verification-supply-chain-design.md`
  and `docs/superpowers/plans/2026-08-28-otp-01-1d-verification-supply-chain.md`.
- Adjust any remaining workflow tests referring to removed CI contracts.

**Interfaces:**
- Document actual single-build artifacts and optional existing verifier CLI
  signatures; retain their tests and honest native comparison scope.

- [x] Describe provenance/checksums separately from opt-in source rebuilds; state
  that publication does not require a successful reproducibility comparison.
- [x] Update workflow ownership, artifact layout, tool-upgrade instructions, and
  native build-host rationale. Remove instructions to rerun production CI until
  runner image identities match; retain optional-tool requirements where needed.
- [x] Run `node --test verify/test/*.test.mjs` and applicable shell test scripts;
  investigate failures caused by changed workflow contracts. Do not run expensive
  Docker rebuilds to validate their deliberate removal from production pipelines.
- [x] Run `git diff --check`; inspect final diff and search active workflows for
  verifier calls, A/B jobs, verified artifact naming, and obsolete dependencies.
- [x] Report local evidence and any target-runner validation still pending.

## Self-review

All approved requirements map to the three tasks. Existing optional verifier
tests protect rebuild support; production workflow tests protect the new graph.
No application behavior or new cross-compilation interfaces are introduced.
