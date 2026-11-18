# Spike: host behaviours for hook-delivered rules

Task 1 of the plan. A throwaway stub engine, Claude Code hooks and an OpenCode plugin were installed at their
tracked paths in a disposable clone. Each ship-gate behaviour of design section 8 was then run in real headless
sessions. Only this file is kept. The raw logs, the clone and the stubs were deleted.

## Versions

| Item | Version |
|---|---|
| Claude Code | 2.1.326 (`claude -p`, Sonnet model) |
| OpenCode | 2.3.2 (`opencode run --standalone`, default OpenAI model), next to `opencode-rules` 2.0.0 |
| Python | 3.14.4 (engine run with `-I`) |
| Node | 26.13.1 (only used for `node --check`; OpenCode loads the plugin itself) |
| OpenSpec | 2.0.0 |

## Antigravity (step 1.5, pointer-only)

Antigravity is pointer-only and is not part of the ship gate, which requires behaviours 1–6 in Claude Code and
OpenCode. It was observed through the orchestrator's Antigravity driver (model `gemini-3.8-flash`) in the real
working tree, with a temporary stub pointer block in the root `AGENTS.md` and a stub rule under
`.agents/hooked-rules/`, both reverted afterwards.

| Probe | Result |
|---|---|
| Stub rule body visible before any trigger | Not visible (as required) |
| Stub pointer block visible | Not visible |
| Control: root `AGENTS.md` itself in context at start | Not in context |
| Control: any `.agents/rules` content in context at start | Not in context |

**Result: inconclusive.** The body being absent holds, but the control shows that this driver loads neither the
root `AGENTS.md` nor the rules at session start, so the missing pointer is not evidence about Antigravity's own
behaviour. This matches the earlier agent-guidance verification, which also recorded an inconclusive Antigravity
driver run. Whether Antigravity reads the root `AGENTS.md` natively stays unverified; it is recorded as a blocker
for the owner to accept, and no later task depends on it.

**Repeated on the finished implementation (final verification).** With the real pointer block in the root
`AGENTS.md` and the real pilot rule, the same driver again reported the root `AGENTS.md`, the pointer line and the
rule body all as not in context at the start. The rule body is absent, as required, but the missing pointer is not
evidence about Antigravity: the driver does not load the root `AGENTS.md` at all. The result stays inconclusive and
is recorded as a blocker for the owner to accept.


## Open item 1: Claude Code facts

- **Skill hook input.** `PostToolUse` with `tool_name: "Skill"` names the skill in `tool_input.skill`, namespaced
  names included (`"superpowers:verification-before-completion"`). `tool_response` is
  `{success, commandName}`.
- **`/<skill>` typed by the user.** It reaches `UserPromptSubmit` verbatim: `prompt` is the raw text, starting with
  `/<skill>`. It does not also produce a `Skill` tool call. Additional context returned from
  `UserPromptSubmit` reached the model.
- **`PreCompact` and `agent_id`.** A manual `/compact` (`claude -p --resume <id> "/compact"`) fires `PreCompact`
  with `trigger: "manual"` and no `agent_id`, then `SessionStart` with `source: "compact"`. `/compact` does not
  fire `UserPromptSubmit`. A subagent autocompaction fires `PreCompact` with `trigger: "auto"` **and the
  subagent's `agent_id`**. It is followed by `SessionStart` with `source: "compact"` and the same `agent_id`.
  One subagent run fired `PreCompact` three times, and not every one was followed by a `SessionStart`. Repeated
  re-arms are harmless.
- **Nonzero exit.** A Bash call that exits nonzero never reaches `PostToolUse`. It fires
  **`PostToolUseFailure`** instead, with `error: "Exit code N\n…"` and `is_interrupt`. The `PostToolUse`
  `tool_response` for Bash has no exit-code field (`stdout`, `stderr`, `interrupted`, `isImage`,
  `noOutputExpected`). So a `PostToolUse` on Bash means success, as design section 3 assumes.
- **Denied calls.** A call denied by `PreToolUse` gets no `PostToolUse`. The denial reason reached the model
  verbatim.
- **Subagent hook inputs** carry `agent_id` on `PreToolUse` and `PostToolUse`, and `session_id` is the parent's.
- **SessionStart** fires with `source` `startup`, `resume` or `compact`. Plain project hooks in
  `.claude/settings.json` load in `claude -p` with no trust prompt.
