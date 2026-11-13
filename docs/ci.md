# CI: component tree, PR title check, commit guard

`ci.yml` runs on pull requests to `master` and on pushes to `master`. It does not run one flat list of
jobs. A `changes` job decides which components a change touches, and each affected component (a node of
`.github/components.json`) calls its own reusable check workflow. Checks are reported as
`<node> / <check>`, for example `android / lint` or `native-host / compile (macos)`.

Only two checks are required on `master`:

| Required check | Workflow | What it means |
|---|---|---|
| `ci-ok` | `ci.yml` | `changes` succeeded and no selected node (or `commit-guard`) failed or was cancelled |
| `pr-title` | `pr-title.yml` | the PR title and body, the future squash commit, are a Conventional Commit |

Every other check reports a status but is not required. Skipped nodes do not block `ci-ok`.

## Tree

```
ci.yml
├── changes            components.py check + last-green selection -> one boolean output per node
├── android            check-android.yml         lint | unit-tests | release-hook-guard | instrumented-tests
├── web-extension      check-web-extension.yml   lint | unit-tests | browser-tests
├── installer          check-installer.yml       lint | unit-tests (linux, windows, macos) | scripts-tests
├── rust               check-rust.yml            lint | unit-tests | jni-integration
├── protocol           check-protocol.yml        contract-tests
├── native-host        check-native-host.yml     lint | unit-tests (linux, windows, macos) | build-contract
│                                                | compile (windows, macos)
├── bridge-scaffold    check-bridge-scaffold.yml lint | unit-tests
├── supply-chain       build-supply-chain.yml    build (SBOM, licence, audit and remote-code policy)
├── tooling            check-tooling.yml         unit-tests
├── commit-guard       push to master only
└── ci-ok              always()
```

What each check runs:

| Check | Commands |
|---|---|
| `android / lint` | `spotlessCheck`, `detekt`, the raw `android.util.Log` guard, `lintDebug` |
| `android / unit-tests` | `testDebugUnitTest` |
| `android / release-hook-guard` | `:app:releaseHookGuard` |
| `android / instrumented-tests` | `connectedDebugAndroidTest` on an API 33 emulator (KVM) |
| `web-extension / lint` | `./gradlew rustWasm`, `npm run format:check`, `npm run lint`, `npm run typecheck` |
| `web-extension / unit-tests` | `./gradlew rustWasm`, `npm test`, `npm run build && npm run test:bundle` |
| `web-extension / browser-tests` | `./gradlew rustWasm`, `npm run build`, Playwright Chromium, `npm run test:browser` |
| `rust / lint` | `rustFormat`, `rustLint` (host, WASM and Android targets) |
| `rust / unit-tests` | `rustTest` (native and WASM) |
| `rust / jni-integration` | the clean contract in one job, in this order: `assembleDebug` (root alias), `verify-apk-jni.sh`, `rustHostJni`, `rustWasm`, extension `npm ci && npm run build`, `./gradlew clean`, `assert-clean.sh`, `assembleDebug` (root alias), `verify-apk-jni.sh`, then the Rust tool cache refill |
| `protocol / contract-tests` | `protocolCheck` |
| `native-host / lint` | `bridgeHostToolchainCheck`, `bridgeHostFormat`, `bridgeHostLint` (Linux) |
| `native-host / unit-tests (linux)` | `bridgeHostTest` |
| `native-host / unit-tests (windows, macos)` | `bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest`; lint runs here as well because clippy only sees the cfg-gated WinRT / CoreBluetooth adapters on their own OS |
| `native-host / build-contract` | `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py'` |
| `native-host / compile (windows, macos)` | `bridgeHostBuild`; uploads the CI artifacts `native-host-windows` (exe + `SHA256SUMS`) and `native-host-macos` (mode-preserving tar + `.sha256`). These are uploaded on every run of this node, an intentional exception to "no label, no component artifact": they are check artifacts, never release assets |
| `installer / lint` | `bridgeInstallerFormat`, `bridgeInstallerLint` (Linux) |
| `installer / unit-tests (linux)` | `bridgeInstallerTest` |
| `installer / unit-tests (windows, macos)` | `bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest` |
| `installer / scripts-tests` | `bridgeInstallerScriptsTest` (all installer Python suites, including its `test_build_contract.py`) |
| `bridge-scaffold / lint` | `bridgeFormat`, `bridgeLint`, `bridgeTypecheck`, `bridgeRustFormat`, `bridgeRustLint` |
| `bridge-scaffold / unit-tests` | `bridgeNpmTest`, `bridgeRustTest` |
| `supply-chain / build` | `verify/verify-supply-chain.sh` and the report upload (`build-supply-chain.yml`, shared with `publish-release.yml`) |
| `tooling / unit-tests` | `node --test verify/test/workflow-contracts.test.mjs`, `./gradlew scriptsTest` |

