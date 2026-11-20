# Stop gate spike: Claude Code `Stop` and OpenCode 2.3.2

Spike for `hook-workflow-rules` (design §3 and §7). Everything below is from real runs on this
machine on 2026-11-20, in disposable directories under `/tmp/opencode/`; no run touched this
checkout's `.claude/`, `opencode.json` or any other file.

Versions: Claude Code `2.1.328`, OpenCode `v2.3.2`, Node `v26.13.1`, Python `3.14.4`.

## Summary for Task 8

- Claude Code: a `Stop` hook that prints `{"decision":"block","reason":…}` is obeyed; the model
  continues in the same turn. On the next stop `stop_hook_active` is `true`, and a second block sent
  then is obeyed too. A user interrupt fires no `Stop`.
- OpenCode: `ctx.session.synthetic({sessionID, id, text, resume: true})` resumes the same session and
  resolves in about 5 ms, well before the resumed run ends. A child (task) session is identified by
  `parentID` from `ctx.session.get`, and is skipped.
- **Deviation to raise with the owner:** OpenCode 2.3.2 never emits `session.idle` (nor
  `session.status`). Turn completion arrives as `session.execution.succeeded`; abort as
  `session.execution.interrupted` (`reason: "user"`); error as `session.execution.failed`. See
  section 2A. The resuming call itself works, so the OpenCode half is otherwise verified.

## 1. Claude Code 2.1.328

### 1.1 Setup

A disposable git repo at `/tmp/opencode/spike-claude` with:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 -I /tmp/opencode/spike-claude/stop-hook.py", "timeout": 5 } ] }
    ]
  }
}
```

The hook appends its stdin JSON to `stop-log.jsonl`, keeps a counter in `stop-counter`, and prints
`{"decision":"block","reason":"CANARY-1-81cbfb4c: say the canary"}` on run 1,
`{"decision":"block","reason":"CANARY-2-0a3acc12: say the canary"}` on run 2, and nothing on run 3 and
later. `CLAUDE_CONFIG_DIR` pointed at a disposable copy of the credentials, so the user's global
hooks and config did not participate. Run:

```bash
CLAUDE_CONFIG_DIR=/tmp/opencode/spike-claude/claude-config \
  claude -p "Say hello in one short sentence. Do not use any tools." \
  --output-format stream-json --verbose
```

(One run resolved the model as `claude-sonnet-5-5`, an earlier one as `claude-opus-5-5`; the hook
behaviour was the same.)

### 1.2 Result: two blocks, both obeyed

`stop-log.jsonl` got exactly three lines:

```text
{"n":1,"stop_hook_active":false,"last_assistant_message":"Hello! Good to meet you.", ...}
{"n":2,"stop_hook_active":true, "last_assistant_message":"CANARY-1-81cbfb4c", ...}
{"n":3,"stop_hook_active":true, "last_assistant_message":"CANARY-2-0a3acc12", ...}
```

Transcript (`stream-json`, assistant text in order, then the result):

```text
ASSISTANT: Hello! Good to meet you.
ASSISTANT: CANARY-1-81cbfb4c
ASSISTANT: CANARY-2-0a3acc12
RESULT subtype: success | result: CANARY-2-0a3acc12 | num_turns: 3
```

Raw first stdin line of the hook (trimmed to the fields that matter):

```json
{"event": {"background_tasks": [], "cwd": "/tmp/opencode/spike-claude", "effort": {"level": "medium"},
 "hook_event_name": "Stop", "last_assistant_message": "Hello! Good to meet you.", "permission_mode": "default",
 "prompt_id": "41c25cb4-…", "session_crons": [], "session_id": "02c6cccb-…",
 "stop_hook_active": false, "transcript_path": "/tmp/opencode/spike-claude/claude-config/projects/…/02c6cccb-….jsonl"}, "n": 1}
