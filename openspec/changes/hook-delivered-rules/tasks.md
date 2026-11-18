## 1. Spike: prove the host behaviours (ship gate, first)

- [x] 1.1 Create a disposable spike repository with its own git directory: `git clone --no-hardlinks` of this checkout into the session scratchpad, followed by `git remote remove origin`, so it has no writable remote and its hook state never touches the live git common directory. In it, put stub files **at the real tracked paths**, so the spike exercises the wiring that ships:
  - a stub engine at `scripts/agent_rules.py`, with JSON-file state, deny-once, pending, context, re-arm, `void`, and a test-only switch that fails after the write;
  - in the plugin, a test-only switch that makes the host injection fail after a successful engine response;
  - hooks in `.claude/settings.json`;
  - the local V2 plugin in `scripts/opencode-hooked-rules/`, with its entry in `opencode.json`;
  - a stub hooked rule;
  - a stub pointer block in its root `AGENTS.md`.

  The stub logs raw hook inputs to the scratchpad only, never inside a checkout.
- [x] 1.2 Make the spike non-publishing:
  - commits land only in the disposable repository;
  - `gh` resolves to a PATH shim inside the scratchpad that records its arguments and exits 0, so `gh pr create` and `gh pr edit` never reach GitHub;
  - the workflow moment runs only the trigger command `openspec instructions finalize` against a dummy change. The agent is told never to carry out the returned workflow, which includes pushes.
- [x] 1.3 Claude Code: resolve design Open item 1. Record:
  - the `Skill` hook input field that names the skill;
  - whether a user `/<skill>` reaches `UserPromptSubmit`;
  - whether `PreCompact` carries `agent_id`;
  - how a nonzero exit shows in `PostToolUse`.
- [x] 1.4 OpenCode 2.x: resolve design Open item 2. Record:
  - the local plugin entry syntax next to `opencode-rules`;
  - whether `execute.before` can stop a call with text the model sees;
  - the `execute.after` completion and exit fields;
  - whether the output append persists in the transcript.
- [x] 1.5 In fresh sessions of Claude Code, OpenCode and Antigravity, observe that the full stub rule is **not** in context before any trigger, and that the pointer block is. Antigravity is pointer-only.
- [x] 1.6 Run all outcomes of ship-gate behaviours 1–6 (the `hooked-rule-delivery` "End-to-end verification and ship condition" requirement) in both Claude Code and OpenCode, including the two parallel-batch residual cases. Record **sanitized** outcomes (no raw inputs, paths or prompts) with tool versions in `openspec/changes/hook-delivered-rules/spike.md`.
- [x] 1.7 If any outcome cannot be made to work in a tool, stop and ask the owner (pointer-only for that tool, or hold the change) before any further task. In either case, delete the disposable repository and the scratchpad shim and logs, and confirm that `git status` of the main checkout shows only `spike.md`.

## 2. Engine: rule parsing and pointers (tests first)

- [x] 2.1 Write failing tests in `scripts/tests/test_agent_rules.py` for the hooked-rule parser, then create `scripts/agent_rules.py` (stdlib, Python 3.8+, `-I`) to pass them. The tests cover:
  - the restricted grammar and the key set;
  - a namespaced skill;
  - duplicate key, unknown key, `[]`, an invalid regex and no trigger list;
  - the 4,096-byte body passing and 4,097 failing;
  - rule identity as file name plus sha256.
- [x] 2.2 Write failing tests, then implement `pointers --write` and `pointers --check`. The tests cover:
  - a fresh block, a stale block and a missing block;
  - a block that contains the always-on Conventional Commits format sentence for the commit-types rule.

## 3. Engine: contract, state, decisions (tests first)

- [x] 3.1 Define the engine contract and test it before any behaviour:
  - the input parsing for `--format claude` and `--format plain`;
  - the plain response `{decision, text, entries: [{rule, write_id}]}`, emitted only when output is emitted;
  - `pass` emitting nothing;
  - `deny` never emitting `allow`;
  - checkout resolution from `cwd`, with a silent pass outside a checkout.
