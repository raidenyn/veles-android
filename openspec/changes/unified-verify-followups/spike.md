# Spike S1 — a `resume: true` synthetic prompt while the session is busy

Spike for `unified-verify-followups` design D5. Everything below is from real OpenCode v2.3.2 runs on
this machine on 2026-11-21, in a disposable directory under `/tmp/opencode/s1-oc/`; no run touched this
checkout's `opencode.json`, plugins, `.agents/` or git state, and none of the throwaway plugins, configs
or event logs is committed.

## S1.1 Question

D5 sends a verifier's block with its own `ctx.session.synthetic({ …, resume: true })` when the verifier
finishes. The verifier can finish while a new user message is already running a turn. What does that
synthetic prompt do then: is it **queued after the current turn**, **rejected**, or **interleaved** into
the turn? D5 stands if the answer is queued or interleaved; its fallback (keep the block and send it at
the session's next turn end) applies only if the resume is rejected.

## S1.2 Setup

Versions and host:

| Item | Value |
|---|---|
| OpenCode | `opencode v2.3.2` (`/home/dev/.opencode/bin/opencode`) |
| Node (driver only) | `v26.13.1` |
| Python (driver only) | `3.14.4` |
| Model | `ollama-cloud/deepseek-v4.1-flash:1017` |
| Disposable project | `/tmp/opencode/s1-oc` |

The disposable project had this `opencode.json` (a local plugin path must be a directory; a file path is
rejected with "configured plugin path must be a directory"):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama-cloud/deepseek-v4.1-flash:1017",
  "plugins": ["./spike-plugin"],
  "permission": "allow"
}
```

The throwaway plugin (`/tmp/opencode/s1-oc/spike-plugin/index.js`, not committed) subscribes to
`ctx.event.subscribe({signal})` and logs every event. On the first root-session
`session.execution.succeeded` it "arms"; then it counts later bus events and, at the configured count,
makes the one call under test — no wall clock, no timers:

```js
if (event.type === "session.execution.succeeded" && !armed && sessionID) {
  armed = true
  root = sessionID
  const info = await ctx.session.get({ sessionID })
  log({ phase: "armed", sessionID: root, parentID: info.parentID ?? null })
  continue
}
if (!armed) continue
later += 1
if (fired || later < DELAY) continue   // DELAY from SPIKE_DELAY_EVENTS
fired = true
const started = Date.now()
ctx.session.synthetic({ sessionID: root, id: `msg_spike_${Date.now()}`, text: "SPIKE-RESUME", resume: true })
  .then((value) => log({ phase: "synthetic-resolved", ms: Date.now() - started, value: JSON.stringify(value) }))
  .catch((error) => log({ phase: "synthetic-rejected", message: String(error) }))
```

The call is the same host call D5 makes (`ctx.session.synthetic({resume: true})`), detached exactly as the
tracked `scripts/opencode-hooked-rules` plugin detaches its resume, so the event loop never blocks.

Isolation and driver:

```bash
export OPENCODE_CONFIG_DIR=/tmp/opencode/s1-oc/config
export XDG_DATA_HOME=/tmp/opencode/s1-oc/data      # auth.json copied here; no user config
export XDG_CACHE_HOME=/tmp/opencode/s1-oc/cache
export XDG_STATE_HOME=/tmp/opencode/s1-oc/state
export OPENCODE_PASSWORD=spike-pass-1              # Basic auth for the local v2 API
opencode serve --port 4141
```

Runs are driven over the v2 HTTP API against that kept-alive server; `GET /api/event` (SSE) is recorded
independently of the plugin's own log. Each run starts a fresh server (fresh plugin state) and a fresh
session.

```bash
python3 /tmp/opencode/s1-oc/run.py busy 8 \
  "Reply with exactly TURN-ONE-OK and nothing else." \
  'Run the shell command `seq 1 40` with no other arguments, then reply with its output and nothing else.' \
  S1-busy-a
```

The driver makes exactly these calls:

```text
POST /api/session                          {"title":"spike S1-busy-a"}   -> session id
POST /api/session/<id>/prompt              {"text": <turn 1>}            (the turn that arms the plugin)
   ... wait for that session's session.execution.succeeded ...