```

So: a block continues the same turn; the next stop carries `stop_hook_active: true`; a block sent when
`stop_hook_active` is true is obeyed as well (the model quoted canary 2), and the third stop with no
output ends the turn.

### 1.3 Result: a user interrupt fires no `Stop`

Counter and log reset, a long essay prompt started, then SIGINT to the process group as soon as the
first assistant text appeared (headless mode has no Esc key; this is the interrupt a headless run
supports):

```text
started pgid=269402
sending SIGINT to process group -269402
exit=0
--- stop log present? ---
NO stop-log.jsonl
--- last few stream events ---
('ASSISTANT', '# The History of Tea\n\n## Origins in Ancient China\n\nTea begins in the mountainous southwest of China, …')
('user', None)
('result', 'error_during_execution')
```

The result record: `{"subtype":"error_during_execution","is_error":true,"num_turns":2}`. The counter file
was not created either, so no `Stop` line was produced. (An interactive Esc was not exercised; the
headless process has no Esc. The SIGINT path is what a headless run can interrupt.)

## 2. OpenCode 2.3.2

### 2.1 Setup

A disposable project at `/tmp/opencode/spike-oc` with `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama-cloud/deepseek-v4.1-flash:1017",
  "plugins": ["./spike-plugin"]
}
```

and a throwaway plugin at `spike-plugin/index.js` that subscribes with `ctx.event.subscribe({signal})`,
logs every event, resolves each `sessionID` with `ctx.session.get({sessionID})`, and reacts on the first
root-session `session.execution.succeeded` by calling `ctx.session.synthetic({ …, resume: true })`.

Two setup facts worth keeping:

- A local plugin path **must be a directory**. With `"plugins": ["./plugin.js"]` the server logged
  `level=WARN message="configured plugin path must be a directory" target=…/plugin.js` and the plugin
  never loaded; `"./spike-plugin"` (with `index.js`) loads.
- The session runs were driven either by the TUI (`opencode --standalone --prompt '…'` in a pty via
  `script`) or headless (`opencode --standalone run --format json …`), with
  `OPENCODE_CONFIG_DIR`/`XDG_DATA_HOME`/`XDG_CACHE_HOME` isolated to the disposable directory and
  `auth.json` copied in.

### 2A. `session.idle` is never emitted (design deviation for §3)

Zero `session.idle` events (and zero `session.status` events) arrived in any run: headless
`opencode run --standalone` runs, a headless client against a kept-alive `opencode serve`, and several
TUI runs, including ones held open 30–50 s after the turn ended. The log instead ends a turn with:

```text
… session.execution.started   ses_e08f… parent=null
… session.execution.succeeded ses_e08f… parent=null
```

This matches upstream OpenCode issue
[#55358](https://github.com/anomalyco/opencode/issues/55358) ("events: session.idle is declared in the
V2 schema but never emitted (completion arrives as session.execution.*)", reproduced by the maintainers
on 2.0.28). V2 completion is `session.execution.succeeded`; abort and error are the variants below.

Consequence: the design's "plugin maps `session.idle` for a root session to `stop`" cannot be built as
written on 2.3.2. Keying on `session.execution.succeeded` for a root session gives the same "turn
finished" moment, and `failed`/`interrupted` are naturally excluded, which is what the design wants
for a stop gate (no stop after an abort or an error).

### 2.2 The resuming call: form, resolution, and effect

The call form (today's `ctx.session.synthetic({ …, resume: false })`, with `resume: true`):

```js
await ctx.session.synthetic({
  sessionID,
  id: `msg_spike_${String(Date.now())}`,
  text: `Reply with exactly CANARY-OC-7f2a91d5 and nothing else.`,
  resume: true,
})
```

Observed sequence in a TUI run (seq, event; `ms` is measured around the call):

```text
26 event session.execution.succeeded ses_e08eefa09ffe… parent=null
27 consider-inject                 ses_e08eefa09ffe… parent=null
28 synthetic-start                 ses_e08eefa09ffe… resume=true ms=0.0
29 synthetic-resolved              ses_e08eefa09ffe… ms=4.2
30 event session.inbox.enqueued    ses_e08eefa09ffe…
31 event session.execution.started ses_e08eefa09ffe…
33 event session.step.started      ses_e08eefa09ffe…
37 event session.text.ended        ses_e08eefa09ffe… text=CANARY-OC-7f2a91d5
43 event session.execution.succeeded ses_e08eefa09ffe…
```

The call resolves with the queued message record, not with the run's result:

```json
{"phase":"synthetic-resolved","sessionID":"ses_e08e96f6…","ms":5.170962,
 "value":"{\"id\":\"msg_spike_1795146814666\",\"sessionID\":\"ses_e08e96f6…\",\"time\":{\"created\":1795146814668},
 \"type\":\"synthetic\",\"payload\":{\"text\":\"Reply with exactly CANARY-OC-7f2a91d5 and nothing else.\"},
 \"delivery\":\"steer\"}"}
