# docs/AGENTS.md

Documentation, decision records and the agent-file check. `docs/` and `openspec/` belong to the
non-release `docs` CI node (`check-docs.yml`); the node's checks are `docs / agent-files` and
`docs / unit-tests`. `docs/ci.md` holds the CI tree and per-check commands.

## The agent-file check

`docs/checks/check_agent_files.py` is a standard-library Python check (Python 3.8+) of the agent
guidance layout: removed legacy files (`CLAUDE.md`, `CLAUDE.local.md`, `.claude/CLAUDE.md`,
`CONTRIBUTION.md`, `.opencode/`, `.claude/commands/`), the `AGENTS.md` line caps and start-at-root
notices, node-map parity, rule frontmatter and scope patterns, the hook-delivered rules (parsed by
the engine imported from `scripts/agent_rules.py`) and their pointer block, the `.claude/` symlinks,
hooks and skill set, `opencode.json`, `.github/CODEOWNERS` and the plugin mentions. Run it and its tests from the
repository root:

```bash
python3 docs/checks/check_agent_files.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

It prints one finding per violation (`severity path: message`), exits non-zero on errors and zero
with warnings, and takes `--platform` so the Windows symlink warning path is testable. It is
structural: it never runs `openspec update` or compares generated skill text.

## The Gate B evidence matrix validator

- `docs/checks/check_gate_b_matrix.py` validates `docs/gate-b/matrix.json`; run `python3 docs/checks/check_gate_b_matrix.py` (a clean result is necessary, not sufficient, and it never records the Gate B decision).

## Documentation site

The Mintlify site is built from the repository root: `docs.json` is its configuration and the root
`.mintignore` is an allowlist that decides which files it publishes. Layout: `docs/user/` (the Users
product), `docs/dev/` (the Developers product, with thin pages that summarise or link to files kept
elsewhere), `docs/assets/`, and the curated pages published in place (`BUILD.md`, `docs/ci.md`,
`docs/releasing.md`, `docs/reproducible-builds.md`, `docs/superspec-workflow.md`, `docs/decisions/`,
`docs/rfcs/` and `openspec/specs/`). The Overview page follows the root node map.

- **Allowlist rule:** the first `.mintignore` pattern is `/*`; every other line is `!/path`,
  `/folder/*` or `!/folder/path`. A page inside an already re-included folder (`docs/user/`,
  `docs/dev/`) needs only a navigation entry in `docs.json`; a file or folder outside them also needs
  a re-include line. Anything not re-included stays private.
- **Default ignores win:** Mintlify's built-in default ignore list (`README.md`, `CONTRIBUTING.md`, ...)
  beats a re-include, so such a file cannot be published through `.mintignore`; link to it on GitHub
  instead, as `docs/dev/contributing.mdx` does for `CONTRIBUTING.md`.
- **Wrapper rule:** each capability under `openspec/specs/` needs a wrapper page in `docs/dev/specs/<capability>.mdx` and a
  navigation entry. When a capability is removed or renamed, remove or rename its wrapper in the same
  change.
- **MDX constraint:** published Markdown files and specs are parsed as MDX. Bare `<`, `{`, `}` and
  similar text outside code spans and fences can break the build; write them inside backticks.
- **The guard sees only tracked files:** `git add` a new page before running it.

```bash
python3 docs/checks/check_site.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

The guard is standard-library Python and runs in CI as the second step of `docs / agent-files`. A
local preview or full validation is optional: `npx mint@4.2.1199 validate`. Owner-side hosting settings
are in `docs/mintlify-owner-setup.md` (unpublished).

## Adding a rule or a node

**A rule** is one concern in one file in the flat `.agents/rules/` directory (no subdirectories),
named `<scope>-<topic>.md` with a known scope (`repo`, `android`, `web-extension`, `rust`,
`protocol`, `native-bridge`, `host`, `installer`, `macos`, `windows`, `verify`, `scripts`, `docs`).

- `repo-*` rules are always on: frontmatter `trigger: always_on` and no `globs`/`paths`.
- Node rules use `trigger: glob` plus identical YAML lists `globs` and `paths` of patterns under the
  rule's scope (`android/**`, or `native-bridge/installer/macos/**` plus `native-bridge/host/**` for a
  rule two sibling nodes consume), no brace expansion and no `..` segment. The filename names the
  narrowest common parent for the list, and a parent pattern such as `native-bridge/**` also applies
  inside `host/` and `installer/`.
- Frontmatter keys are limited to `trigger`, `globs` and `paths`; a node rule is at most 24,000
  bytes and a `repo-*` rule at most 4,096 bytes (the always-on context budget).
- List the file in the `## Rules` section of the `AGENTS.md` of the scope's folder (the root
  `AGENTS.md` for `repo-*`); the check fails when a rule is unlisted or a listed file is missing.

**A node** gets its own `AGENTS.md` with the nested start-at-root notice and a literal `## Rules`
section, an entry in the root node map, a `global-overrides` entry in `.github/components.json`
mapping the exact path to `["docs"]` (host adds `native-host`), a node definition with its paths,
`ci-paths`, jobs and release flag, and its `check-<node>.yml` call in `ci.yml` and `ci-ok`. Add the
new file to `NODE_FILES` in `docs/checks/check_agent_files.py`.

## OpenSpec: version and update procedure

The CLI is `>= 2.0.0, < 3` (2.0.0 last verified; 1.x is unsupported). Run updates on Linux or macOS
only — `openspec update` under Windows has not been shown to work on 2.x. The procedure is: assert
`openspec --version` is in range, run `openspec update --dry-run`, run `openspec update`, commit the
regenerated skills, then run `openspec update` again to prove idempotence (an empty diff). A newer
2.x that rewrites skill text is a normal "regenerate skills" diff, not a failure.

Generated skills live in `.agents/skills/openspec-*`, with one `.claude/skills/<name>` symlink per
skill. Never hand-edit them: `openspec update` overwrites them, and lasting changes belong in a
non-`openspec-` skill or in `openspec/config.yaml`. `.agents/skills/.openspec-target` is a tool-ID
list, not version metadata, and is excluded when the two skill directories are compared.

The workflow list used on this machine (`~/.config/openspec/config.json`, profile `custom`), which
determines the generated skill set, is: propose, explore, new, continue, apply, ff, sync, archive,
bulk-archive, verify, update. CI does not install the OpenSpec CLI; the structural check catches a
1.x regression through the forbidden legacy directories.

## OpenSpec skills

- `openspec-apply-change`
- `openspec-archive-change`
- `openspec-bulk-archive-change`
- `openspec-continue-change`
- `openspec-explore`
- `openspec-ff-change`
- `openspec-new-change`
- `openspec-propose`
- `openspec-sync-specs`
- `openspec-update-change`
- `openspec-verify-change`

## The `superspec` schema fork

`openspec/schemas/superspec/` (schema version v5) is a **third-party** schema installed by the
`superspec-setup` integration, not by OpenSpec. This repository forks it: its `/opsx:<name>`
command references were rewritten to the OpenSpec 2 skill names (`/openspec-propose`,
`/openspec-apply-change`, …) because the `/opsx:` commands no longer exist after 2.0. `openspec`
does not manage or overwrite `openspec/schemas/`, but reinstalling the schema restores the upstream
`/opsx:` references, so re-apply the skill-name rewrite after any reinstall and verify with
`grep -rn '/opsx:' openspec/schemas` (expected: no hits).

A reinstall also drops the fork's own edits, so re-apply each divergence as well:

1. **Apply pre-flight**: the `apply:` instruction's step 0a review pre-flight — refuse to start
   unless the plan has an approved record from every reviewer its preset lists, every recorded
   hash is current (note condition: a hash re-pointed for a non-triggering edit must carry its
   note; a triggering edit that was not re-reviewed refuses the start), the upstream set is
   complete and a coding model is confirmed.
2. **Sixth verify check**: the review-records audit in the `verify` instruction, its "6. Review
   records" section in `templates/verify.md`, and the six-check counts in `README.md` and
   `INTEGRATION.md`. The same note condition: a hash re-pointed after a non-triggering edit must
   carry its note, and a re-point without it or a triggering edit that was not re-reviewed is FAIL.
3. **Finalize hand-off**: the `finalize` instruction's always-create PR step (no prompt, no skip), the
   open-PR confirmation and the fail-closed stop, the receipt written only after confirmation, the
   `tracker.md` record (committed and pushed as part of the PR) and the In Review confirmation, the per-outcome
   hand-off to `.agents/rules/repo-superspec-closeout.md`, and the matching "Next step" bullets in
   `templates/finalize.md`.
4. **Archive once, in the closeout**: the replaced direct-archive directions in the `verify` and
   `finalize` instructions, `apply:` step 4, `templates/verify.md`'s PASS option and next-step note,
   the `README.md` golden path and `INTEGRATION.md`'s golden path and command table. Archive runs
   once, inside the closeout, when the required checks are green.
5. **Version bump**: `version: 5` in `schema.yaml` with its "v5 (current)" description entry, and
   the current-version mentions in `README.md`, `INTEGRATION.md` and this file.
6. **Derived project statuses**: the draft spec pre-review PR (`gh pr create --draft`), finalize's
   `gh pr ready` step and its `state,isDraft` confirmation, the closing keyword and follow-up list
   in the PR body, the In Review confirmation (only after a `project_flow.py sync` that exits 0
   and prints `sync: complete`, re-run once for the closing-keyword lag before reporting another
   Status; the Status is never written), the post-merge Done check that needs the same complete
   `sync`, the `merge-locally` close-the-issue-then-`sync` step and the claim-based `discarded` and
   `kept-as-is` bullets, in `schema.yaml`, `INTEGRATION.md` and `templates/finalize.md`.

## OpenCode and the `opencode-rules` plugin

OpenCode has no native path-scoped rules, so scoped rules reach it only through the third-party
`opencode-rules` plugin declared in `opencode.json`. The plugin loads `additionalRuleDirectories`
(today `.agents/rules`) and delivers a rule only when the files the agent reads match its `globs`;
`repo-*` rules are always on. The root `AGENTS.md` and every node's `## Rules` list keep the files
discoverable if the plugin is not active.

- **Install**: the plugin is configured once, in `opencode.json`, at the range pinned there; OpenCode
  installs a missing configured plugin in the background. There is no separate install command and
  the version must not be repeated in any other document — `opencode.json` is the single source.
- **Check and update**: `opencode plugin list` shows what is installed; `opencode plugin update`
  updates an installed package within the pinned range. Widen the range only by editing
  `opencode.json` in a reviewed change; the agent-file check rejects an unpinned or unbounded entry.
- **Observed caveat**: if `opencode-rules` is also enabled in a user-level OpenCode config, two
  plugin instances load and project rules are not delivered. Configure it once — remove or disable
  the duplicate global entry.
- **Observed repeated-read caveat** (`opencode-rules` 2.0.0): reading the same file a second time in
  one session can drop the scoped rule that the first read delivered, so a rule that applied earlier
  can stop applying. If a scoped rule appears to vanish, read the rule files listed in the node's
  `## Rules` section by hand; the always-on `repo-*` rules are unaffected.
- **Untrusted checkouts**: any agent configuration in a checkout (`opencode.json`,
  `.claude/settings.json`, auto-discovered plugins and more) can make your tool install and run code,
  and this repository's hooks run a script on every shell command and prompt. Review an untrusted PR
  from a trusted checkout (its diff, not its working tree) or with a file viewer that is not an agent.
  Run an agent in it only after disabling all of that tool's project configuration; removing the
  known entries is not enough, since the checkout can add others.
- **Plugin unavailable**: with an offline cache miss, an install failure or the plugin disabled,
  OpenCode still starts; scoped rules are simply not delivered, and the root `AGENTS.md` pointer plus
  each node's listed rule files keep them discoverable. This degraded state is supported, not a
  repository error.

## Hook-delivered rules

A hook-delivered rule reaches the agent only when it matters: the Claude Code hooks and the local
OpenCode plugin run `scripts/agent_rules.py`, which adds the rule at a skill or workflow command, or
denies a gated shell command once with the rule text. The root `AGENTS.md` pointer block is the
always-on fallback.

- **Adding a rule**: one file `.agents/hooked-rules/<scope>-<topic>.md`; frontmatter keys `pointer`
  (required), `skills`, `commands`, `gates` (double-quoted strings, regexes for the last two); a body
  of at most 4,096 bytes. Run `python3 -I scripts/agent_rules.py pointers --write`, then the
  agent-file check.
- **Supported configuration**: start the agent at the repository root, with no parent `CLAUDE.md` or
  `CLAUDE.local.md`.
- Grammar, delivery, gates, degraded modes and verified versions:
  [docs/hook-delivered-rules.md](hook-delivered-rules.md).

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