POST /api/session/<id>/prompt              {"text": <turn 2>}            (the new user message, sent 15-25 ms later)
GET  /api/event                            (SSE, all session.* events)
GET  /api/session/<id>/message?order=asc   (transcript)
GET  /api/session/<id>/inbox               (pending inbox items)
```

`opencode run --standalone` was not used: it exits at the end of its turn (archived spike §6.1), so it
cannot send a second user message while a turn is in flight. The HTTP driver is the one that can, and it
uses the same `prompt` endpoint (and the same default `delivery`) the TUI uses. The TUI sends prompts as
`delivery: "steer"`; a prompt posted without `delivery` was recorded by the server as
`delivery: "steer"` too.

## S1.3 Control — the same resume while the session is idle

`SPIKE_DELAY_EVENTS=1`; after turn 1's `session.execution.succeeded` the driver creates a second (unused)
session, whose `session.created` is the one later event that fires the resume. Event order, relative to
that `session.created`:

```text
session.created (second session)                        (the counter event, later=1)
synthetic-start later=1
synthetic-resolved ms=5 delivery=steer
session.inbox.enqueued  msg_spike_…  item=synthetic  delivery=steer  text='SPIKE-RESUME'
session.execution.started
session.inbox.delivered msg_spike_…
session.step.started … / session.text.ended … / session.step.ended … / session.execution.succeeded
```

The call resolved in 5 ms with `delivery: "steer"` and started a new execution 4 ms after it was
enqueued; turn 1 had ended 31 ms earlier and nothing was running. This reproduces archived-spike §2.2 on
2.3.2 in this setup; it is the baseline for the busy case below.

## S1.4 The busy case — primary run (`S1-busy-a`)

Session `ses_e039b9183ffeRubeDarJBe30nV`, `SPIKE_DELAY_EVENTS=8`. Turn 1 answered `TURN-ONE-OK`; the
driver posted the second user message 23 ms after turn 1's `session.execution.succeeded`. Times are the
bus events' own `created` fields, in milliseconds after that second user message was enqueued (`later` is
the plugin's counter of events after arming):

```text
t(ms)  event
    0  session.inbox.enqueued  item=user        delivery=steer  "Run the shell command `seq 1 40` …"   later=0
    5  session.execution.started                                                                       later=1
   18  session.inbox.delivered                    (the user message)                                  later=2
  510  session.step.started                      msg_…h4kPJt  (turn 2, step 1)
  649  synthetic-start                           later=8
  655  session.inbox.enqueued  item=synthetic  delivery=steer  "SPIKE-RESUME"                          later=9
  656  synthetic-resolved                        ms=7
  801  session.step.ended                        msg_…h4kPJt
  814  session.inbox.delivered                    msg_spike_…  (the synthetic)                       later=16
 1243  session.step.started                      msg_…oibKb5  (turn 2, step 2 — still the same execution)
 1620  session.step.ended                        msg_…oibKb5
 1641  session.execution.succeeded               (turn 2 ends, later=29)
```

No `session.execution.failed`, no `session.execution.interrupted`, no `session.inbox.delivery.changed`,
no `session.idle`; the `synthetic` call returned the admitted item:

```json
{"id":"msg_spike_1795235806132","sessionID":"ses_e039b9183ffeRubeDarJBe30nV",
 "type":"synthetic","payload":{"text":"SPIKE-RESUME"},"delivery":"steer"}
