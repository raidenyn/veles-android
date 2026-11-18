# Hook-Delivered Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the commit/PR type guide to Claude Code and OpenCode agents only when it is needed, and again after a
context compaction. A shared hook engine does this, with an always-on pointer as the fallback.

**Architecture:**

- One stdlib Python engine, `scripts/agent_rules.py`, parses `.agents/hooked-rules/*.md`, decides each hook event
  (pass, context or deny) and records delivery state in the git common directory under a lock.
- Claude Code calls it directly from `.claude/settings.json` hooks.
- A small OpenCode V2 plugin, `scripts/opencode-hooked-rules/`, spawns it.
- The agent-file check imports the engine's parser and guards the wiring.

**Tech Stack:** Python 3.8+ stdlib (`-I`), `unittest`; Node (OpenCode V2 plugin, `node --test`); JSON config.

**Spec:** `openspec/changes/hook-delivered-rules/` — `design.md`, `specs/hooked-rule-delivery/spec.md`,
`specs/agent-guidance-layout/spec.md`, `specs/commit-conventions/spec.md`, `specs/ci-pipeline/spec.md`; task
list `tasks.md`.

## Global Constraints

- **Runtime:** Python 3.8 compatible, stdlib only, invoked as `python3 -I`. No third-party packages.
- **No clock in tests.** Never sleep or read the wall clock in tests (`.agents/rules/repo-no-time-based-tests.md`).
  Inject `sleep` and state directories.
- **Permissions:** never emit `permissionDecision: "allow"`. A pass prints nothing and exits 0.
- **Fail open:** an internal error never blocks a tool call.
- **Limits:**
  - hook timeout: 5 seconds;
  - joined context cap: 9,000 bytes;
  - hooked-rule body cap: 4,096 bytes;
  - session files kept: 50.
- **State location:** `$(git rev-parse --git-common-dir)/agent-rules/`. State never goes in tracked paths.
- **Pointer block markers:** `<!-- hooked-rules:begin -->` / `<!-- hooked-rules:end -->` under
  `## Hook-delivered rules` in the root `AGENTS.md`.
- **Fixed texts:**
  - fallback sentence: *"If your tool did not deliver a rule below, read its file yourself at the moment it names."*
  - failure notice: *"Rule hooks failed: read the hooked rules listed in AGENTS.md by hand at the moments they
    name."*
- **Test-only switches** (environment variables):
  - `VELES_AGENT_RULES_FAIL_AFTER_WRITE=1` makes the engine fail after its write;
  - `VELES_AGENT_RULES_FAIL_INJECTION=1` makes the plugin's injection fail.
- **Commit subjects** are Conventional Commits; the pre-push node checks apply (Task 10).
- **Python 3.8 syntax:** no `X | Y` unions and no `list[...]` in runtime annotations. Use `typing` or comments.
- **Exit codes:** the `hook` subcommand exits 0 except under the test-only post-write failure switch, where it exits
  1 with no stdout after committing state. It **never exits 2**, because Claude Code treats exit 2 as blocking.
  `pointers --check` keeps its own exit 1 for a stale or missing block.
- **Running Python tests:** always `python3 -I -m unittest discover -s <dir> -p '<file>' -v`, matching
  `scriptsTest`. A dotted module path does not import under `-I`.
- **Every commit is green:** each task that adds a checker invariant also updates the shared clean fixture
  (`clean_files` and `make_repo` in `docs/checks/test_check_agent_files.py`) so that unrelated clean and
  single-finding tests keep passing.

## Review Focus

1. **A hooked-rules checkout without `.git`** (a source tarball, or `git rev-parse` failing): the engine must pass
   silently, not error on every shell call. Test in Task 3.
2. **A huge or binary `tool_response`** in a `PostToolUse` event: parsing must not crash or exceed the timeout. The
   engine reads only the fields it needs. Test in Task 3.
3. **A non-UTF-8 command string or rule file:** fail open with the notice, never a traceback. Test in Task 5.
4. **Two concurrent sessions in the same checkout:** each keeps its own state, and pruning by one never removes the
   other's live file. Test in Task 4.
5. **A Windows path in `SKILL.md` reads** (`\skills\name\SKILL.md`): the skill moment still matches after
   normalisation. Test in Task 5.

---

### Task 1: Spike — prove host behaviours (ship gate)