```

So: **it resolves before the resumed run ends** (≈5 ms; the resumed run started about 50 ms later and
ran to completion). `delivery` comes back as `"steer"`. The resumed run is in the same session, and the
model's next message was the canary. The same behaviour was observed through the headless
`opencode run --standalone` client (4.7 ms; the client printed `OK`, then `CANARY-OC-7f2a91d5`), so the
resume is not TUI-only.

### 2.3 Child (task) session

The child is a normal session record with `parentID` set; the root has `parentID` null. From a run where
the agent used the task tool:

```text
13 event session.created            ses_e08eaed50ffe… parent=null
30 event session.created            ses_e08eae85fffe… parent=ses_e08eaed50ffe…
34 event session.execution.started  ses_e08eae85fffe… parent=ses_e08eaed50ffe…
55 event session.execution.succeeded ses_e08eaed50ffe… parent=null
56 consider-inject                  ses_e08eaed50ffe… parent=null
57 synthetic-start                   ses_e08eaed50ffe…
82 event session.execution.succeeded ses_e08eae85fffe… parent=ses_e08eaed50ffe…
83 consider-inject                  ses_e08eae85fffe… parent=ses_e08eaed50ffe…   (no injection)
```

The plugin's root-only guard skipped the child, as intended. The child's completion is the same
`session.execution.succeeded`, so the parent check is the only discriminator (it is not a different
event type).

### 2.4 Abort

Interrupting an in-flight run through the server API (`POST /api/session/<id>/interrupt`, via
`opencode api --server … POST /api/session/<id>/interrupt`, which returned `{"interrupted":true}`)
emitted:

```text
14 event session.execution.started     ses_e08e9e14… parent=null
43 event session.execution.interrupted ses_e08e9e14… parent=null reason=user
```

No `session.idle`, no `succeeded`. The `reason` field was `user`.

Two weaker ways of interrupting were also tried and did **not** produce a terminal event: Esc and Ctrl-C
written to the TUI's pty, and SIGINT to the TUI process. Each ended the client and its private server
(the log ended at `location.shutdown` and the plugin's `dispose` with the run still in flight). So a
user abort that the host actually processes is `session.execution.interrupted`; a client kill is not
distinguishable from shutdown and yields nothing.

### 2.5 Error

Pointing the disposable config at a model that cannot authenticate produced:

```text
15 event session.execution.started ses_e08eceb09… parent=null
20 event session.execution.failed  ses_e08eceb09… parent=null
   error={"type":"provider.auth","message":"API key not valid. Please pass a valid API key.","status":400,
          "response":{"body":"{\"error\": {\"code\": 400, …}}"}}
22 dispose
```

No `session.error` event was seen in any run; the failed execution carries the error object itself.
No `session.idle` followed either.

## 3. Per-host status

| Item | Claude Code 2.1.328 | OpenCode 2.3.2 |
|---|---|---|
| Block reaches the model, turn continues | VERIFIED | n/a |
| Second block with `stop_hook_active: true` obeyed | VERIFIED | n/a |
| User interrupt produces no stop event | VERIFIED (SIGINT; no `Stop` line) | VERIFIED (`execution.interrupted`, no `succeeded`) |
| `session.idle` usable as the trigger | n/a | **NOT AVAILABLE** — never emitted (#55358); `session.execution.succeeded` is the turn-end event |
| Resuming synthetic prompt continues the same session | n/a | VERIFIED (`resume: true`) |
| Resolving before the resumed run ends | n/a | VERIFIED (≈5 ms) |
| Child session identified and skipped | n/a | VERIFIED (`parentID`) |
| Abort event name | n/a | `session.execution.interrupted` (`reason: "user"`) |
| Error event name | n/a | `session.execution.failed` (structured `error`) |
| Interactive Esc in a TUI as the interrupt | UNVERIFIED — headless only, no Esc | UNVERIFIED as an event (client exit, no terminal event) |

## 4. What Task 8 should use

- Resuming prompt: `ctx.session.synthetic({ sessionID, id, text, resume: true })`; it returns quickly
  with the queued message (`delivery: "steer"`), so a 5 s hook budget is ample.
- Turn-end trigger: `session.execution.succeeded` for a root session (`ctx.session.get().parentID` is
  null), **not** `session.idle`.
- Never resume after `session.execution.failed` or `session.execution.interrupted` (and a killed client
  has no terminal event at all).
- Child sessions are distinguished only by `parentID`.

The owner should confirm the substitution of `session.execution.succeeded` for `session.idle` before
Task 8; everything else the task needed is verified.

## 5. Reproducing

Artifacts are throwaway: `/tmp/opencode/spike-claude/` (hook, `.claude/settings.json`, stop log,
transcripts), `/tmp/opencode/spike-oc/` (plugin, `opencode.json`, `events.jsonl`) and the runner scripts
in `/tmp/opencode/spike-oc-scripts/` (`run-claude.sh`, `interrupt.sh`, `resume-test.sh`,
`child-test.sh`, `interrupt-test.sh`, `error-test.sh`). Key commands:

```bash
# Claude Code blocks
CLAUDE_CONFIG_DIR=/tmp/opencode/spike-claude/claude-config \
  claude -p "Say hello in one short sentence. Do not use any tools." --output-format stream-json --verbose
