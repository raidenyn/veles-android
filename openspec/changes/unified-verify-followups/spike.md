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
   ... wait for that session's session.execution.succeeded in the SSE stream ...
POST /api/session/<id>/prompt              {"text": <turn 2>}            (the new user message, sent 15-25 ms later)
GET  /api/event                            (SSE, all session.* events)
GET  /api/session/<id>/message?order=asc   (transcript)
GET  /api/session/<id>/inbox               (pending inbox items)
```

The timing logic a rerun needs, in order: start a fresh server with `SPIKE_DELAY_EVENTS` and a fresh
`SPIKE_LOG`; subscribe to `GET /api/event`; create the session and post turn 1; block until the SSE
record for this session's first `session.execution.succeeded` arrives (the driver polls the captured
stream every 10 ms); immediately post turn 2 with the same endpoint; keep the server alive and the
stream open until no event has arrived for 8 s; then read the transcript and the inbox. Turn 2 must still
be running when the counter fires: the delay is counted in bus events, so it must be smaller than the
events the running turn emits before its `session.execution.succeeded` (turn 2 emitted 29, 38 and 70
events in these runs; a delay of 8 is safely inside, and `S1-busy-finalstep` used 24). The plugin excerpt
above is the whole decision logic; the per-run log lines the tables quote (`phase`, `later`, `type`) are
what it writes.

`opencode run --standalone` was not used: it exits at the end of its turn (archived spike
`openspec/changes/archive/2026-11-20-hook-workflow-rules/spike.md` §6.1), so it cannot send a second
user message while a turn is in flight. The HTTP driver is the one that can, and it uses the documented
`session.prompt` operation. A prompt posted without `delivery` was recorded by the server as
`delivery: "steer"`; no TUI request was captured in this spike (the 2.3.2 binary's own submit path calls
`session.prompt({ …, delivery: "steer" })`, but that is read from the binary, not observed here).

## S1.3 Control — the same resume while the session is idle

`SPIKE_DELAY_EVENTS=1`; after turn 1's `session.execution.succeeded` the driver creates a second (unused)
session, whose `session.created` is the one later event that fires the resume. Event order, relative to
that `session.created`:

```text
session.created (second session)                        (the counter event, logged later=0)
synthetic-start later=1
synthetic-resolved ms=5 delivery=steer
session.inbox.enqueued  msg_spike_…  item=synthetic  delivery=steer  text='SPIKE-RESUME'
session.execution.started
session.inbox.delivered msg_spike_…
session.step.started … / session.text.ended … / session.step.ended … / session.execution.succeeded
```

The call resolved in 5 ms with `delivery: "steer"` and started a new execution 4 ms after it was
enqueued; turn 1 had ended 31 ms earlier and nothing was running. This reproduces archived spike
`openspec/changes/archive/2026-11-20-hook-workflow-rules/spike.md` §2.2 on 2.3.2 in this setup; it is the
baseline for the busy case below.

## S1.4 The busy case — primary run (`S1-busy-a`)

Session `ses_e039b9183ffeRubeDarJBe30nV`, `SPIKE_DELAY_EVENTS=8`. Turn 1 answered `TURN-ONE-OK`; the
driver posted the second user message 23 ms after turn 1's `session.execution.succeeded`. Times are the
bus events' own `created` fields, in milliseconds after that second user message was enqueued. The
`later` column is the plugin's event counter as logged at the top of its handler — the plugin logs the
event and then increments, so the column is the count *before* that event; the first event after arming
therefore shows `later=0`, and `synthetic-start later=8` is the incremented count at the eighth event.

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

**Repeat (`S1-busy-b`, same shape, `seq 1 20`).** Same ordering: the synthetic enqueued at t=675 ms
(mid step 1, tool call in flight), delivered at t=1074 ms right after that step ended, and a second step
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
parked for the next turn. Step 2 had already produced the user's answer (`1…20`) before the delivery, so
in this run the block came after the user's request was answered.

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

The observed consequence is worth stating in `docs/opencode-rules.md` alongside D5: the block is
delivered at the next step boundary of the running turn. If it arrives before the turn's final step, the
model may answer it instead of the user's request (`S1-busy-a`, `S1-busy-b`, where the requested final
answer was never produced); if it arrives during the final step, the user's answer is produced first and
the turn is extended (`S1-busy-finalstep`). That is the owner's accepted trade-off in D5 ("the running
one still resumes when it finishes, even if the user sent a new message meanwhile"); it is not a
rejection and needs no queueing change.

## S1.7 Limits

- Exercised on OpenCode `v2.3.2` with one model (`ollama-cloud/deepseek-v4.1-flash:1017`) and the `build`
  agent. The admission rule is host-side (`Session.Inbox.Delivery` is `steer | queue`); a `queue`
  delivery would behave differently, but the observed default for a prompt posted without `delivery` is
  `steer`. No TUI request was captured in a run; the 2.3.2 binary's submit path passes
  `delivery: "steer"`, but that is read from the binary rather than observed.
- The resume text was the literal `SPIKE-RESUME` (the brief's value), so model behaviour after delivery
  varies; only the delivery position is claimed here.
- Not exercised: a synthetic admitted at the exact moment the execution transitions to `succeeded`.
  The `S1-busy-late` run landed just after it and behaved as the idle control; the `S1-busy-finalstep`
  run landed just before it and extended the turn.
- Artifacts, all throwaway: `/tmp/opencode/s1-oc/{oc.sh,run.py,spike-plugin/}`, per-run
  `<run>.plugin.jsonl` (every bus event with the counter), `<run>.sse.jsonl`, `<run>.events.txt`,
  `<run>.messages.json`, `<run>.inbox.json` for `S1-idle`, `S1-busy-a`, `S1-busy-b`,
  `S1-busy-finalstep`, `S1-busy-late`.

## Acceptance — Task 10 real-session runs (2026-11-21)

Everything below comes from real sessions and hook runs on the coding server on 2026-11-21. The
clones were made from this branch (HEAD `7db9b870`) into `/tmp/opencode/t10-claude/` and
`/tmp/opencode/t10-oc/`; each clone's `origin` was retargeted to a local bare repository before any
push. No run touched this worktree's `.claude/`, `opencode.json`, plugins or git state: the Claude
clone kept the tracked `.claude/settings.json` and ran with `CLAUDE_CONFIG_DIR` holding only
`.credentials.json`; the OpenCode clone replaced its `opencode.json` with a disposable
`["./scripts/opencode-hooked-rules", "./debug-events"]` config and omitted the tracked
`opencode-rules` package, as in the archived recipe §6.1. `./debug-events` is a throwaway plugin that
only appends bus events; it is excluded from git.

| Tool | Version | Notes |
|---|---|---|
| Claude Code | `2.1.329` | headless `claude -p … --output-format stream-json --include-hook-events --dangerously-skip-permissions`; model resolved as `claude-opus-5-5` |
| OpenCode | `v2.3.2` | kept-alive `opencode serve --port 4151` driven over the v2 HTTP API (`--standalone` exits at the turn end and kills the detached verifier, archived §6.1); model `opencode/deepseek-v4.1-flash-free` (the isolated provider catalog did not offer the ollama-cloud model S1 used) |
| Node | `v26.13.1` shell; `v26.8.1` through the verifier's `path_prepend` | the supply-chain check pins 26.8.1 |
| Python | `3.14.4` | the engine, the verifier and the drivers |

Both clones were provisioned the way the fix texts say: `verify.py config set java_home
/home/dev/.jdk/zulu21`, `config set android_sdk /home/dev/.android-sdk`, `config set path_prepend
/home/dev/.local/node-26.8.1/bin`, and `cargo-deny`/`cargo-cyclonedx` on `PATH`. The missing
`cargo-deny` also served as the absent-tool scenario before it was installed.

**Branch defect found by the acceptance.** On the branch HEAD this acceptance started from, the
`--branch` gate failed `tooling.verify-tests`: `native-bridge/host/scripts/test_build_contract.py`
(`Documentation.test_docs_distinguish_production_host_from_scaffold`) required
`bridgeHostToolchainCheck`, `bridgeHostFormat`, `bridgeHostLint` and `bridgeHostTest` in
`native-bridge/host/AGENTS.md`, while this change's `docs/checks/check_agent_files.py` rejects exactly
those verifier-run commands there ("… is run by verify.py …; remove it from the doc"). The push
scenarios below first ran on a disposable clone carrying the clone-only predecessor
`204ec119 test(native-host): stop requiring verifier-run commands in the host docs check`, which
reduced that test's `TASKS` to `("bridgeHostBuild",)` — enough to exercise the gate, but it dropped the
reproducible-builds coverage. The landed fix is
`1b0c50c3 test(tooling): align the host docs test and require verify node modules`: it keeps all five
tasks checked in `docs/reproducible-builds.md` and adds `HOST_DOC_TASKS = ("bridgeHostBuild",)`, the
only command the host `AGENTS.md` must name (the four verifier-run tasks are documented in
`docs/reproducible-builds.md` and must not be repeated in the host `AGENTS.md`), so both checks agree. The same commit
adds `verify.node-modules` to `tooling.verify-tests`'s `requires`, because that command had run without
`verify/node_modules` and failed `verify/test/license-policy.test.mjs` with a spawn ENOENT.

### Claude Code 2.1.329

| Run | What it ran on | Observed | Measured | Result |
|---|---|---|---|---|
| C1a repeated block (session `c699781e`; prompt "do not change any files") | clone, `web-extension/src/version.ts` with `const unusedBrokenLint = 1;` | four Stops: three `{"decision":"block"}` (attempts 1–3 of 3), then `{"systemMessage":"…not blocking again (attempt 4 of 3)"}` | 423.3 s total, 3 blocks | PASS for the block text and the per-problem cap; the model obeyed the no-edit instruction and did not fix the tree |
| C1b block → fix → pass (session `9438a49e`) | same broken file, prompt without the no-edit clause | Stop 1 blocked with the lint tail, its re-run command and the log path; the model deleted the unused constant and ended; Stop 2 printed nothing | block time **not preserved** (the run directory was pruned; it included the first supply-chain run, 73.9 s, and the lint — see C1a's 423.3 s over 3 blocks); the fixed-tree pass was **1.1 s** | PASS |
| C2 absent tool (session `6708545d`) | clean tree, `cargo-deny` removed from `PATH` | Stop 1 blocked: `supply-chain: NOT RUN (supply-chain.supply-chain: path-tool: cargo-deny …)`, `fix: Run ./gradlew installCargoDeny …`, `rerun: cd . && verify/verify-supply-chain.sh` | **3.0 s** | PASS |
| C4a push without a branch pass (session `854e9e55`) | clone at `7db9b870`, local bare remote, no `branch-pass.json` | both `git push origin HEAD:refs/heads/acceptance-claude` calls denied by the pre-push rule before git ran; the second identical call denied identically | engine pre-tool gate, no verifier run | PASS (twice) |
| C5 ref-deletion pushes | bare remote with `throwaway-a/-b/-c` | `git push origin --delete throwaway-a`, `git push origin -d throwaway-b`, `git push origin :throwaway-c` all ran and deleted the refs, with no pass | engine pre-tool gate | PASS (all three forms) |
| C4b push after the pass (session `854e9e55`) | clone at `204ec119`, `build/verify/branch-pass.json` `{head: 204ec119…, base: 043e9aa…, nodes: [docs, native-host, protocol, supply-chain, tooling]}` | `git push origin HEAD:refs/heads/acceptance-claude` ran: `* [new branch] HEAD -> acceptance-claude` | branch gate **698.5 s** (exit 0), run detached under `systemd-run --user` | PASS |
| C3 linked worktree (hook dispatch, `t10-wt-dispatch`) | the clone's `Stop` command run with a hand-built event `{"session_id": "t10-wt-dispatch", "cwd": "/tmp/opencode/t10-claude/wt"}`; the worktree is at `42f7c2ff` (one commit on the clone), with the broken lint | the launcher dispatched to `/tmp/opencode/t10-claude/wt/scripts/verify.py`; the block's log path and failing file are under `…/wt/`; the worktree's own table and changes decided it | **450.9 s** | **hook dispatch PASS; real-session move UNVERIFIED** — the event was hand-built: a headless `-p` session's Stop event carries the launch `cwd`, and the worktree-entry tool was not exercised, so no real session entered that worktree. The OpenCode O3 row is the real-session move evidence; a real-session Claude run of the worktree's full gate is a follow-up |

C1b block text (trimmed):

```text
{"decision": "block", "reason": "verify: the task gate found problems; fix them and stop again (attempt 1 of 3)

docs: PASS (remembered)
native-host: PASS (remembered)
  NOT RUN (branch gate only: native-host.build-contract)
protocol: NOT RUN (nothing planned: no check and no related test for the changed files)
supply-chain: PASS
tooling: PASS (remembered)
web-extension: FAIL
  web-extension.lint: FAIL (exit 1)
    rerun: cd web-extension && npm run lint
    log: /tmp/opencode/t10-claude/repo/build/verify/runs/20261121T065721-1493948/web-extension.lint.1.log
    tail:
      /tmp/opencode/t10-claude/repo/web-extension/src/version.ts
        67:7  error  'unusedBrokenLint' is assigned a value but never used  @typescript-eslint/no-unused-vars
steps: supply-chain.supply-chain OK; web-extension.format OK; web-extension.lint FAIL; …" }
```

C4a/C4b (trimmed):

```text
TOOL: Bash {"command": "git push origin HEAD:refs/heads/acceptance-claude"}
RESULT: PreToolUse:Bash hook error: Commit everything first: the branch pass needs a clean tree, … then push.
        (… no branch pass for HEAD 7db9b8705bb2)
ASSISTANT: The hook blocked the first push. You asked for the same command twice, so I'm running it again.
TOOL: Bash {"command": "git push origin HEAD:refs/heads/acceptance-claude"}
RESULT: PreToolUse:Bash hook error: Commit everything first: … no branch pass for HEAD 7db9b8705bb2
… after `python3 -I scripts/verify.py --branch` (exit 0, 698.5 s):
TOOL: Bash {"command": "git push origin HEAD:refs/heads/acceptance-claude"}
RESULT: To /tmp/opencode/t10-claude/remote.git
        * [new branch]        HEAD -> acceptance-claude
```

### OpenCode v2.3.2

| Run | What it ran on | Observed | Measured | Result |
|---|---|---|---|---|
| O1 broken lint → block → fixed tree (session `ses_e02f2b41…`) | clone, `web-extension/src/version.ts` with the unused constant | `session.execution.succeeded`, then the plugin's `synthetic({resume:true})` with the block summary; the model removed the line; the next turn end's verifier run wrote no synthetic | turn 1 end at 4.3 s; block synthetic at **252.1 s** (cold: supply-chain ran); fixed-tree verifier run **0.2 s**, silent | PASS |
| O2 new user message while the verifier runs (session `ses_e02edd9d…`) | same broken tree; turn 2 = `sleep 120` | the synthetic was enqueued 25 s into turn 2 (shell tool in flight), delivered immediately after `session.step.ended` at 123 s, and the same execution started three more steps before `succeeded` at 148 s | verifier **25 s** for this warm run | PASS, interleaved exactly as S1 predicted (not rejected, not parked) |
| O3 session moved into the linked worktree (session `ses_e02e98ed…`) | `session.move({directory: /tmp/opencode/t10-oc/wt})`, worktree at `dfb6e490` with its own `docs/verify.md` change and broken lint | the worktree's `verify.py` ran and wrote its own `build/verify/last-run.json`; the block names `docs: FAIL … (docs/verify.md, web-extension/src/version.ts)` from the worktree | **175.7 s** | PASS for the worktree run. The first turn end after `move` still ran in `/tmp/opencode/t10-oc/repo` (a move takes effect at the next delivery boundary) and reported `verification already running; nothing was checked`, contending with the O2 session's in-flight run; the next turn end ran the worktree's verifier. That boundary is a known host limit, not a verifier result |
| O4 push without a branch pass (session `ses_e02e61de…`) | clone, no `branch-pass.json` | both pushes denied with the pre-push rule text before git ran | engine pre-tool gate | PASS (twice) |
| O5 push after the pass (session `ses_e02ab470…`) | clone, clean tree, no `branch-pass.json`; the marker change is committed as `83733ede test(extension): add the acceptance marker` | the session ran `python3 -I scripts/verify.py --branch` itself (run `20261121T090615`, record written 09:09:18) and then `git push origin HEAD:refs/heads/accept-oc`: `73ef2ad4..83733ede  HEAD -> accept-oc`; a second push reported `Everything up-to-date` | session branch run **~183 s** (start to record), exit 0; a detached verification of the same commit **247.4 s**, exit 0; record `{base 73ef2ad4, head 83733ede, nodes [protocol, supply-chain, web-extension]}` | PASS — the record came from a clean tree with a real base and a non-empty node list, unlike the earlier dirty-tree attempt |

O2 event order (the plugin's own bus log, ms after turn 2's user message was enqueued):

```text
     0  session.inbox.enqueued  item=user  "Run the shell command `sleep 120` …"
    61  session.execution.started
   742  session.step.started                  (the shell tool call starts)
 25200  session.inbox.enqueued  item=synthetic  "verify: the task gate found problems; …"
122027  session.step.ended                    (the sleep ends)
122027  session.inbox.delivered  msg_00024f…  (delivered at the step boundary, same execution)
123433  session.step.started                  (a new step of the same execution answers the block)
 …      three more steps of that execution
147812  session.execution.succeeded
```

No `session.execution.failed`, no `session.inbox.delivery.changed`, no rejection: the block was
admitted as `delivery: "steer"` and parked until the in-flight step ended, exactly as S1's verdict.

### Summary

| Scenario | Claude Code `2.1.329` | OpenCode `v2.3.2` |
|---|---|---|
| Broken lint blocks with the failing step, its tail, re-run command and log path; the fixed tree then passes | PASS (C1a block text and per-problem cap; C1b block → fix → silent pass) | PASS (O1 block summary via `synthetic({resume:true})`, then a silent 0.2 s run) |
| An absent required tool → `NOT RUN` with its fix | PASS (C2, `cargo-deny` absent) | not part of the OpenCode scenarios |
| A session moved into a linked worktree at a different revision runs that worktree's verifier | **hook dispatch PASS; real-session move UNVERIFIED** (C3: a hand-built Stop event dispatched to `wt/scripts/verify.py`; no real Claude session entered the worktree) | PASS (O3, `session.move`, the worktree's own verifier) |
| `git push` without a branch pass is denied | PASS, twice (C4a) | PASS, twice (O4) |
| A ref-deletion push to a throwaway branch passes without a proof | PASS (C5, `--delete`, `-d` and `:ref`) | not part of the OpenCode scenarios |
| The same push passes after a complete `--branch` run | PASS (C4b; branch gate 698.5 s, exit 0) | PASS (O5; session run ~183 s, record base `73ef2ad4` / head `83733ede` with three nodes, then the push) |
| A new user message while the verifier runs | not applicable (the host is not driven this way) | PASS (O2, interleaved: admitted as `steer`, delivered at the next step boundary, the same execution extended) |

**No product behaviour changed (tooling only; the device and browser rule's exemption applies).**
Every run exercised the `Stop` hooks, the verifier (`scripts/verify.py`), the hooked-rules engine, the
branch-pass record and the OpenCode plugin. No application, web-extension, native-host or installer
behaviour was touched, so the real-device/browser run the rule asks for does not apply.

The acceptance also found one branch blocker, recorded above: the branch's own `--branch` gate failed
`tooling.verify-tests` until `native-bridge/host/scripts/test_build_contract.py` stopped requiring the
four verifier-run commands in `native-bridge/host/AGENTS.md` (the change's `check_agent_files.py`
forbids them there). The landed fix is `1b0c50c3`: `HOST_DOC_TASKS = ("bridgeHostBuild",)` for the host
`AGENTS.md` while all five tasks stay checked in `docs/reproducible-builds.md`; the clone-only
predecessor `204ec119` (which reduced `TASKS` itself) shipped in the disposable clone only.

### Main worktree

The fixes landed as `1b0c50c3 test(tooling): align the host docs test and require verify node modules`
on this worktree, and `python3 -I scripts/verify.py --branch` then ran twice, detached, with the tree
clean except the pre-existing `tasks.md` modification (saved and restored around the runs so the
run could start from a clean tree). The first run exited **2** after **1042.2 s**: `docs`,
`native-host`, `protocol` and `tooling` passed, but `supply-chain` was `NOT RUN` because
`verify/verify-supply-chain.sh` looks for `build/verify-tools/cargo-deny/bin/cargo-deny` in the
worktree and that path was missing (the script had installed `cargo-cyclonedx` there itself). After
copying the pinned `cargo-deny 0.20.2` there, the second run exited **0** after **358.2 s** with
`docs`, `native-host`, `protocol`, `supply-chain` and `tooling` all PASS, and wrote:

```json
{"base": "043e9aa659aeb0de605f80be2a12c2fcd9bd7e56",
 "head": "1b0c50c3138ca629663bb7c38855f75161391f45",
 "nodes": ["docs", "native-host", "protocol", "supply-chain", "tooling"],
 "version": 1}
```

`git push -u origin HEAD:refs/heads/chore/unified-verify-followups` then ran and created the branch;
`git ls-remote origin` reports `1b0c50c3` for it:

```text
 * [new branch]        HEAD -> chore/unified-verify-followups
branch 'chore/unified-verify-followups' set up to track 'origin/chore/unified-verify-followups'.
```

This section was corrected after that push (review finding 1). The branch pass and push for the
correcting commit, whose only change is this file, are recorded in the task report; they follow the
same steps.

