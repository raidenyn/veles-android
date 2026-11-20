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