# Claude Code interrupt
bash /tmp/opencode/spike-claude/interrupt.sh
# OpenCode turn end -> synthetic resume (TUI or headless)
bash /tmp/opencode/spike-oc-scripts/resume-test.sh resume1 "Reply with the single word OK and do not use any tools." 50
bash /tmp/opencode/spike-oc-scripts/run-oc.sh cli1 "Reply with the single word OK and do not use any tools."
# OpenCode child session
bash /tmp/opencode/spike-oc-scripts/child-test.sh child2 "Use the task tool with subagent_type explore to read README.md and report its first line. Then reply DONE." 60
# OpenCode abort via the API
bash /tmp/opencode/spike-oc-scripts/interrupt-test.sh intr1
# OpenCode provider error
bash /tmp/opencode/spike-oc-scripts/error-test.sh err1
```
## 6. Task 12 — checks and live verification (2026-11-20)

Everything below is from real runs on this machine, in disposable directories under `/tmp/opencode/`.
No run touched this checkout's `.claude/`, `opencode.json`, `AGENTS.md` or git state.

### 6.1 Versions and disposable setups

| Tool | Version | Setup |
|---|---|---|
| Claude Code | `2.1.328` | a clone at `/tmp/opencode/t12-claude/repo` of branch `chore/move-rules-to-hooks` (HEAD `33b85f4d`), with `CLAUDE_CONFIG_DIR=/tmp/opencode/t12-claude/claude-config` holding only `.credentials.json`; the tracked `.claude/settings.json` ran unchanged. Model resolved by the host as `claude-sonnet-5-5` (seen in the `Co-Authored-By` trailers). |
| OpenCode | `v2.3.2` | a clone at `/tmp/opencode/t12-oc/repo` (same branch and HEAD) with `OPENCODE_CONFIG_DIR`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` and `XDG_STATE_HOME` isolated under `/tmp/opencode/t12-oc/`; model `ollama-cloud/deepseek-v4.1-flash:1017`. |
| Node | `v26.13.1` | used by `node --test`. |
| Python | `3.14.4` | used by the engine and the checks. |
| openspec CLI | `2.1.0` | `openspec status`, `new change`, `instructions`, `validate`. |

The OpenCode config used in the disposable clone was:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama-cloud/deepseek-v4.1-flash:1017",
  "plugins": ["./scripts/opencode-hooked-rules", "./debug-events"],
  "permission": "allow"
}
```

`./scripts/opencode-hooked-rules` is the tracked local entry (the same path and form as `opencode.json`).
`./debug-events` is a throwaway diagnostic plugin in the clone that only appends every bus event and
both tool hooks to `/tmp/opencode/t12-oc/events.log`; it changes no decision. The `opencode-rules`
package entry was omitted from the disposable config (to avoid a network install of the package); the
local hooked-rules plugin is the subject of this verification. The `permission` value was changed for
the permission scenario and `allow` elsewhere. The plugin loads only for configured directories
(`./plugin.js` is rejected: "configured plugin path must be a directory").

Two headless facts that shaped the OpenCode runs:

- `opencode run --standalone` exits at the end of its turn, and the plugin's detached stop call is
  killed with the process: the session showed `session.execution.succeeded` followed immediately by
  `location.shutdown` and no stop record. The stop-gate runs therefore used a kept-alive
  `opencode serve --port 4137` with `opencode run --server …` (the same server the spike used), and
  the final `opencode run --standalone` runs cover the outcomes that do not need a post-turn resume.
- The resumed synthetic turn is stopped with `POST /api/session/<id>/interrupt` (returned
  `{"interrupted":true}`) as soon as the synthetic message was observed, so the model could not act on
  the block text and mutate the disposable receipts. The interrupt itself then produced
  `session.execution.interrupted` with `reason: "user"` and no further `stop`.

### 6.2 Step 1 — checks

`python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` selected
`supply-chain=true tooling=true docs=true` (the other nodes `false`); `supply-chain` is selected only
through its `ci-paths: docs/checks/`, so its own `AGENTS.md` checks are the docs checks below. Every
check ran from the repository root.

| Command | Result |
|---|---|
| `./gradlew scriptsTest` | `Ran 871 tests in 29.609s` — `OK`; `BUILD SUCCESSFUL` |
| `node --test scripts/opencode-hooked-rules/index.test.mjs` | `tests 148`, `pass 148`, `fail 0` |
| `python3 docs/checks/check_agent_files.py --root .` | no output, exit 0 |
| `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` | `Ran 259 tests in 50.995s` — `OK` |
| `openspec validate --all --json` | `items 31`, `passed 31`, `failed 0`, exit 0 |
| `python3 docs/checks/check_gate_b_matrix.py` | exit 0 (its standard "a validator pass is not Gate B acceptance" banner) |
| `python3 docs/checks/check_site.py --root .` | exit 0 |
| `python3 -I scripts/components.py check` | exit 0 |
| `node --test scripts/check-mac-comparisons.test.mjs` | `pass 11`, `fail 0` |

### 6.3 Claude Code 2.1.328 — outcomes 1–6

#### Outcome 1 — loading and permissions: PASS

The tracked `.claude/settings.json` was the only hook config (`CLAUDE_CONFIG_DIR` held credentials
only), and the tracked `PreToolUse` command
`python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude` ran on every shell call
(`hook_started`/`hook_response` for `PreToolUse` in the stream). With the engine passing and
`--permission-prompts none`, a command that normally asks was still refused by the permission flow,
not by the hook:

```text
TOOL_USE: Bash {"command": "touch permission-marker.txt", ...}
HOOK PreToolUse       len=0
TOOL_RESULT: Permission for this tool use was denied. It requires approval, and this session has no
approval surface — nobody can answer a permission prompt here — so it was denied automatically.
```

`permission-marker.txt` was not created. The hook's output was empty, so it neither granted nor
denied.

#### Outcome 2 — block once, parallel batch, residuals: PASS

Block once and a changed-title re-run, in one session:

```text
TOOL_USE: Bash {"command": "git commit -m 'test: first attempt'"}
TOOL_RESULT: PreToolUse:Bash hook error: # Commit and PR types | Write every commit subject as a
Conventional Commit: `type(scope): description`. …
ASSISTANT: The first commit was blocked and the second ran and succeeded.
TOOL_USE: Bash {"command": "git commit -m 'docs(notes): add notes file'"}
TOOL_RESULT: [chore/move-rules-to-hooks 302ed996] docs(notes): add notes file | 1 file changed, 1 insertion(+)
```

Parallel batch: the model emitted two `Bash` blocks in one assistant message (same API message id
`msg_011ChDd5K4cZaAKWtkwotvBb`, streamed as two records), and both were denied — `git commit` by the
`commit` pattern and `gh pr create` by the `gh pr create` pattern (plus the real-device-testing rule):

```text
msg_id msg_011ChDd5K4cZaAKWtkwotvBb uses ['Bash']   (git commit …)
msg_id msg_011ChDd5K4cZaAKWtkwotvBb uses ['Bash']   (gh pr create …)
TOOL_RESULT (commit):  PreToolUse:Bash hook error: # Commit and PR types …
TOOL_RESULT (gh pr):   PreToolUse:Bash hook error: # Commit and PR types … (and the real-device rule)
```

Residual cases (run and recorded, not pass/fail):

- *A moment with a gate in one parallel batch*: a `Read` of `.agents/skills/openspec-apply-change/SKILL.md`
  and `git commit -m 'docs: moment plus gate'` in one message. The read's `PostToolUse` delivered the
  rules and the commit **ran** (`a4f9d39f`, "1 file changed"). The moment was processed first.
- *Two same-kind gates in one parallel batch*: `git commit -m 'test: batch one'` and
  `git commit -m 'docs: batch two'` in one message. The first was **denied**, the second **ran**
  (`5e8dba0b`) as a "re-run" before the model had seen the rule — the documented residual.

#### Outcome 3 — moments: PASS

Each moment kind was a separate session, each followed by a gated command in the same context:

| Moment | Hook output | Later gated command |
|---|---|---|
| model-invoked skill (`Skill` `openspec-apply-change`) | `PostToolUse` 8,442 bytes, starting `# Commit and PR types` | `git commit` ran — `0fbed217` |
| user prompt `/openspec-apply-change` | `UserPromptSubmit` 8,447 bytes | `git commit` ran — `7c05bb25` |
| read of `SKILL.md` | `PostToolUse` 8,442 bytes | `git commit` ran — `d3d94212` |
| successful `openspec instructions finalize --change demo2` | `PostToolUse` 6,933 bytes | `git commit` ran — `4039eb7c` |

