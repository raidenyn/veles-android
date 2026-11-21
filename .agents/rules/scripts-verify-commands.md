---
trigger: glob
globs: ["scripts/verify-commands.json"]
paths: ["scripts/verify-commands.json"]
---

Editing `scripts/verify-commands.json`? Read `docs/verify.md` first: it explains every field, the
related-test strategies, requirements and their fix text, and generated outputs. The table is the
single source of the commands the verifier runs: a node doc (or a rule it lists) must not repeat a
`check` or `test` entry, and the agent-file check reports each duplicate. Run
`python3 docs/checks/check_agent_files.py --root .` after the edit.