The extension jobs import the generated WASM package, so each one activates the pinned
Rust toolchain, restores `build/rust-tools` and runs `./gradlew rustWasm` before `npm ci`
and the TS commands. `build-web-extension.yml` does the same before its build/package
chain. `check-rust.yml`'s `jni-integration` builds both test-only Rust outputs
(`rustHostJni`, `rustWasm`) before it builds the extension, and the clean contract still
removes them afterwards.

Each command that ran in CI before the split runs in exactly one check workflow. The workflow contract
suite (`verify/test/workflow-contracts.test.mjs`) holds that inventory and fails if a command is lost or
duplicated. CI checks never upload a release APK or an extension zip: those come only from label-driven PR
builds and changed-only master builds in `release-build.yml`; signed products come only from `publish-release.yml` (`docs/releasing.md`).

## Node selection

`changes` checks out the full history and keeps the same step order: select, check the map, fail on
unaccounted paths. Selection writes the `<node>=true|false` outputs first, then the
`scripts/components.py check` step validates every tracked file, then the failure step fails the job
when the selection reported an unaccounted changed path. The selected nodes still run and `ci-ok` fails.

The base and the selection mode differ by event:

- **First pull request run** (no evidence head): each node is diffed against `git merge-base
  origin/<base branch> HEAD`, without rename detection, so a file moved from one component to another
  selects both.
- **Later pull request runs**: `scripts/last_green.py` looks up each node's newest green head and passes
  it as `bases.json`; `components.py changed` diffs each node from that head to
  `github.event.pull_request.head.sha`. Master movement after the evidence head is ignored because the
  evidence head is an ancestor of the pull request head.
- **Push to `master`**: every node is selected with `--all`; the Actions API is never called. Ownership
  validation still runs on `github.event.before..HEAD`.
- **API failure**: a non-zero `last_green.py` exit makes `changes` emit
  `::warning::last-green evidence unavailable; selecting every node`, select every node with `--all`
  (reason `fallback` in the summary) and still validate ownership on the full diff.

A node with no usable evidence head falls back to the merge-base diff per node; it runs only when a
path it owns changed against the merge base. A missing or all-zero base, a global path, an unaccounted
path or a base that is not in the history selects every node.

`last_green.py` decides "green" as one complete attempt: a completed `ci.yml` `pull_request` run for an
ancestor of the pull request head that belongs to this pull request (for fork pull requests whose
`pull_requests` list is empty, the head repository and ref must match), whose pinned attempt has every
job in the node's declared `jobs` set with conclusion `success` and no extra `<node> / ...` job.
Skipped, neutral, cancelled, failed, running and missing jobs do not count, and results of different
runs or attempts are never combined. Evidence is used only when the component map's normalised
selection definition at that head equals the current one. The lookup is bounded (20 candidate heads and
100 API calls by default); running out of budget degrades to per-node merge-base selection, not an
error.

`changes` appends `summary.md` to the job summary: one row per node with `run`/`skip`, the evidence head
or `none`, and the reason (`never passed`, `incompatible map`, `changed since <sha>`,
`unchanged since <sha>` or `fallback`).

The rules for each changed path:

| Changed path matches | Selects |
|---|---|
| a node's `paths` | that node (and every other node that lists it) |
| a node's `ci-paths` | that node; these are check-only inputs (lint config, verifier scripts) that never cut a release |
| a node's `release-paths` | nothing in CI; these are release-only inputs (they still count for release attribution) |
| `global-overrides` | exactly the listed nodes instead of every node; the path still counts for release attribution through `global` |
| `retired-paths` | the listed nodes; the prefix is accounted for validation even when the files no longer exist |
| `global` (`.github/`, root Gradle files, `gradle/`, the wrapper, `.java-version`) | every node |
| `ci-global` | every node; check-only, never cuts a release |
| `unowned` (`docs/`, `openspec/`, `README.md`, ...) | nothing |
| nothing above (unaccounted) | every node, and `changes` fails after writing the outputs |

Current overrides: `.github/components.json` selects `tooling`; `.github/workflows/check-android.yml` and
`.github/workflows/build-android.yml` select `android`, `tooling` and `installer`.

Every node also declares `jobs`, its expected job display names without the `<node> / ` prefix; the
workflow contract suite derives them from the reusable workflow each caller invokes and fails on drift,
on a matrix job and on any shape it cannot derive.

