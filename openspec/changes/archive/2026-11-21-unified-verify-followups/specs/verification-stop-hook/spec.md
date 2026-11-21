## ADDED Requirements

### Requirement: OpenCode turn-end verification
The OpenCode plugin (`scripts/opencode-hooked-rules/`) SHALL, at a root session's `session.execution.succeeded`,
start the verifier hook `python3 -I <plugin checkout>/scripts/verify.py --hook opencode`, with
`{"session_id": <session id>, "cwd": <session directory>}` on stdin and a budget of 1,800,000 ms, in a per-session
slot separate from the engine's `stop` call. The engine's `stop` handling MUST stay as the `hooked-rule-delivery`
Host adapters requirement defines it, at every turn end, whether or not a verifier run is in flight. A verifier
block MUST be sent as its own synthetic prompt with `resume: true`; a verifier `systemMessage` as a synthetic
message with `resume: false`; empty, malformed or oversized output MUST be ignored. While a session's verifier run
is in flight, a later turn end MUST start no second run, and the in-flight run MUST still resume the session when it
finishes, even if the user sent a new message meanwhile. A child session's turn end and an `interrupted` or `failed`
execution MUST start no run. On budget expiry or plugin dispose the plugin MUST send SIGTERM to the launcher and,
after a 5-second grace, SIGKILL; on SIGTERM the launcher MUST stop its dispatched child and exit 0. Before the plugin
change ships, a spike on a real OpenCode session MUST record what a `resume: true` synthetic prompt does while the
session is busy with a new request; if OpenCode rejects it, the plugin MUST keep the block and send it once at the
session's next turn end, and `docs/opencode-rules.md` MUST state the observed behaviour.

#### Scenario: Verifier blocks
- **WHEN** a fake verifier child prints a block and the engine passes
- **THEN** the plugin sends one `resume: true` synthetic prompt holding the verifier text

#### Scenario: Engine and verifier both block
- **WHEN** the engine blocks at a turn end and the verifier later blocks too
- **THEN** the engine's resume is sent within its 5-second budget, and the verifier's is sent separately when its run ends

#### Scenario: Notice
- **WHEN** the verifier prints a `systemMessage`
- **THEN** the plugin sends a `resume: false` synthetic message with that text

#### Scenario: Budget expiry and dispose
- **WHEN** the verifier launcher outlives the budget, or the plugin is disposed while it runs
- **THEN** the plugin sends SIGTERM, then SIGKILL after the grace, and sends no message; a Python test shows the launcher stops its dispatched child on SIGTERM

#### Scenario: Turn end while the verifier runs
- **WHEN** the user sends a new message while the verifier runs, and a second turn end arrives before it finishes
- **THEN** the second turn end sends the engine's `stop` event but starts no verifier run, and the first run still resumes the session with its block

#### Scenario: Tested without an OpenCode turn
- **WHEN** the plugin's unit tests run
- **THEN** they drive these scenarios through the injected engine, spawn and timer seams, with no sleep and no OpenCode process

## MODIFIED Requirements

### Requirement: The hook runs the active checkout's verifier
The Claude Code `Stop` hook command `python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true`
and its OpenCode counterpart `python3 -I <plugin checkout>/scripts/verify.py --hook opencode`, run by the
OpenCode plugin, SHALL act only as a launcher and watchdog. It MUST read the event from stdin, resolve the checkout of the event's
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

### Requirement: Hook output contract
In hook mode, for both hosts, the command SHALL always exit 0 and write only JSON to stdout: nothing for a silent pass,
`{"decision":"block","reason":"<summary>"}` to keep the agent working, and `{"systemMessage":"<text>"}` to let the stop
through with a notice. Its own errors MUST go to stderr and let the stop through. The block reason MUST be the
agent-readable summary of the task gate, starting with a line that says to fix the problems and stop again and
gives the attempt number. It MUST NOT consult `stop_hook_active`. Both hosts MUST read the same stdin event (a JSON object with a non-empty
`session_id` and `cwd`). Hook state MUST key each session as `<host>:<session_id>`, and the per-problem bound, the
per-session backstop and the deadline MUST be the same for both hosts.

#### Scenario: Lint failure blocks
- **WHEN** the agent ends a turn with a lint error in a changed extension file
- **THEN** the hook prints a block whose reason names the failing step, its tail, the re-run command and the log path, and exits 0