- [x] 3.2 Implement the state store with failing tests first:
  - per-session files under the git common directory;
  - per-session locks (`fcntl` / `msvcrt`) and the index lock;
  - atomic writes;
  - per-key generations;
  - never-reused write ids;
  - one transaction wrapper in which the decision, the generation read and the update happen under the lock before any output.

  Tests cover lock contention (with the lock held in-process) and differing rule identities across worktrees.
- [x] 3.3 Implement pruning: 50 sessions, ordered by the index counter, never by the clock. It skips a busy victim and never prunes the current session. Tests cover both cases, and a pruned session returning at generation 0.
- [x] 3.4 Implement the moments, with failing tests first:
  - skill tool calls;
  - a leading `/<skill>` prompt;
  - `SKILL.md` reads matched by real path;
  - successful command moments matched by whole-string `re.search`, including a backgrounded launch.

  Tests cover a nonzero exit as no moment, and a successful workflow command followed by an unblocked `git commit`.
- [x] 3.5 Implement the gates and the pending set, with failing tests first:
  - block-once and the re-run;
  - `deny A → deny B → retry A`;
  - overlapping patterns recorded together;
  - a different-pattern sibling denied again.

  Also cover the table-driven matched forms of the spec, and the `"work;tree"` boundary as unmatched.
- [x] 3.6 Implement combining, with failing tests first:
  - filename order and the 9,000-byte cap with whole-rule inclusion;
  - cap at a moment (only included rules credited);
  - cap at a denial with three rules (the omitted rule is not pending, and the retry is denied again).
- [x] 3.7 Implement `compacted` and `void`, with failing tests first.
  - `compacted` re-arms per key, so a child reset leaves the parent intact, with the whole-session fallback when no agent id is given.
  - `void` removes only an exact write-id match, and is a no-op after a sibling rewrite, after pruning, or for another key.
- [x] 3.8 Implement fail-open, with failing tests first. These cases pass with the read-by-hand notice:
  - a malformed event;
  - an unreadable rule;
  - a corrupt state file;
  - a lock timeout.

  Also cover the test-only switch that fails after the write.

## 4. Adapters

Each step below that changes a file the agent-file check inspects lands together with its 6.1 invariant and tests, so the check stays green after every step.

- [x] 4.1 Land in the same step as the hook-wiring invariant of 6.1 and its tests, and no earlier than 6.3 (CODEOWNERS), which must land first or in the same step so the engine and plugin are owner-covered from the first commit that activates them. Add the four hooks to `.claude/settings.json`: `PreToolUse` `Bash`, `PostToolUse` `Bash|Skill|Read`, `UserPromptSubmit` and `PreCompact`. Use the exact engine command and `timeout: 5`, with field handling as recorded in 1.3.
- [x] 4.2 Create the V2 plugin in `scripts/opencode-hooked-rules/`. It maps:
  - `execute.before` to a deny;
  - `execute.after` to an output append;
  - `prompt` to context;
  - `session.compaction.started` to `compacted`.

  The plugin treats only an engine response whose decision is `deny` as a denial. A spawn failure, a 5-second timeout, a nonzero exit, undecodable output and a failed host injection all pass the call with the notice, never a denial. After a failed injection it sends one `void` per response entry.
- [x] 4.3 Also no earlier than 6.3. Add the local plugin entry to `opencode.json` next to `opencode-rules`, with the syntax recorded in 1.4. Land it in the same step as the two-entry allow-list invariant of 6.1 and its tests.

## 5. The pilot rule and guidance edits