Implements tasks 1.1–1.7. This task is throwaway apart from `spike.md`.

**Files:**
- Create (main checkout): `openspec/changes/hook-delivered-rules/spike.md`
- Create (disposable clone in the scratchpad only):
  - a stub `scripts/agent_rules.py`;
  - `.claude/settings.json` hooks;
  - `scripts/opencode-hooked-rules/`;
  - the `opencode.json` entry;
  - a stub `.agents/hooked-rules/repo-spike.md`;
  - a stub pointer block.

- [ ] **Step 0: Record the starting state.** Save `git status --porcelain` of the main checkout to
  `<scratchpad>/status-before`.
- [ ] **Step 1: Create the disposable repository.**

  ```bash
  git clone --no-hardlinks . <scratchpad>/spike
  git -C <scratchpad>/spike remote remove origin
  ```

  Expected: `git -C <scratchpad>/spike remote` prints nothing.
- [ ] **Step 2: Install the stubs at the real tracked paths in the clone.** These are the minimum behaviours of
  Tasks 3–7: deny-once with pending, context, per-key re-arm, `void` by write id, both failure switches, and raw-input
  logging to `<scratchpad>/spike-logs/`.
- [ ] **Step 3: Add the `gh` shim.** Put `<scratchpad>/bin/gh` first on PATH. It appends its argv to
  `<scratchpad>/spike-logs/gh.log` and exits 0. Expected: `which gh` names the shim.
- [ ] **Step 4: Record the Claude Code hook fields** in `spike.md`:
  - the `Skill` input field that names the skill;
  - whether `/<skill>` reaches `UserPromptSubmit` as `prompt`;
  - the `PreCompact` `agent_id`;
  - the `PostToolUse` exit-code field.
- [ ] **Step 5: Record the OpenCode 2.x facts** in `spike.md`:
  - the local plugin entry syntax in `opencode.json`;
  - whether `execute.before` can stop a call with text the model sees (and how: a throw or a field);
  - the `execute.after` completion and exit fields;
  - whether the output append persists.
- [ ] **Step 6: Observe a fresh session in Claude Code, OpenCode and Antigravity.** The stub rule body is absent from
  context and the pointer block is present.
- [ ] **Step 7: Run every outcome of ship-gate behaviours 1–6** in Claude Code and in OpenCode (see the
  `hooked-rule-delivery` spec requirement "End-to-end verification and ship condition"). Include the two parallel-batch
  residual cases. For the workflow moment, first create a real dummy change in the clone with
  `openspec new change spike-dummy`, then run only `openspec instructions finalize --change spike-dummy`. Tell the
  test agent explicitly not to carry out the returned workflow. Record
  sanitized outcomes and versions in `spike.md`, with no raw inputs, prompts or paths.
- [ ] **Step 8: Gate.** If any outcome fails in a tool, stop and ask the owner. Otherwise continue.
- [ ] **Step 9: Clean up on pass and on stop.** Remove `<scratchpad>/spike`, `<scratchpad>/bin` and
  `<scratchpad>/spike-logs`. Expected: `git status --porcelain` in the main checkout equals `status-before`, plus
  `spike.md`.
- [ ] **Step 10: Commit.**

  ```bash
  git add openspec/changes/hook-delivered-rules/spike.md
  git commit -m "docs(agents): record the hooked-rules host spike"
  ```

---

### Task 2: Engine — rule parser and pointer block

Implements tasks 2.1–2.2.

**Files:**
- Create: `scripts/agent_rules.py`
- Test: `scripts/tests/test_agent_rules.py` (same import pattern as `scripts/tests/test_commitlint.py`)

**Interfaces:**
- Produces:
  - `RuleError(Exception)`;
  - `Rule` namedtuple `(name, identity, pointer, skills, commands, gates, body)`, where `identity` is the tuple
    `(name, sha256_hex_of_file)`, `commands` and `gates` are lists of compiled regexes, and `skills` is a list of
    strings;
  - `parse_rule(name: str, text: str) -> Rule`, which raises `RuleError`;
  - `load_rules(root: str) -> tuple[list[Rule], list[tuple[str, str]]]`, returning the rules sorted by name and the
    errors as `(path, message)`;
  - `render_pointer_block(rules) -> str`;
  - CLI `pointers --write|--check [--root DIR]`, where `--check` exits 1 when the block is stale or missing.

