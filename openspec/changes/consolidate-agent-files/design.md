## Context

Agent guidance lives in tool-specific places: a 336-line `CLAUDE.md` (Android architecture plus
build commands for every node, loaded for every task), a duplicate `CONTRIBUTION.md` stub, and two
OpenSpec-generated trees (`.claude/`, `.opencode/`) with duplicate skills and command files whose
text differs only in command spelling (`/opsx:new` vs `/opsx-new`). The repo is a component tree
(`.github/components.json`: android, web-extension, installer, rust, protocol, native-host,
bridge-scaffold, supply-chain, tooling); CI selects nodes per diff and aggregates in `ci-ok`.

Constraints and recon (2026-11-14, official docs plus spikes on Claude Code 2.1.323, OpenCode 2.2.7,
OpenSpec 1.17/2.0.0 and a Windows 11 PC):

| | Claude Code | OpenCode | Antigravity |
|---|---|---|---|
| Root instructions | `AGENTS.md` only when no `CLAUDE.md`/`CLAUDE.local.md`/`.claude/CLAUDE.md` exists in the working dir **or any ancestor** (≥ 2.1.277; ≥ 2.1.281 for Bedrock/telemetry-off sessions) | `AGENTS.md` | `AGENTS.md`, `GEMINI.md` |
| Nested `AGENTS.md` | on demand when it reads a file there (verified) | on demand when it reads a file or lists a directory there, once per session (verified on 2.3.0; V2 docs) | not loaded (workspace + parents) |
| Skills | `.claude/skills` only; **never `.agents/`** | `.agents/skills`, `.claude/skills`, `.opencode/skills` | `.agents/skills` |
| Rules | `.claude/rules/` (dir symlink verified); `paths:` frontmatter scopes a rule to matching files (verified) | no native scoping; third-party plugin `opencode-rules` reads `.agents/rules` via `additionalRuleDirectories` and scopes by `globs` on files read/edited (verified on 2.3.0 with plugin 2.0.0) | `.agents/rules/*.md` (one level deep), `trigger` frontmatter incl. glob scoping (docs), 24 KB/file |
| Commands | skills are slash commands (verified) | skills loaded by name | skills are slash commands |

- OpenSpec 2.0.0 (`latest`, 2026-11-11) is skills-only: `openspec update` writes one copy to
  `.agents/skills/`, creates per-skill symlinks `.claude/skills/<name>` → `../../.agents/skills/<name>`,
  removes command files and `.opencode/`, and is idempotent. On a copy of this repo it reported 61
  changes (40 deletions, 20 additions, `.agents/skills/.openspec-target` marker). The `superspec`
  schema works unchanged. OpenSpec 1.17 `update` with command symlinks present corrupted the layout.
  The CLI is installed globally at 2.0.0; the repo is not yet migrated.
- Windows (OpenSpec 1.16.1): git symlinks become text files unless `core.symlinks=true`; with them on,
  `openspec update` rewrote skills and created `.claude/commands/`, alternating between runs. Not
  reproducible on Linux; unexplained.
- `scripts/components.py` validates deleted paths as well as added ones; the component map forbids
  wildcards; `native-host / build-contract` (`native-bridge/host/scripts/test_build_contract.py`)
  reads `CLAUDE.md` for host task names and artifact names.

## Goals / Non-Goals

**Goals:**
- One tool-neutral source of agent guidance working in OpenCode, Claude Code and Antigravity, proven
  rather than assumed, with a tiny root file and node details that load only where relevant.
- Delete `CLAUDE.md` and `CONTRIBUTION.md`; root `AGENTS.md` ≤ 60 lines (hard cap 120) with no
  platform specifics; one `AGENTS.md` per node and platform folder.
- `.agents/` canonical for repo-wide rules and skills; the fewest possible symlinks.
- Repo-wide rules (framework example, to be extended later): Conventional Commits; no time-based unit
  tests (no sleeps or wall-clock dependence, inject a clock); before pushing, run the checks listed in
  the `AGENTS.md` of every node you touched; never regenerate, replace or commit signing keys.
  Node rules are separate files scoped to the node's folder (see D5), listed from the node `AGENTS.md`.
- OpenSpec 2.x skills-only (`>= 2.0.0, < 3`, last verified 2.0.0), checked structurally, with no CLI in CI.
- A new non-release `docs` CI node plus a repo-owned check that prevents drift.

**Non-Goals:**
- No rulesync/ruler now (fallback only if hooks, MCP or per-tool permissions are needed later); no
  committed duplicates of one content across tool folders.
- No change to builds, release gating or app behaviour; gates that read `CLAUDE.md` are repointed,
  never weakened.
