# OpenSpec skills

The workflow list used on this machine (`~/.config/openspec/config.json`, profile `custom`) determines the
generated skill set: propose, explore, new, continue, apply, ff, sync, archive, bulk-archive, verify,
update.

## OpenSpec skills

`docs/checks/check_agent_files.py` reads this list as the expected generated set; a differing list in
`.agents/skills/` fails the check.

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
