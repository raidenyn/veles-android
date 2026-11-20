# Apply Receipt

> Generated at the end of the apply phase to mark code-implementation
> complete and provide verify with the state it needs.
> Overwritten on each apply iteration; iteration counter grows.

**Change**: `decouple-docs-ci`
**Iteration**: `1`
**Applied at**: `2026-11-20 01:57`
**Executor**: `subagent-driven-development`

---

## Workspace

- **Worktree**: `/home/dev/.t3/worktrees/veles-android/t3-f402d01c-996b02dde3595f53`
- **Branch**: `chore/decouple-supply-installer-ci`

---

## Commits

- **Range**: `165b2e0f..HEAD` at this receipt's last update
- **Count**: 10

---

## Tasks

- **Completed**: `16 of 18` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: `6.1`, `6.2` (pre-push checks and the post-push CI confirmation)

---

## Selection evidence

Three one-line commits on a scratch branch (deleted after the run), each on top of the last,
run with `python3 -I scripts/components.py changed --base C^ --head C`:

| Scratch commit | File touched | Nodes selected |
|---|---|---|
| `fcf5369b` | `docs/checks/check_site.py` | `docs` |
| `cf2a6c33` | `.github/workflows/check-docs.yml` | `docs`, `tooling` |
| `ed89b415` | `verify/test/workflow-contracts.test.mjs` | `tooling` |

All other nodes were `false` in every run.

Historical commits re-run against the new map:

| Commit | Change | Nodes selected |
|---|---|---|
| `f37cc4bc` | #174 | `docs`, `native-host` (the host `AGENTS.md` coupling is out of scope) |
| `165b2e0f` | #175 | `docs`, `tooling` |

---

## Next step

Run the Task 6 pre-push checks, then `openspec-verify-change`.
