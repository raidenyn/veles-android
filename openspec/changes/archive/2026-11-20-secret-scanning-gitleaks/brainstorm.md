## Design Summary

Nothing scans commits for secrets today. Signing keys and keystore passwords are git-ignored and none is
tracked, so the risk is a future mistake, but a published signing key cannot be taken back. This change adds a
cheap, layered safety net:

1. **GitHub secret scanning and push protection** (owner action). The repository is public, so both are free.
   The agent's token has no admin rights (`permissions.admin` is `false` and `security_and_analysis` reads
   back `null`), so the state cannot be read or changed by the agent. The owner checks and enables both under
   Settings → Code security; the agent records the result the owner reports.
2. **A pinned gitleaks and a `pre-commit` hook.** `./gradlew installGitleaks` installs gitleaks v8.30.1
   (released 2026-03-21) into `build/release-tools/gitleaks/`, verifying a per-platform SHA-256 pinned in
   `scripts/release-tools.toml` before extracting. `.githooks/pre-commit` runs it on the staged changes; it
   blocks a commit on a finding and, when the binary is not installed, warns, prints `./gradlew
   installGitleaks` and lets the commit through.
3. **A repository config.** `.gitleaks.toml` extends the default rule set and allowlists only the test-data
   paths that a full-history scan actually flags, each with a comment saying why.
4. **Tests and docs.** A stdlib Python test drives the hook against a temporary git repository; the
   docs that describe the hooks and the signing-key rule point at the scanner.
5. **CI decision.** No new CI job: push protection plus the local hook is the chosen enforcement (option (a)
   of the issue), recorded in `docs/ci.md`.

Facts checked on 2026-11-20 in this checkout:

- `.githooks/` holds only `commit-msg` (Conventional Commits, `scripts/commitlint.py`) and
  `install-hooks.gradle.kts`, which sets `core.hooksPath=.githooks` only when it is unset and skips `CI=true`.
  A new `pre-commit` file in `.githooks/` is therefore active wherever `commit-msg` already is, with no
  installer change.
- `build.gradle.kts` has `InstallGitCliffTask` (download, SHA-256 check before extraction, extract only the
  executable, check `--version`) and `scriptsTest`, which depends on `installGitCliff` and passes the binary
  through `VELES_GIT_CLIFF`.
- gitleaks v8.30.1 publishes `gitleaks_8.30.1_<os>_<arch>.tar.gz` (`.zip` for Windows) and a
  `checksums.txt`. The GitHub asset digests are: `linux_x64` `551f6fc8…f2470eb`, `linux_arm64`
  `e4a487ee…fb4b080`, `darwin_arm64` `b40ab0ae…aeb6a5`, `darwin_x64` `dfe101a4…1952709`, `windows_x64`
  `d29144de…2afc4e` (full values to be cross-checked against `checksums.txt` at apply).
- `.github/components.json`: `.githooks/`, `scripts/` and `build.gradle.kts` belong to the `tooling` node;
  `docs/`, `.agents/` and `CONTRIBUTING.md` to `docs`. A root `.gitleaks.toml` or `.gitleaksignore` is owned by
  no node yet and must be registered.
- `check-tooling.yml` runs `./gradlew scriptsTest`, so the new hook test runs in CI whenever tooling paths
  change.
- No tracked `*.keystore`, `*.jks`, `*.p12`, `*.pem`, `*.key`, `*.pfx` or `.env*` file.

## Alternatives Considered