- **Background subagent completion** comes back to the parent as a `UserPromptSubmit` whose prompt starts with
  `<task-notification>`. Its first word is not `/<skill>`, so it never matches.
- **Hook failure.** A hook that exits 1 is a non-blocking error: the tool call ran. In `-p` mode the error was not
  shown to the model.
- **Parallel Bash calls.** Claude Code ran the Bash calls of one assistant message one after another. Each call's
  `PreToolUse`, run and `PostToolUse` finished before the next call's `PreToolUse`. Two denied siblings both got
  their `PreToolUse` before either retry.

## Open item 2: OpenCode 2.x facts

- **Local plugin entry.** The working form is a **directory path string** in the `plugins` array, next to the
  `opencode-rules` object:

  ```json
  "plugins": [
    {"package": "opencode-rules@>=2.0.0 <3", "options": {"additionalRuleDirectories": [".agents/rules"]}},
    "./scripts/opencode-hooked-rules"
  ]
  ```

  The directory holds `index.js`, and no `package.json` is needed. Forms that do not work:
  - `"./scripts/opencode-hooked-rules/index.js"` is rejected with "configured plugin path must be a directory".
  - `{"package": "file:./scripts/opencode-hooked-rules"}` passes the schema, but it is treated as an npm install
    and fails to load.
  - `{"path": "…"}` is silently dropped by the config schema.

  The relative path resolved against the repository root, which here was both the config directory and the
  session directory.
- **Tool names and inputs** seen by the hooks:
  - the shell tool is **`shell`**, not `bash`, with `input.command`;
  - `read` has `input.path`, relative to the session directory as the model wrote it;
  - `skill` has **`input.id`**, the skill name;
  - `task` starts subagents;
  - `patch` edits files.
- **`execute.before` input:** `{tool, sessionID, agent, messageID, id, input}`. `agent` is the agent *name*, for
  example `build`, and not an identity.
- **Stopping a call with model-visible text.** Yes. **Throwing an `Error` from the `execute.before` handler** stops
  the call. The tool part ends with status `error`, and its output is exactly the error message, which the model
  read and acted on. No field is needed. `execute.after` does **not** fire for a call stopped this way.
- **`execute.after` completion and exit fields.** The input is
  `{tool, sessionID, agent, messageID, id, input, status, result?, error?}`.
  - For `shell`, `result.output` is `{exitCode, output, wallTimeSeconds}`, `result.metadata` is
    `{status, truncated, exit}`, and `result.content` is an array of `{type: "text", text}`.
  - **A failing command still has `status: "completed"`**, with `metadata.exit: 1` and `output.exitCode: 1`. The
    success test must be `status === "completed" && result.metadata.exit === 0`, and `status` alone is not enough.
  - A call rejected by a permission rule has `status: "error"`, no `result`, and an `error` object.
- **Output append persists.** The plugin pushed `{type: "text", text}` onto the `result.content` array, mutating
  the input in place.
  - The model saw the text in that tool's output.
  - `opencode session export` showed it stored in the tool part.
  - In a later turn the model still quoted it.
  - For `skill` and `read`, `result.content` is an array too, while `result.output` is an object, not a string.
- **Prompt-turn injection.** `context.session.hook('prompt')` input is
  `{sessionID, messageID, prompt: {text, files}, delivery}`. The handler cannot change the prompt. The working
  mechanism is the one `opencode-rules` uses:

  ```js
  await ctx.session.synthetic({ sessionID, id: "msg_<unique>", text, resume: false })
  ```

  The synthetic message is stored before the user message in the transcript. The model reported seeing it as part
  of the preceding user-role content, and the later gated call passed. The `id` must start with `msg_`, and
  `resume: false` is needed so that no run starts on the rule block alone.
- **Compaction.** It is observed on `ctx.event.subscribe({signal})`, an async iterable of `{type, data}`.
  - `session.compaction.started` carries `data: {sessionID, reason, inputID}`, with `reason: "manual"` in the
    spike.
  - It is followed by many `session.compaction.delta` events and one `session.compaction.ended`.
  - For a subagent, `sessionID` is the **child session id**, so the host identifies the compacting subagent.
- **Subagents.** A subagent is a child session. Hook inputs carry the child's `sessionID`.
  `ctx.session.get({ sessionID })` returns the session info with `parentID`. A plugin can therefore map a child to
  `session = root parent id` and `agent = child session id`. The child's first prompt, written by the parent's
  `task` call, also passes through the `prompt` hook.
