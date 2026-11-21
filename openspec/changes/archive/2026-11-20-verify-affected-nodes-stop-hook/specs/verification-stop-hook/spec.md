## ADDED Requirements

### Requirement: The hook runs the active checkout's verifier
The Claude Code `Stop` hook command `python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true`
SHALL act only as a launcher and watchdog. It MUST read the event from stdin, resolve the checkout of the event's
`cwd` with `agent_rules.discover_checkout` semantics and `realpath`, and treat it as a checkout of this repository
only when its git common directory equals the launcher's. It MUST then run the task gate in a child process started
from that checkout's own `scripts/verify.py`, so the code, component map, table, state and logs are always the
active checkout's. A separate clone, another repository, a non-checkout `cwd` or a checkout without
`scripts/verify.py` MUST let the stop through silently. A missing launcher script MUST NOT block, which the
`|| true` ensures.

#### Scenario: Session moved into a linked worktree
- **WHEN** the session started in the main checkout and its `cwd` is now a linked worktree at a different revision
- **THEN** the worktree's `scripts/verify.py` runs the task gate against the worktree's changes and table

#### Scenario: Worktree without the verifier
- **WHEN** the active linked worktree is on a branch from before this change
- **THEN** the hook prints nothing and exits 0

### Requirement: Hook output contract
In hook mode the command SHALL always exit 0 and write only JSON to stdout: nothing for a silent pass,
`{"decision":"block","reason":"<summary>"}` to keep the agent working, and `{"systemMessage":"<text>"}` to let the stop
through with a notice. Its own errors MUST go to stderr and let the stop through. The block reason MUST be the
agent-readable summary of the task gate, starting with a line that says to fix the problems and stop again and
gives the attempt number. It MUST NOT consult `stop_hook_active`.

#### Scenario: Lint failure blocks
- **WHEN** the agent ends a turn with a lint error in a changed extension file
- **THEN** the hook prints a block whose reason names the failing step, its tail, the re-run command and the log path, and exits 0

#### Scenario: Internal error fails open
- **WHEN** the hook raises an unexpected exception
- **THEN** it writes one line to stderr, prints nothing to stdout and exits 0

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
output MUST NOT be recorded. Manual runs MUST never read a record; `--branch` MUST never write one.

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

### Requirement: Blocking and the per-problem bound
The hook SHALL block on any `FAIL`, timeout, unexpected `NOT RUN` or selection error, with the fix. A problem's
identity MUST be its node, step id and outcome kind, or the selection error's kind with its paths (or, for a base
error, the base); when several problems block in one run, the first in the report's order is the identity. The same
identity MUST block at most 3 times in a row, after which the hook lets the stop through with a `systemMessage` listing what
is still red. Expected `NOT RUN`s (device, platform, branch-only, none related, nothing planned) MUST NOT block.

#### Scenario: Missing tool blocks with its fix
- **WHEN** the extension's checks need Node 22 and only Node 18 is on `PATH`
- **THEN** the hook blocks with the requirement's fix text

#### Scenario: Fourth repeat passes
- **WHEN** the same step has failed and blocked on three consecutive stops
- **THEN** the fourth stop with that failure is not blocked and shows a notice

### Requirement: Per-session backstop and deadline
The hook SHALL keep per-session state in `<git-common-dir>/veles-verify/hook-state.json`, shared by every worktree,
keyed by `session_id`. Admission MUST happen atomically under a lock: a run reserves its time budget and one block
slot, counting live reservations, so overlapping runs of one session cannot spend the same allowance. A session MUST
get at most 5 blocks and 60 minutes of hook run time since its last clean stop; at either limit, or with less than
one minute left, the hook MUST NOT start a run and MUST let the stop through with the session's last blocking
summary. Each run's budget MUST be the smaller of 1,740 seconds, the time left in the session, and the time left
before the launcher's outer deadline. Settlement of every admitted run that started — whatever its outcome, including no work and an internal error —
MUST remove the reservation and add its measured run time to the time spent, so unused budget returns to the
session; a cancelled reservation (contention) MUST be removed without a charge. Then, by outcome: a blocking run
adds one block and updates the repeat counter; a clean stop — at least one node selected, the run completed,
nothing to block — resets the blocks, the repeat counter and the time spent to zero and clears the stored blocking summary; a stop with nothing changed or
only unowned paths, an internal error and a pass at the repeat bound leave the blocks and the repeat counter as they
are and keep the time just charged. A reservation expires when its start plus its budget plus
120 seconds has passed; admission MUST process expired reservations first, charging each its budget plus 120
seconds, before checking the allowance. Sessions beyond the newest 50 MUST be pruned, except sessions with a live reservation. The
settings timeout MUST be 1,800 seconds; the launcher's outer deadline MUST be its start plus 1,740 seconds, and the
launcher MUST kill its child 30 seconds after the admitted deadline the child reports, or, when none was reported,
30 seconds after the outer deadline (1,770 seconds after its start), before Claude Code's 1,800-second limit.