- No rewrite of guidance content: facts move; only material already recorded in code or `docs/` is
  dropped. No extension of the rule set beyond the framework example. No `GEMINI.md`.

## Decisions

**D1. `.agents/` as source, minimal symlinks, repo-owned check (over rulesync, ruler).** rulesync
would turn `.agents/` into a generated copy, duplicate every file, compete with `openspec update`
over the same paths and add an npm dependency to the supply-chain verifier; ruler is beta, has no
commands and writes the legacy `.agent/`. With OpenSpec 2.0 the problem they solve (per-tool command
and skill copies) is gone. Rejected sub-options: a one-line `CLAUDE.md` (`@AGENTS.md`) or a
`CLAUDE.md`↔`AGENTS.md` symlink (unneeded on Claude Code ≥ 2.1.277; a symlink adds a Windows
failure mode), and command files bridged through `.agents/workflows` (clobbered by `openspec update`
1.17).

**D2. Target layout** (symlinks marked):
```
AGENTS.md                           root, ≤ 60 lines: what Veles is, node map, pointers
opencode.json                       plugins: [{ package: "opencode-rules@>=2.0.0 <3", options: { additionalRuleDirectories: [".agents/rules"] } }]
.gitattributes                      `eol=lf` for `.agents/**`, `**/AGENTS.md`, `opencode.json` (none exists today)
.agents/rules/<scope>-<topic>.md    flat; `repo-*` always on, node rules scoped by path (D5)
.agents/skills/openspec-*/          single copy, generated by OpenSpec 2
.claude/settings.json               kept (Claude-only config)
.claude/rules -> ../.agents/rules                       SYMLINK (directory)
.claude/skills/openspec-* -> ../../.agents/skills/…     SYMLINKS (created by `openspec update`)
android/ web-extension/ rust/ protocol/ verify/ scripts/ docs/        each AGENTS.md
native-bridge/AGENTS.md  native-bridge/host/AGENTS.md  native-bridge/installer/AGENTS.md
native-bridge/installer/macos/AGENTS.md  native-bridge/installer/windows/AGENTS.md
docs/checks/check_agent_files.py (+ tests)
removed: CLAUDE.md, CONTRIBUTION.md, .opencode/, .claude/commands/
```
There is no `CLAUDE.md` anywhere (any would hide `AGENTS.md` from Claude Code). Node directories get no
stubs.

**D3. Loading nested guidance.** The root file carries a mandatory node map: "before changing files
under `<dir>/`, read `<dir>/AGENTS.md`". Claude Code and OpenCode 2.x load nested files on demand
natively (both verified), so for them the map is a backstop; Antigravity does not load them, so for it the
map is the mechanism. No OpenCode plugin is needed or used. `rust/AGENTS.md` and `protocol/AGENTS.md` say Android CI also runs for their
changes and point at `android/AGENTS.md`. The root file holds no Android, macOS or Windows content;
platform facts are visible only when an agent works there.

**D4. One canonical destination per platform.** Android signing and the architecture sections →
`android/AGENTS.md`; the Android PR lint gate (`./gradlew spotlessCheck detekt lintDebug` plus the raw
`android.util.Log` guard from `check-android.yml`) → the scoped rule `.agents/rules/android-lint-before-push.md`,
listed from `android/AGENTS.md`; macOS signing identity and TCC rules →
`native-bridge/installer/macos/AGENTS.md`; Windows facts → `native-bridge/installer/windows/AGENTS.md`;
`native-bridge/host/AGENTS.md` keeps its build commands, a one-line pointer to the platform files and
the artifact names the build-contract gate needs (`native-host-windows`, `native-host-macos`,
`native-host.cdx.json`, Gradle task names); per-node command blocks → their node file; release and
Conventional Commits → `scripts/AGENTS.md` pointing at `docs/releasing.md`. A "pointer instead of a
copy" is allowed **only where the target document really holds the fact**: `docs/ci.md` has no workflow
ownership table, clean contract or runner pins (these are partly in `docs/reproducible-builds.md`), and
several facts appear nowhere outside `CLAUDE.md` (the OTP-23 fixture keystore and its signing init
script, `codeFormat`, `core.hooksPath` hook behaviour, `bridgeInstallerSupplyChain`, the retired
`build-toolchain-manifest.yml`, the architecture class names). Such facts move into the owning node's
`AGENTS.md` (or are dropped explicitly as obsolete). The plan therefore includes a fact-by-fact
traceability table from every `CLAUDE.md` section to its destination, and `verify.md` confirms it.