### Alternative A: Add an always-run CI scan job now (issue option (b))
- **Approach**: A job outside the path-selective component tree runs `gitleaks git` on every pull request.
- **Pros**: Enforcement on every PR, even when the hook is not installed and push protection is off.
- **Cons**: Changes the CI tree and its contract tests (`verify/test/workflow-contracts.test.mjs`: one
  change-gated check workflow per node; declared jobs equal the callers' jobs), and adds a required check.
  Push protection already blocks the dangerous case (a recognised secret reaching GitHub) before CI could.
- **Why not chosen**: Larger and more invasive than the risk warrants for a first step; it remains a
  documented follow-up in `docs/ci.md`.

### Alternative B: Use the `pre-commit` framework (Python package) with the gitleaks hook
- **Approach**: Add `.pre-commit-config.yaml` and let developers run `pre-commit install`.
- **Pros**: Widely known; manages the gitleaks version itself.
- **Cons**: A new third-party Python dependency and its own network installs; it replaces `core.hooksPath`
  with its own hook shim and conflicts with the existing `.githooks/commit-msg` setup; no SHA-256 pinning in
  the repository's own format.
- **Why not chosen**: The repository already has a pinned-tool pattern and a hooks directory; this would add
  a second mechanism for the same thing.

### Alternative C: Rely only on GitHub push protection
- **Approach**: Owner enables push protection; no local tooling.
- **Pros**: No code at all.
- **Cons**: Catches only provider-recognised patterns and only at push time; nothing catches a secret before
  it lands in local history, where it then has to be rewritten out.
- **Why not chosen**: The issue asks for the local layer, and it is cheap given the existing patterns.

## Agreed Approach

Issue option (a): push protection plus a local `pre-commit` hook running a pinned gitleaks, no new CI job. It
reuses the two patterns the repository already has (`.githooks/` via `core.hooksPath` and the
SHA-256-pinned tool install), keeps the CI tree and its contracts unchanged, and puts the hard enforcement on
GitHub's side, where it covers pushes from any machine. Alternative A is the recorded follow-up.

## Key Decisions

- **Pinned version** gitleaks v8.30.1, five targets matching git-cliff's: Linux x64/arm64, macOS
  arm64/x64, Windows x64. Unsupported platforms get a clear error from `installGitleaks`, and the hook then
  warns as for a missing binary.
- **Shared install task.** Generalise `InstallGitCliffTask` into one pinned-tool install task used by both
  `installGitCliff` and `installGitleaks`, rather than copying it. (Agent's call: one copy of the
  verify-before-extract logic.) What differs between the two tools becomes task inputs: the download URL, the
  archive file name, the path of the executable inside the archive (`git-cliff-<ver>/git-cliff` versus
  `gitleaks` at the archive root; confirmed against the archive at apply), the executable name, the version
  command and its expected output (`git-cliff <ver>` versus whatever the pinned gitleaks prints for
  `version`, confirmed at apply), and the platform key. gitleaks uses its own platform names, so
  `scripts/release-tools.toml` gets `gitleaks = "8.30.1"` and keys `gitleaks-linux_x64`,
  `gitleaks-linux_arm64`, `gitleaks-darwin_arm64`, `gitleaks-darwin_x64`, `gitleaks-windows_x64`; the
  git-cliff keys are unchanged. One shared resolver reads the JVM's OS and architecture once, and each
  tool maps the result to its own platform key (`gitCliffTarget` today becomes that tool's map). `installGitCliff` keeps its name, output path and behaviour, so
  `release-build.yml`, `publish-release.yml` and the contract tests that assert they call it
  (`verify/test/workflow-contracts.test.mjs`) need no change; `node --test
  verify/test/workflow-contracts.test.mjs` and a clean `./gradlew installGitCliff scriptsTest` confirm it.
  The file's header comment is reworded to cover both tools, naming each tool's release URL and cross-check
  source (git-cliff: asset digest API and `.sha512` sidecars; gitleaks: asset digest API and the release's
  `checksums.txt`).
- **Hook behaviour.** The hook finds the repository root with `git rev-parse --show-toplevel` (as
  `commit-msg` does). It resolves the binary from `VELES_GITLEAKS` if set, else
  `<root>/build/release-tools/gitleaks/gitleaks[.exe]`; each worktree has its own `build/`, so each worktree
  runs `installGitleaks` once. Missing binary → warning on stderr naming `./gradlew installGitleaks`, exit 0.
  Present → run the pinned version's staged-changes scan (the subcommand and flags are taken from the pinned
  binary's `--help` at apply; `gitleaks git --staged` is expected for v8.19+) with `--redact` so no secret
  value is printed, `--config "<root>/.gitleaks.toml"` when that file exists, and a dedicated leak exit code
  (`--exit-code`, a value gitleaks does not use for its own errors, chosen at apply). That leak exit code
  blocks the commit with a message naming the finding report and `git commit --no-verify` as the explicit,
  deliberate bypass. Any other non-zero exit is a tool error (bad config, unknown flag, crash): the hook
  prints a warning with the exit code and lets the commit through, matching the convenience-layer intent;
  the test suite catches a broken config or flag before it ships.
- **Config.** `.gitleaks.toml` uses `[extend] useDefault = true`. Path allowlists are added only for paths a
  full scan of the current tree actually flags as test data, each with a comment saying what the data is and
  why it is not secret. The issue's three fixture paths (`protocol/` conformance fixtures,
  `rust/veles-crypto/tests/protocol_fixtures.rs`, `native-bridge/feasibility/evidence/**`) are the expected
  ones; a flagged test-data path outside them may be allowlisted the same way, narrowed to the smallest path
  that covers it, and is listed in the PR. History-only false positives (files no longer in the tree) go into
  `.gitleaksignore` by fingerprint. Those fingerprints embed the commit SHA, so a history rewrite would make
  them stale; that is accepted because the history is not rewritten. gitleaks also loads `.gitleaksignore`
  in the staged scan, which is harmless: commit-bound fingerprints never match a staged finding.
- **Full-history scan first.** Run `gitleaks git --redact` over the whole history before the config is
  final; triage every finding. A real secret stops the work and is reported to the owner for rotation; it is
  never allowlisted. Only counts are recorded.
- **Tests.** `scripts/tests/test_pre_commit_hook.py`, stdlib `unittest`, no network and no sleep: a temporary
  git repository with the repository's `.gitleaks.toml`; a staged fake AWS-style key is refused; a clean staged
  file passes; `VELES_GITLEAKS` pointing at a path that does not exist warns and passes. The fake key is
  assembled at runtime from string pieces, so the test file itself contains no literal key and neither the
  hook, the full scan nor push protection flags it; it must not end in `EXAMPLE`, because the default AWS
  rule ignores such keys, and its body must be random-looking base32 (A–Z, 2–7) so it clears the rule's
  entropy floor; that it is flagged is confirmed at apply. A fourth case points `VELES_GITLEAKS` at a stub
  script that exits with a non-leak, non-zero code and asserts the hook warns and passes. `scriptsTest`
  depends on `installGitleaks` and passes the binary through `VELES_GITLEAKS`. Skips are per test: every
  case needs `sh` and `git`; only the refused and clean cases need the real binary.
- **Digest mismatch check.** Verified once at apply by running `installGitleaks` against a deliberately wrong
  digest (temporarily, then restored) and recording that it fails. This is a deliberate deviation from a
  repeatable test: a test would need either the network or a permanent override knob in the build, and the
  mismatch branch is the same shared code `installGitCliff` already relies on.
- **Docs.** `docs/ci.md` "Git hooks" gains the `pre-commit` hook and the CI decision; `CONTRIBUTING.md` "Git
  Hooks" gains `./gradlew installGitleaks`; `scripts/AGENTS.md` names the pinned gitleaks and its install
  command next to git-cliff; `.agents/rules/repo-signing-keys.md` gains one line pointing at the scanner.
- **Component map.** `.gitleaks.toml` and `.gitleaksignore` are added to the `tooling` node; `python3 -I
  scripts/components.py check` passes.
- **No real-device run.** The change has no Android, extension or shared-logic behaviour; it is tooling and
  docs, which the real-device rule exempts. The hook is exercised in a real git checkout instead.
- **Untracked work.** The issue is in no GitHub Project, so it is untracked: no issue or board writes. The
  issue asks for results to be "recorded in the issue"; they are recorded in the PR description instead, and
  the PR carries a non-closing `Refs` reference. The owner may copy them to the issue.

## Open Questions

- The owner's answer on secret scanning and push protection (enabled, or declined) — needed before the PR is
  final; the agent cannot read the setting.
- Whether an always-run CI scan job (Alternative A) should follow as its own issue.