```

The transcript (`GET /api/session/<id>/message?order=asc`) shows where the synthetic landed:

```text
user       Reply with exactly TURN-ONE-OK and nothing else.
assistant  TURN-ONE-OK
idle       succeeded
user       Run the shell command `seq 1 40` with no other arguments, then reply with its output and nothing else.
assistant  … tool shell {"command":"seq 1 40"} …          (finish: tool-calls)
synthetic  SPIKE-RESUME
assistant  "I'm not sure what \"SPIKE-RESUME\" refers to — there's no pending task I can see. …"
idle       succeeded
```

The assistant step after the synthetic reasoned: *"The user sent \"SPIKE-RESUME\". This seems like a test
of whether I follow instructions …"*. In this run the model answered the block instead of the user's
pending request, i.e. the block replaced the user's expected next step. The synthetic never left that
execution: the session's inbox was empty afterwards and no new `session.execution.started` followed it
before the turn's end.

## S1.5 Repeat and the last-step case

**Repeat (`S1-busy-b`, same shape, `seq 1 20`).** Same ordering: synthetic enqueued at t=675 ms (later=8,
mid step 1, tool call in flight), delivered at t=1074 ms right after that step ended, and a second step
(t=1699 ms) of the *same* execution answered the block. No rejection, no `session.inbox.delivery.changed`.

**Resume during the last streaming step (`S1-busy-finalstep`, `SPIKE_DELAY_EVENTS=24`).** The counter put
the synthetic in the middle of step 2, which was streaming the final answer:

```text
t(ms)  event
    0  session.inbox.enqueued  item=user       "Run the shell command `seq 1 20` …"
    5  session.execution.started
   16  session.inbox.delivered   (user)
  441  session.step.started      (step 1, tool call)
 1144  session.step.ended
 1575  session.step.started      (step 2, streaming)
 1590  session.inbox.enqueued  item=synthetic  delivery=steer   (synthetic-resolved ms=6)
 1689  session.text.ended       '1\n2\n…\n20'   (step 2 would have finished here)
 1700  session.step.ended
 1718  session.inbox.delivered  msg_spike_…
 2131  session.step.started      (step 3 — the same execution does not end)
 3277  session.step.started      (step 4)
 4561  session.execution.succeeded
```

So even a steer admitted while the turn's last step was streaming is delivered at that step's end and
starts another step of the same execution; the turn is extended rather than ended, and the block is not
parked for the next turn.

An accidental extra case, `S1-busy-late` (`SPIKE_DELAY_EVENTS=30`), fired the counter two events after
turn 2's `session.execution.succeeded`. By then the session was idle, and that run behaved exactly like
the control: admitted as `delivery: "steer"`, `session.execution.started` within the same ms.

## S1.6 Verdict and decision

**Verdict: interleaved.** A `resume: true` synthetic admitted while a turn is running is not rejected and
not parked until the next turn end. OpenCode admits it as a `steer` inbox item (the call resolves in
5–9 ms with `{"delivery":"steer"}`, the same value it returns while idle), and delivers it at the next
step boundary of the running execution: `session.inbox.delivered` arrives after the in-flight
`session.step.ended` and before that turn's `session.execution.succeeded`, and the same execution starts
another step that sees the text. When the session is idle it starts a new execution at once.

**Decision: D5 as written.** A verifier block may be sent as its own `synthetic({resume: true})` when the
verifier finishes, without the fallback that keeps the block for the next turn end.

The observed consequence is worth stating in `docs/opencode-rules.md` alongside D5: the block enters the
user's running turn as the newest message, and in all three busy runs the model answered it instead of
producing the final answer the user's message asked for. That is the owner's accepted trade-off in D5
("the running one still resumes when it finishes, even if the user sent a new message meanwhile"); it is
not a rejection and needs no queueing change.

## S1.7 Limits

- Exercised on OpenCode `v2.3.2` with one model (`ollama-cloud/deepseek-v4.1-flash:1017`) and the `build`
  agent. The admission rule is host-side (`Session.Inbox.Delivery` is `steer | queue`); a `queue`
  delivery would behave differently, but the default and the TUI's choice are both `steer`.
- The resume text was the literal `SPIKE-RESUME` (the brief's value), so model behaviour after delivery
  varies; only the delivery position is claimed here.
- Not exercised: a synthetic admitted at the exact moment the execution transitions to `succeeded`.
  The `S1-busy-late` run landed just after it and behaved as the idle control; the `S1-busy-finalstep`
  run landed just before it and extended the turn.
- Artifacts, all throwaway: `/tmp/opencode/s1-oc/{oc.sh,run.py,spike-plugin/}`, per-run
  `<run>.plugin.jsonl` (every bus event with the counter), `<run>.sse.jsonl`, `<run>.events.txt`,
  `<run>.messages.json`, `<run>.inbox.json` for `S1-idle`, `S1-busy-a`, `S1-busy-b`,
  `S1-busy-finalstep`, `S1-busy-late`.