**D5. Rules (decided: path-scoped files, option A).** All rules are one concern per file in a single
flat `.agents/rules/` (Antigravity scans one level deep) named `<scope>-<topic>.md`, where `<scope>` is
`repo` or a node/platform id (`android`, `web-extension`, `rust`, `protocol`, `native-bridge`, `host`,
`installer`, `macos`, `windows`, `verify`, `scripts`, `docs`). `repo-*` rules have `trigger: always_on` and
no `globs`/`paths`. Node rules carry three keys with identical patterns and no brace expansion:
`trigger: glob`, `globs: ["<dir>/**"]` (Antigravity and the OpenCode plugin) and `paths: ["<dir>/**"]`
(Claude Code), so they load only when the agent reads or edits a file in that folder. Locality, not
physical placement, is the rule: the files sit in `.agents/rules/` (owned by `docs`, so editing one never
starts a code node's CI, and one `.claude/rules` symlink suffices). Each node `AGENTS.md` keeps its
architecture and commands and lists its rule files, which also serves tools that do not scope. The first
node rule is `android-lint-before-push.md` (the Android PR lint gate and `android.util.Log` guard);
further node rules come later (see D5a for the scope map and D5b for the OpenCode trust boundary). Alternatives rejected: rule files inside node folders (a `.claude/rules`
symlink per node, not scanned by Antigravity or OpenCode, and each file would need a `global-overrides`
entry) and `## Rules` sections only (no separate files, advisory in tools that do not load nested files).
**OpenCode** has no native scoping, so it uses the third-party plugin `opencode-rules`, pinned to the
supported range `>=2.0.0 <3` in `opencode.json`; installing and qualifying it is a separate system-level task, and this change
assumes it works and tests the repo's rules against it (D11). If the plugin is absent the root `AGENTS.md`
still points at `.agents/rules/`, so nothing breaks, rules just load less automatically.

**D5d. The plugin is a stated requirement for OpenCode users.** Scoped rules reach OpenCode only through
`opencode-rules`, so the repository says so where contributors read: the root `AGENTS.md` (one line: OpenCode
needs the `opencode-rules` plugin at the range pinned in `opencode.json`, see `docs/AGENTS.md`),
`CONTRIBUTING.md` (an "AI agent tooling" section with the per-tool requirements) and `docs/AGENTS.md`
(install, update, disable-when-reviewing-untrusted-checkouts, and the plugin-unavailable behaviour). None of
them repeats the version range: the pin lives only in `opencode.json`, which the system-level task qualifies,
so there is one source and no documentation drift. The check enforces that the root `AGENTS.md` and
`CONTRIBUTING.md` mention `opencode-rules`.

**D5c. Launch location (decided: Option A with "start at the repo root").** Observed (Claude Code 2.1.325
with a symlinked `.claude/rules`; OpenCode 2.3.0 with `opencode-rules` 2.0.0): started at the repo root, both
deliver `repo-*` rules and scoped rules as designed; started inside a node folder (for example `android/`),
**neither delivers any rule file** (Claude Code does not follow the symlink from a subfolder; the plugin takes
the launch folder as the project directory, so `.agents/rules` and `android/**` no longer resolve), while the
`AGENTS.md` ancestor chain still loads. The owner accepted the limitation instead of adding real-file copies or
dropping scoped files. Mitigation: every `AGENTS.md` (root and nested) carries a short fixed notice, enforced by
the check:

- root: `> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there.`
- nested: `> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.`

Nested files then list their rule files (D5), so a session started in a node folder degrades to "read the
listed rules", which the root pointer and the node file make discoverable. Verification records the
node-folder launch result for each tool.

**D5a. Scope mapping, cross-folder rules and the frontmatter format.** Normative `<scope>` → pattern map:

| scope | pattern | scope | pattern |
|---|---|---|---|
| `repo` | none (always on) | `host` | `native-bridge/host/**` |
| `android` | `android/**` | `installer` | `native-bridge/installer/**` |
| `web-extension` | `web-extension/**` | `macos` | `native-bridge/installer/macos/**` |
| `rust` | `rust/**` | `windows` | `native-bridge/installer/windows/**` |
| `protocol` | `protocol/**` | `verify` | `verify/**` |
| `native-bridge` | `native-bridge/**` | `scripts` / `docs` | `scripts/**` / `docs/**` |

Parent scopes are cumulative: a `native-bridge-*` rule also applies inside `host/` and `installer/` and must
be written for that (scaffold rules that are wrong there belong in a narrower scope, or in
`native-bridge/AGENTS.md` prose). One rule has one scope; a concern spanning folders lives once in its home
scope and the other nodes' `AGENTS.md` instruct the agent to **read** the linked rule (not merely list it):
`rust/AGENTS.md` and `protocol/AGENTS.md` say "Android CI also runs for your changes; read
`.agents/rules/android-lint-before-push.md`". `openspec/` has no scope (it belongs to the `docs` node in CI
but no `openspec/**` rule is planned). Frontmatter keys are limited to `trigger`, `globs` and `paths`
(no `keywords`, `fileContains`, `description` or other selectors, so activation stays path-only); size cap is
24,000 bytes.

