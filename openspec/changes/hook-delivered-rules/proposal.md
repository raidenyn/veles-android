## Why

Every always-on rule is loaded at session start whether or not it matters, and nothing re-delivers a rule after a
long session's context is summarized. As more rules are added, the context fills up and agents forget rules when
they matter. The commit-type rule shows the cost. Agents type changes `feat` that are not user-visible, such as
`feat(ci): …` or toolchains, and `feat` bumps a release component's minor version. This change delivers a rule
only when it is needed, and again after a summary, in both Claude Code and OpenCode.

## What Changes

- **Hook-delivered rules (new).**
  - Rules live in `.agents/hooked-rules/`, which no tool loads up front.
  - A shared stdlib Python engine (`scripts/agent_rules.py`) delivers a rule without blocking at its declared
    moments:
    - a skill invoked by the model or by the user as `/<skill>`;
    - a skill's `SKILL.md` being read;
    - a workflow command that exits successfully.
  - The engine blocks the first gated command once, with the rule as the reason, when the rule was not delivered
    since the last compaction. A retry that matches the pending gate pattern proceeds. A sibling call that matches
    a different pattern is denied too.
  - Delivery state is per context and re-armed by compaction. Hooks fail open and never grant permission.
- **Adapters (new).**
  - Claude Code: hooks in `.claude/settings.json`.
  - OpenCode: a local V2 plugin in `scripts/opencode-hooked-rules/`, declared next to `opencode-rules`.
  - Antigravity: no adapter; it gets the pointer only.
- **Always-on pointers (new).** A generated block in the root `AGENTS.md` holds one line per hook-delivered rule.
  The agent-file check fails when the block is stale.
- **Commit and PR types (modified).**
  - From: `.agents/rules/repo-conventional-commits.md`, which is always on, states only the format, and gives no
    guidance on choosing a type.
  - To: `.agents/hooked-rules/repo-commit-types.md`, with a short guide for each of the 11 `commitlint.py` types
    and a purpose rule for mixed changes. The format rule stays always on in its pointer.
  - Gates: `git commit`, `gh pr create`, `gh pr edit`.
  - Moments: `openspec-apply-change`, `opsx:apply`, `openspec instructions finalize`.
  - Reason: it reduces type misclassification that causes unintended release bumps, without loading the guide all
    session. It is advisory: a wrongly chosen valid type can still pass.
  - Impact: non-breaking. `commitlint.py`, the type list and the bump rules are unchanged.
- **Agent-guidance layout (modified).**
  - From: one rule directory, nine root-listed `repo-*` rules, and exactly one OpenCode plugin.
  - To: the hooked-rules directory and its grammar, the pointer block, eight `repo-*` rules plus one hook-delivered
    rule, and a second, local OpenCode plugin. CODEOWNERS covers the new executable paths.
  - The existing scenarios change:
    - the startup-load and frontmatter scenarios for `repo-*` rules are narrowed to `.agents/rules/`;
    - the blanket second-plugin rejection gains an exception for the one approved local plugin;
    - the Claude Code hook wiring becomes an agent-file check invariant.
  - Impact: guidance only.
- **CI routing (modified).** The engine and plugin files route to both the `tooling` and `docs` nodes, because the
  agent-file check imports the engine's parser.
- **Ship gate.**
  - A first spike proves six end-to-end behaviours in both tools. If one fails, work stops for the owner's
    decision.
  - The behaviours are re-run after implementation, and the completion report and the PR record what ran in which
    tool and version.
- **Out of scope:** moving the closeout, tracker-intake, review-gate and roster rules (follow-ups); prompt-pattern
  moments and sticky re-delivery; Antigravity hooks; any deterministic or LLM type check.

## Capabilities

### New Capabilities
- `hooked-rule-delivery`: covers these parts:
  - the hooked-rule file format;
  - the engine's events, decisions, delivery state, gates, re-arm and fail-open behaviour;
  - the Claude Code and OpenCode adapters;
  - the generated pointer block;
  - the end-to-end verification and ship condition.

### Modified Capabilities
- `agent-guidance-layout`: these requirements change:
  - the rule-layout requirement admits `.agents/hooked-rules/` and drops Conventional Commits from the always-on
    `repo-*` list;
  - the root `AGENTS.md` carries the pointer block;
  - the `opencode.json` allow-list admits the local hooked-rules plugin;
  - CODEOWNERS covers the engine and the plugin;
  - the agent-file check enforces the new invariants.
- `commit-conventions`: adds a requirement for commit and PR type guidance. It lists each type's meaning, keeps
  `feat`/`fix`/`perf` for shipped-product changes, and gives the purpose rule for mixed changes, delivered as a
  hook-delivered rule.
- `ci-pipeline`: the scenario that names `.agents/rules/repo-conventional-commits.md` follows the moved file. The
  engine and plugin files route to both `tooling` and `docs`.

## Impact

- **New files:**
  - `scripts/agent_rules.py` and its tests in `scripts/tests/`;
  - `scripts/opencode-hooked-rules/`;
  - `.agents/hooked-rules/repo-commit-types.md`.
- **Edited files:**
  - `.claude/settings.json` (hooks);
  - `opencode.json` (the second plugin entry);
  - the root `AGENTS.md` (the pointer block and the rules list);
  - `docs/AGENTS.md`: a new hook-delivered rules section, and an untrusted-checkout warning that the repository's
    hooks now run a script on every shell command;
  - `scripts/AGENTS.md`: guidance-only changes are typed `docs`, no longer `docs`/`chore`;
  - `docs/checks/check_agent_files.py` and its tests;
  - `.github/components.json`, `.github/CODEOWNERS` and `scripts/tests/test_components.py`.
- **Removed:** `.agents/rules/repo-conventional-commits.md`.
- **Runtime:** a hook runs on every shell command in Claude Code and OpenCode, bounded to 5 seconds and failing
  open. Contributors need `python3` on `PATH` for delivery; without it they get the pointer only.
- **No product code changes:** `android/`, `web-extension/`, `rust/`, `protocol/` and `native-bridge/` are
  untouched.