- [ ] **Step 1: Write the failing parser tests.** The rule-file tests use this fixture:

  ```text
  pointer: "..."
  skills: ["opsx:apply"]
  commands: ["\\bopenspec\\s+instructions\\s+finalize\\b"]
  gates: ["\\bgit\\b[^;&|\\n]*?\\scommit\\b"]
  ```

  | Test | Assertion |
  |---|---|
  | `test_valid_rule` | the parsed fields equal the inputs, and `identity[1]` is the sha256 of the text |
  | `test_unknown_key` | a `sticky:` key raises `RuleError` and the message names `sticky` |
  | `test_duplicate_key` | a duplicate key raises `RuleError` |
  | `test_empty_flow_list` | `gates: []` raises `RuleError` |
  | `test_bad_regex` | an invalid regex raises `RuleError` |
  | `test_no_trigger` | a rule with no trigger list raises `RuleError` |
  | `test_body_boundary` | a 4,096-byte body parses and a 4,097-byte body raises |
  | `test_missing_pointer` | a rule without `pointer` raises `RuleError` |
  | `test_body_multibyte` | a body of 1,366 three-byte characters (4,098 bytes) raises |
  | `test_identity_raw_bytes` | a CRLF copy of a rule has a different `identity` sha |

- [ ] **Step 2: Run the tests and see them fail.**

  Run: `python3 -I -m unittest discover -s scripts/tests -p 'test_agent_rules.py' -v`

  Expected: FAIL (module missing).
- [ ] **Step 3: Implement `parse_rule` and `load_rules`.**
  - Copy (do not move) the grammar of `docs/checks/check_agent_files.py` `parse_frontmatter`: `key: scalar`, and
    flow or block lists of double-quoted items with only the `\"` and `\\` escapes.
  - Extend it with a double-quoted scalar for `pointer`.
  - The checker keeps its own parser until Task 8 switches it to the engine's and deletes the duplicate.
  - The identity sha256 is over the raw file bytes, before newline normalisation.
  - The body cap counts UTF-8 bytes.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Write the failing pointer tests.**

  | Test | Assertion |
  |---|---|
  | `test_pointer_block_render` | the block lists one `pointer — path` line per rule, in name order, plus the fixed sentence |
  | `test_pointer_check_stale` | `--check` exits 1 after a pointer edit |
  | `test_pointer_check_missing` | `--check` exits 1 when the markers are absent |
  | `test_pointer_write_idempotent` | `--write` twice leaves the same text |
  | `test_commit_types_pointer_keeps_format` | rendering a rule whose `pointer` contains "Conventional Commit `type(scope): description`" keeps that text verbatim |

- [ ] **Step 6: Implement the pointer subcommand, then run the tests.** Expected: PASS.
- [ ] **Step 7: Commit.**

  ```bash
  git add scripts/agent_rules.py scripts/tests/test_agent_rules.py
  git commit -m "chore(agents): add the hooked-rule parser and pointer block"
  ```

---

### Task 3: Engine — event contract and decisions

Implements task 3.1.

**Files:**
- Modify: `scripts/agent_rules.py`
- Test: `scripts/tests/test_agent_rules.py`

**Interfaces:**
- Consumes: `Rule` and `load_rules` (Task 2).
- Produces:
  - `Event` namedtuple `(kind, session, agent, cwd, tool, command, ok, skill, path, prompt, write_ids)`, where
    `kind` is one of `tool-before`, `tool-after`, `prompt`, `compacted` and `void`;
  - `parse_event(fmt: str, payload: dict) -> Event`, with `fmt` either `"claude"` or `"plain"`. The Claude mapping is
    `PreToolUse` → `tool-before`, `PostToolUse` → `tool-after`, `UserPromptSubmit` → `prompt`, `PreCompact` →
    `compacted`, using the fields recorded in `spike.md`;
  - `Decision` namedtuple `(kind, text, entries)`, where `kind` is `pass`, `context` or `deny`, and `entries` is a list
    of `(identity, write_id)`;
  - `render(fmt, event, decision) -> str`, which returns `""` for a pass. For Claude a deny is
    `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":text}}`
    and a context is `{"hookSpecificOutput":{"hookEventName":<name>,"additionalContext":text}}`. For plain it is
    `{"decision","text","entries":[{"rule":[name,sha],"write_id":n}]}`;
  - `checkout_for(cwd) -> Optional[Tuple[str, str]]`, returning `(toplevel, common_dir)`. `common_dir` is made
    absolute against `cwd` when git prints it relative. It returns `None` when git fails or the top level has no
    `.agents/hooked-rules/`;
  - the plain input `{"event": <Event.kind>, "session", "agent"?, "cwd", "tool"?, "command"?, "ok"?, "skill"?,
    "path"?, "prompt"?, "write_ids"?}`. `event` takes exactly the `Event.kind` values, and a `void` request
    carries `write_ids: [int, ...]`.