- **Parallel calls.** OpenCode ran the `execute.before` hooks of a parallel batch concurrently. Both hooks entered
  before either engine result returned, and the engine lock serialized them.
- **Headless limits.**
  - `/compact` is **not** a command in `opencode run`; the model just answered it. Compaction was therefore
    triggered by a test-only line in the stub plugin: on a magic prompt it called the host API
    `ctx.session.compact({ sessionID })`. That is a real host compaction, and its events are the ones above.
    Autocompaction was not exercised.
  - `opencode run` must be started with `--standalone`, or the plugin, environment and `PATH` shim come from the
    background service and not from the session.
  - A permission `ask` cannot be answered headless. The run rejects it with a "non-interactive run cannot ask"
    error.

## Stub plugin shape that worked (for Task 7)

```js
export default {
  id: "spike-hooked-rules",
  async setup(ctx) {
    const dir = ctx.location.directory            // session directory = engine cwd
    await ctx.tool.hook("execute.before", async (input) => {
      if (input.tool !== "shell") return
      const res = await runEngine({ event: "tool-before", ...(await keyOf(input.sessionID)), cwd: dir,
                                    command: input.input.command })
      if (res.failed) return                      // fail open
      if (res.decision === "deny") throw new Error(res.text)   // model sees exactly this text
    })
    await ctx.tool.hook("execute.after", async (input) => {
      // shell: ok = input.status === "completed" && input.result?.metadata?.exit === 0
      // skill: input.input.id   read: input.input.path (relative to dir)
      // on context: input.result.content.push({ type: "text", text: res.text }); on throw -> void each entry
    })
    await ctx.session.hook("prompt", async (input) => {
      // input.prompt.text; on context:
      // await ctx.session.synthetic({ sessionID: input.sessionID, id: "msg_…", text: res.text, resume: false })
    })
    ;(async () => {
      for await (const ev of ctx.event.subscribe({}))
        if (ev.type === "session.compaction.started") await runEngine({ event: "compacted", ...(await keyOf(ev.data.sessionID)), cwd: dir })
    })()
  },
}
// keyOf(sid): ctx.session.get({ sessionID: sid }) -> info.parentID ? { session: root, agent: sid } : { session: sid }
// runEngine: spawn("python3", ["-I", engine, "hook", "--format", "plain"]), JSON on stdin, 5 s kill timeout,
//            nonzero exit / timeout / bad JSON -> { failed }.
```

## Ship-gate matrix

"Pass" means that a real headless session showed the outcome, with the engine's decision log matching it.

| # | Outcome | Claude Code 2.1.326 | OpenCode 2.3.2 |
|---|---|---|---|
| — | Fresh session: pointer block present, rule body absent | Pass | Pass |
| 1 | Integration loads from its tracked location | Pass (`.claude/settings.json` hooks) | Pass (directory entry next to `opencode-rules`, outside `.opencode/`) |
| 1 | A command that normally asks still asks | Pass: the hook passed silently and the command was refused for lack of approval | Pass: project `ask` rule; the hook passed and the host still asked, rejected headless |
| 2 | Gated command stopped once with the rule text; re-run proceeds | Pass | Pass |
| 2 | Re-run whose title the model changed proceeds | Pass | Conditional pass: the gated commit was denied with the rule text, the model then changed the commit title, and the re-run proceeded. Driven with the pointer block removed from the clone's `AGENTS.md`; with the pointer intact the model reads the rule first and never sends a non-conforming title (see the bullet "Changed-title re-run on OpenCode") |
| 2 | Parallel sibling with a different gate pattern also stopped | Pass: both denied, then both retries passed | Pass: both denied, then both retries passed |
| 2 | Residual A: moment and gate in one batch | Recorded: the gate **passed**, because Claude ran the moment's call first, so the moment landed before the gate | Recorded: the gate was **denied**, because hooks ran concurrently and the gate decided before the moment's after-hook |
| 2 | Residual B: two same-kind gates in one batch | Recorded: first denied, second **passed** as a "re-run" without the model having seen the rule | Recorded: same as Claude Code |
| 3 | Model-invoked skill adds the rule; later gate passes | Pass | Pass (appended to the `skill` output) |
| 3 | User `/<skill>` adds the rule; later gate passes | Pass (`UserPromptSubmit` context) | Pass (`session.synthetic`) |
| 3 | `SKILL.md` read adds the rule; later gate passes | Pass (through the `.claude/skills` symlink, matched by real path) | Pass (relative `path`) |
| 3 | Successful workflow command adds the rule; later gate passes | Pass | Pass |
| 3 | Failing workflow command is not a moment | Pass (`PostToolUseFailure`; the next gate was denied) | Pass (`metadata.exit: 1`; the next gate was denied) |
| 4 | After compaction with no moment, the next gate is blocked | Pass (`/compact`) | Pass (host compaction through the test trigger) |
| 4 | Parent delivery not credited to a subagent | Pass | Pass |
| 4 | Subagent delivery not credited to the parent | Pass | Pass |
| 4 | Subagent compacts: host identifies it, and the parent's delivery survives | Pass (forced autocompaction; `agent_id` present; child gate denied, parent gate passed) | Pass (child session compacted; child gate denied, parent gate passed) |
| 4 | Whole-session fallback | Not applicable: the host identifies the subagent | Not applicable: the host identifies the subagent |
| 5 | Hooks or plugin disabled: pointer block visible | Pass (`disableAllHooks`; no hook ran) | Pass (plugin entry removed; plugin not loaded) |
| 6 | Forced engine failure after the write does not block | Pass (exit 1 is non-blocking) | Pass (the plugin caught the failure and passed) |
| 6 | Stale credit on the first and second later gated call | Observed: the first later call passed by confirming the stale pending entry, and the second passed as delivered | Observed: same as Claude Code |
| 6 | Forced injection failure sends `void`; the next gate is denied again | Not applicable (OpenCode only) | Pass: `void` removed the entry with that write id, and the next gate was denied |

