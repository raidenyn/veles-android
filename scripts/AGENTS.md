# scripts/AGENTS.md

Release tooling, Conventional Commits checks and the component map. The stdlib-Python suites live
in `scripts/tests/`; the pinned git-cliff is `scripts/release-tools.toml`. Run everything from the
repository root.

## Build & Test

```bash
./gradlew installGitCliff      # pinned, SHA-256-verified git-cliff 2.14.2 -> build/release-tools/git-cliff
./gradlew scriptsTest          # stdlib-Python suites in scripts/tests (commitlint, component map, release planner/assets/readiness)
node --test scripts/check-mac-comparisons.test.mjs

python3 -I scripts/commitlint.py --message "feat(app): add thing"   # validate a message (or --file PATH); exit 0 valid, 1 invalid
python3 -I scripts/components.py check                              # every tracked file is owned by a node in .github/components.json
python3 -I scripts/components.py changed --base <sha>               # <node>=true|false per component for a diff
python3 -I scripts/last_green.py --pr <n> --head <sha> --head-repo <owner/name> --head-ref <ref>  # per-node last green heads (CI; needs GITHUB_TOKEN)
```

`scriptsTest` depends on `installGitCliff` and points `VELES_GIT_CLIFF` at the pinned binary.

## Conventional Commits

Every commit subject is a Conventional Commit (`type(scope): description`), enforced by
`scripts/commitlint.py` through three gates: the `.githooks/commit-msg` hook, the required
`pr-title` check on the PR title and body (the future squash commit), and `commit-guard` on master
pushes. `feat` bumps the minor version, `fix`/`perf` the patch, `!` or a `BREAKING CHANGE:` footer
the major; every other type bumps nothing. Guidance-only changes must be typed `docs`/`chore` so
they do not bump a release component. See `docs/releasing.md` and `docs/ci.md`.

## Versions and tags

Three release components are versioned independently with SemVer computed from Conventional Commits
by git-cliff 2.14.2, pinned with SHA-256 digests in `scripts/release-tools.toml` (`cliff.toml`). State
is the tags `android-v<semver>`, `web-extension-v<semver>` and `installer-v<semver>`; only commits
touching a component's paths in `.github/components.json` count, and a never-tagged component starts
at `0.1.0`.

Versions are injected at build time, not read from a checked-in file: the web extension reads
`VELES_EXTENSION_VERSION`, the installer reads `VELES_INSTALLER_VERSION`, and the native host reads
`VELES_HOST_VERSION`. The Android app version name/code are computed at build time by the
`androidGitVersion` plugin — see `android/AGENTS.md` for its `prefix` and `codeFormat`. The combined
release tag (`YYYY.MM.DD[.N]`), the runbook, recovery, rollback and one-time owner settings are in
`docs/releasing.md`.

## Git hooks (`core.hooksPath`)

`./gradlew installGitHooks` sets `git core.hooksPath=.githooks` (whose `commit-msg` runs
`scripts/commitlint.py`). It is also wired into `:app:preBuild`, acts only when `core.hooksPath` is
unset (otherwise it warns and leaves the existing value alone, so the hook is not active) and is
skipped when `CI=true`. The same task is shared by the root and the independent Android build.

## Workflow ownership

Each component is built exactly once per invocation in its own reusable workflow. `ci.yml` is the
PR/master orchestrator: its `changes` job (`scripts/components.py`) selects component nodes, each
calls its `check-<node>.yml`; PR runs skip nodes unchanged since their last green head
(`scripts/last_green.py`), master pushes run every node; `commit-guard` runs on master pushes and
the required aggregate is `ci-ok`. `CI_LAST_GREEN=off` disables last-green selection.

| Workflow | Role |
|---|---|
| `ci.yml` | PR/master orchestrator: `changes` node selection, per-node `check-*.yml` calls, last-green skipping, `commit-guard` on master pushes, required aggregate `ci-ok` (see `docs/ci.md`) |
| `pr-title.yml` | required check: the PR title + body (the future squash commit) must be a Conventional Commit |
| `check-android.yml`, `check-web-extension.yml`, `check-rust.yml`, `check-protocol.yml`, `check-installer.yml`, `check-bridge-scaffold.yml`, `check-tooling.yml` | per-node lint/test checks; never upload release products |
| `check-native-host.yml` | OTP-24 production host, the `native-host` node: Linux `lint`/`unit-tests`/`build-contract`, `windows-2025`/`macos-26` `unit-tests` and `compile`; CI artifacts `native-host-windows` / `native-host-macos`. No installer, no Tauri |
| `build-android.yml` | one **unsigned** release APK + mapping + `SHA256SUMS` (`android-release`); accepts no signing secrets |
| `build-web-extension.yml` | extension ZIP + sidecar + `SHA256SUMS` (`web-extension`); bundle tests |
| `build-rust.yml` | `rust-jni-wasm` package |
| `build-native-windows.yml` | OTP-25 Windows installer inputs (UNSIGNED): `native-unsigned-windows` / `native-fixture-windows`; provisions the pinned NSIS. No Tauri, no signing |
| `build-native-macos.yml` | OTP-25 macOS installer inputs (UNSIGNED), same shape under the pinned Xcode: `native-unsigned-macos` / `native-fixture-macos`. No Tauri, no signing |
| `build-supply-chain.yml` | SBOM/licence/audit reports (`supply-chain-reports`); report validation + policy enforcement (six SBOMs incl. `native-host.cdx.json` and `native-installer.cdx.json`, vendored `bluest` patch provenance) |
| `release-build.yml` | label-driven PR component builds, changed-only master builds and the release **preview** job summary; signs, tags and publishes nothing |
| `build-pr-installers.yml` | PR DEBUG installers via labels after approval of the secret-free `native-installer-debug` environment |
| `publish-release.yml` | the only workflow that signs, tags or publishes: owner-only `workflow_dispatch` on `master` |

Windows runs on `windows-2025`; macOS runs on `macos-26` with job-level
`DEVELOPER_DIR=/Applications/Xcode_26.6.app`. Runner labels are versioned, not immutable image
SHAs. Retired workflows that trigger nothing: `release.yml`, `release-native-installers.yml`,
`build-native-host.yml` and `build-toolchain-manifest.yml`. Runner identities and the clean
contract are documented in `docs/reproducible-builds.md`; the release runbook, recovery, rollback
and one-time owner settings are in `docs/releasing.md`.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