*Blocking prerequisite, new evidence:* Antigravity documents `globs` as a comma-separated **string** (examples
quote one string and show no YAML list), whereas `opencode-rules` 2.0.0 accepts **arrays only** (a string
would be discarded and the rule would become unconditional). The spikes proved the array form for Claude
Code (`paths`) and the OpenCode plugin (`globs`), not for Antigravity. Task 0 of the implementation runs
`homepc agy` on the PC to test whether Antigravity accepts `globs: ["android/**"]` and matches relative to the
workspace root. If it does, the scheme stands. If it does not, the decision returns to the owner with these
fallbacks: (a) Antigravity-only `trigger: model_decision` with a `description` (non-deterministic), (b)
node rules for Antigravity live in node `AGENTS.md` prose (Option C for that tool) while Claude Code and
OpenCode keep the scoped files, (c) switch OpenCode to an alternative plugin or a repo-owned one. The
implementation does not proceed with scoped rules until this is resolved.

*Result (apply, 2026-11-14):* the owner directed the spike at the Antigravity integration available on this
machine (the T3 Antigravity driver, `gemini-3.8-flash`) instead of `homepc agy`, which fails on the PC with
`ANTIGRAVITY_LS_ADDRESS is not set`. A fresh Antigravity child that read `android/x.txt` did not receive a
rule with `trigger: glob` + list `globs`; a control rule with `trigger: always_on` was also not loaded, so
the driver does not read `.agents/rules` at all and the run is **inconclusive for the IDE**, not a rejection
of the list form. Ruling: keep the D5a scheme (the list form is required by the OpenCode plugin and Claude
Code `paths`), record Antigravity as documentation-only evidence in `verify.md`, and rely on the planned
mitigation — every node `AGENTS.md` lists its rule files in a `## Rules` section — so an Antigravity session
that never loads scoped rules can still follow the instructions by reading the listed file. A future IDE
spike decides whether Antigravity needs fallback (a) or (b).

**D5b. Trust boundary for `opencode.json`.** `opencode.json` and any other repo-controlled agent
configuration can make contributors' tools install and run code (OpenCode installs missing configured
plugins in the background), so a version constraint alone is not a trust control: a PR that edits the range
or adds a plugin would satisfy it. Therefore (i) the check allows only the keys `$schema` and `plugins`,
exactly one plugin entry whose package is `opencode-rules` constrained to the supported range
`>=2.0.0 <3` (a caret or inequality range that admits no version below 2.0.0 or at 3.0.0 or above;
`latest`, `*` and any range crossing the major bound are rejected), and fails on anything else;
(ii) a
`.github/CODEOWNERS` entry names the owner for `opencode.json`, `.claude/`, `.mcp.json` (if present), `.agents/` and `**/AGENTS.md` (anything that can instruct an agent to run commands) so
the owner can require code-owner review in branch protection (the setting is the owner's, not part of the
change); (iii) contributor guidance tells reviewers not to launch agents in an untrusted PR checkout, or to
disable project configuration, until `opencode.json` and `.claude/settings.json` have been read; (iv)
package-integrity and transitive-dependency policy are delegated to the separate system-level task (the
range is the single source it qualifies). Owner decision (apply, 2026-11-14): use the bounded range
`>=2.0.0 <3` instead of one exact version; the checker, the spec and this decision change together, and
CODEOWNERS remains the control for edits that widen it. The supported degraded state when the plugin cannot be used is: OpenCode
starts (offline cache miss, install failure or plugin disabled), scoped rules are not delivered, and the root
`AGENTS.md` pointer plus the node files' read-the-rule instructions keep them discoverable; verification
tests startup and manual discovery in that state, not only an uninstalled plugin.