A failing workflow command is not a moment: with the command exactly
`openspec instructions finalize --change nope` (exit 1), Claude Code fired `PostToolUseFailure` (not
wired) so there was no `PostToolUse`; the later `git commit` was **denied** (`PreToolUse` 1,892 bytes)
and its retry ran (`59ed3cfb`). (A first attempt that appended `; echo EXIT_CODE=$?` made the shell
call exit 0, and the engine correctly treated it as a successful command; that run is not used as
evidence.)

Repeat of the same trigger: the first `openspec instructions finalize --change demo2` appended 6,933
bytes, the second identical call appended 0 bytes, and the later commit ran (`66e9f0a1`). A different
trigger: the apply skill delivered 8,442 bytes, then `openspec instructions finalize` delivered the
commit-types rule **again** (`PostToolUse` 6,933 bytes, starting `# Commit and PR types`), and the
commit ran (`b3c23854`).

#### Outcome 4 — re-arm and isolation: PASS (one UNVERIFIED sub-case)

- *Re-arm:* a session delivered the rules with `openspec instructions finalize --change demo2`
  (generation 0). A headless `/compact --resume <session>` raised the state's generation to 1
  (`generations {'b34a18b7…': 1}`). The next `git commit` in that session was **denied**
  (`PreToolUse` 1,892 bytes) and its retry ran (`16d49507`). Compaction with no moment in between
  therefore re-blocked the gate.
- *Parent → subagent:* the parent delivered the rules; a `Task`/`Agent` subagent's `git commit` was
  denied (`PreToolUse` 1,892 bytes) and its retry ran (`6ce652f5`). The state held a separate child
  key ending in the agent id (`…/a273493890d34ee28`), so the host identifies subagents.
- *Subagent → parent:* the subagent ran `openspec instructions finalize --change demo2` (delivered
  6,933 bytes in the child); the parent's `git commit` was denied (1,892 bytes) and its retry ran
  (`465627fb`).