- [ ] **Step 1: Write the failing tests.**

  | Test | Assertion |
  |---|---|
  | `test_parse_claude_pre_tool_use` | yields `kind == "tool-before"` and the command |
  | `test_parse_plain_void` | carries `write_ids` |
  | `test_render_pass_is_empty` | `render` of a pass returns `""`, in both formats |
  | `test_render_never_allow` | `"allow"` appears in no rendered output, across every kind and format |
  | `test_plain_entries` | the plain render carries the rule identity and write id |
  | `test_outside_checkout_passes` | `checkout_for` of a temp dir with no `.agents/hooked-rules/` is `None` |
  | `test_no_git_passes` | for a dir with `.agents/hooked-rules/` but no `.git`, `checkout_for` returns `None` (Review Focus 1) |
  | `test_large_tool_response` | a `PostToolUse` payload with a 5 MB `tool_response` parses, and the event carries only the needed fields (Review Focus 2) |

- [ ] **Step 2: Run the tests and see them fail.**

  Run: `python3 -I -m unittest discover -s scripts/tests -p 'test_agent_rules.py' -v`

  Expected: the new tests FAIL.
- [ ] **Step 3: Implement `parse_event`, `render` and `checkout_for`.** `checkout_for` uses one
  `git -C cwd rev-parse --show-toplevel --git-common-dir`, and a failure returns `None`.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** with the message `chore(agents): define the hook event contract`.

---

### Task 4: Engine — state store, transactions and pruning

Implements tasks 3.2–3.3.

**Files:**
- Modify: `scripts/agent_rules.py`
- Test: `scripts/tests/test_agent_rules.py`

**Interfaces:**
- Produces:
  - `Store(state_dir: str, sleep=time.sleep, lock_attempts=50)`;
  - `Store.transaction(session: str)`, a context manager that yields `SessionState`, holds the per-session lock,
    writes atomically on exit, and raises `LockTimeout` after `lock_attempts` failed non-blocking tries;
  - `Store.next_write_id() -> int`, which reads the index under the index lock and is never reused;
  - `Store.prune(current_session)`, which keeps 50 by index order, skips busy victims and never prunes the current
    session. It runs only after the session transaction has released its lock. It reads the order under the index
    lock, **releases the index lock**, then tries each victim's lock without waiting. No code path ever holds the
    index lock while acquiring a session lock;
  - `SessionState` with `generation(key) -> int`, `bump(key)`, `bump_all()`, and `entries` mapping
    `(key, rule_identity)` to `{"state": "delivered"|"pending", "patterns": set, "generation": int, "write_id": int}`;
  - key is `session` or `f"{session}/{agent}"`.

- [ ] **Step 1: Write the failing tests.** They use `sleep=lambda s: None`.

  | Test | Assertion |
  |---|---|
  | `test_atomic_roundtrip` | an entry written in one transaction is read in the next |
  | `test_write_ids_monotonic` | ids strictly increase across sessions and after a prune |
  | `test_lock_contention` | a lock held in-process makes `transaction` raise `LockTimeout` after `lock_attempts` |
  | `test_per_key_generation` | `bump("s/a")` leaves `generation("s")` unchanged |
  | `test_prune_keeps_50` | 52 sessions are pruned to 50, by index order |
  | `test_prune_skips_busy` | a held victim survives the prune |
  | `test_prune_never_current` | the current session is never pruned |
  | `test_pruned_session_restarts` | a returning pruned session starts at generation 0 with no entries |
  | `test_two_sessions_isolated` | each of two sessions in one store sees only its entries (Review Focus 4) |
  | `test_rule_identity_differs` | an entry for `(name, shaA)` is not found for `(name, shaB)` |

