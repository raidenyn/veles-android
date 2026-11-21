## Design Summary

Source: the three open items of the follow-up issue to the affected-node verifier change (push-gate
enforcement, OpenCode parity, the Android lint rule). The issue is on no enabled project, so the work is
untracked; the pull request carries a non-closing reference.

The owner's intent: `python3 -I scripts/verify.py` is the single deterministic gate, so agent context stops
repeating what it runs. Duplicating check and test commands in every node `AGENTS.md` (and in
`.agents/rules/android-lint-before-push.md`) defeats the point of the hook and the automatic verification.
OpenCode is where most coding sessions happen, so every part of the solution is OpenCode-first: shared code in
the engine and the verifier, with thin host adapters, never a Claude-only mechanism with an OpenCode note.

Three parts, each agreed in the session:

1. **Guidance holds only what the verifier does not run.** Node `AGENTS.md` files drop every command that is a
   `check` or `test` entry of `scripts/verify-commands.json`; they keep build, release, device, platform and
   targeted one-off lines (for example `--tests "<class>"`). The table loses its `doc` field and the
   two-direction drift check is replaced by one direction: a node doc (or a rule it lists) must not document a
   command that is a `check` or `test` entry. A doc line is forbidden when its argv and working directory equal
   a `check`/`test` entry's or, for a task-only `./gradlew` line, when every task it names is a task of
   `check`/`test` entries with the same working directory (the existing task-subset matcher). A line with any
   extra argument, such as `--tests "<class>"`, is a targeted line and stays allowed. Producer commands
   (`./gradlew rustWasm` and the others) stay documented as setup an agent may need, but lose `doc` too. The
   `excluded` list exists only to cover doc lines for the reverse check, so it goes with that check.
   `android-lint-before-push.md` is deleted with every pointer to it (`android/AGENTS.md`, `rust/AGENTS.md`,
   `protocol/AGENTS.md`).
   The pre-push rule loses its "run the node checks by hand" fallback: where `verify.py` cannot run it already
   prints `NOT RUN` with the fix.
2. **The push gate requires proof, enforced by the agent hooks.** A successful `verify.py --branch` writes a
   branch-pass record bound to the commit it checked. The shared hooked-rules engine
   (`scripts/agent_rules.py`, used by Claude Code's `PreToolUse` and by the OpenCode plugin) gains a
   proof-requiring gate: a hooked rule declares which proof its gates require, and a matching command is denied
   until that proof holds for the current commit. Only `git push` is gated now; the mechanism is generic so a
   later moment (for example `gh pr create`) is one more pattern on a rule.
3. **OpenCode runs the Stop-time task gate.** `verify.py` gains `--hook opencode`; the OpenCode plugin runs it
   at a root session's turn end after the rules engine, with its own long budget, and on a block resumes the
   session with the summary, even when the user has sent a new message meanwhile. Admission (blocks and time
   per session) is the same shared state Claude Code uses. Testability: the plugin's injectable seams (the
   `engine(...)` argument, `spawn`, timers) cover the handler without an OpenCode turn; one real OpenCode turn
   is the acceptance. Whether OpenCode accepts a resuming synthetic prompt while the session is already busy
   with a new user request is **not yet observed**; the design starts with a spike that records the real
   behaviour.

## Alternatives Considered

### Part 1 — what node guidance keeps

#### Alternative A: Only what the verifier does not run (chosen)
- **Approach**: drop `check`/`test` lines from node docs; keep build, release, device, platform and targeted lines.
- **Pros**: removes the duplication; agents keep the one-off commands they need for targeted work.
- **Cons**: the drift check must be rewritten; the `doc` field and its spec text go.
- **Why chosen**: the smallest context that still serves targeted work.

#### Alternative B: No commands at all
- **Approach**: every node points at `verify.py --list`.
- **Pros**: smallest context.
- **Cons**: agents lose the build and targeted-test lines the verifier does not run.
- **Why not chosen**: it removes useful guidance that is not duplication.

#### Alternative C: Keep the lists, drop only the lint rule
- **Approach**: retarget the six Android `doc` fields to `android/AGENTS.md` and delete the rule.
- **Pros**: smallest change.
- **Cons**: the duplication the owner objects to stays.
- **Why not chosen**: it does not address the intent.