#### Scenario: Session limit
- **WHEN** a session has been blocked 5 times since its last clean stop
- **THEN** the next stop runs nothing and passes with a notice naming what is still red

#### Scenario: Worktree move keeps the allowance
- **WHEN** a session used 4 blocks and 10 minutes in one worktree, has no live reservation, and moves to another worktree of the same repository
- **THEN** a run there is admitted with one block slot left

#### Scenario: Nothing changed keeps the counters
- **WHEN** a session with 2 blocks, time left and no live reservation stops with no changed path
- **THEN** the stop passes silently, the blocks and the repeat counter are unchanged, and the time spent grows by that run's measured time

### Requirement: Timeouts, process groups and contention
Every command, producer, resolver and version probe SHALL run in its own process group, cut off at the earlier of
its step limit and 45 seconds before the run deadline (the reserve for termination, settlement and the report),
then terminated (`SIGTERM`, a 10-second grace, `SIGKILL`) and reported as `timeout`, with what finished, what
was cut off and that command's tail; the agent is blocked and retries after fixing. Running groups MUST be recorded
in `build/verify/running.json`; the next process to obtain the run lock MUST be the only one to terminate a dead
run's recorded groups, and MUST confirm each is gone before planning, else treat the run as contention. A run lock
held by another live run MUST make the hook pass with "verification already running; nothing was checked", never
a pass of any node.

#### Scenario: Command exceeds the budget
- **WHEN** a test is still running 45 seconds before the run deadline
- **THEN** its process group is terminated, the hook blocks naming the timed-out step, the steps that finished and those not reached

#### Scenario: Orphan from a killed run
- **WHEN** the launcher killed a stuck child whose Gradle client is still running, and the agent stops again with block slots and time left
- **THEN** the new run terminates that recorded group and confirms it is gone before running anything

#### Scenario: Concurrent run
- **WHEN** a manual `verify.py --branch` holds the run lock and the agent stops with block slots and time left
- **THEN** the hook passes with the "already running" notice and records nothing

### Requirement: Documented known limits
`docs/verify.md` SHALL list the hook's accepted known limits, each with its workaround (run `--branch`, or delete
`build/verify/` and the generated output): a Gradle home redirected through a Java option is not followed; the
per-session cap can be exceeded by up to 150 seconds per run; a deadline that expires outside a running command
reports the affected work as `no hit` or `not reached`; a producer whose build tool does not track an input can
leave a stale output; separate clones are not verified; and a session pruned after 50 newer sessions starts with a
fresh allowance.

#### Scenario: Limits listed
- **WHEN** `docs/verify.md` is read
- **THEN** it lists each of these limits with its workaround

### Requirement: Real-session acceptance
Before the change is done, the hook SHALL be exercised in a real Claude Code session on the coding server with both
`Stop` commands wired: a deliberately broken lint blocks with a readable summary and the fixed tree then passes; an
absent tool yields `NOT RUN` with its fix; a session moved into a linked worktree at a different revision runs that
worktree's verifier; and the measured hook times are recorded in the pull request with the statement that no product
behaviour changed.

#### Scenario: Acceptance recorded
- **WHEN** the pull request is opened
- **THEN** its description lists each acceptance run, what it ran on, and the measured times