**Result:** every behaviour 1–6 outcome passes in both Claude Code and OpenCode, except that the changed-title
re-run on OpenCode is a conditional pass (pointer removed; see the bullet "Changed-title re-run on OpenCode"). Both
residual batch cases were run and their outcomes recorded.

## How the hard cases were driven

- **Claude Code subagent compaction.** A subagent was made to read several large files while two test-only
  environment variables shrank the autocompact window: `CLAUDE_CODE_AUTO_COMPACT_WINDOW` and
  `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`. The parent stayed below the threshold.
- **OpenCode compaction.** As described above, through `ctx.session.compact` from a test-only prompt trigger. The
  child session was compacted by resuming it directly with `opencode run -s <child>`.
- **Changed-title re-run on OpenCode.** The pointer block was removed from the clone's `AGENTS.md` for these runs.
  Otherwise the model followed the pointer, read the rule first and never sent a non-conforming title. The
  sequence was: the initial gated commit was denied with the rule text, the model changed the commit title, and the
  re-run proceeded. This shows the gate path under a modified setup, not the same outcome with the pointer intact,
  so it is a conditional pass. The final verification (plan Task 10) must drive this same sequence with the same
  test-only driver (pointer removed) and report it as conditional again.
- **Workflow moment.** Only the trigger command ran, against a dummy change created in the clone with
  `openspec new change spike-dummy`. The test agents were told not to carry out the returned workflow. `gh` was a
  `PATH` shim that only logged.

## Concerns for later tasks

1. **Reading the rule file through the pointer is not a moment.** OpenCode's model followed the pointer and read
   the stub rule file before almost every commit. It still got one denial, because a read of the
   rule file earns no credit. Counting a read of a hooked rule's own file as a delivery of that rule would remove
   this needless block. This is a design change, so the owner decides.