#### Scenario: Internal error fails open
- **WHEN** the hook raises an unexpected exception
- **THEN** it writes one line to stderr, prints nothing to stdout and exits 0

### Requirement: Real-session acceptance
Before the change is done, the hook SHALL be exercised in a real Claude Code session on the coding server with both
`Stop` commands wired: a deliberately broken lint blocks with a readable summary and the fixed tree then passes; an
absent tool yields `NOT RUN` with its fix; a session moved into a linked worktree at a different revision runs that
worktree's verifier. The same hook SHALL also be exercised in a real OpenCode session through the plugin: a
deliberately broken lint resumes the session with a readable summary and the fixed tree then ends silently; and a
push without a branch pass is denied in both hosts and passes after a complete `--branch` run. The measured hook times are recorded in the pull request with the statement that no product
behaviour changed.

#### Scenario: Acceptance recorded
- **WHEN** the pull request is opened
- **THEN** its description lists each acceptance run, what it ran on, and the measured times

### Requirement: Remembered passes
After a node passes in the task gate, the verifier SHALL record a fingerprint of everything that decided the result:
the resolved base commit, the planned argv lists and working directories, the node's table entry with its producers and requirements, the
effective environment (an allowlist of build-affecting variables, the machine config, each required tool's resolved
path and version output, `android/local.properties`; every planned reusable command and producer MUST declare
every tool it invokes as a requirement, or declare `"reuse": false`), the content and mode of every path that can select the node,
the declared inputs walked on the filesystem (large ignored trees by device, inode, mode, size and status-change
time; Gradle's effective user home for `./gradlew` commands), and the content of every generated output a planned
command consumed, captured right before it ran. The hook MUST skip a node whose recomputed fingerprint matches,
after re-probing its requirements, and report it as `PASS (remembered)` in `build/verify/last-run.json` and in any
block reason or notice of that stop; a stop where every selected node is remembered is a clean, silent pass. A record MUST be written only when the
source side of the fingerprint is the same before and after the run, and MUST be deleted on the node's `FAIL` or
`NOT RUN`. A node with a `"reuse": false` command or producer among its planned steps, disagreeing generated snapshots or a link inside a generated
output MUST NOT be recorded. Manual runs MUST never read a remembered-pass record; `--branch` MUST never write one (its branch-pass record, capability `affected-node-verification`, is a separate file).

#### Scenario: Unchanged node is skipped
- **WHEN** the extension passed at the last stop and nothing that decided it changed
- **THEN** the next stop runs none of its commands, records `web-extension: PASS (remembered)` in `build/verify/last-run.json`, and prints nothing to stdout

#### Scenario: Base changed
- **WHEN** the extension passed, then the base commit changes while the selected paths, plan and inputs stay the same
- **THEN** the next stop runs the extension's commands again

#### Scenario: Generated package replaced in place
- **WHEN** the extension passed and `web-extension/rust-wasm/pkg/` was then replaced with different bytes
- **THEN** the next stop runs the extension's commands again

#### Scenario: Installed dependency edited
- **WHEN** a file under `web-extension/node_modules/` was edited after the last pass
- **THEN** the next stop runs the extension's commands again

#### Scenario: Edit during the run
- **WHEN** a selecting file of a node changes while that node's commands run and they pass
- **THEN** no record is written for the node

### Requirement: Documented known limits
`docs/verify.md` SHALL list the hook's accepted known limits, each with its workaround (run `--branch`, or delete
`build/verify/` and the generated output): a Gradle home redirected through a Java option is not followed; the
per-session cap can be exceeded by up to 150 seconds per run; a deadline that expires outside a running command
reports the affected work as `no hit` or `not reached`; a producer whose build tool does not track an input can
leave a stale output; separate clones are not verified; a session pruned after 50 newer sessions starts with a
fresh allowance; a launcher killed with SIGKILL leaves its dispatched child running to its deadline and holding the
run lock, and steps' own process groups outlive `_stop_child`, until the next run's orphan cleanup; and a turn-end
run during a background `--branch` run reports contention and checks nothing (workaround: stop again after the
branch run finishes).

#### Scenario: Limits listed
- **WHEN** `docs/verify.md` is read
- **THEN** it lists each of these limits with its workaround