Because every workflow file is under `.github/`, a workflow edit selects every node unless a
`global-overrides` entry names it. The two Android workflows select only `android`, `tooling` and
`installer`, because the tooling workflow contract suite and the installer `scripts-tests` read them.
Every other workflow, such as `ci.yml`, `check-rust.yml`, `build-rust.yml` and `publish-release.yml`,
stays global and still selects every node, including `native-host / build-contract` and
`installer / scripts-tests`, which own the two Python build-contract suites.

Node jobs use `if: !cancelled() && needs.changes.outputs.<node> == 'true'`, so when `changes` writes its
outputs and then fails (an unaccounted path), the selected nodes still run and `ci-ok` still fails.

The top-level `permissions` stays `contents: read`. The `changes` job declares `contents: read`,
`actions: read` and `pull-requests: read` (a job-level mapping replaces the inherited one) and uses no
secret, so pull request runs from forks can read their own evidence with the read-only token.

## Last-green kill switch and rollback

Set the repository variable `CI_LAST_GREEN=off` to make every pull request run behave like a first run
(no evidence heads, merge-base selection) without changing the workflow. `scripts/last_green.py` then
returns `null` for every node and exits 0 without calling the API.

To roll back the optimisation entirely, keep `CI_LAST_GREEN=off` or revert the `changes` job commit.
`release-build.yml` keeps its own before-based `components.py changed --base "$BEFORE"` selection and is
unaffected either way.

## ci-ok

`ci-ok` runs with `if: always()` and needs `changes`, every node and `commit-guard`. It fails when
`changes` did not succeed, or when any needed job's result is `failure` or `cancelled`. Skipped jobs pass.

## PR title check

`pr-title.yml` runs on `pull_request` types `opened`, `edited`, `reopened` and `synchronize`. No other
workflow triggers on `edited`, so editing a title re-runs only this check. It:

1. checks out the PR's base commit, not the head;
2. writes the title, a blank line and the body to `$RUNNER_TEMP/pr-message.txt` and runs the base
   commit's `scripts/commitlint.py --file` on it (a file, so a large body cannot hit the argv limit);
3. passes the title and body through `env:` (`PR_TITLE`, `PR_BODY`), never interpolated into a script, so
   a title such as ``feat: `rm -rf /` $(id)`` is validated as text.

GitHub appends ` (#<PR number>)` to the squash commit title. The validator does not count one such trailing
suffix towards the 100-character header limit, so a title that passes `pr-title` also passes
`commit-guard` after the merge.

Bootstrap: if the base has no `scripts/commitlint.py` (only the PR that introduces it), the job fetches
the head commit, runs `git show "$HEAD_SHA:scripts/commitlint.py"` into `$RUNNER_TEMP` and emits the
warning `bootstrap: validator taken from the PR head`.

Limitation: a PR can edit `pr-title.yml` itself. The check protects against mistakes by trusted
collaborators, not against a malicious workflow editor. `commit-guard` is the backstop.

## Commit guard

`ci.yml` groups runs per ref for pull requests and per commit SHA for master pushes (`group: ci-${{
github.event_name == 'pull_request' && github.ref || github.sha }}`) and cancels superseded runs for pull
requests only (`cancel-in-progress: ${{ github.event_name == 'pull_request' }}`). A shared push group would
not be enough: GitHub keeps one pending run per group and drops the older pending one when a newer push
arrives. Each master push therefore runs in its own group and is neither cancelled nor dropped, because the
next push validates only its own range and a skipped `commit-guard` would leave the earlier commits
unchecked. `release-build.yml` uses the same shape (`release-build-` prefix) for its changed-only builds.

On a push to `master`, `commit-guard` runs `scripts/commitlint.py --message` on every commit in
`git rev-list "$BEFORE..$AFTER"` (only the pushed head when `before` is missing or all zeros) and fails
with an annotation naming each commit that is not a Conventional Commit. The release planner ignores such
commits.

## Future work

These checks do not exist yet. Adding them is outside the component-tree change:

- **protocol lint**: the protocol node has contract tests only, no format or lint of `protocol/`.
- **Swift / NSIS lint**: the installer's SwiftPM frontend and NSIS scripts are compiled by the installer
  producers but not linted or format-checked.
- **actionlint**: workflows are checked by the contract suites, not by `actionlint`.
- **shellcheck**: shell scripts under `rust/scripts/`, `verify/`, `native-bridge/` and `.githooks/` are not
  linted.
- **ruff**: the stdlib Python under `scripts/` and `native-bridge/*/scripts/` is tested but not linted.
- **coverage**: no node reports or gates test coverage.
