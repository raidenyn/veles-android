## MODIFIED Requirements

### Requirement: Pointer block in the root AGENTS.md
The root `AGENTS.md` SHALL carry a generated block between `<!-- hooked-rules:begin -->` and
`<!-- hooked-rules:end -->`, under a `## Hook-delivered rules` heading. It MUST hold one line per hooked rule (its
`pointer`, then its path) and nothing else. Each `pointer` MUST itself say when to read the rule's file (it names
the moments in words), so the block needs no separate explanatory sentence.

`python3 -I scripts/agent_rules.py pointers --write` MUST write the block, and `pointers --check` MUST fail when it
is stale. Each pointer MUST keep its rule's must-never-miss fact always on:

- commit types: the Conventional Commits format rule;
- closeout: after apply, verify and then finalize run without asking, and finalize pushes and creates or readies
  the PR (owner-authorized);
- review gate: every planning file and the closeout docs go through the review gate, the preset is asked first,
  and the workflow stops after the plan;
- roster: models and presets live only in the roster, and no model is substituted;
- real-device testing: a behaviour change needs a real device, emulator or Chrome run before it is called done,
  stated in the PR or report;
- pre-push checks: before a push, run `python3 -I scripts/verify.py` (the task gate), whose complete run the push
  gate requires.

#### Scenario: Stale block
- **WHEN** a hooked rule's `pointer` changes and the block is not regenerated
- **THEN** `pointers --check` and the agent-file check fail

#### Scenario: Pointer without hooks
- **WHEN** the hooks and the OpenCode plugin are disabled and a session starts at the repository root, with no parent `CLAUDE.md` or `CLAUDE.local.md`
- **THEN** the agent's context contains the pointer block, including the closeout pointer's authorization to finalize without asking

#### Scenario: Pre-push pointer names the verify command
- **WHEN** the pointer block is generated from `.agents/hooked-rules/repo-pre-push-checks.md`
- **THEN** its line names `python3 -I scripts/verify.py`, does not name `--branch` as the required command, and keeps the rule's moments, `openspec instructions finalize` and before `git push`

### Requirement: Pre-push rule triggers
`.agents/hooked-rules/repo-pre-push-checks.md` SHALL be delivered at `openspec instructions finalize`, SHALL gate
`git push` with a regex of the same form as the `git commit` gate (`git`, then anything up to a shell operator, then
the word `push`), SHALL declare `requires: ["branch-pass"]`, and SHALL exempt ref-deletion pushes (`--delete`, `-d`,
and a `:<ref>` or `+:<ref>` refspec). Its text MUST tell the agent to commit everything first, to run
`python3 -I scripts/verify.py` (the task gate) in the background and wait for it because a foreground run can exceed
the shell tools' limits, to fix every `FAIL` and unexpected `NOT RUN`, and then to push; it MUST say that
`python3 -I scripts/verify.py --branch` is the optional full local run, which satisfies the gate as well, and that CI
runs every test; it MUST NOT tell the agent to run node checks by hand.

#### Scenario: Push without delivery
- **WHEN** an agent that has not received the rule and has no branch pass for HEAD runs `git push origin feature`
- **THEN** the call is denied with the rule text, and the re-run is denied again until a branch pass exists

#### Scenario: Push without a pass is denied every time
- **WHEN** an agent with no branch pass for HEAD runs `git push origin feature` twice
- **THEN** both calls are denied with the rule text

#### Scenario: Push during finalize
- **WHEN** `openspec instructions finalize` succeeded in the context, a branch pass exists for HEAD, and the agent then runs `git push`
- **THEN** the push is not denied

#### Scenario: Task-gate pass allows the push
- **WHEN** the agent commits everything, a complete `python3 -I scripts/verify.py` run passes, and the agent runs `git push origin feature`
- **THEN** the push is not denied

#### Scenario: Rule names the task gate
- **WHEN** `.agents/hooked-rules/repo-pre-push-checks.md` is read
- **THEN** its pointer and body name `python3 -I scripts/verify.py` as the command to run, mention `--branch` only as the optional full run, and keep the commit-first and background instructions

#### Scenario: Pass made stale by a commit
- **WHEN** the agent commits after the branch pass and runs `git push`
- **THEN** the push is denied naming the pass's commit and HEAD