- [ ] **Step 2: Run the tests and see them fail.**
- [ ] **Step 3: Implement `Store` and `SessionState`.** Lock with `fcntl.flock(LOCK_EX|LOCK_NB)` on POSIX or
  `msvcrt.locking(LK_NBLCK)` on Windows, using one `.lock` file per session plus `index.lock`. Write through
  `tempfile.mkstemp` in the same directory, then `os.replace`.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** with the message `chore(agents): add the locked delivery-state store`.

---

### Task 5: Engine — moments, gates, cap, re-arm, void, fail-open

Implements tasks 3.4–3.8.

**Files:**
- Modify: `scripts/agent_rules.py`
- Test: `scripts/tests/test_agent_rules.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `decide(rules, state: SessionState, key: str, event: Event, next_write_id) -> Decision`, a pure function that
    mutates `state`;
  - `handle(stdin_text: str, fmt: str, env: dict, store_factory) -> Tuple[str, int]`, which runs one transaction.
    Recoverable failures return the failure notice as context with exit 0, wherever the host format has a context
    channel. A Claude event whose JSON cannot be parsed has no `hook_event_name`, and so no channel: it returns
    `("", 0)` and writes the notice to stderr. Only the test-only post-write switch returns `("", 1)`, after the state
    is committed;
  - CLI `hook --format claude|plain`.

- [ ] **Step 1: Write the failing moment tests.**

  | Test | Assertion |
  |---|---|
  | `test_skill_tool_moment` | a skill tool call adds context and marks the rule delivered |
  | `test_slash_prompt_moment` | a prompt starting `/opsx:apply` is a moment; plain prompt text is not |
  | `test_skill_md_read_moment` | a read of `.agents/skills/openspec-apply-change/SKILL.md` matches by real path, and the Windows form `\skills\...\SKILL.md` matches too (Review Focus 5) |
  | `test_command_moment_success` | `openspec instructions finalize --change x` with `ok=True` is a moment, and a following `git commit` passes |
  | `test_command_moment_failure` | the same with `ok=False` records nothing |
  | `test_background_launch` | a backgrounded command counts at launch |

- [ ] **Step 2: Write the failing gate tests.**

  | Test | Assertion |
  |---|---|
  | `test_block_once_then_retry` | the first `git commit -m "feat: x"` is denied, and `git commit -m "docs: x"` passes |
  | `test_accumulated_pending` | `git commit` denied, `gh pr create` denied, then `git commit` passes |
  | `test_sibling_denied` | while pending on `git commit`, `gh pr edit 5 --title x` is denied again |
  | `test_overlap_patterns` | a call matching two gates records both patterns |
  | `test_matched_forms` | table-driven: each form listed in the spec scenario "Matched forms" matches |
  | `test_boundary_semicolon` | `git -C "work;tree" commit` does not match |

- [ ] **Step 3: Write the failing cap, re-arm and `void` tests.**

  | Test | Assertion |
  |---|---|
  | `test_cap_moment` | three 4,000-byte rules: only the two that fit are credited |
  | `test_cap_denial` | three rules at a denial: the omitted rule is not pending, and the retry is denied with the omitted rule's text |
  | `test_filename_order` | the joined text follows rule names in order |
  | `test_rearm_main` | after `compacted` on the main key, the next commit is denied |
  | `test_rearm_child_keeps_parent` | after `compacted` with an agent id, the parent's entry survives |
  | `test_rearm_unidentified_all` | `compacted` without an agent id bumps every key of the session |
  | `test_void_exact` | `void` with a matching write id removes the entry; a stale id, another key or a pruned session is a no-op |

- [ ] **Step 4: Write the failing fail-open tests.**

  | Test | Assertion |
  |---|---|
  | `test_malformed_event_plain` | invalid JSON in plain format returns the notice as a plain `context` response, with exit 0 |
  | `test_malformed_event_claude` | invalid JSON in Claude format gives empty stdout, the notice on stderr and exit 0. Without the event name there is no model-visible channel, which the spec's "where a channel exists" allows; `docs/AGENTS.md` documents it |
  | `test_unreadable_rule` | an undecodable or non-UTF-8 rule file yields the notice and a pass (Review Focus 3) |
  | `test_corrupt_state` | a corrupt session file yields the notice and a pass |
  | `test_lock_timeout_passes` | `LockTimeout` yields the notice and a pass |
  | `test_fail_after_write_switch` | with `VELES_AGENT_RULES_FAIL_AFTER_WRITE=1` the state is committed, stdout is empty and the exit is 1 (never 2) |

- [ ] **Step 5: Run the tests and see them fail.**
- [ ] **Step 6: Implement `decide` and `handle`.**
  - Gates are evaluated in `tool-before`, moments in `tool-after` and `prompt`.
  - The cap is applied before any pending write, and only included rules get entries and write ids.
  - `handle` catches every exception outside the transaction body and returns the notice.
- [ ] **Step 7: Run the tests.** Expected: PASS. Then run the whole suite with `./gradlew scriptsTest`; expected:
  PASS.
- [ ] **Step 8: Commit** with the message `chore(agents): deliver hooked rules at moments and gates`.

---

### Task 6: CODEOWNERS and Claude Code wiring

Implements tasks 6.3 and 4.1, with the matching task 6.1 invariants. Task 8 owns the rest of the checker; this task
adds only the hook-wiring and CODEOWNERS invariants.

**Files:**
- Modify: `.github/CODEOWNERS` (add `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/` with the existing
  owner)
- Modify: `.claude/settings.json`
- Modify: `docs/checks/check_agent_files.py`: add the two paths to `CODEOWNER_PATTERNS` **and**
  `CODEOWNER_SAMPLE_PATHS`, so an ownerless or shadowed entry fails; add a new `check_claude_hooks(root)`
- Test: `docs/checks/test_check_agent_files.py`. In the same commit:
  - add the hooks block to the shared clean fixture (`clean_files`);
  - add the two paths to `EXPECTED_CODEOWNER_PATTERNS`, which the existing CODEOWNERS tests use to rebuild the
    fixture entry, so those tests still isolate their single violation.

**Interfaces:**
- Produces: `check_claude_hooks(root) -> list[Finding]`, called from `check_repo`. It expects exactly these hooks:

  | Hook | Matcher |
  |---|---|
  | `PreToolUse` | `Bash` |
  | `PostToolUse` | `Bash\|Skill\|Read` |
  | `UserPromptSubmit` | none |
  | `PreCompact` | none |

  Each hook has the command `python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude` and
  `timeout: 5`.

- [ ] **Step 1: Write the failing tests:**
  - `test_hooks_clean`;
  - one `test_hook_missing_<name>` per hook;
  - a wrong-matcher, wrong-command and missing-timeout test per hook;
  - `test_hook_wrong_matcher`;
  - `test_hook_wrong_command`;
  - `test_hook_missing_timeout`;
  - `test_codeowners_engine_missing`;
  - `test_codeowners_plugin_missing`.

  Each negative test asserts one finding with `assert_finding`.
- [ ] **Step 2: Run the tests and see them fail.**

  Run: `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`
- [ ] **Step 3: Implement the check, and add the CODEOWNERS lines and the hooks block to `.claude/settings.json`.**
  Keep `enabledPlugins` unchanged.
- [ ] **Step 4: Run the tests and the check.** Run the tests, then `python3 docs/checks/check_agent_files.py --root .`.
  Expected: PASS, with no errors.
- [ ] **Step 5: Commit** with the message `chore(agents): wire the hooked-rule engine into Claude Code`.

---

### Task 7: OpenCode plugin

Implements tasks 4.2–4.3, with the allow-list invariant of task 6.1.

**Files:**
- Create:
  - `scripts/opencode-hooked-rules/index.js`, the V2 `{ id, setup(context) }` plugin;
  - `scripts/opencode-hooked-rules/package.json`, if the spike's entry syntax needs one;
  - `scripts/opencode-hooked-rules/index.test.mjs`.
- Modify:
  - `opencode.json` (the local entry, with the syntax from `spike.md`);
  - `docs/checks/check_agent_files.py` (`check_opencode` accepts exactly `opencode-rules` plus that local entry);
  - `scripts/AGENTS.md` (add `node --test scripts/opencode-hooked-rules/index.test.mjs` to the node checks).
- Test: `docs/checks/test_check_agent_files.py`. In the same commit:
  - add the local plugin entry to the shared clean fixture's `opencode.json`, and to the default of the
    `opencode_text()` helper, so the existing range and options tests keep a valid two-entry config. Explicit
    `plugins=` overrides in negative tests stay as they are;
  - rewrite the existing `test_opencode_second_plugin_fails` so that a third plugin, or a second plugin other than
    the local hooked-rules entry, fails;
  - update every assertion or message that says "exactly one" plugin.

**Interfaces:**
- Consumes: CLI `hook --format plain` (Task 5), with the plain response of Task 3.
- Produces:
  - `runEngine(event, {spawn, timeoutMs = 5000}) -> Promise<{decision, text, entries} | null>`, where `null` means
    an infrastructure failure;
  - the hook handlers wired in `setup(context)`.

- [ ] **Step 1: Write the failing plugin tests** with a fake `context` and a fake `spawn`:

  | Test | Assertion |
  |---|---|
  | `deny stops call` | the `execute.before` handler throws or sets the stop field per `spike.md`, with the engine text |
  | `context appends output` | the engine text is appended to `output.output` |
  | `spawn failure passes` | a missing Python passes and appends the notice |
  | `timeout passes` | a fake spawn that never resolves, advanced through an injected timer, passes with the notice |
  | `nonzero exit passes` | a nonzero engine exit passes |
  | `bad JSON passes` | undecodable engine output passes |
  | `injection failure voids` | with `VELES_AGENT_RULES_FAIL_INJECTION=1`, one `void` per entry is spawned |
  | `compaction maps` | `session.compaction.started` sends `compacted` |
  | `prompt registers` | `setup` registers `context.session.hook('prompt')`; a `/opsx:apply` prompt sends `prompt` and adds the engine text to the prompt turn through the mechanism recorded in `spike.md` |
  | `empty stdout passes` | exit 0 with empty stdout is a normal pass, with no notice, which is distinct from a `null` transport failure |

- [ ] **Step 2: Run the tests and see them fail.**

  Run: `node --test scripts/opencode-hooked-rules/index.test.mjs`
- [ ] **Step 3: Implement the plugin.**
  - Register `execute.before`, `execute.after`, `session.hook('prompt')` and the event stream.
  - Only `decision === "deny"` stops a call.
  - Empty stdout with exit 0 is a pass.
  - A spawn error, a timeout, a nonzero exit or undecodable output means `null`: pass with the notice.
- [ ] **Step 4: Write and run the failing checker tests:**
  - `test_opencode_two_entries_ok`;
  - `test_opencode_third_plugin`;
  - `test_opencode_missing_local_entry`.
- [ ] **Step 5: Add the `opencode.json` entry and update `check_opencode`, then run both suites.** Expected: PASS.
- [ ] **Step 6: Commit** with the message `chore(agents): add the OpenCode hooked-rules plugin`.

---

### Task 8: The pilot rule, layout checks and guidance

Implements tasks 5.1–5.5 and the remaining task 6.1 invariants.

**Files:**
- Create: `.agents/hooked-rules/repo-commit-types.md`, with the text of the `commit-conventions` spec "Commit and PR
  type guidance": the format line, 11 type lines, the breaking and purpose rules, and the frontmatter `skills`,
  `commands` and `gates` of design §7.
- Delete: `.agents/rules/repo-conventional-commits.md`
- Modify:
  - root `AGENTS.md`: drop the rule from `## Rules`, leaving eight, and add the pointer block via `pointers --write`;
  - `docs/AGENTS.md`: the "Hook-delivered rules" section with the six topics of the spec, and the untrusted-checkout
    warning;
  - `scripts/AGENTS.md`: guidance-only changes are typed `docs`.
