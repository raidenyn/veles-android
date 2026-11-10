## Design Summary

Veles ships three independently consumable components from one repository: the Android app, the
Chrome web extension, and the Windows/macOS installers (which carry the native host). Shared code
(`rust/`, `protocol/`) is compiled into more than one of them. Today only Android is versioned,
everything else is frozen at `0.1.0`, releases are APK-only, and CI runs everything on every PR.

The agreed design (full detail in `design.md`):

- **Component map** — `.github/components.json` lists release nodes (`android`, `web-extension`,
  `installer`) and check-only nodes (`rust`, `protocol`, `native-host`, `bridge-scaffold`,
  `supply-chain`, `tooling`) as path prefixes, including shared paths. It drives both release
  planning and CI selection.
- **Versioning** — git-cliff computes each release node's next SemVer from Conventional Commits that
  touched its paths since its last `<component>-v<semver>` tag. Tags are the only state: no release
  PR, no bot commits, no committed CHANGELOG. Builds get their version from the tag or an injected
  input (Android via `androidGitVersion` prefix; extension via env/`git describe`; installer + host via
  producer input injected into the packaged descriptor and `option_env!`).
- **Publishing** — one owner-only (`raidenyn`, master, `workflow_dispatch`) `publish-release`
  workflow plans, builds and signs only changed components, carries unchanged components' assets
  byte-for-byte from the previous combined release, pushes component tags, and publishes one combined
  GitHub Release tagged `YYYY.MM.DD` with a version table. It replaces `release.yml`,
  `release-native-installers.yml` and the manual prerelease path.
- **Conventional Commits** — one stdlib validator `scripts/commitlint.py` used by a `.githooks`
  commit-msg hook (auto-installed by Android Gradle builds, or `./gradlew installGitHooks`; no root
  `package.json`, no npm lifecycle scripts) and by
  a required `pr-title` check (`opened, edited, reopened, synchronize`, validator taken from the base
  SHA). Squash commits use the PR title; a post-merge guard flags anything that slips through.
- **CI tree** — `ci.yml` orchestrates `check-<node>.yml` reusable workflows (`android / lint`,
  `android / unit-tests`, …), each gated by the changes job; a single `ci-ok` aggregate (with
  `always()`) is the only required CI check.
- **Builds** — PR builds stay label-driven (`build-android`, `build-web-extension`,
  `build-windows-installer`, `build-macos-installer`; `build-watch` reserved); master pushes build only
  changed components and publish a release preview summary.

## Alternatives Considered

### Alternative A: release-please (manifest mode)
- **Approach**: release-please action maintains a release PR that bumps `.release-please-manifest.json`,
  CHANGELOGs and version files; merging it tags components.
- **Pros**: widely used; manifest config; native outputs per path; reads previous versions from
  GitHub Releases with a tag fallback (verified in v17.11.2 source).
- **Cons**: the release PR is mandatory (no PR-less mode); commits are attributed to packages only by
  path prefix (no `additional-paths`), so a change only in `rust/` bumps nothing unless stamp files
  are committed; needs a PAT/App token so CI runs on its PR; aborts while a merged release PR is
  untagged.
- **Why not chosen**: the user wants no release PR and shared-path attribution without stamps.

### Alternative B: cocogitto
- **Approach**: `cog bump --auto` per package with `[packages.x] include = [...]` for shared paths.
- **Pros**: native shared-path `include`; per-package tags and changelogs; also has `cog check`.
- **Cons**: creates a bump commit by default (`disable_bump_commit` needed); adds a global monorepo tag
  scheme; heavier opinionated workflow.
- **Why not chosen**: git-cliff gives the same shared-path support with no commits and fewer
  conventions to fight.

### Alternative C: Nx (`nx affected` + `nx release`) for CI selection and releases
- **Approach**: wrap each component in an Nx project; `nx affected` selects tasks; `nx release`
  versions projects.
- **Pros**: project graph, task caching.
- **Cons**: Node + nx npm tree on every CI path and a new npm project for `verify/` (SBOM, licenses,
  install scripts); `project.json` wrappers around Gradle/Cargo/npm; `nx release` independent mode
  makes per-project releases and a release commit; no Gradle version action found.
- **Why not chosen**: high cost, and its strengths overlap with Gradle/Cargo caching already in place.

(CI selection alternatives also compared: built-in `on.paths` — skipped workflows leave required
checks Pending; `dorny/paths-filter@v4` — viable, but a second glob dialect and a third-party action.)

## Agreed Approach

Tag-driven git-cliff versioning over a single component map, an owner-only combined publish, a
stdlib commit validator shared by the hook and the PR check, and a component-tree CI with a single
aggregate gate. git-cliff won over release-please because it needs no release PR and supports shared
paths natively; the in-repo `components.py` won over Nx and paths-filter because it adds no
dependencies and shares one path map with the release planner.

## Key Decisions

User decisions:
1. Combined release tag format `YYYY.MM.DD`.
2. Release state is git tags; no release PR. Tool: git-cliff (pending a confirming spike).
3. Only `raidenyn` can produce signed artifacts and publish, through one `publish-release` workflow
   that decides what changed; `release-native-installers.yml` is not triggered separately.
4. Shared code must bump its consumers (manifest/component map, no stamps).
5. Versions are injected at build time (installer descriptor included).
6. Squash enforcement route A (personal-account repo: `PR_TITLE` + required check + post-merge guard).
7. Local hook via `scripts/commitlint.py` (no root `package.json`).
8. Components: android, web-extension, native host, installer (win+mac); scaffold/feasibility/site/
   verify excluded from releases.
9. Host artifact may be built in CI but is never a release asset.
10. CI structured as a tree `<component> / <check>`; missing lint tasks are out of scope but tracked.

Builder's calls (made by the assistant, flagged for owner review):
- The native host is a check-only node, versioned with the installer (it has no release artifact).
- `components.json` (JSON, path prefixes only) instead of YAML/globs.
- Android initial version `0.1.0` (above old `0.0.3`); extension and installer `0.1.0`.
- Same-day releases `YYYY.MM.DD.2`, `.3`, …
- Squash body = `PR_BODY`, with a rule that a `BREAKING CHANGE:` footer requires `!` in the title.
- `release-publish` approval environment before publishing the draft (owner edits notes there).
- Android keystore moves to protected environment `android-release`.
- `release-build.yml` loses `workflow_dispatch` and the manual prerelease publish.
- Extension zip only built with `build-web-extension` (no longer on every PR).
- Separate `native-host` and `installer` CI matrices.
- Supply-chain policy runs in CI when lockfiles/manifests/`verify/` change, and before every publish.

## Open Questions

- Spike outcome: does git-cliff resolve each component's previous tag independently with
  `--include-path` + `--tag-pattern`, and does `androidGitVersion` 0.4.14 honour `prefix`? Fallbacks
  are defined in design.md.