**D6. OpenSpec 2.x upgrade, version floor.** `openspec update` regenerates with the installed CLI, so
the migration procedure is: use CLI `>= 2.0.0, < 3` (2.0.0 verified; assert `openspec --version` is in
range), `update --dry-run`, `update`, commit, then run `update` again to prove idempotence and record it
in `verify.md`. Contributor guidance states the range. A newer 2.x that rewrites skill text is a normal
"regenerate skills" diff, not a failure. The earlier exact pin was dropped: it existed to make a CI
`update --dry-run` comparable, and the structural check below makes that CI step unnecessary.
`.agents/skills/.openspec-target` is a tool-ID list (not version metadata) and is excluded when
comparing skill directories. References to
`/opsx:<name>` in live files (including `openspec/schemas/superspec/` instructions and templates) become
the skill names (`/openspec-propose`, …); archived and frozen evidence is untouched.

**D7. `docs` CI node.** Non-release; owns `docs/`, `openspec/`, `AGENTS.md`, `.agents/`, `.claude/`,
`opencode.json`, `.gitattributes`, `.github/CODEOWNERS` (via its override), `README.md`, `BUILD.md`, `CONTRIBUTING.md` (enumerated, no wildcards) and every nested
`AGENTS.md` (enumerated per path, selecting only `docs` instead of its own node, except `native-bridge/host/AGENTS.md`, which also selects `native-host`; `native-bridge/AGENTS.md`, which matches no
scaffold path today, gets an explicit owner). Adds `check-docs.yml` (called from `ci.yml`, included in
`ci-ok` and last-green selection), updates `docs/ci.md`, replaces the `ci-pipeline` scenario
"documentation only → no node runs", and reconciles the `component-versioning` requirement that root
build files select every non-Android node. **Decision (my call, from review): root build files
(`build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`) do not select `docs`**: the docs check
is plain Python and does not use Gradle, so the requirement becomes "every non-Android, non-docs node"
with the exemption stated in the spec, and the matching assertions in `scripts/tests/test_components.py`
change in the same commit.

*Nested `AGENTS.md` edits select only `docs`* (decided; answers the earlier open question).
`owner_nodes` unions every matching node, so a nested `AGENTS.md` would otherwise select a wide set
(`protocol/AGENTS.md`: native-host, protocol, rust, web-extension, android, bridge-scaffold;
`android/AGENTS.md`: android and protocol; `installer/{macos,windows}/AGENTS.md`: installer,
supply-chain, protocol). `global-overrides` is evaluated first for exact file names, so each nested
`AGENTS.md` gets an entry such as `"protocol/AGENTS.md": ["docs"]`; no schema change. The
`ci-pipeline`/`component-versioning` specs and `docs/ci.md` currently describe overrides as applying to
global paths only and are reworded. `.github/CODEOWNERS` (new, D5b) sits under the global `.github/` prefix and
also gets a `global-overrides` entry `["docs"]`. `.github/workflows/check-docs.yml` also gets a `global-overrides` entry
(`["docs", "tooling", "installer", "supply-chain"]`, see D9; `check-android.yml` has a similar entry) so editing it does not run every node.

*Tests and specs that use `docs/ci.md` as "the path that selects nothing"* move to another unowned fixture
path (`site/` or `LICENSE`): `test_components.py` (`test_unowned`, the git-fixture tests asserting no node
runs), `test_last_green.py` (the "docs only" commits against the real map), the `pr-last-green-selection`
scenarios and `docs/ci.md`. The map gains 12 new exact-name `global-overrides` entries (10 nested
`AGENTS.md`: android, web-extension, rust, protocol, host, installer, macos, windows, verify, scripts;
`.github/CODEOWNERS`; `check-docs.yml`), 15 in total, all expressible in the current schema (overrides are
checked first and may name owned, non-global files).

*Alternative considered and rejected*: no new node: add the agent-file paths to `tooling`'s `ci-paths`
and put the check in `scripts/` (already scanned by the remote-code check, stdlib tests in place). It
avoids a workflow, `ci.yml` output changes and the root-build exemption, but the owner asked for a
dedicated `docs` node that owns documentation and its checks, and it keeps `scripts/` about tooling.

*`basename-owners` (decided from review).* A PR that adds a nested `AGENTS.md` without its override, or a `CLAUDE.md` created by `/init`, or edits `.github/components.json` to drop an override, would otherwise not select `docs` (an unlisted nested file selects its code nodes, a file in an unowned folder selects nothing) and the failure would surface only on the master push. The map therefore gains a small key `basename-owners: {"AGENTS.md": ["docs"], "CLAUDE.md": ["docs"], "CLAUDE.local.md": ["docs"]}`, implemented in `scripts/components.py` (union in `owner_nodes`, included in `selection_spec`), and `.github/components.json` maps to `["tooling", "docs"]`. This is the only code change to `components.py`.

