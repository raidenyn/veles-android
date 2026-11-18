# docs/AGENTS.md

Docs, decisions and the agent-file check; fork `(schema version v5)`. Rule/node:
`docs/agent-guidance.md`; OpenSpec: `docs/openspec-workflow.md` and `docs/openspec-skills.md`; OpenCode
needs the `opencode-rules` plugin pinned in `opencode.json`: `docs/opencode-rules.md`.

## Commands
```bash
python3 docs/checks/check_agent_files.py --root .
python3 docs/checks/check_gate_b_matrix.py
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

The agent-file check prints `severity path: message` per violation, exits non-zero on errors and zero
with warnings, and never runs `openspec update`.

## Hook-delivered rules

Start at the repository root, with no parent `CLAUDE.md` or `CLAUDE.local.md`; add one
`.agents/hooked-rules/<scope>-<topic>.md` file, run `python3 -I scripts/agent_rules.py pointers --write`,
then the check. Each rule adds one line to the root, which is capped at 40 lines: compress or link before
adding. Grammar, delivery, degraded modes and versions: `docs/hook-delivered-rules.md`.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