- [ ] 5.1 Create `.agents/hooked-rules/repo-commit-types.md`. It holds the format rule, the 11-type guide, the breaking-change and purpose rules, and the pilot skills, commands and gates. Add a test that compares the guide's type lines with the `TYPES` list of `scripts/commitlint.py` (one line per type).
- [ ] 5.2 Land in the same step as the moved-rule, pointer-freshness and hooked-rule-format invariants of 6.1 and their tests. Remove `.agents/rules/repo-conventional-commits.md`. Update the root `AGENTS.md`: drop the entry from the `## Rules` list, leaving eight `repo-*` rules, and generate the pointer block with `pointers --write`.
- [ ] 5.3 Update `scripts/AGENTS.md`: guidance-only changes are typed `docs`.
- [ ] 5.4 Add the "Hook-delivered rules" section to `docs/AGENTS.md`. It covers:
  - adding a rule;
  - delivery, gates, pending entries and re-arm;
  - the supported configuration;
  - the degraded modes;
  - the residuals;
  - the verified tools and versions.

  Also extend the untrusted-checkout warning to the hooks. Replace the reference to the old rule path in `docs/AGENTS.md` and in the root `AGENTS.md`.
- [ ] 5.5 Do not rewrite any other file that names the old path. These are left alone:
  - `openspec/specs/**`, which is synced from the deltas at closeout;
  - this change's artifacts and `reviews/`, which are approval evidence;
  - archived changes, which are history.

  The checker and test consumers are updated in 6.1 and 6.2.

## 6. Checks, routing and ownership (each with its config change, green together)

- [ ] 6.1 Extend `docs/checks/check_agent_files.py` and `docs/checks/test_check_agent_files.py` together. Each invariant is landed with the configuration change it checks (4.1, 4.3, 5.2); this task owns the invariants and their tests. The check imports the parser from `scripts/agent_rules.py`. Each invariant gets a positive and a negative test:
  - the hooked-rule format, including the 4,096/4,097-byte boundary;
  - pointer-block freshness;
  - each Claude Code hook individually: missing, wrong matcher, wrong command, missing timeout;
  - the two-entry `opencode.json` allow-list, with a third plugin rejected;
  - the moved rule absent from `.agents/rules/` and from the `repo-*` list;
  - the legacy size and frontmatter checks scoped to `.agents/rules/`.
- [ ] 6.2 Add the `global-overrides` entries in `.github/components.json` and the `scripts/tests/test_components.py` cases in the same step. `global-overrides` matches exact file names only, so there is one entry per tracked file under `scripts/opencode-hooked-rules/`, plus one for `scripts/agent_rules.py`. The cases:
  - an engine-only change selects exactly `tooling` and `docs`;
  - a test iterates over every tracked file under `scripts/opencode-hooked-rules/` and asserts that each, changed alone, selects exactly `tooling` and `docs`;
  - the guidance-edit case uses `.agents/hooked-rules/repo-commit-types.md` and selects only `docs`.
- [x] 6.3 Add `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/` to `.github/CODEOWNERS`, with the existing agent-guidance owner, in the same step as the CODEOWNERS invariant and its tests in `check_agent_files.py`.

## 7. Verification

- [ ] 7.1 Commit the work as a candidate, then run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` against that committed diff. For every node it selects, run the checks listed in that node's `AGENTS.md`. For this change that is at least:
  - `docs`: the agent-file check and its tests, and the Gate B matrix validator;
  - `tooling`: `./gradlew scriptsTest`, `node --test scripts/check-mac-comparisons.test.mjs`, the offline `python3 -I scripts/project_flow.py check` and `python3 -I scripts/components.py check`.
- [ ] 7.2 Repeat the spike setup of 1.1–1.2, without the stubs, against the finished implementation in a disposable clone of the candidate commit. The plugin's injection-failure switch is still used for behaviour 6. Re-run 1.5 and all outcomes of ship-gate behaviours 1–6 in Claude Code and OpenCode, then clean up as in 1.7.

  Record each outcome, tool and version for both runs in `spike.md`, the completion report and the PR body. A failed outcome stops the work for the owner's decision, as in 1.7.
- [ ] 7.3 Commit the sanitized `spike.md` evidence. Then re-run the selector and every selected node's checks of 7.1 on the actual final diff (`git merge-base origin/master HEAD` to `HEAD`), so the evidence commit is checked too.
