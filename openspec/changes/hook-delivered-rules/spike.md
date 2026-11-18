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

Antigravity was not exercised here. The controller covers it separately (step 6, Antigravity only).

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