**D8. The check (`docs/checks/check_agent_files.py`, stdlib Python, run by `check-docs`).** Fails when
any `CLAUDE.md`, `CLAUDE.local.md` or `.claude/CLAUDE.md` exists **anywhere** in the repository (scanned
over `git ls-files -co --exclude-standard`, so a nested `/init` output is caught while ignored build and
vendor trees are not), or `CONTRIBUTION.md`, `.opencode/` or `.claude/commands/` reappear; the root file
exceeds the hard cap of 120 lines (above the 60-line target is a warning); the root node map and the
tracked `AGENTS.md` files disagree in either direction (every listed file exists, every tracked nested
`AGENTS.md` is listed, `docs/` included); a rule has
invalid frontmatter; `.claude/rules` or a `.claude/skills/*` entry is not the expected symlink (a
regular-file stand-in on Linux/macOS is an error; on Windows an explicit warning that Claude Code
bridges are inactive); or `.agents/skills` and `.claude/skills` disagree. The check is structural: it
does not run or compare the output of `openspec update`, so any 2.x CLI regenerating skill text is
acceptable, while a regression to 1.x output is caught by the forbidden legacy directories. Negative
tests cover the recursive `CLAUDE.md` case and the forbidden legacy command directory.

Machine-enforced rule invariants (each with a negative test): flat `.agents/rules/` with no subdirectory;
file name `<scope>-<topic>.md` with a known scope; size ≤ 24,000 bytes; frontmatter keys limited to
`trigger`, `globs`, `paths`; `repo-*` has `trigger: always_on` and neither `globs` nor `paths`; node rules
have `trigger: glob` and `globs`/`paths` as YAML lists equal to the scope's pattern in the D5a map, with no
brace expansion; every rule is listed by the `AGENTS.md` of its scope's folder and every listed file exists;
`opencode.json` has only `$schema` and `plugins`, exactly one `opencode-rules` entry constrained to `>=2.0.0 <3`
with `additionalRuleDirectories` containing `.agents/rules`. Also: every tracked nested `AGENTS.md` that `docs` does not already own exclusively (all except `docs/AGENTS.md` and `native-bridge/AGENTS.md`) has a `global-overrides` entry in `.github/components.json` (host additionally selects `native-host`); the root and every nested `AGENTS.md` carries the D5c notice; the root `AGENTS.md` and `CONTRIBUTING.md` mention `opencode-rules` as an OpenCode requirement (D5d); `.github/CODEOWNERS` covers `opencode.json`, `.claude/`, `.mcp.json` (if present), `.agents/` and `**/AGENTS.md`. Not machine-enforced (verification or review
instead): behaviour in the three tools, content preservation, that a rule is correct for every child folder
of a cumulative parent scope, and generated-skill content.

**D8a. No OpenSpec CLI in CI** *(decided: replaces an earlier exact-pin design)*. `check-docs.yml` runs the
stdlib checker and its tests with the repo's pinned Node/Python only; it neither installs nor requires the
OpenSpec CLI. That removes a CI-time npm install from the supply-chain surface (no integrity pin, no
`--ignore-scripts` policy entry, no workflow-contract rule for it) and removes the dependence of
`openspec update --dry-run` on the user-wide `~/.config/openspec/config.json` (its workflow list changes
the generated skill count, ≈61–63, and a clean runner also lists the config file). The one thing given up
is CI detection of a hand-edited or stale generated skill; skills are generated, and an overwrite is
visible in the diff.

**D9. Existing gates and scanners.** `test_build_contract.py` `check_docs` is repointed from
`CLAUDE.md` to `native-bridge/host/AGENTS.md` with identical assertions; citations in
`native-bridge/host/scripts/macos-test-bundle.sh` (→ the macOS file) and `verify/check-android-layout.sh`
are updated. `verify/check-remote-code.mjs` scopes gain `docs/checks/` and `check-docs.yml`; checker
changes also select `supply-chain` via `ci-paths`; `verify/test/workflow-contracts.test.mjs` gets
positive and negative tests for the new workflow and check invocation. Files that gates read from docs-owned paths (for example `docs/reproducible-builds.md`, read by the host `test_build_contract.py`) are added to the reading node's `ci-paths`, so a docs-only edit cannot pass PR CI and fail on master. Because `native-host / build-contract`
reads `native-bridge/host/AGENTS.md`, its `global-overrides` entry maps to `["docs", "native-host"]` (all other
nested files map to `["docs"]`), so a PR that drops a required string cannot pass PR CI and fail on master.
`check-docs.yml` maps to `["docs", "tooling", "installer", "supply-chain"]`: the installer's
`test_build_contract.py` reads every workflow for signing credentials (the reason the Android workflows also
select `installer`) and the scanner scans the workflow. `check-remote-code.mjs` scans every file under
`verify/`, `scripts/` and `native-bridge/installer/windows/` as code with no extension filter, so prose in a
nested `AGENTS.md` there could fail `supply-chain` on master only; the scanner therefore excludes files named
`AGENTS.md` (prose), with a negative test showing a script file with the same text is still flagged.