2. **Main-context compaction re-arms subagents.** The re-arm fallback ("no agent id → increment every key of the
   session") also fires on every main-context compaction, because a main compaction never has an agent id on
   either host. Both hosts identify subagent compactions, so the fallback is never needed for them. It errs toward
   extra deliveries, which is safe. Task 4 may prefer "no agent id → main key only".
3. **OpenCode adapter specifics the design does not state.**
   - Match the `shell` tool.
   - Decide success by `metadata.exit === 0`, not by `status`.
   - Take the skill name from `input.id`.
   - Resolve a relative `read` path against the session directory before the `realpath` check.
   - Map child sessions through `parentID`.
4. **Headless compaction on OpenCode** could only be driven through the plugin API. The finished-implementation
   re-run needs the same test trigger, or an interactive session, to compact.
5. **Residual A differs by host** because of the different concurrency: on Claude Code the gate passes, on
   OpenCode it is denied. Both are within the accepted residual.

## Final verification (candidate 7e164d3e)

Plan Task 10 step 2 (task 7.2). The finished implementation was run in real headless sessions in a disposable
clone of candidate `7e164d3e` with no remote. No stubs: the real `.claude/settings.json` hooks, the real
`opencode.json` plugin entry, `scripts/agent_rules.py` and `.agents/hooked-rules/repo-commit-types.md`. `gh` was a
`PATH` shim that only logged, and nothing was pushed. Each outcome was checked against the engine state under the
clone's git directory (`agent-rules/`), not only against what the model said. The raw logs, the clones and the
sessions were deleted afterwards.

### Versions

| Item | Version |
|---|---|
| Claude Code | 2.1.326 (`claude -p`, Sonnet model) |
| OpenCode | 2.3.2 (`opencode run --standalone`, default OpenAI model), next to `opencode-rules` 2.0.0 |
| Python | 3.14.4 (engine run with `-I` by the real hooks) |
| Node | 26.13.1 (OpenCode loads the plugin itself) |
| OpenSpec | 2.0.0 |

### Ship-gate matrix (finished implementation)

"Pass" means that a real headless session showed the outcome, and the engine state matched it.

| # | Outcome | Claude Code 2.1.326 | OpenCode 2.3.2 |
|---|---|---|---|
| — | Fresh session: pointer block present, rule body absent | Pass | Pass |
| 1 | Integration loads from its tracked location | Pass (`.claude/settings.json` hooks) | Pass (`"./scripts/opencode-hooked-rules"` next to `opencode-rules`) |
| 1 | A command that normally asks still asks | Pass: no hook output, and the call was refused for lack of approval | Pass: under a project `ask` rule the plugin passed, and the host asked and then rejected headless |
| 1 | …also after a recoverable engine failure (corrupt state) | Pass: a gated `git commit` that needs approval got the read-by-hand notice, no decision, and was refused for lack of approval | Pass: same probe under an `ask` rule; the plugin passed silently (no state written) and the host asked and rejected |
| 2 | Gated command stopped once with the rule text; re-run proceeds | Pass (`git commit`, `gh pr edit`; `gh pr create` in the batch row) | Pass (`git commit`, `gh pr edit`; `gh pr create` in the batch row) |
| 2 | Re-run whose title the model changed proceeds | Pass (`update stuff` denied, then `chore: …` passed; same for a `gh pr edit` title) | Conditional pass: pointer block removed from the clone's `AGENTS.md`; `update stuff` denied, then `chore: …` passed (same for `gh pr edit`). With the pointer intact the model read the rule first and never sent the bad title; in that run it still changed a denied `chore: …` to `chore(repo): …`, and the changed re-run passed |
| 2 | Parallel sibling with a different gate pattern also stopped | Pass: `git commit` and `gh pr create` both denied, both retries passed (2 patterns pending) | Pass: same |
| 2 | Residual A: moment and gate in one batch | Recorded: the gate **passed**, because Claude ran the moment first | Recorded: the gate (`gh pr edit`) was **denied**, because the hooks ran concurrently |
| 2 | Residual B: two same-kind gates in one batch | Recorded: first denied, second **passed** as a re-run | Recorded: same, driven with two `gh pr edit` calls (the model refused to send two parallel commits) |
| 3 | Model-invoked skill adds the rule; later gate passes | Pass (`openspec-apply-change`) | Pass (rule appended to the `skill` output) |
| 3 | User `/<skill>` adds the rule; later gate passes | Pass (`UserPromptSubmit` context, recorded on the prompt) | Pass (synthetic message stored before the user message) |
| 3 | `SKILL.md` read adds the rule; later gate passes | Pass (absolute path through the `.claude/skills` symlink) | Pass (relative `path`) |
| 3 | Successful workflow command adds the rule; later gate passes | Pass | Pass |
| 3 | Failing workflow command is not a moment | Pass (next gate denied) | Pass (next gate denied) |
| 4 | After compaction with no moment, the next gate is blocked | Pass (`/compact`) | Pass (host compaction through the test trigger) |
| 4 | Immediate gate right after an observed compaction (ordering) | Not applicable (OpenCode only) | Recorded: `compaction.started` and `.ended` were observed before the gate's hook, and the gate was denied, so the reset had been applied |
| 4 | Parent delivery not credited to a subagent | Pass | Pass |
| 4 | Subagent delivery not credited to the parent | Pass | Pass |
| 4 | Subagent compacts: host identifies it, and the parent's delivery survives | Pass (forced autocompaction: only the subagent key's generation rose, to 2; child gate denied, parent gate passed) | Pass (child session compacted: only the child key rose; child gate denied, parent gate passed) |
| 4 | Whole-session fallback | Not applicable: the host identifies the subagent | Not applicable: the host identifies the subagent |
| 5 | Hooks or plugin disabled: pointer block visible | Pass (`disableAllHooks` through `--settings`; no state written, commit not blocked) | Pass (plugin entry removed; no state written) |
| 6 | Forced engine failure after the write does not block | Pass (pending entry written, then exit 1; the call ran) | Pass (pending entry written; the plugin passed, and the after-hook added the notice) |
| 6 | Stale credit on the first and second later gated call | Observed: the first later commit passed by confirming the stale pending entry; a second later call (`gh pr create`) passed as delivered | Observed: same as Claude Code |
| 6 | Forced injection failure sends `void`; the next gate is denied again | Not applicable (OpenCode only) | Pass: the moment's entry was written, then removed by `void`, and the next gate was denied |
| 7 | Claude: recoverable failure on `PreToolUse` puts the notice in the model's view with no permission decision | Pass: the model quoted the notice; the call went through the normal flow (ran when allowed, refused when not) | Not applicable |
| 8 | OpenCode: `ctx.session.get` for a child session has `id`, `parentID` and `location` | Not applicable | Pass: test-driver log showed `id` matching, `parentID` = the parent and an absolute `location.directory`; behaviour matched (child gate denied, parent delivery kept) |
| 9 | OpenCode: session directory, not plugin directory, picks the rules | Not applicable | Pass: plugin loaded from clone A (`OPENCODE_CONFIG`), session in clone B with a canary line in its rule body. The denial text carried clone B's canary, and the state was written to clone B's store |