- Modify: `docs/checks/check_agent_files.py`
  - import the parser via `importlib` from `<root>/scripts/agent_rules.py`, and delete the checker's duplicate parser.
    `check_rules` for `.agents/rules/` keeps its existing entry points and behaviour;
  - replace the old rule path in the checker's own CODEOWNERS sample paths and in its tests;
  - new `check_hooked_rules(root, files)` and `check_pointer_block(root)`;
  - scope `check_rules` size and frontmatter to `.agents/rules/`;
  - the moved-rule rejection;
  - the root roster count of eight.
- Test: `docs/checks/test_check_agent_files.py`, `scripts/tests/test_agent_rules.py`. In the same commit, the
  shared clean fixture gains a copy of `scripts/agent_rules.py`, the hooked rule and the pointer block, and loses the
  old rule.

- [ ] **Step 1: Write the failing tests.**
  - The checker gets one negative test per invariant:
    - `test_hooked_unknown_key`;
    - `test_hooked_body_4097`;
    - `test_hooked_body_4096_ok`;
    - `test_hooked_bad_regex`;
    - `test_pointer_block_stale`;
    - `test_pointer_block_missing`;
    - `test_old_rule_present`;
    - `test_old_rule_listed`;
    - `test_hooked_rule_not_judged_as_repo_rule` (a valid hooked rule raises no `trigger` or size finding);
    - `test_hooked_duplicate_key`;
    - `test_hooked_empty_list`;
    - `test_hooked_no_trigger`;
    - `test_hooked_nested_dir` (a file under `.agents/hooked-rules/x/` fails);
    - `test_hooked_bad_scope_name`.
  - The engine tests get `test_commit_types_covers_commitlint`. The guide's type lines, as a list, equal
    `commitlint.TYPES.split("|")` with the same multiplicity, so each type appears exactly once.