- *Subagent compaction:* **UNVERIFIED** on Claude Code — a subagent's context cannot be made to
  overflow on purpose in a bounded run. The host does carry an agent id in hook payloads (the child
  key above), so the identified-subagent branch is the one wired; the whole-session fallback was not
  observed. (OpenCode's subagent compaction was exercised; see 6.5.)

#### Outcome 5 — pointer fallback: PASS

With `--settings '{"disableAllHooks":true}'` (no hook events fired) and no tools, the model quoted the
`## Hook-delivered rules` section from its context, including the closeout pointer ("finalize pushes
and creates or readies the PR, owner-authorized"):

```text
## Hook-delivered rules
- Every commit subject and PR title is a Conventional Commit `type(scope): description`; …
- Before `git push`, run the checks of every node `scripts/components.py changed` selects; …
- Behaviour changes need a real device, emulator or Chrome run before done, …
```

The HTML marker comments themselves were not present in the quoted copy (Claude Code appears to strip
them from the injected file); the block's six pointer lines were. A first probe that asked for the
text *between the markers* answered `ABSENT`; the section quote is the accurate probe.

#### Outcome 6 — failure after the write: PASS

With `VELES_AGENT_RULES_FAIL_AFTER_WRITE=1` in the host environment, `openspec instructions finalize
--change demo2` **ran** (exit 0 recorded by the model) and the hook only emitted its stderr
diagnostic; the rule text was never shown:

```text
HOOK UserPromptSubmit len=83    agent_rules.py hook: VELES_AGENT_RULES_FAIL_AFTER_WRITE=1: failing after the write
HOOK PreToolUse       len=83    (the same line)
HOOK PostToolUse      len=83    (the same line)
RES   - Generating instructions... <artifact id="finalize" change="demo2" …>
```

The engine had recorded the delivery. In the same session with the switch unset:

- the later `openspec instructions finalize` (same trigger) appended **0** bytes — stale delivery at a
  later moment of the same trigger;
- the first later gated call ran (`d2298f89`) and the second ran (`3aeff0d9`), both with empty
  `PreToolUse` output — stale credit for the first and a second later gated call.

### 6.4 Claude Code 2.1.328 — outcome 7 (stop gate)

`demo` was created in the clone with `openspec new change demo` and a hand-written `apply.md`
(iteration 1). Each row is a fresh session unless stated. `openspec status --change demo` put `demo`
in the session change set. Stop hook outputs are from `--include-hook-events`.

| Scenario | Disk state | Stop results | Result |
|---|---|---|---|
| stall after apply, then a second stop in the same phase | `apply.md` it1, no verify | block (4,283 bytes), pass | PASS — engine line names `demo`; the model continued and ended, the next stop passed |
| continued turn, new phase | as above; the model copied a passing `verify.md` it1 after the first block | block, block, pass | PASS — the second block came in the same continued turn |
| new apply iteration | `apply.md` it2, verify it1 pass | block, pass | PASS |
| failing verify at newest | `apply.md` it2, `verify.md` it2 with `❌ FAIL` ticked | pass | PASS |
| earlier failing verify | `apply.md` it2, `verify.md` it1 with `❌ FAIL` | block, pass | PASS |
| finalized | `finalize.md` present | pass | PASS |
| archived | `openspec/changes/archive/2026-11-20-demo/` present, no finalize | pass | PASS |
| unrelated session | `demo` pending, session never named it, no tools | pass | PASS |
| linked worktree | `apply.md` only in `/tmp/opencode/t12-claude/wt-demo`, main copy has none | block, pass | PASS |
| compaction keeps the record | it1, block recorded; `/compact --resume` (generation 0→1); then a stop-only turn | pass | PASS |
| push without delivery | `git push origin HEAD` twice | deny (443 bytes: the pre-push rule), then push ran (`* [new branch] HEAD -> chore/move-rules-to-hooks`) | PASS |
| push during finalize | `openspec instructions finalize --change demo2` (6,933 bytes appended), then `git push` | push ran (`Everything up-to-date`) | PASS |

The block text is the engine line followed by the closeout rule; its first line, quoted from the
`Stop` hook output:

```text
Change `demo` has an apply receipt and no finalize receipt. Run verify if it has not run, then
finalize now: it pushes and creates the PR without asking. If a rule requires you to ask the user,
or the user told you to stop, say which and end your turn; the next stop is not blocked.
```

`stop_hook_active` was probed directly by temporarily wrapping the clone's `Stop` command with a
stdin logger (the tracked command was restored afterwards). The three stops of the continued-turn run
carried:

```text
stop_hook_active= False last= 'Status exit code: 0. The change `demo` shows 1/9 artifacts …'
stop_hook_active= True last= 'I copied the verify report to `openspec/changes/demo/verify…'
stop_hook_active= True last= 'Report: - **Status exit code:** 0. - **Stop blocks show…'
```

So the post-verify block really was `stop_hook_active: true`, and the agent continued.

Apply-to-finalize run on the disposable `tiny` change (apply skill invoked, one trivial task, then
verify): the agent went from apply to verify without asking, wrote the marker file, and did **not**
reach finalize because verify failed on the missing review records. It did not report the closeout
rule as conflicting with the apply skill's ending; it cited the review gate's "ask first" requirement
as its reason to stop. The stop gate also caught the end of that run (one block, then pass;
`stops [{'change': 'tiny', 'iteration': 1, 'verify_state': 'none'}]`). The final text:

```text
I'm stopping here without finalizing. Verify failed, and the closeout rule says to stop when verify
fails. … Nothing was committed or pushed, and no PR was created.
```

The spike's Claude Code half (a canary block, the next stop reporting `stop_hook_active`, and a user
interrupt firing no `Stop`) is recorded in section 1 (same version `2.1.328`); it was not repeated.

### 6.5 OpenCode 2.3.2 — outcomes 1–6

#### Outcome 1 — loading and permissions: PASS

The local plugin loaded from the tracked `./scripts/opencode-hooked-rules` directory; a first run
created the session's engine state, and the gate/ moment effects below prove it was active. With
`"permission": {"bash": "ask"}` and no `--auto`, a non-gated `touch` was refused by OpenCode's
permission flow (the hook passed):

```text
{"error":{"type":"permission.rejected","message":"This non-interactive run cannot ask the user for
permission, so the request was rejected. Continue without this action."},"content":[]}
```

The file was not created; the plugin did not grant or deny the call.

#### Outcome 2 — block once, parallel batch, residuals: PASS

Block once and changed-title re-run:

```text
USE: shell   (git commit -m 'test: opencode first attempt')
TEXT: The hook blocked the first attempt and delivered the commit-type rule. Now the retry …
USE: shell   (a different subject)
git log: 5dd38a71 chore: opencode second attempt
```

First attempt's tool error carried the rule text
(`{"error":{"type":"unknown","message":"# Commit and PR types\n\nWrite every commit subject …`).

Parallel batch: two `shell` tool calls with the same message id (`msg_1f776676d001EqvFtHSyRHtGrb`),
both denied; the `gh pr create` sibling's denial carried the commit-types and real-device rules:

```text
USE: shell (git commit …)
USE: shell (gh pr create …)
TEXT: Both calls were issued in parallel in one message, and both were intercepted by the repo's hook
gate before they could run — neither command executed.
```

Residual cases (recorded, not pass/fail):

- *A moment with a gate in one parallel batch*: `read .agents/skills/openspec-apply-change/SKILL.md`
  plus `shell git commit -m 'docs: opencode moment plus gate'` in one message. Both ran; the commit
  was created (`971ff3fe`). **This differs from the earlier observation recorded in
  `docs/hook-delivered-rules.md` (denied on OpenCode)**; the order of the two hooks is not guaranteed.
- *Two same-kind gates in one batch*: `git commit -m 'test: opencode batch one'` and
  `git commit -m 'docs: opencode batch two'`. The first **ran** (`75a9820f`), the second was
  **denied** — the same residual with the opposite order to the Claude Code run.

#### Outcome 3 — moments: PASS

| Moment | Evidence | Later gated command |
|---|---|---|
| model-invoked skill (`skill` tool, `openspec-apply-change`) | skill loaded, rule text appended to its output | `git commit` ran — `2d7b2321` |
| user prompt `/opsx:apply` | OpenCode did not expand it as a command (no `skill` tool call); the model quoted the delivered commit-types and closeout rules | `git commit` ran — `a4efdbca` |
| read of `SKILL.md` | rule text appended to the read result | `git commit` ran — `129e7ab0` |
| successful `openspec instructions finalize --change demo2` | rule text appended after `</artifact>` | `git commit` ran — `d85ddb63` |

`/openspec-apply-change` also delivered, but OpenCode expanded it into the skill tool, so `/opsx:apply`
(not expanded) is the isolating evidence for the user-prompt moment.

A failing workflow command (`openspec instructions finalize --change nope`, exit 1) appended nothing;
the later `git commit` was denied (its tool error carried the rule) and the retry ran (`8055e28f`).

Repeat: the first `openspec instructions finalize --change demo2` appended the rule text after
`</artifact>`, the identical second call appended nothing (the model verified the file's last bytes were
`</success_criteria>\n\n</artifact>\n`), and the commit ran (`e8ea053f`). Different trigger: the skill
appended rule text, then the finalize command appended the same commit-types rule again (its state
entry held both `command:…finalize` and `skill:openspec-apply-change` triggers), and the commit ran
(`145a3bbb`).

#### Outcome 4 — re-arm and isolation: PASS

- *Re-arm:* a session delivered the rules (`finalize`) at generation 0; `POST
  /api/session/<id>/compact -d '{}'` returned a `compaction` inbox item and the state's generation rose
  to 1; the next `git commit` was denied and its retry ran (`ef0e3641`).
- *Parent → subagent:* the parent delivered; a `subagent` (`general`) child's commit was denied and
  its retry ran (`b4e7c3e0`). The state held the child key
  `…/ses_e0881ea02ffeMVMs1c9H4aM75N`.
- *Subagent → parent:* the child ran `openspec instructions finalize --change demo2`; the parent's
  commit was denied (rule text in the error) and its retry ran (`acc0e28c`).
- *Subagent compaction:* compacting the child session through the API (`POST
  /api/session/<child>/compact -d '{}'`) produced `generations {<child key>: 1}` while the parent key
  stayed at generation 0 — the parent's delivery survived. PASS.

#### Outcome 5 — pointer fallback: PASS

With the plugin removed from the disposable `opencode.json` and no tools, the model quoted the whole
section including the markers:

```text
## Hook-delivered rules
<!-- hooked-rules:begin -->
- Every commit subject and PR title is a Conventional Commit `type(scope): description`; …
…
<!-- hooked-rules:end -->
```

#### Outcome 6 — failure after the write: PASS

With `VELES_AGENT_RULES_FAIL_AFTER_WRITE=1` on the server, `openspec instructions finalize --change
demo2` ran and appended **nothing** (the plugin showed only the read-by-hand notice); the state
recorded the delivery. With the switch off, in the same session:

- the repeat finalize appended nothing (stale delivery at a later moment of the same trigger);
- the first and second later commits both ran (`6e8f94ac`, `b368509f`) — stale credit.

With `VELES_AGENT_RULES_FAIL_INJECTION=1`, a new session's finalize appended nothing and the plugin
sent `void` for its entries: the state then held only the later gated rule entry (`triggers=[]`, the
retry's pending confirmation) and none of the four delivered rules. The next `git commit` was
**denied** again. PASS.

### 6.6 OpenCode 2.3.2 — outcome 7 (stop gate)

The spike's OpenCode half (root `session.execution.succeeded` resumes with a synthetic prompt; a child
session's success, an interrupted execution and a failed execution are told apart) is section 2, same
version `2.3.2`. The final run used a kept-alive server; each stop-gate row is a fresh session, and
the synthetic resume is interrupted immediately after it is observed so it cannot act.

Representative event sequence (observed in `events.log`):

```text
ENQUEUED user Run the shell command `openspec status --change demo` …
session.execution.started
session.execution.succeeded
ENQUEUED synthetic Change `demo` has an apply receipt and no finalize receipt. Run verify if it has
not run, then finalize now: it pushes and creates the PR without asking. …
session.execution.started
interrupt={"interrupted":true}
session.execution.interrupted reason=user
```

| Scenario | Disk state | Event result | Result |
|---|---|---|---|
| stall after apply | `apply.md` it1 | synthetic block; stop record `{demo,1,none}` | PASS |
| second stop, same phase | same session, second prompt | `succeeded`, no synthetic | PASS |
| after a passing verify | `verify.md` it1 PASS | synthetic block; record `{demo,1,pass}` | PASS |
| new apply iteration | `apply.md` it2, verify it1 pass | synthetic block; record `{demo,2,none}` | PASS |
| failing verify at newest | `apply.md` it2, verify it2 FAIL | no synthetic, no record | PASS |
| earlier failing verify | `apply.md` it2, verify it1 FAIL | synthetic block; record `{demo,2,none}` | PASS |
| finalized | `finalize.md` present | no synthetic | PASS |
| archived | archive copy present | no synthetic | PASS |
| unrelated session | `demo` pending, never named | no synthetic | PASS |
| linked worktree | `apply.md` only in `/tmp/opencode/t12-oc/wt-demo` | synthetic block; record `{demo,1,none}` | PASS |
| push without delivery | `git push origin HEAD` twice | denied (pre-push text), then `* [new branch]` | PASS |
| push during finalize | finalize instructions then `git push` | `Everything up-to-date`, not denied | PASS |
| interactive Esc abort in the TUI | a pty TUI run, Esc at 12 s | `session.execution.started`, then `location.shutdown`; **no** `session.execution.interrupted` | **UNVERIFIED** — the TUI client exits without a terminal event, so the plugin cannot see it (accepted residual) |

The interrupted-execution discrimination was observed in the five interrupted stop-gate runs:
`session.execution.interrupted reason=user` and no further synthetic message. A child session's
success produced no stop (the stop-gate rows above run in root sessions; the subagent runs in 6.5
show the child key and no child stop record).

### 6.7 Outcome summary

| Outcome | Claude Code 2.1.328 | OpenCode 2.3.2 |
|---|---|---|
| 1. Loading and permissions | PASS | PASS |
| 2. Block once, changed-title re-run, parallel sibling, both residuals | PASS (residual: moment+gate passed; two gates: first denied, second passed) | PASS (residual: moment+gate passed; two gates: first passed, second denied) |
| 3. Four moments, later gate, failing command, repeat, different trigger | PASS | PASS |
| 4. Re-arm; parent↔subagent isolation; subagent compaction | PASS; subagent compaction UNVERIFIED (cannot force a subagent compaction); host carries `agent_id` | PASS, including a child-session compaction of the subagent |
| 5. Pointer fallback | PASS | PASS |
| 6. Failure after the write, stale credit, void | PASS | PASS |
| 7. Stop gate, push gate, interactive abort | PASS; apply-to-finalize run recorded | PASS; interactive TUI Esc **UNVERIFIED** — the client exits with no terminal event |
