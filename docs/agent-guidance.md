# Agent-file guidance: the 40-line policy, rules and nodes

This page explains the agent-file layout, the check that enforces it, and how to add a rule or a node.
The files themselves are the contract; this page is the procedure.

## The 40-line policy

Every tracked `AGENTS.md` — the root and every node file — is capped at **40 lines**. The count is the
number of lines after CRLF normalization, so blank lines, headings, the notice and the hook pointer block
all count. Exactly 40 passes; 41 fails the agent-file check with
`has <n> lines; the AGENTS.md cap is 40 lines`.

Each file holds only its scope's purpose, commands, constraints, a `## Rules` section, the start-at-root
notice and links to the docs pages that hold the detail. Architecture and procedures belong in `docs/`.

The root is the tightest file: the node map and the eight `repo-*` paths must stay there. A change that
adds a node, a `repo-*` rule, a hooked rule or a root line must compress the root or move a fact to docs.
Each hooked rule adds one line to the root pointer block when `pointers --write` runs; the hook procedure
and the cap are stated in [`docs/AGENTS.md`](AGENTS.md#hook-delivered-rules).

## The agent-file check

`docs/checks/check_agent_files.py` is a standard-library Python check (Python 3.8+) run by the `docs`
node. Run it and its tests from the repository root:

```bash
python3 docs/checks/check_agent_files.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

It prints one finding per violation (`severity path: message`), exits non-zero on errors and zero with
warnings, and is structural: it never runs `openspec update` or compares generated skill text. The Gate B
matrix command stays in [`docs/AGENTS.md`](AGENTS.md).

## Adding a rule

A rule is one concern in one file in the flat `.agents/rules/` directory (no subdirectories), named
`<scope>-<topic>.md` with a known scope (`repo`, `android`, `web-extension`, `rust`, `protocol`,
`native-bridge`, `host`, `installer`, `macos`, `windows`, `verify`, `scripts`, `docs`).

- `repo-*` rules are always on: frontmatter `trigger: always_on` and no `globs`/`paths`.
- Node rules use `trigger: glob` plus identical YAML lists `globs` and `paths` of patterns under the
  rule's scope (`android/**`, or `native-bridge/installer/macos/**` plus `native-bridge/host/**` for a
  rule two sibling nodes consume), no brace expansion and no `..` segment. The filename names the
  narrowest common parent for the list, and a parent pattern such as `native-bridge/**` also applies
  inside `host/` and `installer/`.
- Frontmatter keys are limited to `trigger`, `globs` and `paths`; a node rule is at most 24,000 bytes and
  a `repo-*` rule at most 4,096 bytes (the always-on context budget).
- List the file in the `## Rules` section of the `AGENTS.md` of the scope's folder (the root `AGENTS.md`
  for `repo-*`); the check fails when a rule is unlisted or a listed file is missing.

## Adding a node

A node gets its own `AGENTS.md` with the nested start-at-root notice and a literal `## Rules` section, an
entry in the root node map, a `global-overrides` entry in `.github/components.json` mapping the exact path
to `["docs"]` (host adds `native-host`), a node definition with its paths, `ci-paths`, jobs and release
flag, and its `check-<node>.yml` call in `ci.yml` and `ci-ok`. Add the new file to `NODE_FILES` in
`docs/checks/check_agent_files.py`, and keep the file at or under 40 lines by compressing it or moving a
fact to docs.

## Untrusted checkouts

Any agent configuration in a checkout (`opencode.json`, `.claude/settings.json`, auto-discovered plugins
and more) can make your tool install and run code, and this repository's hooks run a script on every
shell command. Read those files before launching an agent in an untrusted checkout, or disable the
tool's project configuration. The full warning and the OpenCode-specific caveats are in
[`docs/opencode-rules.md`](opencode-rules.md).