- [ ] **Step 2: Run both suites and see them fail.**
- [ ] **Step 3: Write the rule file, delete the old rule, run `python3 -I scripts/agent_rules.py pointers --write`,
  and edit the three `AGENTS.md` files.**
- [ ] **Step 4: Implement the checker changes.** Then run the checker tests, the engine tests and
  `python3 docs/checks/check_agent_files.py --root .`. Expected: all PASS, no errors.
- [ ] **Step 5: Commit** with the message `docs(agents): deliver the commit-type guide as a hooked rule`.

---

### Task 9: CI routing

Implements task 6.2.

**Files:**
- Modify: `.github/components.json` (`global-overrides`: `scripts/agent_rules.py` and each tracked file under
  `scripts/opencode-hooked-rules/` map to `["tooling", "docs"]`)
- Test: `scripts/tests/test_components.py`

- [ ] **Step 1: Write the failing tests:**
  - `test_engine_only_selects_tooling_docs`, asserting the exact set;
  - `test_each_plugin_file_selects_tooling_docs`, which iterates over `git ls-files scripts/opencode-hooked-rules/`;
  - an update to the guidance-edit case so it uses `.agents/hooked-rules/repo-commit-types.md` and expects only
    `docs`.
- [ ] **Step 2: Run them and see them fail, add the overrides, then run them again.**

  Run: `python3 -I -m unittest discover -s scripts/tests -p 'test_components.py' -v` and `python3 -I scripts/components.py check`

  Expected: PASS.
