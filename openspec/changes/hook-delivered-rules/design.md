## Context

The repository's agent guidance is a set of rule files in `.agents/rules/` (always-on `repo-*` rules and
path-scoped node rules) plus skills in `.agents/skills/`. Each tool reads the rules its own way:

- Claude Code reads them through the `.claude/rules` symlink.
- OpenCode reads them through the `opencode-rules` plugin declared in `opencode.json`.
- Antigravity reads `.agents/rules/` natively.
- Every tool reads the root `AGENTS.md` natively, in the supported configuration of section 8.

Every `repo-*` rule is loaded at session start whether or not it is relevant. Nothing re-delivers a rule once a
long session has been summarized (compacted).

The owner is adding more rules and skills and wants them to be effective:

- **Not loaded before needed:** a rule that matters at one moment (a commit, a PR, a workflow step) should not
  occupy context for the whole session.
- **Not forgotten:** a rule must be in force at the moment it matters, even after a long session whose early
  context has been summarized away.

The pilot is the commit-type rule. `scripts/commitlint.py` enforces the Conventional Commits *format* (types
`feat|fix|perf|refactor|docs|test|build|ci|chore|style|revert`) through three gates: `.githooks/commit-msg`, the
required `pr-title` check, and `commit-guard`.

Nothing guides the *choice* of type, and the choice has a release cost. `feat` bumps the minor version, and
`fix`/`perf` the patch version, of each release component whose paths the commit touches (git-cliff with
`cliff.toml`; see `scripts/AGENTS.md` and `docs/releasing.md`). History shows wrong choices:

- `feat(ci): skip unchanged nodes…`
- `feat(otp-01/1b): Rust JNI/WASM toolchain…`
- `feat: add GitHub Actions CI…`

