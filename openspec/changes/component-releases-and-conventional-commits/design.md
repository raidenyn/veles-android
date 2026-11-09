## Context

Veles is one repository that ships several independently consumable components: the Android
app, the Chrome web extension, and the Windows/macOS native installers (which carry the native
Bluetooth host). Shared code (`rust/veles-crypto`, `protocol/`) is compiled into more than one of
them.

Current state (observed on master `e98c371`, 2026-11-09):

- **Versioning.** Only Android has a real version: `com.gladed.androidgitversion` 0.4.14 derives
  `versionName`/`versionCode` (`codeFormat = "MMNNPP"`) from plain `X.Y.Z` tags (`0.0.1`..`0.0.3`).
  `web-extension/package.json`, `native-bridge/host/Cargo.toml`, `native-bridge/installer/Cargo.toml`
  and the installer descriptors `native-bridge/installer/config/{release,fixture}-{windows,macos}.json`
  are frozen at `0.1.0`. The extension manifest takes `pkg.version`
  (`web-extension/src/manifest.ts:94`), `web-extension/scripts/package.mjs:98-118` names the zip from
  `package.json`, and `web-extension/test/smoke.test.ts:12` plus `package-output.test.ts:9` assert that
  version. The host user-agent uses `env!("CARGO_PKG_VERSION")` (`native-bridge/host/src/host.rs:260,283`).
  The installer build reads the config `version` once (`releaseVersion`, `build.gradle.kts:1496-1499`)
  for the Windows uninstaller (`:1631-1635`), and `assemble_unsigned.py:462` writes it to the macOS
  `VERSION` file.
- **Releases.** `release.yml` builds and publishes an APK-only GitHub Release on a pushed `X.Y.Z` tag.
  `build-android.yml` both builds and signs: it accepts keystore secrets (`:13-21`), maps them into
  job-level `env` (`:29-33`) and signs inside `assembleRelease`. `release-build.yml` can publish an
  APK-only manual prerelease. `release-native-installers.yml` is an owner-only (`raidenyn`) master
  dispatch, serialized by `concurrency: release-native-installers` (`:68-70`), that builds, verifies,
  signs (protected environment `native-release`) and ends in a `gate` job running
  `release_gate.py bundle` and `validate` (`:570-592`) — it publishes nothing.
- **Commits.** History is mostly non-conventional (`OTP-25: ...`). The repo allows squash merge only,
  but `squash_merge_commit_title = COMMIT_OR_PR_TITLE` and `squash_merge_commit_message =
  COMMIT_MESSAGES`. No rulesets or branch protection exist on `master`. The repo is public and owned by
  a personal account.
- **CI.** `ci.yml` runs everything on every PR and master push: `lint-check`, `unit-tests`,
  `protocol-contract`, `instrumented-tests`, `web-extension` (also packages and uploads a zip),
  `rust` (format/lint/test, then build → verify JNI → build extension → `clean` → `assert-clean.sh`
  → rebuild → verify JNI, `ci.yml:193-202`), `native-bridge` (Tauri scaffold), `native-host` (reusable
  `build-native-host.yml`) and `workflow-contracts`.
- **Contracts.** Three suites pin the workflow graph: `verify/test/workflow-contracts.test.mjs`
  (Node), `native-bridge/host/scripts/test_build_contract.py` (requires `build-native-host.yml` and
  its CI caller, `:384-398`) and `native-bridge/installer/scripts/test_build_contract.py`
  (`check_release_build`, `check_gate_boundary`, `check_host_workflow_installer`; `:615-731,
  1186-1296`). `verify/check-remote-code.mjs:101-106` scans named workflow files and silently skips
  absent ones.
- **npm policy.** `web-extension/.npmrc` sets `ignore-scripts=true`; lifecycle scripts never run.

Facts verified during exploration (release-please v17.11.2 source, `github/docs` source, Chrome docs):

- release-please requires a release PR and attributes commits to packages only by path prefix.
- Ruleset commit-message restrictions are Enterprise-only; merge queue needs an organization repo.
- A workflow skipped by `on.<event>.paths` leaves required checks Pending; a job skipped by `if:`
  reports Success; a job skipped because a dependency failed "may not block merging" unless the gate
  uses `always()`.