**Result:** every ship-gate outcome for behaviours 1–6 passes in both Claude Code and OpenCode. The changed-title
re-run on OpenCode is a conditional pass with the pointer removed, as in the spike. Both residual batch cases were
run and recorded, with the same outcomes as the spike. Extra checks 7–9 pass. **Status: DONE.**

### How the hard cases were driven

- **Corrupt state:** the clone's `agent-rules/index.json` was truncated for one run and restored afterwards. Only a
  transaction that writes reads the index, so the probes used a gated command (`git commit`). A non-gated `touch`
  took no store path and got no notice. On OpenCode the probe added `"git commit *": "ask"` to the clone's
  `opencode.json` for that run only.
- **Claude subagent compaction:** `CLAUDE_CODE_AUTO_COMPACT_WINDOW=120000` and
  `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60`. The subagent ran the workflow trigger and then read four large files. A
  first attempt with a 100000 window also compacted the parent (whole-session bump), so it was discarded.
- **OpenCode compaction:** a separate test-only plugin under the scratch directory, added as an extra entry to
  the clone's `opencode.json` (the real plugin is unchanged). On a magic prompt it called
  `ctx.session.compact({sessionID})`. It also logged compaction events, the order of `execute.before`, and the
  shape of `ctx.session.get` for checks 4 and 8. The child was compacted by resuming it with `opencode run -s <child>`.
- **OpenCode changed title:** a second clone with the pointer block removed from `AGENTS.md`.
- **Forced failures:** `VELES_AGENT_RULES_FAIL_AFTER_WRITE=1` and `VELES_AGENT_RULES_FAIL_INJECTION=1`, set only
  for the sessions concerned. The state file was read between the calls to show the write came first.
- **Check 9:** clone B had the hooked plugin removed from its own `opencode.json`. `OPENCODE_CONFIG` pointed to a
  scratch config naming clone A's plugin directory by absolute path.

### Defects found

None in the implementation. Observations:

1. Spike concern 1 still holds on OpenCode. The model follows the pointer and reads the rule file before most
   gated commands, which earns no credit, so it still takes one denial.
2. On Claude, the denial reaches the model as `PreToolUse:Bash hook error: <rule text>`. Every model read it as
   the rule and re-ran the command.
3. A compaction between a denial and its retry voids the pending entry, so the retry is denied again. This was
   seen in Claude subagent runs and is the designed re-arm.