**D10. Retired paths.** Deleted `CLAUDE.md`, `CONTRIBUTION.md` and `.opencode/*` stay accounted for
(existing retired-paths mechanism selecting `docs`, or their `unowned` entries kept until the migration
lands) so the deletion PR passes `changes` and `ci-ok`; tests cover the complete migration diff
including deletions.

**D11. Verification standard.** Claude Code, OpenCode **and Antigravity** are each exercised
empirically (Antigravity through `homepc agy` on the PC). For OpenCode the rules are tested against the qualified
plugin: from a worktree outside the repo, at the repo root and from inside a node folder, an `android/`
read delivers the Android rules and a `rust/` read does not, `repo-*` rules are always present, a second
read does not duplicate, and `paths`/`trigger` keys are ignored without error. If one cannot be run, that is reported as a
blocker, not silently downgraded to documentation. `verify.md` records exact tool versions (spiked on
Claude Code 2.1.323 and OpenCode 2.2.7, now 2.1.325 and 2.3.0), re-runs the OpenCode nested-file and
duplicate-skill checks, and states which evidence was run and which was documentation-only. Runs happen
from a worktree outside the repository, because a worktree under the repo's ignored `.worktrees/` has the
main checkout's `CLAUDE.md` as a parent and would hide `AGENTS.md` from Claude Code. The Windows symlink
and `openspec update` spike is re-run on the PC with 2.0.

**D12. Commits.** Conventional Commits, type `docs`/`chore`: no release bump, not a release-breaking
change even though `CLAUDE.md` readers are repointed. `global-overrides` change CI selection only, not
release attribution, so a `feat`/`fix` commit touching only an owned nested `AGENTS.md` (for example
`android/AGENTS.md`) still bumps that component; guidance edits must be typed `docs`.

## Risks / Trade-offs

- **Symlinks on Windows** (`core.symlinks=false` makes `.claude/rules` and skill links plain text) →
  Claude Code sees no rules/skills there. Mitigation: the check warns explicitly; contributor docs state
  `core.symlinks=true` + Developer Mode; only two kinds of symlink remain.
- **`openspec update` misbehaves on Windows** (unexplained flip-flop on 1.16.1) → run OpenSpec updates
  on Linux/macOS until the 2.0 re-test says otherwise; the check detects drift.
- **Claude Code prerequisites**: < 2.1.277, Bedrock/telemetry-off < 2.1.281, or a `CLAUDE.md` /
  `CLAUDE.local.md` in any parent directory hides `AGENTS.md` → document in the root file; optional
  `claude-md-and-agents-md` setting for developers.
- **OpenCode may list skills twice** (scans both `.claude/skills` and `.agents/skills`) → spike; if real,
  document or add an `opencode.json` skills exclusion.
- **OpenCode depends on a third-party plugin for scoped rules** (single maintainer, built on OpenCode's
  beta plugin SDK, may break on OpenCode upgrades) → bounded major-2 range (`>=2.0.0 <3`) in `opencode.json`, checked by the
  docs check; system-level install and a per-bump regression matrix are owned by a separate task; absence
  degrades to the root `AGENTS.md` pointer.
- **Rule frontmatter must stay consistent across three tools** (`paths`, `globs`, `trigger`) → the check
  enforces identical patterns, flat layout, `<scope>-` naming that matches the pattern's folder, and no
  `globs`/`paths` on `repo-*` rules.
- **Antigravity uptake** of `.agents/rules` and the node map is documentation-only evidence → recorded as
  such in `verify.md`.
- **CI blast radius**: nested `AGENTS.md` edits select only `docs` (host also `native-host`); Android CI
  still runs for `rust/` and `protocol/` code edits, which is intended.
- **Deletion PR breaks `changes`/`ci-ok`** if retired paths are not accounted for → D10 plus tests.
- **Tools that rewrite or create instruction files** (OpenCode `/init`, Claude Code `/init`) could
  recreate `AGENTS.md` content or a nested `CLAUDE.md` → the check scans the whole repository for
  `CLAUDE.md` variants and enforces the root line cap; contributor guidance says not to run `/init`.