- Chrome extension `version`: one to four dot-separated integers, each 0..65535, no leading zeros,
  not all zero.

Stakeholders: the owner (`raidenyn`, the only person allowed to sign and publish), contributors, and
end users (one GitHub Release page listing every component's current version).

## Goals / Non-Goals

**Goals:**

1. Version each release component independently with SemVer from Conventional Commits, bumping only
   when commits touched its paths (including shared paths it compiles in).
2. Git tags are the versioning state: no release PR, no bot commits to `master`.
3. One owner-only `publish-release` workflow builds and signs only changed components, carries the
   rest byte-for-byte, and publishes one combined, date-tagged GitHub Release listing every component.
4. Enforce Conventional Commits locally (git hook, no root `package.json`) and on PR titles, and make
   squash commits equal the checked PR title.
5. Restructure CI into a change-gated component tree behind one aggregate gate.
6. Label-driven PR builds; master pushes build only changed components.

**Non-Goals:**

- Adding missing lint/format tooling (tracked as future work).
- The watch component (`build-watch`/`watch` are reserved names only).
- Changing signing identities or the installer signing mechanics inside `release_gate.py`.
- Releasing the standalone native host executable.
- Organization transfer or merge queue (route A chosen).
- Rewriting history or retro-tagging old releases.

**Anti-goals:**

- Any path by which someone other than `raidenyn` signs or publishes.
- A component binary that changed without a version change, or the reverse.
- A required check stuck in Pending, or a failing child reported green.
- Local tooling that forces Android-only contributors to install Node.
- Component tags that claim a release no published combined release contains.

## Decisions

### D1. Component map

`.github/components.json` is the single source of path ownership for release planning and CI
selection. JSON so `python3 -I` stdlib reads it on any runner (Python ≥ 3.8).

```json
{
  "since": "<40-hex onboarding commit>",
  "global": [".github/", "build.gradle.kts", "settings.gradle.kts", "gradle.properties", "gradle/", "gradlew", "gradlew.bat"],
  "unowned": ["docs/", "site/", "openspec/", "native-bridge/feasibility/", "README.md", "LICENSE", "CLAUDE.md"],
  "nodes": { "<name>": { "release": true, "tag-prefix": "android-v", "initial-version": "0.1.0", "paths": ["..."] } }
}
```

- `paths`, `global`, `unowned`: directory prefixes ending in `/` or exact repo-relative files; no
  wildcards (so the Python matcher and git-cliff's `p/**` globs cannot disagree).
- `global` paths select **every** CI node (the root Gradle files define tasks for every component;
  workflows define everything) and are included in **every** release component's git-cliff paths,
  because a root build change can change any binary. Only bumping types bump, so a `build:`/`ci:`
  commit there releases nothing, while a `fix(build): …` that changes outputs releases all three.
- A changed file that is neither owned, global nor unowned selects every node, and the ownership
  check fails (see D7).

| node | release | paths |
|---|---|---|
| `android` | yes, `android-v`, 0.1.0 | `app/`, `rust/`, `protocol/`, `config/detekt/` |
| `web-extension` | yes, `web-extension-v`, 0.1.0 | `web-extension/`, `rust/`, `protocol/`, `verify/lib/` |
| `installer` | yes, `installer-v`, 0.1.0 | `native-bridge/installer/`, `native-bridge/host/`, `protocol/` |
| `rust` | no | `rust/` |
| `protocol` | no | `protocol/`, `rust/`, `app/`, `web-extension/`, `native-bridge/` |
| `native-host` | no | `native-bridge/host/`, `protocol/` |
| `bridge-scaffold` | no | `native-bridge/` children other than `host/`, `installer/`, `feasibility/` (listed explicitly) |
| `supply-chain` | no | `verify/`, `licenses.toml`, every `package.json`/`package-lock.json`/`Cargo.toml`/`Cargo.lock` path |
| `tooling` | no | `scripts/`, `.githooks/`, `cliff.toml`, `verify/test/` |

The implementer audits cross-directory dependencies — source imports and build/config references
(e.g. `web-extension/scripts/package.mjs:36` imports `verify/lib/checksum-manifest.mjs`;
`app/build.gradle.kts:16-19` reads `config/detekt/detekt.yml`) — and records each as a path entry; a
unit test pins the known ones.
Windows and macOS installers are one release unit; the host is check-only and versioned with the
installer.

### D2. Versions and notes from tags with git-cliff

`scripts/release_plan.py` runs a pinned git-cliff (spike: **2.14.2**) per release node. Confirmed by the
Task 1 spike (git-cliff 2.14.2):

```bash
git-cliff --config cliff.toml \
  --include-path 'app/**' --include-path 'rust/**' \
  --tag-pattern '^android-v[0-9]+\.[0-9]+\.[0-9]+$' --bumped-version     # -> android-v0.1.1
git-cliff --config cliff.toml <same --include-path/--tag-pattern> --unreleased --strip all   # notes
```

- The range starts at the node's own latest matching tag; tags of other components have no effect, and a
  `rust/`-only fix counts for Android. `--bumped-version` **carries the tag prefix** (`android-v0.1.1`),
  so the planner strips the prefix. Output is on stdout; INFO/WARN logs go to stderr.
- With zero relevant bumping commits it prints the current tag unchanged, which means **no release**: the
  planner compares the output to the current tag.
- With no matching tag, `--bumped-version` fails unless `initial_tag` matches the tag pattern, and
  `initial_tag` in `cliff.toml` is global. The planner therefore handles the no-tag case itself (see below)
  and never calls `--bumped-version` without a tag. Task 4 must keep `initial_tag` out of `cliff.toml`. (Fallback `git describe --match '<prefix>*'` is not
  needed for tagged components.)
- `cliff.toml` pins the bump policy. `breaking_always_bump_major` is a real option in 2.14.2:

```toml
[git]
conventional_commits = true
filter_unconventional = true
commit_parsers = [
  { field = "raw_message", pattern = "^[a-z]+(\\([^)]*\\))?!:", group = "Breaking" },
  { field = "raw_message", pattern = "(?m)^BREAKING[ -]CHANGE:", group = "Breaking" },
  { message = "^feat", group = "Features" },
  { message = "^fix", group = "Bug Fixes" },
  { message = "^perf", group = "Performance" },
  { message = ".*", skip = true },
]

[bump]
features_always_bump_minor = true
breaking_always_bump_major = true
```

  Parser order matters; the catch-all `skip` must be last.

  Without the final `skip` parser, git-cliff bumps patch for `docs`/`chore`/`refactor`/`test`/`build`/`ci`/
  `style`/`revert`; with it they do not bump or appear in notes. The `raw_message` parsers keep
  breaking commits of those types (`docs!:`, a `BREAKING CHANGE:` footer) from being skipped (the
  `body`, `footer` and `breaking` parser fields did not work for this). Verified on 0.3.2 and 1.2.3:
  `feat` → minor (0.4.0 / 1.3.0); `fix`/`perf` → patch; `feat!`, `fix(x)!`, `docs!`, `BREAKING CHANGE:`,
  `BREAKING-CHANGE:` footer → major (0.3.2 → 1.0.0, 1.2.3 → 2.0.0); the other types and
  non-conventional commits → unchanged. Known limit: the literal phrase inline in prose does not count
  (needs a footer line).
- Pinned git-cliff **2.14.2**, download base `https://github.com/orhun/git-cliff/releases/download/v2.14.2/`;
  archive SHA-256 (computed locally; Task 4 pins these):

  | asset | sha256 |
  |---|---|
  | `git-cliff-2.14.2-x86_64-unknown-linux-gnu.tar.gz` | `24f397c733add5390fdceee3a2088588ab0d5f944ce00d34cb7029b888cf2db4` |
  | `git-cliff-2.14.2-aarch64-apple-darwin.tar.gz` | `a0be97e237d440d34c5e508ec21d070b2c85cbb8be48a46a56f9a4e8bd247f05` |
  | `git-cliff-2.14.2-x86_64-apple-darwin.tar.gz` | `4e647d2deb152aca1e987f225cf42b080b991dd98f3bce4027ef95446d982ac3` |
  | `git-cliff-2.14.2-x86_64-pc-windows-msvc.zip` | `6ece2112b3f4af462190ecbea4aeb1315fff6af415629b597f84319073c31131` |

- No tag → `initial-version` (the planner sets it, not git-cliff); notes limited to `since..HEAD` by passing
  the range positionally (`--unreleased --strip all <since>..HEAD`, verified to scope the notes). Android starts at 0.1.0 (`versionCode`
  100 > 3). Old plain tags `0.0.x` never match.
- CHANGELOG files are not committed; notes live in combined release bodies.

### D3. Version injection

| component | mechanism |
|---|---|
| Android | `androidGitVersion { prefix = "android-v"; codeFormat = "MMNNPP" }` (spike: plugin 0.4.14 honours `prefix`; no fallback needed. Tag `android-v1.2.3` → name `1.2.3`, code `10203`; `android-v0.1.0` → code `100`; with no `android-v*` tag, old `0.0.x` tags are ignored → name `untagged-…`, code `0`, so `maxOf(code, 1)` stays; web tags do not interfere). Publish creates the tag **only in the runner's checkout** before building; it reaches the remote only in the `publish` job. |
| web-extension | `resolveExtensionVersion()`: `VELES_EXTENSION_VERSION` if set, else `git describe --tags --match 'web-extension-v*'` (numeric part → `version`, full string → `version_name`), else `0.0.1`. Every value is validated against Chrome's rules. `build-web-extension.yml` gets an optional `version` input mapped to the build step env and checks out with `fetch-depth: 0`. `package.mjs` names the zip from the **emitted** `dist/manifest.json`; `smoke.test.ts` and `package-output.test.ts` stop asserting `package.json`'s version. |
| installer + host | One effective version: Gradle `installerVersion = providers.environmentVariable("VELES_INSTALLER_VERSION").orElse(<config version>)` (production channel only; fixture keeps the config value). It feeds `assemble_unsigned.py assemble --version` (descriptor **and** the macOS `VERSION` file, both passes), `generate-uninstaller.ps1 -Version`, and the host cargo env `VELES_HOST_VERSION` (read by `option_env!`). It is declared `inputs.property("installerVersion", …)` on the host, uninstaller and assemble/stage tasks so a version-only change re-runs them. `build-native-{windows,macos}.yml` get an optional `version` input, exported only for `build-kind: release`. `Cargo.toml`/`Cargo.lock` are never modified. |

The installer approval model is unchanged: `release_gate.py authorize` already compares
`VELES_DESCRIPTOR_SHA256` with the in-run packaged descriptor.

### D4. Owner-only `publish-release` workflow

Trigger `workflow_dispatch`; `concurrency: { group: publish-release, cancel-in-progress: false }`;
`permissions: contents: read` at workflow level. Every job repeats the guard (`refs/heads/master`,
`github.actor == github.triggering_actor == raidenyn`, 40-hex `github.sha`) and needs `guard`.

```
 guard
 plan            release_plan.py + previous release-manifest.json -> plan.json (RELEASE/CARRY)
                 fails: nothing to release | orphaned component tags | shallow repo
 supply-chain    build-supply-chain.yml (policy)
 build-android   build-android.yml (UNSIGNED; version tag created locally)      if android RELEASE
 sign-android    env android-release: apksigner (pinned build-tools 36.0.0) sign + verify,
                 cert SHA-256 == vars.VELES_ANDROID_CERT_SHA256, signed checksums
 build-web-ext   build-web-extension.yml with version input                      if web-ext RELEASE
 build-/verify-/sign-<os>   moved from release-native-installers.yml, release kind + version input;
                 installer RELEASE selects BOTH platforms                      if installer RELEASE
 gate-installer  release_gate.py bundle + validate, PLATFORM=both (ported job) if installer RELEASE
 carry           release_assets.py verify-carry against previous release-manifest.json
 ready           if: always(); release_ready.py over toJSON(needs) + plan.json
 draft           contents: write, id-token: write, attestations: write; stage assets +
                 release-manifest.json + SHA256SUMS; attest new
                 assets; gh release create <tag> --draft --target <sha> (no tags pushed)
 publish         env release-publish; contents: write; re-download draft assets, compare with
                 the digests recorded by draft; git push --atomic <RELEASE component tags>;
                 PATCH draft=false
```

- **Android signing:** `build-android.yml` loses its secrets interface entirely and always produces
  `app-release-unsigned.apk` + mapping + `SHA256SUMS`. `sign-android` is an ordinary job (not a
  reusable-workflow caller) with `environment: android-release`; it verifies the downloaded artifact's
  checksums, signs with `apksigner` (v2+v3; minSdk 33), runs `apksigner verify --print-certs`, compares
  the certificate SHA-256 with the repository variable `VELES_ANDROID_CERT_SHA256`, and uploads the
  signed APK with new checksums. The keystore secrets exist only in `android-release`.
- **Installer platform selection:** the old `platform` dispatch input disappears. Installer RELEASE
  always builds, verifies and signs **both** Windows and macOS (one shared version, two required
  assets); every former `inputs.platform` predicate becomes `needs.plan.outputs.installer == 'RELEASE'`,
  and the ported jobs receive `PLATFORM: both`.
- **Installer gate:** `gate-installer` ports `release-native-installers.yml`'s `gate` job (reports
  collection, `bundle`, `validate`) with `PLATFORM: both`, restricted to the installer RELEASE case; its
  result is required by `ready` whenever the installer is RELEASE.
- **Readiness:** `scripts/release_ready.py` receives `toJSON(needs)` and `plan.json` and succeeds only
  when `guard`, `plan`, `supply-chain` and `carry` succeeded and, for each component, every job of that
  component succeeded if it is RELEASE or was skipped if it is CARRY. Any `failure`/`cancelled`
  fails it. `draft` needs `ready` with ordinary success semantics. This keeps the dependency logic in
  unit-tested code instead of long expressions.
- **Release manifest:** every combined release carries `release-manifest.json`:
  `{"schema":1,"release":"2026.11.09","commit":"<sha>","components":{"android":{"version":"0.1.0","assets":{"veles-android-0.1.0.apk":"<sha256>","veles-android-0.1.0-mapping.txt":"<sha256>"}},...}}`.
  Required asset sets: android = APK + mapping; web-extension = zip; installer = Windows `.exe` +
  macOS `.dmg`. `carry` downloads exactly the carried component's listed assets and verifies each
  digest from the previous manifest (and the previous `SHA256SUMS` line for the same name); a missing,
  extra, or mismatched asset fails.
- **Tag ordering and recovery:** no tag reaches the remote before the owner approves `publish`.
  `publish` re-verifies the draft against the digests `draft` recorded, then
  creates the new tags locally for **RELEASE components only** and runs
  `git push --atomic origin refs/tags/<c>-v<next>...` (all of them at `github.sha`, or none), then
  publishes the draft with its date tag. CARRY components' tags are never touched and stay at their
  original commits. `publish` is idempotent: on re-run, a RELEASE tag already at `github.sha` is
  accepted, a RELEASE tag at another commit fails, an already-published release is a no-op.
  `plan` fails closed if any component's latest tag is newer than the version in the latest published
  combined release's manifest (orphaned tags from an interrupted publish); `docs/releasing.md` gives the
  runbook (re-run the failed `publish` job of the same run, or delete the orphaned tags and the draft).
- **Release tag:** UTC `YYYY.MM.DD`, then `.2`, `.3` the same day. **Assets:**
  `veles-android-<v>.apk`, `veles-android-<v>-mapping.txt`, `veles-web-extension-<v>.zip`,
  `veles-installer-windows-<v>.exe`, `veles-installer-macos-<v>.dmg`, `release-manifest.json`,
  `SHA256SUMS`. **Notes:** version table (new / unchanged since `<release>`), new components'
  git-cliff sections, verification instructions.
- **Retired** in the same change: `release.yml`, `release-native-installers.yml`, and
  `release-build.yml`'s `workflow_dispatch`/`publish`.

### D5. Release preview

On every master push `release-build.yml`'s `preview` job runs (no secrets): `gh release list --json
tagName,isDraft,isPrerelease,publishedAt` → `release_plan.py latest` → `gh release download <tag>
--pattern release-manifest.json` (or `{}` when none) → `release_plan.py plan … --summary
"$GITHUB_STEP_SUMMARY"`. The draft plus `release-publish` approval replaces editing a release PR.

### D6. Conventional Commits enforcement

- **Validator:** `scripts/commitlint.py` (stdlib). Header `type(scope)!: subject`, types
  `feat fix perf refactor docs test build ci chore style revert`, lowercase optional scope, header ≤ 100,
  blank line before body, breaking footer requires `!`. Normalises CRLF and ignores `#` comment lines.
  `--allow-git-generated` (hook only) accepts `Merge …`, `Revert "…"`, `fixup!`, `squash!`.
- **Hook installation:** `.githooks/commit-msg`. Gradle task `installGitHooks` (configuration-cache
  safe) sets `core.hooksPath=.githooks` only when it is **unset**; if set to anything else it prints a
  warning and changes nothing; skipped when `CI=true`. Wired to `:app:preBuild`, so any Android build
  installs it. Contributors who never build Android run `./gradlew installGitHooks` or
  `git config core.hooksPath .githooks` (documented). No npm lifecycle hook: `web-extension/.npmrc`
  sets `ignore-scripts=true` by supply-chain policy, and that policy stays.
- **PR title check:** `pr-title.yml`, `pull_request` types `[opened, edited, reopened, synchronize]`,
  `contents: read`, title and body passed via `env:`. It runs the validator from the **base** commit;
  if the base has no `scripts/commitlint.py` (only the PR that introduces it), it runs the head copy and
  emits a warning annotation. **Limitation (documented, accepted with route A):** a PR can also edit
  `pr-title.yml` itself; like `build-pr-installers.yml`'s approval gate, this is enforcement against
  mistakes by trusted collaborators, not against a malicious workflow editor. `commit-guard` on master
  is the backstop.
- **Squash:** `squash_merge_commit_title = PR_TITLE`, `squash_merge_commit_message = PR_BODY`.
  **Ruleset** on `master`: PR required, checks `pr-title` and `ci-ok`, linear history, no force push or
  deletion (enabled after this change merges, so the introducing PR is not blocked).
- **Post-merge guard:** `commit-guard` validates every commit in `before..after` on master pushes.

### D7. CI as a component tree

`ci.yml` orchestrates `check-<node>.yml` reusable workflows (`<caller> / <job>` names):

```
changes      components.py check (always; fails the run on unowned paths) + changed -> node booleans
android      lint | unit-tests | instrumented-tests
web-extension lint | unit-tests | browser-tests
rust         lint | unit-tests | jni-integration (the full existing sequence: assembleDebug -> verify
             JNI -> extension build -> clean -> assert-clean.sh -> assembleDebug -> verify JNI)
protocol     contract-tests
native-host  lint | unit-tests [linux, windows, macos] | build-contract | compile [windows, macos]
installer    lint | unit-tests [linux, windows, macos] | scripts-tests
bridge-scaffold lint | unit-tests
supply-chain build-supply-chain.yml policy
tooling      unit-tests (node workflow contracts, scripts/ tests; the Python build-contract suites stay
             in native-host / build-contract and installer / scripts-tests, selected by any workflow edit)
commit-guard (push to master)
ci-ok        if: always(); fails if changes did not succeed or any needs result is failure/cancelled
```

Only `ci-ok` and `pr-title` are required. PR base = `git merge-base` with the base branch; push base =
`github.event.before`; missing/zero base, any `global` path, or an unowned unknown path → all nodes.

### D8. Builds

`release-build.yml`: PR jobs keyed on labels `build-android` / `build-web-extension` (types `opened,
reopened, synchronize, labeled, unlabeled`); push jobs keyed on `changes` (android, web-extension, and
`build-rust.yml` when `rust` changed); `preview` (D5). Jobs shared by both events use
`if: always() && (<pr label condition> || (<push> && needs.changes.outputs.<node> == 'true'))` so the
push-only `changes` skip does not block PR builds. `build-pr-installers.yml` keeps its approval
environment with labels `build-windows-installer` / `build-macos-installer`. Master pushes never
build installers. The extension zip is produced only by these builds.

### D9. Contract migration

Every task that moves or deletes a workflow updates, in the same commit, all three suites (including
tests outside the graph/signing groups that read the workflow, e.g. the Node `tag release publishes
from one build…` test that reads `release.yml` and the `reusable component workflows referenced by the
caller graph` test that requires `release-build.yml` to call `build-supply-chain.yml`) and
`verify/check-remote-code.mjs` scopes (replacing `build-native-host.yml` and
`release-native-installers.yml` with `check-native-host.yml`, `check-installer.yml`,
`publish-release.yml`). Ported invariants: host workflow artifact/Xcode/pin mutations
(`check_host_workflow`), installer checks on every platform (`check_host_workflow_installer`), release
build never builds native installers and has no publication (`check_release_build`, rewritten), the
signing boundary and gate (`check_gate_boundary`, retargeted to `publish-release.yml` with
`sign-windows`/`sign-macos`/`sign-android` as the only credentialed jobs), the platform-selection
tests (rewritten to assert installer RELEASE ⇒ both platforms), the tag-release publication-integrity
assertions (concurrency never cancels, single build, provenance, checksums, draft digest check —
retargeted to `publish-release.yml`), plus the negative-mutation tests of each. When an intermediate
commit changes a producer that a not-yet-retired workflow still consumes, that consumer is adapted in
the same commit (Task 5 makes the old manual prerelease publisher consume the unsigned artifact).

## Risks / Trade-offs

- [git-cliff per-component tag resolution or prefix bumping misbehaves] → resolved by the Task 1 spike
  (2.14.2): works with `--tag-pattern` + `--include-path`; prefix is kept in output; no-tag handled by the planner.
- [`androidGitVersion` 0.4.14 `prefix` unusable] → resolved by the spike: `prefix = "android-v"` works; no fallback.
- [Merger hand-edits the squash dialog; PR edits `pr-title.yml`] → accepted limitation of route A;
  `commit-guard` makes it visible; the planner ignores non-conventional commits.
- [Interrupted publish leaves tags or a draft] → tags pushed only after approval and atomically;
  `publish` idempotent; `plan` detects orphaned tags; runbook.
- [Path map drift] → `global` fail-open, unknown paths select everything and fail the ownership check.
- [Previous release edited or deleted] → `carry` verifies against `release-manifest.json` digests and
  fails closed; attestations are digest-based.
- [Post-build Android signing differs from Gradle signing] → `apksigner verify --print-certs` plus the
  pinned certificate fingerprint; `verify-apk-jni.sh` runs on the signed APK too.
- [Contract suites drift during migration] → D9: every workflow-moving commit runs all three suites.
- [Splitting CI adds runner setup] → only changed nodes run; caches carry over.

## Migration Plan

1. Land validator, hook, component map, planner, version injection and the CI tree with all contract
   suites migrated, in one PR (its `pr-title` uses the head validator via the bootstrap rule).
2. Same PR: new `release-build.yml`, `publish-release.yml`; delete `release.yml` and
   `release-native-installers.yml` so there is always exactly one release path.
3. Owner settings after merge: squash title/body; environment `android-release` (keystore secrets moved
   from repo scope, variable `VELES_ANDROID_CERT_SHA256`); environment `release-publish`; labels;
   finally the `master` ruleset.
4. First owner run: bootstrap release of all three components at 0.1.0.

Rollback: revert the PR; old tags and releases are untouched; delete any new component tags and the
combined release.

## Open Questions

- None blocking; the Task 1 spike may switch D2/D3 to their documented fallbacks.