- [ ] **Step 3: Commit** with the message `ci: route the hooked-rule engine to the tooling and docs nodes`.

---

### Task 10: Verification and evidence

Implements tasks 7.1–7.3.

- [ ] **Step 1: Run the node checks.** Run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`.
  For each selected node, run its `AGENTS.md` checks:
  - **docs:** `python3 docs/checks/check_agent_files.py --root .`, then
    `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`, then
    `python3 docs/checks/check_gate_b_matrix.py`;
  - **tooling:** `./gradlew scriptsTest`, `node --test scripts/check-mac-comparisons.test.mjs`,
    `node --test scripts/opencode-hooked-rules/index.test.mjs`, `python3 -I scripts/project_flow.py check` and
    `python3 -I scripts/components.py check`.

  Expected: all pass.
- [ ] **Step 2: Run the final verification.**
  - Capture a fresh `status-before` baseline (Task 1 Step 0).
  - Repeat Task 1 Steps 1, 3 and 6–9 against a `--no-hardlinks` clone of `HEAD`, without stubs, using the plugin's
    injection-failure switch for behaviour 6.
  - Record both runs (outcome, tool, version) in `spike.md`. The cleanup comparison allows only the modification of
    the now-tracked `spike.md`.
  - On any failure, stop for the owner.
- [ ] **Step 3: Commit the evidence** with the message `docs(agents): record the hooked-rules verification`.
- [ ] **Step 4: Re-run the checks on the final diff.** Re-run Step 1 on the final diff, then run the review-record
  audit (`openspec-verify-change`'s "Review records" check). Expected: all pass, and the audit is PASS.
- [ ] **Step 5: Copy the evidence matrix.** Copy the outcome, tool and version matrix of both runs from `spike.md`
  into the completion report and the PR body.