**Scope (owner's call, after the first design review):** this change builds the mechanism and moves only the
commit-type rule. `repo-superspec-closeout.md`, `repo-tracker-intake.md`, `repo-superspec-review-gate.md` and
`repo-review-models.md` stay always-on and are follow-ups. Those follow-ups add what they need:

- prompt-pattern moments;
- re-delivery after compaction;
- workflow scenario tests.

Tool APIs as known today:

- **Claude Code** (2.1.326 on this machine) offers these hooks, as documented:
  - `PreToolUse`, which can deny a call with a reason the model reads;
  - `PostToolUse`, which takes `additionalContext`;
  - `UserPromptSubmit`, which takes `additionalContext`;
  - `PreCompact`.

  The `Skill` tool is a hook matcher, and subagent calls carry `agent_id`. The exact field that names the skill
  in the `Skill` hook input is unverified.
- **OpenCode** (2.3.0 on this machine) uses the **V2 plugin API**. The installed `@opencode-ai/plugin` 1.18.15
  typings describe V1 and are not evidence. The `opencode-rules` 2.0.0 source (its `src/index.ts` and
  `src/runtime/orchestrator.ts`) shows the V2 shape:
  - `setup(context)`;
  - `context.tool.hook('execute.before' | 'execute.after', fn)`;
  - `context.session.hook('prompt', fn)`;
  - an `event.subscribe()` stream carrying `session.compaction.started`.

  Whether `execute.before` can stop a call, with text the model reads, is unverified.

## Goals / Non-Goals

**Goals:**

- A general mechanism for **hook-delivered rules**: the full text of a rule reaches the agent at the moments it
  declares, and before the commands it gates when it was not delivered since the last compaction.
- It works the same in **Claude Code** and **OpenCode**, from one shared engine.
- Each hook-delivered rule keeps a **one-line pointer** that is always on in the root `AGENTS.md`, so an agent
  whose hooks are missing or broken still knows the rule exists and when to read it.
- Pilot rule: **commit and PR types**, with a short guide for every type `commitlint.py` accepts.

**Non-Goals:**

- Moving any other rule (see Scope).
- Prompt-pattern moments, and re-delivery right after a compaction ("sticky" rules). They belong to the
  follow-ups.
- Antigravity hook support. It gets the pointer only.
- A deterministic check that the type matches the changed files (owner's call: PRs almost always mix file
  kinds).
- An LLM or subagent validator of the type.
- Changing `commitlint.py`, the `pr-title` check, the type list or the release bump rules.

**Anti-goals** (failure even if it technically works):

- A hook failure blocks the agent's work. Hooks fail open.
- A hook changes the contributor's permission policy, for example by auto-approving a command.
- A rule is blocked-and-delivered on every commit, teaching the agent to re-run without reading.
- A rule silently never reaches a context that needs it: no pointer, or a record that marks it delivered to a
  context that never saw it.
- The mechanism is presented as judging the type. It reminds; it does not judge.

## Approaches

Considered for the timing problem. The PR title is part of the command that triggers the hook, so a hook that only
adds context after the command arrives after the title was written.

- **A. Block once per context, re-arm after compaction.** This is the chosen safety net.
- **B. Block every gated command once.** Every commit costs a retry, and agents learn to re-run blindly. Rejected.
- **C. Deliver earlier only** (keywords, `git add`). The triggers are unreliable and miss commits the agent
  decides on alone. Rejected as the only mechanism.
- **D. Deterministic type check from the changed files.** Rejected by the owner.

**Agreed:** early moments plus the block-once gate (A). The details are in `brainstorm.md`.

## Decisions

### 1. Hook-delivered rule files

- **Location:** a new flat directory **`.agents/hooked-rules/`**, *not* `.agents/rules/`. Anything in
  `.agents/rules/` is loaded up front by Claude Code, `opencode-rules` and Antigravity.
- **Name:** `<scope>-<topic>.md`, using the scope vocabulary of `.agents/rules/`.
- **Frontmatter grammar:** the restricted grammar the agent-file check already parses (`key: scalar` and flat
  lists of double-quoted strings, where only `\"` and `\\` escapes are allowed). It is extended by exactly these
  keys, and no others:
  - `pointer` (required): a double-quoted one-line summary that names the moments in words.
  - `skills`: a list of skill names, namespaced names included (`"opsx:apply"`). The rule is delivered when the
    skill is invoked by the model, invoked by the user as `/<name>`, or when its `SKILL.md` is read.
  - `commands`: a list of Python regexes (`re.search`). Each fires after a matching shell call succeeds.
  - `gates`: a list of Python regexes. Each fires before a matching shell call.

  An omitted list is empty, and a rule needs at least one entry in `skills`, `commands` or `gates`. These are
  errors:
  - duplicate keys;
  - a key outside this set;
  - an empty list written as `[]`;
  - an invalid regex.
- **Body:** the full rule text, at most 4,096 bytes.
- **Rule identity:** the file name plus the sha256 of the file. Delivery state is recorded against it (section 4).
- **One parser.** The parser lives in `scripts/agent_rules.py`. The agent-file check imports it by path from
  `--root`, so a rule that passes the check also parses at runtime.

Example:

```markdown
---
pointer: "Commit and PR types: read before choosing a commit or PR type."
skills:
  - "openspec-apply-change"
  - "opsx:apply"
commands:
  - "\\bopenspec\\s+instructions\\s+finalize\\b"
gates:
  - "\\bgit\\b[^;&|\\n]*?\\scommit\\b"
  - "\\bgh\\s+pr\\s+(create|edit)\\b"
---
```

### 2. Always-on pointers in the root `AGENTS.md`

- **The block.** A generated block in the root `AGENTS.md`, between `<!-- hooked-rules:begin -->` and
  `<!-- hooked-rules:end -->`, under a `## Hook-delivered rules` heading. It holds one line per rule (`pointer`
  text, then the file path) and one fixed sentence:

  > *If your tool did not deliver a rule below, read its file yourself at the moment it names.*

- **Why there:**
  - In the supported configuration (section 8), every supported tool reads the root `AGENTS.md` natively, with no
    hooks and no rule loader.
  - A file in `.agents/rules/` would not work: OpenCode sees that directory only through `opencode-rules`.
- **Generation:**
  - `python3 -I scripts/agent_rules.py pointers --write` writes the block. The agent-file check fails if the block
    is stale.
  - Root `AGENTS.md` is 52 lines today, with a warning at 60 and a limit at 120. The pilot removes one `## Rules`
    entry and adds the heading, the sentence and one pointer line, so the file stays below the warning.
- **Format rule kept on:** the commit-types pointer keeps the format rule itself always on ("every commit
  subject and PR title is a Conventional Commit `type(scope): description`"). Only the type guide is deferred.

### 3. The engine

- **Form:** one standard-library Python script (3.8+, run with `-I`), **`scripts/agent_rules.py`**, owned by the
  `tooling` node (path `scripts/`). Its tests are in `scripts/tests/`.
- **Subcommands:**
  - `hook`: reads one event on stdin, decides and records it in one locked transaction, and prints the decision.
  - `pointers --write | --check`.
- **Input formats:**
  - `--format claude` reads Claude Code's hook JSON and writes its hook output JSON.
  - `--format plain` is a small normalized JSON used by the OpenCode plugin:

    ```
    {event, session, agent?, cwd, tool?, command?, ok?, skill?, path?, prompt?}
    ```

- **Events:**
  - `tool-before`: gates.
  - `tool-after`: command moments, and skill moments (a skill tool call, or a read whose real path ends in
    `/skills/<name>/SKILL.md`).
  - `prompt`: a prompt whose first word is `/<skill>` counts as a skill moment. No other prompt is matched.
  - `compacted`: re-arm (section 4).
  - `void`: OpenCode only; removes one recorded entry, as described below.
- **Which repository.**
  - Rules and state come from the checkout that contains the event's `cwd`: Claude Code's `cwd` field, or the
    OpenCode session directory.
  - When `cwd` is not inside a checkout that has `.agents/hooked-rules/`, the engine passes silently.
  - A command aimed at another checkout (`git -C <other> commit`, `cd <other> && git commit`) is still judged
    with the `cwd` checkout's rules. The `cwd` checkout is the boundary, and cross-checkout resolution is out of
    scope.
- **Decisions.** There are three, and none of them is a permission grant:
  - **pass**: no output, exit 0. The host's normal permission flow is untouched.
  - **context `<text>`**: passes, adding text (Claude Code `additionalContext`; OpenCode output append).
  - **deny `<text>`**: Claude Code `permissionDecision: "deny"`; OpenCode stops the call.

  The engine **never** emits `permissionDecision: "allow"`, because that would skip the contributor's permission
  prompts.
- **Size:**
  - When several rules match one event, their texts are joined in filename order and capped at 9,000 bytes,
    below Claude Code's 10,000-character `additionalContext` limit.
  - Only rules that fit whole are included, and only those count as delivered. An omitted rule stays
    undelivered, so its gate catches it later.
- **Command moments fire only after success:**
  - Claude Code: `PostToolUse` with a zero exit, not `PostToolUseFailure`.
  - OpenCode: an `execute.after` whose result reports completion and a zero exit. The exact OpenCode field is
    checked in the spike.
  - A backgrounded command counts when it launches.
  - In a `;` or `||` chain the moment may fire even though its part did not run. That is harmless: the text still
    reached the model.
- **One transaction per event, the same on both hosts.** `hook` makes the decision and records it (a delivery,
  or a pending denial) in one locked transaction, then prints. There is no separate confirmation step, so no
  decision and credit can drift apart across a compaction or a prune.
  - **Failure after the write fails open, on both hosts.** If the engine or the host fails, cancels or times out
    after the write, the adapter lets the tool call proceed without the text. Claude Code treats a failed or
    timed-out hook as a non-blocking error, and the OpenCode plugin catches the error and passes. Nothing is ever
    blocked by infrastructure.
  - **The residual.** The recorded credit or pending entry may then be stale. Until that context's next
    compaction, **every** later gated call of that rule in the context can pass without the guide. A stale
    pending entry becomes confirmed on the next same-pattern call. This is documented and accepted for the pilot:
    it is advisory and fails open. The spike exercises it (behaviour 6) and records both the first and a second
    later gated call.
  - **The OpenCode plugin narrows it.** Each write gets a unique **write id**, taken from a monotonic counter in
    the store-level index file. The counter is never reset, including after pruning, so an id is never reused.
    Every `hook` result in the plain format returns the write id. If the plugin's own injection throws, it sends
    `void` with that id. Under the lock, the engine removes the entry only if the entry still carries exactly that
    write id. A sibling that extended or confirmed the entry gave it a newer id, so a late `void` is a no-op and
    never erases newer credit.
- **Fail open, with a notice where one can be sent:**
  - An internal error (unreadable rule, bad event, corrupt state, lock timeout) passes the call. It adds this
    context: *"Rule hooks failed: read the hooked rules listed in AGENTS.md by hand at the moments they name."*
    The error goes to stderr.
  - The OpenCode plugin is JavaScript, so it sends the same notice itself when Python is missing or the engine
    times out.
  - In Claude Code a missing Python surfaces as a non-blocking hook error the user sees, and the agent has only
    the pointer. This is documented.
- **Timeouts:** every hook has a bounded timeout of 5 seconds (Claude Code hook `timeout`; a spawn timeout in the
  plugin). A timeout fails open. These are runtime bounds, not test timing: tests inject the failure.
- **Speed:** the hot path is parse, match, one `git rev-parse` and a small locked read.

### 4. Delivery state

- **Delivered** means the rule text reached a context: as added context at a moment, or as a denial reason. It is
  recorded against the rule identity (file name plus sha256), so a sibling worktree whose rule differs does not
  inherit the credit.
- **Context key:**
  - the main context is `session id`, which both tools always provide;
  - a subagent is `session id + agent id`: Claude Code's `agent_id`, or OpenCode's child session id;
  - a call without an agent id is the main context, never "unknown".
- **Store:** one JSON file per session under `$(git rev-parse --git-common-dir)/agent-rules/`, plus a
  store-level index file.
- **Transactions:**
  - Every event runs one read–check–update transaction under an exclusive per-session lock file: `fcntl.flock` on
    POSIX and `msvcrt.locking` on Windows, with a bounded wait.
  - The decision, the generation read and the write all happen inside the lock. Writes go through a temporary
    file and an atomic rename.
  - **Generations are per context key.** Each key (main context, or session plus agent) has its own generation,
    and every delivery and pending entry records the generation of its key.
- **Ordering assumption:**
  - Hooks run synchronously within their tool call or prompt, and compaction runs between model turns. So a hook
    and a compaction of the same context do not interleave, and the lock serializes everything else.
  - If a host broke this assumption, the failure would be one missed re-arm, never a block.
- **Gate denial: pending, then confirmed.** This handles a parallel tool batch, where the model sends several
  calls before it has seen any denial:
  - A denial records the rule as **pending** for that context, with the set of gate patterns the denied call
    matched.
  - A further denial of the same rule **adds** its patterns to that set and never replaces it. So
    `deny A → deny B → retry A` passes on the retry of A.
  - While a rule is pending, a gated call that matches **any** pattern in the set passes as the re-run and
    confirms the rule.
  - A gated call that matches only patterns **not** in the set is denied again with the same text, and its
    patterns join the set. It was a sibling written before the model saw the rule.
- **Moments confirm directly.** One limit remains. When a moment and a gate fall in the same parallel batch, the
  gate's call was written before the moment's text arrived, but it may still pass if the moment's hook finishes
  first. The same applies to two siblings of the same gate kind (two `git commit` calls in one batch). Both
  residual cases are documented, exercised in the spike (behaviour 2), and accepted: such batches are unusual,
  and the format is still enforced by `commitlint.py`.
- **Compaction re-arm:**
  - Triggered by Claude Code `PreCompact` and OpenCode `session.compaction.started`.
  - It increments the generation of the compacting context's key. That voids every delivery and pending entry
    of that key and leaves every other key untouched.
  - When the host identifies a compacting subagent, only its key is incremented. When it does not, the main key
    and every subagent key of the session are incremented, which errs toward an extra delivery.
  - A re-arm never credits any context with anything.
- **Pruning:**
  - After a write, the store keeps the newest 50 session files, ordered by a monotonic counter in the index file,
    which is updated under its own lock. The clock is never used.
  - A victim is pruned only while the pruner holds that victim's lock. It is tried without waiting and skipped
    when busy, so pruning can never race the victim's own transaction.
  - The current session is never pruned. A pruned session that comes back starts at generation 0 with nothing
    delivered, which is the safe direction.

### 5. Adapters

**Claude Code** — `.claude/settings.json` gains `hooks`. Each hook runs
`python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude` with `timeout: 5`. The script
path comes from the install checkout; the rules come from the event's `cwd` (section 3).

| Hook | Matcher | Engine event |
|---|---|---|
| `PreToolUse` | `Bash` | `tool-before` (gates) |
| `PostToolUse` | `Bash\|Skill\|Read` | `tool-after` (command and skill moments) |
| `UserPromptSubmit` | — | `prompt` (`/<skill>` only) |
| `PreCompact` | — | `compacted` |

**OpenCode** — a V2 plugin at **`scripts/opencode-hooked-rules/`**, a tracked directory outside `.opencode/`. It
is declared in `opencode.json` as a local plugin entry next to `opencode-rules`. The exact local-entry syntax is
checked in the spike. The plugin spawns the engine with `--format plain`, with a 5-second timeout, maps the
decisions, and on a failed injection sends `void` with the write id it received (section 3):

| V2 hook or event | Engine event | Effect |
|---|---|---|
| `context.tool.hook('execute.before')` | `tool-before` | a deny stops the call with the text |
| `context.tool.hook('execute.after')` | `tool-after` | context text is appended to the tool output, which is kept in the transcript |
| `context.session.hook('prompt')` | `prompt` | context text is added to the prompt turn |
| event `session.compaction.started` | `compacted` | — |

A per-call `context.session.hook('context')` injection affects only one model call, so it is not used.

**Antigravity and other tools** — the pointer only.

### 6. Matching

- Gates and command moments run `re.search` over the whole shell call string, so chained commands and
  `bash -c "…"` wrappers are caught. A call that matches several patterns records all of them (section 4).
- **In scope:** literal invocations, with environment-variable prefixes and with git global options before the
  subcommand. The `git commit` gate matches `git`, then anything up to a shell operator, then the word `commit`.
  The covered forms include:
  - `git -C "work tree" commit`;
  - `git -c k=v commit`;
  - `git --no-pager commit`;
  - `git --git-dir="work tree/.git" commit`;
  - `git --work-tree . commit`.

  Each listed form has a matcher test.
- **Out of scope:** a quoted argument that contains a shell operator character (`git -C "work;tree" commit`). The
  matcher treats the `;` as an operator. A boundary test records this case as unmatched.
- **Accepted over-match:** a non-commit command that contains the word, such as `git log --grep commit`, is denied
  once. That costs one round trip, which matches the over-match policy below.
- **Out of scope:** commands reached only through shell variables, aliases or scripts.
- **Pilot gates:**
  - `git commit` in any form, including `--amend`;
  - `gh pr create`;
  - `gh pr edit`.

  Forms without an inline title (`--fill`, an editor) are gated the same way.
- An over-match, such as a message containing the words "gh pr create", costs one extra delivery, never a wrong
  outcome.

### 7. The pilot rule: commit and PR types

`.agents/hooked-rules/repo-commit-types.md` replaces `.agents/rules/repo-conventional-commits.md`. It keeps that
file's format rule and adds the type guide.

The owner set these constraints:

- `feat` only for user-visible features;
- `build` for build tooling, dev dependencies and Gradle;
- `docs` for any documentation;
- `test` for tests;
- `chore` rarely;
- the full `commitlint.py` list is covered.

The guide:

- `feat` — new behaviour a user of a shipped product can see: the Android app, the web extension, the native host
  or the installer. Bumps the minor version of the components whose paths the commit touches.
- `fix` — corrects wrong behaviour in a shipped product. Bumps the patch version of the touched components.
- `perf` — a shipped product gets faster or smaller with no behaviour change. Bumps the patch version of the
  touched components.
- `refactor` — restructures product code with no behaviour change.
- `test` — only adds or changes tests.
- `docs` — Markdown and documentation, including agent rules, skills, `AGENTS.md` and `openspec/`.
- `build` — Gradle, dependencies (including dev dependencies), toolchains, release packaging such as signing and
  git-cliff config.
- `ci` — GitHub workflows, including release and CD workflows, and the scripts only CI runs.
- `style` — formatting only.
- `revert` — undoes an earlier commit.
- `chore` — rare; only when nothing above fits.

A `!` or a `BREAKING CHANGE:` footer bumps the major version of the touched components whatever the type, as
`scripts/AGENTS.md` and `cliff.toml` define.

A mixed change takes the type of its purpose, not of most of its files. For example, a user-visible feature with
its tests and docs is `feat`, and new CI tooling with its docs is `ci`.

The rule's triggers:

- **Skills:** `openspec-apply-change`, `opsx:apply`.
- **Commands:** `openspec instructions finalize`.
- **Gates:** `git commit`, `gh pr create`, `gh pr edit`.

Related edits:

- `scripts/AGENTS.md`'s "guidance-only changes must be typed `docs`/`chore`" becomes `docs`.
- References to the old file move to the new path: `AGENTS.md`, `docs/AGENTS.md`, `docs/checks/check_agent_files.py`
  and its tests, `scripts/tests/test_components.py`, and `openspec/specs/agent-guidance-layout/spec.md` and
  `ci-pipeline/spec.md`. The live set is found with `grep -rn repo-conventional-commits` over tracked files.
  Archived changes are left alone.
- **Spec delta.** `agent-guidance-layout` currently mandates a single rule directory, nine root-listed `repo-*`
  rules (Conventional Commits among them) and exactly one OpenCode plugin. The delta adds:
  - `.agents/hooked-rules/` and its grammar;
  - the pointer block;
  - the local plugin;
  - a roster of eight `repo-*` rules plus one hook-delivered rule.

*Builder's taxonomy calls:* the `ci`/`build` split above, and the purpose rule for mixed changes.

### 8. Verification behaviours and ship conditions

The first implementation task is a spike in a real session of **each** tool. It proves these behaviours end to
end:

1. **Loading and permissions.** The hooks or plugin load from their tracked location (on OpenCode, outside
   `.opencode/`, next to `opencode-rules`). A command that normally asks for permission still asks.
2. **Block once.** A gated command is stopped once, the model reads the rule text, and the re-run proceeds,
   including a re-run whose title the model changed.
   - In a parallel batch, a sibling matching a different gate pattern is also stopped.
   - The two residual batch cases of section 4 are run and their outcome is recorded.
3. **Moments.** Each moment kind adds the rule without blocking, and a later gated command in that context is
   not blocked. Each kind is verified separately:
   - a model-invoked skill;
   - a user-invoked `/<skill>`;
   - a `SKILL.md` read;
   - a workflow command.

   A failing matching workflow command (nonzero exit) must not count as a moment.
4. **Re-arm and isolation.** After a compaction, with no early moment in between, the next gated command is
   blocked again. A subagent's delivery is not credited to the parent, and the parent's is not credited to the
   subagent.
   - When a subagent compacts, the spike checks that the parent's delivery survives if the host identifies the
     subagent. If the host does not, it checks the whole-session fallback.
5. **Pointer fallback.** With the hooks and the plugin disabled, the main context shows the pointer block from
   the root `AGENTS.md`.
6. **Failure after the write.** A forced engine failure after its write, injected through a test-only environment
   switch that makes `hook` exit with an error after writing, does not block the tool call on either host. The
   resulting stale credit is observed and recorded for the first and a second later gated call. On OpenCode, a forced injection failure sends `void`, and the
   next gated call is denied again.

The **supported configuration** is the one the root `AGENTS.md` already states: no parent `CLAUDE.md` or
`CLAUDE.local.md`, and the agent started at the repository root. Outside it, or in a subagent that does not load
`AGENTS.md`, the pointer may be absent. The hooks are then the only delivery, the same exposure today's always-on
rules have. `docs/AGENTS.md` says so.

Behaviours 1–5 are the brainstorm's five. Behaviour 6 is added by this design as a stricter condition, because
the design relies on fail-open behaviour after a write.

**Ship condition:** the pilot ships only when behaviours 1–6 work in both tools. If one cannot be made to work in
a tool, implementation stops and the owner decides: accept that tool as pointer-only for the pilot, or hold the
change.

### 9. Checks, ownership, routing and docs

- **`docs/checks/check_agent_files.py`:**
  - imports the parser from `scripts/agent_rules.py`;
  - learns the new directory, the grammar, the 4,096-byte body cap and regex validity;
  - checks pointer-block freshness;
  - checks the `hooks` block in `.claude/settings.json`;
  - checks the local OpenCode plugin entry next to `opencode-rules`;
  - still rejects `.opencode/`.

  Its tests cover each new finding.
- **CI routing:** `.agents/hooked-rules/` already falls under the `docs` node. The checker imports the engine's
  parser, so an engine-only change must also run the `docs` check. `.github/components.json` therefore gains
  `global-overrides` entries mapping `scripts/agent_rules.py` and each file under
  `scripts/opencode-hooked-rules/` to `["tooling", "docs"]`. A `components.py changed` test covers an isolated
  engine change.
- **`.github/CODEOWNERS`** gains `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/`. They are code
  that runs automatically in contributor sessions, the same reason the file already covers `/.claude/`,
  `/opencode.json` and `/.agents/`.
- **`docs/AGENTS.md`** gains a "Hook-delivered rules" section covering:
  - how to add a rule, with the grammar;
  - delivery, gates, pending and confirmed, and re-arm;
  - the supported configuration;
  - each degraded mode and what the agent sees in it;
  - the residual cases;
  - the verified state of each tool and version.

  Its "Untrusted checkouts" note is extended: the repository's hooks now run a script on every shell command.

### 10. Testing

- **Unit tests** (stdlib `unittest`, `scripts/tests/`) use fake events, an injected state directory and the
  store's own counters, with no sleeps and no clock. They cover:
  - every decision, and pass meaning no output;
  - each moment kind, and a nonzero exit as no moment;
  - pending and confirmed: a same-pattern re-run, a different-pattern sibling, overlapping patterns;
  - the 9,000-byte cap with partial crediting;
  - one transaction per event;
  - `void`: removes exactly the entry with its write id, and is a no-op once a sibling has rewritten the entry,
    after pruning, or for another key;
  - pending accumulation (`deny A → deny B → retry A` passes);
  - re-arm per key: a child reset leaves the parent intact, and the whole-session fallback resets all keys;
  - compaction or pruning between two events of a context;
  - rule identity across differing worktree rules;
  - lock contention, simulated by holding the lock in-process;
  - pruning without the clock, including skipping a busy victim;
  - fail-open on a malformed event, an unreadable rule, a corrupt state file and a lock failure;
  - the matching forms in and out of scope;
  - the grammar and its error cases;
  - pointer generation and `--check`;
  - both formats.
- **Real environment:** the section 8 behaviours in Claude Code and in OpenCode in this repository, re-run after
  implementation. The completion report and the PR state what ran, in which tool and version. The Android device
  rule does not apply, because nothing in `android/` changes.

## Risks / Trade-offs

- **One extra round trip** per context, or after each compaction, when no early moment fired. Accepted.
- **Parallel-batch residuals** (section 4) can let one gated call pass unguided. Documented and accepted; the
  format stays enforced by `commitlint.py`.
- **Over-credit** when the host fails, cancels or times out after the engine's write, on both hosts (section 3).
  Documented and accepted.
- Claude Code's `PostToolBatch` hook could later close the same-batch residual. That is noted as a follow-up and
  not used in the pilot.
- **A hook runs on every shell command.** It is bounded by a 5-second timeout and fails open, so it adds latency,
  never a block.
- **Tool API drift.** OpenCode's V2 plugin API is new, and Claude Code's hook JSON can change. The agent-file
  check pins the wiring, and the section 8 spike is repeated when either tool's major version changes.
- **The gate cannot see whether a rule was used,** only whether it was delivered. Accepted.
- **Untrusted checkouts** now run more code. Mitigated by the CODEOWNERS entries and the extended advice.

## Open items (resolved by the section 8 spike, before the plan relies on them)

1. **Claude Code:**
   - the `Skill` hook input field that names the skill;
   - whether a user-typed `/<skill>` reaches `UserPromptSubmit` in a matchable form;
   - whether `PreCompact` carries `agent_id` for a subagent compaction;
   - how a nonzero exit shows in `PostToolUse`.
2. **OpenCode 2.x:**
   - the local plugin entry syntax;
   - whether `execute.before` can stop a call with text the model sees;
   - the `execute.after` completion and exit fields;
   - whether the output append persists in the transcript.
3. **The final pointer wording.**