### Part 2 — what enforces the push

#### Alternative A: A `pre-push` git hook that runs `--branch`
- **Pros**: tool-neutral, covers humans.
- **Cons**: about 14 minutes blocking each push; an agent's shell timeout kills the run mid-way.
- **Why not chosen**: the cost lands on every push and fails inside agent shells.

#### Alternative B: A `pre-push` git hook that checks a proof record
- **Pros**: tool-neutral, milliseconds.
- **Cons**: git hooks only see a push; not reusable for other agent moments.
- **Why not chosen**: the owner wants the gate to scale to other agent moments, which a git hook cannot see.

#### Alternative C: The proof check in the agent hooks (chosen)
- **Pros**: one engine implementation shared by Claude Code and OpenCode; any command pattern can require a
  proof, so it scales beyond the push.
- **Cons**: a push from a plain terminal is not gated (accepted: the target is agents).

### Part 3 — OpenCode user input during a detached Stop run

- **Cancel on new input**: the newest message wins; rejected by the owner.
- **Finish and show a notice only**: no resume; rejected by the owner.
- **Finish and resume with the block (chosen)**: the report always reaches the agent, even mid-request.

## Agreed Approach

Part 1 Alternative A, Part 2 Alternative C gating only `git push`, Part 3 "finish and resume". All three keep
the logic in the shared engine and verifier and give Claude Code and OpenCode the same behaviour through thin
adapters.

## Key Decisions

- Node docs keep only commands that are not `check`/`test` table entries (owner).
- The proof gate is implemented in the hooked-rules engine and gates only `git push` for now (owner).
- The gate is generic: a rule names a proof predicate from a fixed registry, the same way `stops` names stop
  predicates (my call, to make "scalable" concrete).
- A branch pass is a **complete** `verify.py --branch` run: exit 0, no `--deadline`, no `--base` (the default
  merge base with `origin/master`), no step `not_reached`, and a clean working tree with the same HEAD at both
  the start and the end of the run (staged, unstaged and untracked paths all count). Only such a run writes
  the record; it holds the HEAD commit and the base. The gate recomputes both in the command's working
  directory (HEAD and `git merge-base origin/master HEAD`) and passes only when both equal the record (my call,
  so a deadline-cut or `--base HEAD` run proves nothing).
- Gate scope: every command the existing push pattern matches, checked against HEAD of the event's working
  directory; ref-deletion pushes (`--delete`, `-d`, a `:<ref>` refspec) pass without a proof. A push of a ref
  other than HEAD, or one run with `-C <dir>`, is still checked against the event directory's HEAD; an
  accepted limit, stated in the docs (my call).
- No override flag for the push gate; if `verify.py` cannot run, its `NOT RUN` fix text is the path (my call).
- The denial text tells the agent to run the branch gate in the background and wait for it, because a
  foreground run exceeds Claude Code's 10-minute and OpenCode's default shell-tool limits (my call, from the
  14-minute measured branch run).
- OpenCode resumes with the block even after new user input (owner).
- The "run verify" pointer lives once in the root `AGENTS.md`; node files do not repeat it (my call).
- `excluded` is removed with the reverse check: every entry only covers a doc line for that check; the CI-only
  inventory already lives in `docs/verify.md` (my call).
- Specs to modify: `affected-node-verification` (the `doc` field and "The table cannot drift from the docs"),
  `agent-guidance-layout` (the lint rule as the initial node rule, the rust/protocol pointers, the pre-push
  fallback text), `hooked-rule-delivery` (proof-requiring gates, "Pre-push rule triggers"),
  `verification-stop-hook` (the OpenCode host).
- Accepted costs: without the reverse check a stale build or release line in a doc goes unchecked; a machine
  where `--branch` reports an unexpected `NOT RUN` (for example no NDK) cannot push until it is provisioned; a
  Stop-hook run during a background branch run reports contention and checks nothing (my call, following the
  owner's direction).
- Untracked: no `tracker.md`, no claim; the PR body says `Refs` (intake check).

## Open Questions

- None blocking. The exact frontmatter syntax of the proof requirement, the record's path and the OpenCode
  budget constants are left to the design.