- **Third-party schema fork**: `openspec/schemas/superspec/` is installed by `superspec-setup`; rewriting
  its `/opsx:` references to skill names forks it, and a reinstall would restore them → record the fork in
  `docs/AGENTS.md` and re-apply on reinstall (my call: the `/opsx:` commands no longer exist after 2.0).
- **Line endings**: no `.gitattributes` exists, so Windows autocrlf could alter `openspec update` output
  and rule frontmatter → add the `eol=lf` entries above.
- **Tool-version drift**: behaviour was spiked on older tool versions → `verify.md` records versions and
  re-runs the key checks (D11).
- **Node map as shared hand-maintained list**: the root node map, the checker and `components.json` could
  drift → the checker verifies the map against tracked `AGENTS.md` files in both directions.
- **`native-bridge/AGENTS.md` is a parent of `host/` and `installer/`**: Claude Code loads it for them
  too → it holds only scaffold content and states that Tauri is not the approved production path; the
  host and installer files are self-contained.
- **A newer 2.x changes generated skill text** → a normal regenerate-skills PR; contributors outside the
  range (1.x, or 3.x) are told in the guidance, and a 1.x regression is caught by the structural check.
- **Skill overwrite**: `openspec update` overwrites any hand edit inside `openspec-*` skills → none may
  exist (verified generated); lasting changes go in non-`openspec-` skills or `openspec/config.yaml`.
- **Command rename** (`/opsx:new` → `/openspec-new-change` etc.) changes muscle memory; stale
  references in live docs/schema would give agents obsolete commands → audited and migrated in the same
  change.

## Migration Plan

1. Commit the planning artifacts first (the 2.0 upgrade guide requires a clean commit before
   `update`).
2. Check `openspec --version` is in `>= 2.0.0, < 3`, `openspec update --dry-run` (expect ≈61–63 changes, depending on the global workflow profile),
   `openspec update`, commit on its own; re-run `update` and confirm no diff. **In the same commit** add
   temporary `unowned` entries to `.github/components.json` for every new path outside a node (`.agents/`,
   `.gitattributes`, `opencode.json`, root `AGENTS.md`, `native-bridge/AGENTS.md`), because `components.py` rejects
   unaccounted tracked and changed paths (`owner_nodes` is `None` for them). Existing `.claude/` and
   `.opencode/` entries already cover their interim changes and deletions.
3. Add `.agents/rules/*.md` (`repo-*` and the first node rule `android-lint-before-push.md`), `opencode.json` (bounded plugin range) and the `.claude/rules` symlink.
4. Write the node `AGENTS.md` files and the root file by moving content out of `CLAUDE.md`; in the same
   commit repoint `test_build_contract.py` and the script citations, then delete `CLAUDE.md` and
   `CONTRIBUTION.md` (so CI stays green at every commit).
5. Add the `docs` node, **atomically with groups 6 and 7 of the tasks in one commit** (`.github/CODEOWNERS` joins here: it is under the global `.github/` prefix and its override may only name an existing node; partial activation turns the existing selection tests and `verify/test/workflow-contracts.test.mjs` red): `components.json` (node, retired paths; the temporary `unowned` entries transfer
   to `docs` atomically here), `check-docs.yml` (D8a: no OpenSpec CLI), `ci.yml`/`ci-ok`,
   `docs/ci.md`, `docs/AGENTS.md`, the check script and tests, scanner scopes and workflow-contract
   tests, selection tests in `scripts/tests/test_components.py`, and the `component-versioning`
   root-build exemption with its test updates.
6. Migrate live references to removed files and `/opsx:` names (docs, `superspec` schema); leave frozen
   evidence.
7. Verify (Claude Code, OpenCode and Antigravity empirically, including native Windows scoping and the
   plugin-unavailable state; Windows PC re-test of symlinks and `openspec update` on 2.x) and record in
   `verify.md`. **Before step 3, run the Antigravity prerequisite spike of D5** (array `globs`).

Rollback: revert the PR. To restore the 1.x layout locally, install `@fission-ai/openspec@1` and run
`openspec update` (untested; the global config was already migrated by the 2.0 install and keeps
`profile: custom`).

## Open Questions

- Does OpenCode list each skill twice? (Not testable with its CLI so far; spike during implementation.)
- Does `openspec update` 2.0 behave on Windows with symlinks on and off? (Spike on the PC.)
- Does Antigravity pick up `.agents/rules/*.md` (including glob-scoped rules) and the root node map as expected? (Docs only; the T3 Antigravity driver does not read `.agents/rules` at all — see D5a Result.)
- Which `opencode-rules` range does the system-level task qualify? (Decided in that separate task; owner
  decision in apply: `>=2.0.0 <3`. `opencode.json` is the single source.)
