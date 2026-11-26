## MODIFIED Requirements

### Requirement: The hook runs the active checkout's verifier
`python3 -I scripts/verify.py --hook claude` and `--hook opencode` SHALL act only as a launcher and watchdog. Neither
host wires them: turn-end verification runs from the agent harness verify gate, whose command
`python3 -I scripts/verify_gate.py` runs the task gate at a ticked task and at every turn end, reading remembered passes
under a deadline when the tree has uncommitted changes, and on a clean tree passing on an existing branch pass for HEAD
or else running as the plain manual run; it waits for a held run lock instead of failing, and lints the one edited
file after an edit. The `--hook` mode stays until the hooked-rules engine moves to the hook runtime. It MUST read the event from stdin, resolve the checkout of the event's
`cwd` with `agent_rules.discover_checkout` semantics and `realpath`, and treat it as a checkout of this repository
only when its git common directory equals the launcher's. It MUST then run the task gate in a child process started
from that checkout's own `scripts/verify.py` with the same `--hook` host, so the code, component map, table, state and logs are always the
active checkout's. A separate clone, another repository, a non-checkout `cwd` or a checkout without
`scripts/verify.py` MUST let the stop through silently. A missing launcher script MUST NOT block, which the
`|| true` ensures.

#### Scenario: Session moved into a linked worktree
- **WHEN** the session started in the main checkout and its `cwd` is now a linked worktree at a different revision
- **THEN** the worktree's `scripts/verify.py` runs the task gate against the worktree's changes and table

#### Scenario: Worktree without the verifier
- **WHEN** the active linked worktree is on a branch from before this change
- **THEN** the hook prints nothing and exits 0

#### Scenario: Host passed to the child
- **WHEN** the launcher runs with `--hook opencode`
- **THEN** the dispatched child also runs with `--hook opencode`, and the hook state keys the session as `opencode:<session_id>`

## REMOVED Requirements

### Requirement: OpenCode turn-end verification
**Reason**: The agent harness verify gate verifies OpenCode turn ends through the hook runtime's own plugin, so the
hooked-rules plugin no longer starts `verify.py --hook opencode`.
**Migration**: `./.agents/harness/hook-runtime/opencode` in `opencode.json` and `verify-gate` in
`.agents/harness.json`; run `node .agents/harness/verify-gate/cli.mjs run` by hand.
