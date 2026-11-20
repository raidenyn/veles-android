---
trigger: glob
globs: ["scripts/verify-commands.json"]
paths: ["scripts/verify-commands.json"]
---

Editing `scripts/verify-commands.json`? Read `docs/verify.md` first: it explains every field, the
related-test strategies, requirements and their fix text, generated outputs, and the check that
keeps this table and the node docs in step. Run `python3 docs/checks/check_agent_files.py --root .`
after the edit.
