## 1. Spike: confirm the versioning tools (throwaway, results recorded in design.md)

- [x] 1.1 In a scratch repo, prove the pinned git-cliff resolves each component's previous tag independently with repeated `--include-path` and a per-component `--tag-pattern`, bumps from prefixed tags, and emits per-component unreleased notes
- [x] 1.2 Prove the bump policy with the pinned `[bump]` settings: `feat`, `fix`, `perf`, `!`, `BREAKING CHANGE:`, `BREAKING-CHANGE:` (including below 1.0) and every non-bumping type
- [x] 1.3 Prove the no-tag case (initial version, notes after an onboarding SHA)
- [x] 1.4 Prove `androidGitVersion` 0.4.14 honours `prefix = "android-v"` and ignores plain `0.0.x` tags, or choose the documented fallback
- [x] 1.5 Record the confirmed commands or fallbacks in design.md D2/D3

## 2. Commit conventions (local)

- [x] 2.1 `scripts/commitlint.py` with tests for every commit-conventions scenario (CRLF, `#` lines, git-generated subjects only with `--allow-git-generated`)
- [x] 2.2 `.githooks/commit-msg`
- [x] 2.3 Root `installGitHooks` task (only when unset, warn otherwise, skip on `CI=true`, configuration-cache safe) wired to `:app:preBuild`; verified in a disposable clone

## 3. Component map and selection

- [x] 3.1 `.github/components.json` per design.md D1, after auditing cross-directory imports
- [x] 3.2 `scripts/components.py` (`check`, `changed` with `--no-renames`, global and unknown paths select everything) with tests

## 4. Release tooling

- [x] 4.1 Pinned git-cliff installer task, registered with the clean contract and supply-chain policy; `cliff.toml` with the pinned bump policy
- [x] 4.2 `scripts/release_plan.py` (`latest`, `plan`; RELEASE/CARRY, orphaned-tag and shallow-repo refusal, release tag naming, notes and summary) with fixture-repo tests
- [x] 4.3 `scripts/release_assets.py` (expected asset sets, manifest writing, carry verification, staging, draft verification) with tests
- [x] 4.4 `scripts/release_ready.py` (readiness over `toJSON(needs)` and the plan) with tests for every RELEASE/CARRY combination, failures and cancellations
- [x] 4.5 `scriptsTest` Gradle task running all `scripts/tests`

## 5. Version injection

- [x] 5.1 Android `android-v` prefix (or fallback)
- [x] 5.2 Extension `resolveExtensionVersion` with Chrome validation, zip name from the emitted manifest, smoke/package tests updated, `build-web-extension.yml` `version` input and full-history checkout (+ contract tests)
- [ ] 5.3 Installer effective version through descriptor, macOS `VERSION`, uninstaller and host, declared as task inputs; producers' `version` input (+ contract tests)

## 6. Unsigned Android producer

- [x] 6.1 Remove the signing interface from `build-android.yml` and the secrets passed by `release-build.yml`; update the Node contract tests

## 7. CI tree

- [ ] 7.1 `check-*.yml` workflows with the existing steps; `rust / jni-integration` keeps the full clean-contract sequence
- [ ] 7.2 `ci.yml` orchestrator (`changes` with map check, node callers, `commit-guard`, `ci-ok`) and `pr-title.yml` with the bootstrap fallback
- [ ] 7.3 Delete `build-native-host.yml`; migrate the host and installer Python contract tests, the Node contract tests and `check-remote-code.mjs` scopes in the same commit
- [ ] 7.4 `docs/ci.md`

## 8. Builds

- [ ] 8.1 `release-build.yml`: label PR builds, changed-only master builds, preview job; no dispatch or publish; migrate `check_release_build` and Node tests
- [ ] 8.2 `build-pr-installers.yml` label rename (+ tests)

## 9. Publish workflow

- [ ] 9.1 `publish-release.yml` (guard, concurrency, plan, supply chain, Android build + `sign-android`, extension build, installer build/verify/sign moved verbatim, `gate-installer`, carry, ready, draft, publish)
- [ ] 9.2 Delete `release.yml` and `release-native-installers.yml`; retarget `check_gate_boundary`, Node signing tests and `check-remote-code.mjs` scopes in the same commit
- [ ] 9.3 `docs/releasing.md` with the runbook (bootstrap, orphaned tags, re-running `publish`, rollback) and owner settings

## 10. Documentation

- [ ] 10.1 `CLAUDE.md` and `README.md`

## 11. Verification and owner setup

- [ ] 11.1 CLAUDE.md lint gate, all three contract suites, `scriptsTest`, host/installer/extension/Android unit tests
- [ ] 11.2 Owner checklist in the PR: squash settings, `android-release` (keystore secrets moved, `VELES_ANDROID_CERT_SHA256`), `release-publish`, labels, then the `master` ruleset
- [ ] 11.3 After merge: bootstrap publish of all three components at 0.1.0
