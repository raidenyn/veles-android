# `CLAUDE.md` traceability (`consolidate-agent-files`)

Every section of the 336-line `CLAUDE.md` mapped to its destination under the target layout, so that
deleting the file loses no fact. Source: `CLAUDE.md` at `da112ab` (336 lines, 13 content headings: five
level-two sections and the eight `Architecture Overview` subsections, plus the `# CLAUDE.md` title).

Destinations follow design **D4** (one canonical destination per platform), **D5/D5a** (rules and their
scope map) and **D2** (target layout). **No target file exists yet.** Every row therefore carries status
`planned` until the writing task creates the file; Task 4 ticks the rows for `android/`, `rust/`,
`protocol/`, `web-extension/` and the root file, Task 5 ticks the rest (plan Task 5 Step 6: "Complete
every `traceability.md` row: each destination exists, or the drop reason is written"). A row may only
become `dropped: <reason>` when the reason is recorded; "pointer instead of a copy" is allowed only
where the target document really holds the fact (D4).

## Preconditions recorded before writing (Task 1)

| Item | Value | Source |
|---|---|---|
| OpenSpec CLI | `2.0.0` — inside the required `>= 2.0.0, < 3` (D6) | `openspec --version`, observed in this worktree |
| Hand edits to generated skills | none: `git diff --stat -- .claude/skills .opencode/skills` is empty, exit 0 | observed in this worktree |
| Qualified `opencode-rules` pin | `opencode-rules` (exact pin resolved by controller; not repeated in any `AGENTS.md` or doc — D5d) | controller-owned system-level task, pending |
| OpenCode version | recorded by the controller with the plugin qualification | pending |
| Antigravity `globs` array spike | owned by the controller (Task 1 Step 2); the scoped-rule scheme is unblocked only if it accepts `globs: ["android/**"]` (D5a) | pending |

## Section-by-section destinations

| # | `CLAUDE.md` heading (line) | Content carried | Destination (planned) | Status |
|---|---|---|---|---|
| 1 | `## Mandatory Local Checks Before Creating a PR` (5) | `./gradlew spotlessCheck detekt lintDebug` on the exact changes; the raw `android.util.Log` guard from the `android / lint` job in `check-android.yml`; `spotlessApply` on failure; never rely on CI; rerun after updates; report blockers instead of claiming readiness | `.agents/rules/android-lint-before-push.md` (scope `android`, `trigger: glob`, `globs`/`paths` `["android/**"]`), listed from `android/AGENTS.md`; `rust/AGENTS.md` and `protocol/AGENTS.md` tell the agent to read it because Android CI also runs for their changes (D4/D5) | planned |
| 2 | `## Stable Signing Identities (Android and macOS builds)` (25) | one stable identity per platform/app identity, not one cross-platform key; Android debug fixture: back up, compare certificate fingerprints before `adb install -r`, never regenerate/uninstall/clear data, stop and ask if the key is lost; OTP-23 fixture key (see extra row E1); macOS: exact approved identity/team/bundle ID, no ad-hoc substitution, key export or TCC reset, Aqua keychain context; no borrowing debug credentials; never record unread fingerprints; pointers `docs/windows-ble-poc.md` (Android) and `docs/desktop-ble-poc.md` (macOS) | Android half → `android/AGENTS.md`; macOS half → `native-bridge/installer/macos/AGENTS.md` (D4); the tool-independent "never regenerate, replace or commit signing keys" rule → `.agents/rules/repo-signing-keys.md` (Task 3) | planned |
| 3 | `## Build & Test Commands` (52) | the command blocks per node, the rustup/NDK r29/Node ≥ 22 prerequisites, the Verification Node exact Node 26.8.1/npm 11.19.0 pin and `verify/package.json` pins, the reusable-workflow ownership table, retired workflows (see E5), runner labels, and the clean contract | split, one block per node: Android → `android/AGENTS.md`; web extension → `web-extension/AGENTS.md`; Rust core → `rust/AGENTS.md`; native-bridge scaffold → `native-bridge/AGENTS.md`; OTP-24 host → `native-bridge/host/AGENTS.md` (keeps the commands plus the strings `check_docs` needs: Gradle task names, `native-host-windows`, `native-host-macos`, `native-host.cdx.json`); supply chain → `verify/AGENTS.md`; installers → `native-bridge/installer/AGENTS.md`; release tooling, `installGitHooks` and Conventional Commits → `scripts/AGENTS.md`. Exact Node/npm pins → `verify/AGENTS.md`. Workflow ownership table → `scripts/AGENTS.md`: `docs/ci.md` has the check tree and per-check commands but no per-workflow ownership/artifact table, so this is carried inline, not pointed at. Runner labels and the clean contract → a one-line pointer to `docs/reproducible-builds.md`, whose "Runner identities" and "Clean contract" sections really hold them (D4) | planned |
| 4 | `## Architecture Overview` (218) | Veles is a single-module Android app (minSdk 33) that intercepts bank OTP notifications, extracts OTP/amount/merchant and re-presents the code with a one-tap "Copy" action | `android/AGENTS.md` (D4: Android content lives only in the Android node file; the root file holds no Android content) | planned |
| 5 | `### Core Flow` (222) | listener → `CompositeMessageHandler` → per-bank `RegexMessageHandler` → `UserNotifierOtpMessageHandler` → `CopyDataReceiver`; `ACCEPTED` cancels the original and stops the chain, `FILTERED` continues; constructor-injected `MessageHandler` and `NotificationStatePreferences` for testability | `android/AGENTS.md` | planned |
| 6 | `### Bank Handler Configuration` (237) | Room `bank_handler_configs.db`, entity `BankHandlerConfig`, `BankHandlerDatabase.SeedCallback.onCreate` seeds UOB Thailand; the three regexes (`otpRegex`, `moneyRegex`, `merchantRegex`) and their group meanings; all three must match for `ACCEPTED` | `android/AGENTS.md` (the `SeedCallback` class name is also listed in E6) | planned |
| 7 | `### Test Screen` (248) | `TestScreen` → `TestNotificationSender` on `VelesTestChannel` → `NotificationListener.onNotificationPosted` → `TestResultFlow.current` → `TestViewModel`/`TestState.lastResult` badge; `TestResultFlow` reset in `onCleared()`; `postProbe` `VISIBILITY_SECRET` probe and the `PermissionsViewModel.verifySensitiveAccess` resets; `MessageHandlingResult.otpMessage` on the result card | `android/AGENTS.md` (the `TestResultFlow` class name is also listed in E6) | planned |
| 8 | `### Sensitive notifications (Android 15+)` (263) | OS redaction on SDK 35+; `SensitiveNotificationsStatus.check()` (runtime permission + hidden AppOp, `NotApplicable`/`Granted(via = Role\|AppOp)`/`NotGranted`); CDM `DEVICE_PROFILE_WATCH` grant via `CompanionAssociationService`/`IntentSenderLauncher`, grant verified not trusted; verification probe and `RedactionState`; `mergedSensitiveState`/`SensitiveNotificationsUiState`; card-only UI rules and its "More options" contents | `android/AGENTS.md` | planned |
| 9 | `### Package Structure` (273) | the package table (`otp/`, `otp/handlers/`, `otp/config/`, `pairing/`, `permissions/`, `permissions/services/`, `permissions/viewmodal/`, `permissions/ui/`, `testing/`, `common/`) with each package's responsibility | `android/AGENTS.md` | planned |
| 10 | `### Navigation` (288) | `VelesPermissionsApp` `NavHost` in a `Scaffold` with `VelesBottomBar`: `permissions` (Home, start), `bank-configs` (Templates), `test`; `bank-config-edit?id={id}` full-screen route hiding the bar; static emerald/gold palette in `Colors.kt`/`Theme.kt`, no Material You dynamic colour, gold accent on `colorScheme.tertiary` | `android/AGENTS.md` | planned |
| 11 | `### Testing Approach` (297) | unit tests (`src/test/`, MockK, no Android runtime) focus on `RegexMessageHandler` and `CompositeMessageHandler`; instrumented tests (`src/androidTest/`) include Compose UI (`VelesPermissionsAppTests`) and listener tests via `mockkStatic`; `TestTags` in `common/ui/TestTags.kt` are the stable selectors and must stay in sync | `android/AGENTS.md` | planned |
| 12 | `### Adding a New Bank Handler` (303) | insert a Room row with the three regexes; for install-time seeding add an `INSERT` in `BankHandlerDatabase.SeedCallback.onCreate`; no new Kotlin classes needed | `android/AGENTS.md` (see E6) | planned |
| 13 | `## Versioning & Releases` (307) | three independently versioned components (`android-v`/`web-extension-v`/`installer-v`), SemVer from Conventional Commits by git-cliff 2.14.2 pinned by SHA-256 in `scripts/release-tools.toml` (`cliff.toml`); path attribution through `.github/components.json`; bump rules; retired plain semver tags and `release.yml`; build-time version injection (`androidGitVersion`, `VELES_EXTENSION_VERSION`, `VELES_INSTALLER_VERSION`, `VELES_HOST_VERSION`); Conventional Commits enforced by `scripts/commitlint.py`, the `.githooks/commit-msg` hook, `pr-title` and `commit-guard`; owner-only `publish-release.yml` (plans RELEASE vs CARRY, signs in `android-release`/`native-release`, draft `YYYY.MM.DD[.N]` release with seven assets, atomic tags after `release-publish` approval); `release-build.yml` never signs or publishes | `scripts/AGENTS.md`, pointing at `docs/releasing.md` for the runbook, recovery, rollback and one-time owner settings (D4: release and Conventional Commits live in `scripts/AGENTS.md`); Conventional-Commit mechanics also in `.agents/rules/repo-conventional-commits.md`; `codeFormat` is Android-specific and goes to `android/AGENTS.md` (E2) | planned |

## Facts that exist nowhere outside `CLAUDE.md` (D4)

"Nowhere outside" means: not in any live `docs/` file; where a fact appears elsewhere it is only in
source code, a gate script, or frozen evidence (`docs/superpowers/…`), which Task 12 must not count as
documentation. These facts move into the owning node's `AGENTS.md`; a pointer is not acceptable for them.

| # | Fact | Where it exists today | Destination (planned) | Status |
|---|---|---|---|---|
| E1 | OTP-23 fixture keystore `android/config/android_keys/otp23-fixture.keystore` and its local Gradle init script `android/config/android_keys/fixture-signing.gradle`; the directory is git-ignored, must be retained and backed up, and never committed with its credentials; the fixture builds with `cd android && ./gradlew --init-script config/android_keys/fixture-signing.gradle assembleDebug assembleDebugAndroidTest`; debug-only, does not authorize production signing changes | `CLAUDE.md` only for the keystore path and the backup/never-commit rule; a frozen plan names the keystore; the init script's *usage* is also in live `docs/otp-09-physical-2026-11-11.md` and `docs/otp-10-physical-runs.md`, which the Android file may point at for the run detail | `android/AGENTS.md` | planned |
| E2 | `codeFormat = "MMNNPP"` (with `prefix = "android-v"`) on the `androidGitVersion` plugin, i.e. how the Android version name/code are computed at build time | `CLAUDE.md` only as prose; otherwise only `android/app/build.gradle.kts` (code) and a frozen plan | `android/AGENTS.md` | planned |
| E3 | `core.hooksPath` hook behaviour: `./gradlew installGitHooks` sets `git core.hooksPath=.githooks` (whose `commit-msg` runs `scripts/commitlint.py`), is also wired into `:app:preBuild`, acts only when `core.hooksPath` is unset (otherwise warns) and is skipped when `CI=true` | `CLAUDE.md` only as prose; otherwise only `.githooks/install-hooks.gradle.kts` (code) | `scripts/AGENTS.md` | planned |
| E4 | `bridgeInstallerSupplyChain` — `verify/verify-supply-chain.sh` for the installers, including the `native-installer.cdx.json` SBOM | `CLAUDE.md` only as prose; otherwise only `build.gradle.kts` and `native-bridge/installer/scripts/test_build_contract.py` (code) | `native-bridge/installer/AGENTS.md` | planned |
| E5 | The retired `build-toolchain-manifest.yml` workflow, with `release.yml`, `release-native-installers.yml` and `build-native-host.yml`: they are retired and trigger nothing | `CLAUDE.md` only as prose; otherwise only `verify/test/workflow-contracts.test.mjs` (a negative assertion) and frozen plans/specs | `scripts/AGENTS.md` (with the workflow ownership table, row 3); if Task 5 judges the list obsolete it becomes `dropped: retired-workflow inventory is enforced by verify/test/workflow-contracts.test.mjs` — the reason must be written in this row | planned |
| E6 | The class names `SeedCallback` (`BankHandlerDatabase.SeedCallback.onCreate`) and `TestResultFlow` (singleton observed by `TestViewModel`, reset in `onCleared()` and by `PermissionsViewModel.verifySensitiveAccess`) | `CLAUDE.md` only as prose; otherwise source code and frozen specs/plans | `android/AGENTS.md` (rows 6, 7 and 12) | planned |

## Live-reference sweep exclusions (for Task 12)

Reminder only, so Task 12 Step 1 does not "fix" frozen evidence. When the live-reference sweep
(`grep -rnE 'CLAUDE\.md|CONTRIBUTION\.md|\.opencode|\.claude/commands|/opsx:'`) reports hits in these
locations, they are **frozen evidence and stay untouched**; list them as exclusions rather than editing
them:

- `docs/superpowers/plans/` and `docs/superpowers/specs/`
- `docs/otp-23-contract-alignment.md`
- `protocol/docs/reviews/`
- `native-bridge/feasibility/evidence/`
- archived changes under `openspec/changes/archive/`

Every other hit is live and must be updated to its new destination (`AGENTS.md`, `.agents/rules/…`) or
to the OpenSpec 2 skill name (`/openspec-propose`, …), never to `/opsx:<name>`.
