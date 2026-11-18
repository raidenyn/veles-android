// Tests for the OpenCode hooked-rules plugin. No wall clock: the kill timer is a fake the test fires,
// and the engine is a fake `spawn` that answers from a script.
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { EventEmitter } from "node:events"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"

import plugin, {
  ENGINE_PATH,
  NOTICE,
  createHandlers,
  createPlugin,
  runEngine,
} from "./index.js"

const DIR = "/work/repo"
const FAIL_NOTICE_TEXT =
  "Rule hooks failed: read the hooked rules listed in AGENTS.md by hand at the moments they name."

// ---- fakes ----------------------------------------------------------------------------------------------

/** A fake timer: `setTimer` records, `fire()` runs every live callback once, `clearTimer` cancels. */
function fakeTimers() {
  const timers = []
  return {
    timers,
    setTimer(fn, ms) {
      const timer = { fn, ms, cleared: false }
      timers.push(timer)
      return timer
    },
    clearTimer(timer) {
      timer.cleared = true
    },
    fire() {
      for (const timer of timers) if (!timer.cleared) timer.fn()
    },
    live() {
      return timers.filter((timer) => !timer.cleared)
    },
  }
}

/**
 * A fake `spawn`. `script(call)` answers each call with one of:
 *   {code, stdout}   the child exits after its stdin is closed
 *   {error}          the child emits an "error" event (python missing)
 *   {throws}         spawn itself throws
 *   {hang: true}     the child never finishes (only the timer ends it)
 *   {stdinError}     stdin emits an "error" event, then the rest of the response plays
 *   {endThrows}      stdin.end throws synchronously
 *   {noStdin}        the child has no stdin
 */
function fakeSpawn(script) {
  const calls = []
  const waiters = []
  function spawn(command, args, options) {
    const call = { command, args, options, input: "", event: null, child: null }
    calls.push(call)
    for (const waiter of waiters.splice(0)) waiter()
    const response = typeof script === "function" ? script(call) : script
    if (response && response.throws) throw response.throws
    const child = new EventEmitter()
    child.killed = false
    child.signals = []
    child.kill = (signal) => {
      child.killed = true
      child.signals.push(signal)
    }
    child.stdout = new EventEmitter()
    child.stderr = new EventEmitter()
    child.stdin = new EventEmitter()
    child.stdin.write = (data) => {
      call.input += data
    }
    child.stdin.end = (data) => {
      if (data !== undefined) call.input += data
      if (response && response.endThrows) throw new Error("write EPIPE")
      try {
        call.event = JSON.parse(call.input)
      } catch {
        call.event = null
      }
      queueMicrotask(() => {
        if (response && response.stdinError) child.stdin.emit("error", new Error("write EPIPE"))
        if (!response || response.hang) return
        if (response.error) return child.emit("error", response.error)
        if (response.stdout && response.stdout.length > 0) {
          child.stdout.emit("data", Buffer.from(response.stdout))
        }
        child.emit("close", response.code ?? 0, null)
      })
    }
    call.child = child
    if (response && response.noStdin) child.stdin = null
    return child
  }
  spawn.calls = calls
  spawn.events = () => calls.map((call) => call.event)
  spawn.called = (count) =>
    new Promise((resolve) => {
      const check = () => (calls.length >= count ? resolve() : waiters.push(check))
      check()
    })
  return spawn
}

const answer = (decision, text, entries = []) => ({
  code: 0,
  stdout: JSON.stringify({ decision, text, entries }),
})
const entry = (name, writeId) => ({ rule: [name, "f".repeat(64)], write_id: writeId })

/** An event stream that yields nothing and ends only when the host aborts it. */
async function* quietStream(signal) {
  if (signal && !signal.aborted) {
    await new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true }))
  }
}

/**
 * A fake host context; `sessions` maps session id -> info (with an optional parentID). `streams` is a list
 * of stream factories `(signal) => asyncIterable`, one per `event.subscribe` call (later calls get a quiet
 * stream); `events` is shorthand for one stream that yields them and then waits for the abort.
 */
function fakeCtx({ sessions = {}, events = [], streams = null, syntheticError = null } = {}) {
  const hooks = {}
  const queue = streams
    ? streams.slice()
    : [
        async function* (signal) {
          yield* events
          yield* quietStream(signal)
        },
      ]
  const ctx = {
    location: { directory: DIR },
    synthetics: [],
    hooks,
    disposed: [],
    subscribeCalls: 0,
    signals: [],
    tool: {
      hook: async (name, handler) => {
        hooks[name] = handler
        return { dispose: async () => ctx.disposed.push(name) }
      },
    },
    session: {
      hook: async (name, handler) => {
        hooks[`session.${name}`] = handler
        return { dispose: async () => ctx.disposed.push(`session.${name}`) }
      },
      get: async ({ sessionID }) => (sessionID in sessions ? sessions[sessionID] : { id: sessionID }),
      synthetic: async (message) => {
        if (syntheticError) throw syntheticError
        ctx.synthetics.push(message)
      },
    },
    event: {
      subscribe: ({ signal } = {}) => {
        ctx.subscribeCalls += 1
        ctx.signals.push(signal)
        const factory = queue.length > 0 ? queue.shift() : quietStream
        return factory(signal)
      },
    },
  }
  return ctx
}

/** A fake `sleep`: records each delay and returns at once; no real time passes. */
function fakeSleep() {
  const delays = []
  const sleep = async (ms) => {
    delays.push(ms)
  }
  sleep.delays = delays
  return sleep
}

/** Boot the real plugin against the fakes; returns everything a test needs. */
async function boot({ script, sessions, events, streams, env = {}, syntheticError } = {}) {
  const timers = fakeTimers()
  const spawn = fakeSpawn(script ?? { code: 0, stdout: "" })
  const sleep = fakeSleep()
  const ctx = fakeCtx({ sessions, events, streams, syntheticError })
  const instance = createPlugin({
    spawn,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    env,
    sleep,
  })
  const dispose = await instance.setup(ctx)
  return { ctx, spawn, timers, sleep, dispose, hooks: ctx.hooks }
}

const shellBefore = (command, sessionID = "ses_a") => ({
  tool: "shell",
  sessionID,
  agent: "build",
  messageID: "msg_1",
  id: "call_1",
  input: { command },
})

function shellAfter(command, { exit = 0, status = "completed", sessionID = "ses_a" } = {}) {
  const content = []
  return {
    tool: "shell",
    sessionID,
    agent: "build",
    messageID: "msg_1",
    id: "call_1",
    input: { command },
    status,
    result: { content, metadata: { status: "completed", truncated: false, exit }, output: {} },
  }
}

// ---- runEngine -------------------------------------------------------------------------------------------

test("runEngine runs python3 -I on the engine with the plain format and the event on stdin", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn(answer("context", "hello", [entry("a.md", 3)]))
  const event = { event: "prompt", session: "s", cwd: DIR, prompt: "/opsx:apply" }
  const result = await runEngine(event, { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer })
  assert.deepEqual(result, { decision: "context", text: "hello", entries: [entry("a.md", 3)] })
  const [call] = spawn.calls
  assert.equal(call.command, "python3")
  assert.deepEqual(call.args, ["-I", ENGINE_PATH, "hook", "--format", "plain"])
  assert.equal(call.options.cwd, DIR)
  assert.deepEqual(call.event, event)
  assert.equal(timers.timers.length, 1)
  assert.equal(timers.timers[0].ms, 5000)
  assert.equal(timers.timers[0].cleared, true, "the kill timer is cleared on completion")
})

test("the engine path is resolved from the plugin directory, not from the cwd", () => {
  const expected = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "agent_rules.py")
  assert.equal(ENGINE_PATH, expected)
  assert.ok(fs.existsSync(ENGINE_PATH))
})

test("runEngine: empty stdout with exit 0 is a pass, not a failure", async () => {
  const timers = fakeTimers()
  const result = await runEngine(
    { event: "compacted", session: "s", cwd: DIR },
    { spawn: fakeSpawn({ code: 0, stdout: "" }), ...timers, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.deepEqual(result, { decision: "pass", text: "", entries: [] })
})

test("runEngine: a missing python (error event) is null", async () => {
  const timers = fakeTimers()
  const error = Object.assign(new Error("spawn python3 ENOENT"), { code: "ENOENT" })
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn: fakeSpawn({ error }), setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.equal(result, null)
  assert.equal(timers.live().length, 0)
})

test("runEngine: spawn throwing synchronously is null", async () => {
  const timers = fakeTimers()
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn: fakeSpawn({ throws: new Error("boom") }), setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.equal(result, null)
})

test("runEngine: a child that never finishes is killed by the fake timer and gives null", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ hang: true })
  const pending = runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  await spawn.called(1)
  assert.equal(timers.live().length, 1)
  timers.fire()
  assert.equal(await pending, null)
  assert.deepEqual(spawn.calls[0].child.signals, ["SIGKILL"])
})

test("runEngine: a nonzero exit is null even when stdout holds a decision", async () => {
  const timers = fakeTimers()
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn: fakeSpawn({ ...answer("deny", "x"), code: 1 }), setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.equal(result, null)
})

test("runEngine: undecodable or misshapen output is null", async () => {
  for (const stdout of [
    "not json",
    "[]",
    '{"decision":"allow","text":"x","entries":[]}',
    '{"decision":"deny","text":5,"entries":[]}',
    '{"decision":"deny","text":"x","entries":"no"}',
    '{"decision":"context","text":"x","entries":[{"rule":["a","b"],"write_id":"1"}]}',
    // whitespace-only output is not the empty pass
    " ",
    "\n",
    // entries must carry the exact tuple and a non-negative safe integer id
    '{"decision":"deny","text":"x","entries":[{"write_id":1}]}',
    '{"decision":"deny","text":"x","entries":[{"rule":"ab","write_id":1}]}',
    '{"decision":"deny","text":"x","entries":[{"rule":["a"],"write_id":1}]}',
    '{"decision":"deny","text":"x","entries":[{"rule":["a",2],"write_id":1}]}',
    '{"decision":"deny","text":"x","entries":[{"rule":["a","b"],"write_id":-1}]}',
    '{"decision":"deny","text":"x","entries":[{"rule":["a","b"],"write_id":1.5}]}',
    '{"decision":"deny","text":"x","entries":[{"rule":["a","b"],"write_id":9007199254740993}]}',
    '{"decision":"deny","text":"x","entries":[{"rule":["a","b"],"write_id":1,"extra":true}]}',
    '{"decision":"deny","text":"x","entries":[null]}',
    '{"decision":"deny","text":"x"}',
    '{"decision":"deny","entries":[]}',
    '{"decision":"deny","text":"x","entries":[],"extra":1}',
    '{"decision":"pass","text":"","entries":[]}',
  ]) {
    const timers = fakeTimers()
    const result = await runEngine(
      { event: "prompt", session: "s", cwd: DIR },
      { spawn: fakeSpawn({ code: 0, stdout }), setTimer: timers.setTimer, clearTimer: timers.clearTimer },
    )
    assert.equal(result, null, stdout)
  }
})

test("runEngine: invalid UTF-8 and a byte order mark are null", async () => {
  for (const stdout of [
    Buffer.from([0x7b, 0xff, 0x7d]),
    Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from('{"decision":"deny","text":"x","entries":[]}')]),
  ]) {
    const timers = fakeTimers()
    const result = await runEngine(
      { event: "prompt", session: "s", cwd: DIR },
      { spawn: fakeSpawn({ code: 0, stdout }), setTimer: timers.setTimer, clearTimer: timers.clearTimer },
    )
    assert.equal(result, null)
  }
})

test("runEngine: a valid multi-byte response decodes", async () => {
  const timers = fakeTimers()
  const text = "caf\u00e9 \u2014 \u{1f4a1}"
  const stdout = Buffer.from(JSON.stringify({ decision: "context", text, entries: [entry("a.md", 0)] }))
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn: fakeSpawn({ code: 0, stdout }), setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.equal(result.text, text)
})

test("runEngine: a stdin error event is null and the child is killed", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ stdinError: true, hang: true })
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.equal(result, null)
  assert.deepEqual(spawn.calls[0].child.signals, ["SIGKILL"])
  assert.equal(timers.live().length, 0)
})

test("runEngine: an early exit 0 with no output after a stdin error is still null", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ stdinError: true, code: 0, stdout: "" })
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.equal(result, null)
  assert.equal(spawn.calls[0].child.killed, true)
})

test("runEngine: stdin.end throwing is null, the child is killed and no timer is left", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ endThrows: true, hang: true })
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.equal(result, null)
  assert.deepEqual(spawn.calls[0].child.signals, ["SIGKILL"])
  assert.equal(timers.live().length, 0)
})

test("runEngine: a child without stdin is null and killed", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ noStdin: true, hang: true })
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.equal(result, null)
  assert.deepEqual(spawn.calls[0].child.signals, ["SIGKILL"])
})

test("runEngine: a spawn error event also kills the child", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ error: new Error("ENOENT") })
  assert.equal(
    await runEngine({ event: "prompt", session: "s", cwd: DIR }, { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer }),
    null,
  )
  assert.deepEqual(spawn.calls[0].child.signals, ["SIGKILL"])
})

// ---- execute.before ---------------------------------------------------------------------------------------

test("deny stops call: execute.before throws exactly the engine text", async () => {
  const { hooks, spawn } = await boot({ script: answer("deny", "Read the rule first.", [entry("r.md", 1)]) })
  await assert.rejects(hooks["execute.before"](shellBefore("git commit -m x")), (error) => {
    assert.ok(error instanceof Error)
    assert.equal(error.message, "Read the rule first.")
    return true
  })
  assert.deepEqual(spawn.events(), [
    { event: "tool-before", session: "ses_a", cwd: DIR, command: "git commit -m x" },
  ])
})

test("execute.before: a pass, a failure and a stray context all let the call through", async () => {
  for (const script of [
    { code: 0, stdout: "" },
    { error: new Error("ENOENT") },
    { code: 3, stdout: "" },
    { code: 0, stdout: "garbage" },
    answer("context", "ignored: before has no output channel"),
  ]) {
    const { hooks } = await boot({ script })
    assert.equal(await hooks["execute.before"](shellBefore("git status")), undefined)
  }
})

test("execute.before: a malformed deny does not stop the call", async () => {
  for (const stdout of [
    '{"decision":"deny","text":"x","entries":[{"write_id":1}]}',
    '{"decision":"deny","text":"x"}',
    " ",
    Buffer.from([0xff]),
  ]) {
    const { hooks } = await boot({ script: { code: 0, stdout } })
    assert.equal(await hooks["execute.before"](shellBefore("git commit")), undefined)
  }
})

test("execute.after: whitespace-only or invalid UTF-8 output appends the notice", async () => {
  for (const stdout of ["\n", Buffer.from([0xc3, 0x28])]) {
    const { hooks } = await boot({ script: { code: 0, stdout } })
    const input = shellAfter("ls")
    await hooks["execute.after"](input)
    assert.deepEqual(input.result.content, [{ type: "text", text: FAIL_NOTICE_TEXT }])
  }
})

test("execute.before: a timeout passes silently", async () => {
  const { hooks, spawn, timers } = await boot({ script: { hang: true } })
  const pending = hooks["execute.before"](shellBefore("git commit"))
  await spawn.called(1)
  timers.fire()
  assert.equal(await pending, undefined)
})

test("execute.before ignores tools other than shell", async () => {
  const { hooks, spawn } = await boot()
  await hooks["execute.before"]({ ...shellBefore("x"), tool: "read", input: { path: "a" } })
  await hooks["execute.before"]({ ...shellBefore("x"), input: {} })
  assert.equal(spawn.calls.length, 0)
})

// ---- execute.after ----------------------------------------------------------------------------------------

test("context appends the engine text to result.content", async () => {
  const { hooks, spawn } = await boot({ script: answer("context", "RULE TEXT", [entry("r.md", 4)]) })
  const input = shellAfter("openspec archive x")
  await hooks["execute.after"](input)
  assert.deepEqual(input.result.content, [{ type: "text", text: "RULE TEXT" }])
  assert.deepEqual(spawn.events(), [
    { event: "tool-after", session: "ses_a", cwd: DIR, tool: "shell", command: "openspec archive x", ok: true },
  ])
})

test("a failing shell command (metadata.exit 1) is sent as not ok", async () => {
  const { hooks, spawn } = await boot()
  await hooks["execute.after"](shellAfter("openspec archive x", { exit: 1 }))
  assert.equal(spawn.events()[0].ok, false)
  assert.equal(spawn.events()[0].command, "openspec archive x")
})

test("a shell call that never completed is sent as not ok and gets no output append", async () => {
  const { hooks, spawn } = await boot()
  const input = { ...shellAfter("x"), status: "error", result: undefined, error: { message: "denied" } }
  await hooks["execute.after"](input)
  assert.equal(spawn.events()[0].ok, false)
})

test("a skill call sends the skill id and appends its context", async () => {
  const { hooks, spawn } = await boot({ script: answer("context", "SKILL RULE", [entry("r.md", 2)]) })
  const input = {
    tool: "skill",
    sessionID: "ses_a",
    agent: "build",
    id: "c",
    input: { id: "opsx:apply" },
    status: "completed",
    result: { content: [], output: {}, metadata: {} },
  }
  await hooks["execute.after"](input)
  assert.deepEqual(spawn.events(), [
    { event: "tool-after", session: "ses_a", cwd: DIR, tool: "skill", skill: "opsx:apply" },
  ])
  assert.deepEqual(input.result.content, [{ type: "text", text: "SKILL RULE" }])
})

test("a read call sends the path as the model wrote it", async () => {
  const { hooks, spawn } = await boot()
  await hooks["execute.after"]({
    tool: "read",
    sessionID: "ses_a",
    agent: "build",
    id: "c",
    input: { path: ".agents/skills/opsx-apply/SKILL.md" },
    status: "completed",
    result: { content: [], output: {}, metadata: {} },
  })
  assert.deepEqual(spawn.events(), [
    {
      event: "tool-after",
      session: "ses_a",
      cwd: DIR,
      tool: "read",
      path: ".agents/skills/opsx-apply/SKILL.md",
    },
  ])
})

test("execute.after ignores other tools and a read or skill call that did not complete", async () => {
  const { hooks, spawn } = await boot()
  await hooks["execute.after"]({ tool: "patch", sessionID: "s", input: {}, status: "completed", result: { content: [] } })
  await hooks["execute.after"]({ tool: "read", sessionID: "s", input: { path: "a" }, status: "error", error: {} })
  await hooks["execute.after"]({ tool: "skill", sessionID: "s", input: { id: "a" }, status: "error", error: {} })
  assert.equal(spawn.calls.length, 0)
})

test("spawn failure passes and appends the notice", async () => {
  const { hooks } = await boot({ script: { error: Object.assign(new Error("ENOENT"), { code: "ENOENT" }) } })
  const input = shellAfter("openspec archive x")
  await hooks["execute.after"](input)
  assert.equal(NOTICE, FAIL_NOTICE_TEXT)
  assert.deepEqual(input.result.content, [{ type: "text", text: FAIL_NOTICE_TEXT }])
})

test("timeout passes with the notice (fake timer)", async () => {
  const { hooks, spawn, timers } = await boot({ script: { hang: true } })
  const input = shellAfter("openspec archive x")
  const pending = hooks["execute.after"](input)
  await spawn.called(1)
  timers.fire()
  await pending
  assert.deepEqual(input.result.content, [{ type: "text", text: FAIL_NOTICE_TEXT }])
})

test("nonzero exit passes with the notice", async () => {
  const { hooks } = await boot({ script: { code: 2, stdout: "" } })
  const input = shellAfter("openspec archive x")
  await hooks["execute.after"](input)
  assert.deepEqual(input.result.content, [{ type: "text", text: FAIL_NOTICE_TEXT }])
})

test("bad JSON passes with the notice", async () => {
  const { hooks } = await boot({ script: { code: 0, stdout: "{nope" } })
  const input = shellAfter("openspec archive x")
  await hooks["execute.after"](input)
  assert.deepEqual(input.result.content, [{ type: "text", text: FAIL_NOTICE_TEXT }])
})

test("empty stdout with exit 0 is a pass with no notice", async () => {
  const { hooks } = await boot({ script: { code: 0, stdout: "" } })
  const input = shellAfter("ls")
  await hooks["execute.after"](input)
  assert.deepEqual(input.result.content, [])
})

test("the engine's own pass-with-notice response is appended like any context", async () => {
  const { hooks } = await boot({ script: answer("context", FAIL_NOTICE_TEXT) })
  const input = shellAfter("ls")
  await hooks["execute.after"](input)
  assert.deepEqual(input.result.content, [{ type: "text", text: FAIL_NOTICE_TEXT }])
})

test("injection failure voids: one void per entry, each with its own write id", async () => {
  const { hooks, spawn } = await boot({
    script: (call) =>
      call.event && call.event.event === "void"
        ? { code: 0, stdout: "" }
        : answer("context", "TEXT", [entry("a.md", 7), entry("b.md", 9)]),
    env: { VELES_AGENT_RULES_FAIL_INJECTION: "1" },
  })
  const input = shellAfter("openspec archive x")
  await hooks["execute.after"](input)
  assert.deepEqual(input.result.content, [], "nothing was appended")
  const events = spawn.events()
  assert.equal(events.length, 3)
  assert.equal(events[0].event, "tool-after")
  assert.deepEqual(events.slice(1), [
    { event: "void", session: "ses_a", cwd: DIR, write_ids: [7] },
    { event: "void", session: "ses_a", cwd: DIR, write_ids: [9] },
  ])
})

test("injection failure without the switch: a result with no content array also voids", async () => {
  const { hooks, spawn } = await boot({
    script: (call) =>
      call.event && call.event.event === "void"
        ? { code: 0, stdout: "" }
        : answer("context", "TEXT", [entry("a.md", 5)]),
  })
  const input = { ...shellAfter("x"), result: { metadata: { exit: 0 } } }
  await hooks["execute.after"](input)
  assert.deepEqual(spawn.events().map((event) => event.event), ["tool-after", "void"])
  assert.deepEqual(spawn.events()[1].write_ids, [5])
})

test("the injection switch is read from the injected env and is off by default", async () => {
  const { hooks } = await boot({ script: answer("context", "TEXT", [entry("a.md", 1)]), env: {} })
  const input = shellAfter("x")
  await hooks["execute.after"](input)
  assert.equal(input.result.content.length, 1)
})

test("an empty-text context with entries still goes through the injection step and voids on failure", async () => {
  const { hooks, spawn } = await boot({
    script: (call) =>
      call.event && call.event.event === "void"
        ? { code: 0, stdout: "" }
        : answer("context", "", [entry("a.md", 31), entry("b.md", 32)]),
    env: { VELES_AGENT_RULES_FAIL_INJECTION: "1" },
  })
  const input = shellAfter("x")
  await hooks["execute.after"](input)
  assert.deepEqual(input.result.content, [])
  assert.deepEqual(spawn.events().slice(1), [
    { event: "void", session: "ses_a", cwd: DIR, write_ids: [31] },
    { event: "void", session: "ses_a", cwd: DIR, write_ids: [32] },
  ])
})

test("an empty-text context with entries and a working injection appends nothing and voids nothing", async () => {
  const { hooks, spawn } = await boot({ script: answer("context", "", [entry("a.md", 33)]) })
  const input = shellAfter("x")
  await hooks["execute.after"](input)
  assert.deepEqual(input.result.content, [])
  assert.equal(spawn.calls.length, 1)
})

test("an empty-text context with no entries injects and voids nothing, even with the switch", async () => {
  const { hooks, spawn, ctx } = await boot({
    script: answer("context", "", []),
    env: { VELES_AGENT_RULES_FAIL_INJECTION: "1" },
  })
  const input = shellAfter("x")
  await hooks["execute.after"](input)
  await hooks["session.prompt"]({ sessionID: "ses_a", prompt: { text: "/x", files: [] } })
  assert.deepEqual(input.result.content, [])
  assert.deepEqual(ctx.synthetics, [])
  assert.equal(spawn.calls.length, 2)
})

// ---- session keys -----------------------------------------------------------------------------------------

test("a child session maps to its root parent as the session and itself as the agent", async () => {
  const sessions = {
    ses_child: { id: "ses_child", parentID: "ses_mid" },
    ses_mid: { id: "ses_mid", parentID: "ses_root" },
    ses_root: { id: "ses_root" },
  }
  const { hooks, spawn } = await boot({ sessions })
  await hooks["execute.before"](shellBefore("git commit", "ses_child"))
  await hooks["execute.before"](shellBefore("git commit", "ses_root"))
  const [child, root] = spawn.events()
  assert.equal(child.session, "ses_root")
  assert.equal(child.agent, "ses_child")
  assert.equal(root.session, "ses_root")
  assert.equal("agent" in root, false)
})

/** Sessions for a chain `s0 -> s1 -> ... -> s<length>` (the last is the root). */
function chain(length) {
  const sessions = {}
  for (let index = 0; index <= length; index += 1) {
    sessions[`s${index}`] = index === length ? { id: `s${index}` } : { id: `s${index}`, parentID: `s${index + 1}` }
  }
  return sessions
}

const UNVERIFIED = {
  "a null lookup result": { ses_a: null },
  "a string lookup result": { ses_a: "oops" },
  "an array lookup result": { ses_a: [] },
  "a numeric parentID": { ses_a: { id: "ses_a", parentID: 5 } },
  "an empty parentID": { ses_a: { id: "ses_a", parentID: "" } },
  "a parent that is itself": { ses_a: { id: "ses_a", parentID: "ses_a" } },
  "a parent cycle": {
    ses_a: { id: "ses_a", parentID: "ses_b" },
    ses_b: { id: "ses_b", parentID: "ses_c" },
    ses_c: { id: "ses_c", parentID: "ses_a" },
  },
  "a malformed ancestor": { ses_a: { id: "ses_a", parentID: "ses_b" }, ses_b: 7 },
  "a chain past the depth cap": { ...chain(15), ses_a: { id: "ses_a", parentID: "s0" } },
}

for (const [name, sessions] of Object.entries(UNVERIFIED)) {
  test(`an unverifiable chain (${name}) skips the engine: before passes silently`, async () => {
    const { hooks, spawn } = await boot({ sessions })
    assert.equal(await hooks["execute.before"](shellBefore("git commit")), undefined)
    assert.equal(spawn.calls.length, 0)
  })

  test(`an unverifiable chain (${name}) skips the engine: after and prompt carry the notice`, async () => {
    const { hooks, spawn, ctx } = await boot({ sessions })
    const input = shellAfter("openspec archive x")
    await hooks["execute.after"](input)
    assert.deepEqual(input.result.content, [{ type: "text", text: FAIL_NOTICE_TEXT }])
    await hooks["session.prompt"]({ sessionID: "ses_a", prompt: { text: "/opsx:apply", files: [] } })
    assert.equal(ctx.synthetics.length, 1)
    assert.equal(ctx.synthetics[0].text, FAIL_NOTICE_TEXT)
    assert.equal(spawn.calls.length, 0)
  })
}

test("an unverifiable chain skips the compaction event", async () => {
  const { spawn, ctx } = await boot({
    sessions: { ses_a: { id: "ses_a", parentID: 5 } },
    events: [
      { type: "session.compaction.started", data: { sessionID: "ses_a" } },
      { type: "session.compaction.started", data: { sessionID: "ses_ok" } },
    ],
  })
  await spawn.called(1)
  assert.deepEqual(spawn.events(), [{ event: "compacted", session: "ses_ok", cwd: DIR }])
  assert.ok(ctx.subscribeCalls >= 1)
})

test("a chain of exactly the depth cap (16 lookups) resolves to its root", async () => {
  const sessions = { ...chain(14), ses_a: { id: "ses_a", parentID: "s0" } }
  const { hooks, spawn } = await boot({ sessions })
  await hooks["execute.before"](shellBefore("git commit"))
  assert.deepEqual(spawn.events()[0].session, "s14")
  assert.equal(spawn.events()[0].agent, "ses_a")
})

test("a rejected session lookup skips the engine and is retried next time", async () => {
  const { hooks, spawn, ctx } = await boot()
  let fail = true
  ctx.session.get = async ({ sessionID }) => {
    if (fail) throw new Error("gone")
    return { id: sessionID }
  }
  const input = shellAfter("x")
  await hooks["execute.after"](input)
  assert.equal(spawn.calls.length, 0)
  assert.deepEqual(input.result.content, [{ type: "text", text: FAIL_NOTICE_TEXT }])
  fail = false
  await hooks["execute.before"](shellBefore("git status"))
  assert.equal(spawn.events()[0].session, "ses_a")
})

test("a verified key is cached and the host is asked once", async () => {
  const { hooks, ctx } = await boot({ sessions: { ses_c: { id: "ses_c", parentID: "ses_r" } } })
  const real = ctx.session.get
  let gets = 0
  ctx.session.get = async (arg) => {
    gets += 1
    return real(arg)
  }
  await hooks["execute.before"](shellBefore("git status", "ses_c"))
  await hooks["execute.before"](shellBefore("git status", "ses_c"))
  await hooks["execute.before"](shellBefore("git status", "ses_c"))
  assert.equal(gets, 2, "one lookup per hop, then the cache")
})

/** Let pending promise callbacks run (microtasks only; no timer). */
async function flush() {
  for (let index = 0; index < 20; index += 1) await Promise.resolve()
}

test("racing cold lookups cache only a verified result and never overwrite it", async () => {
  const ctx = fakeCtx()
  const gates = []
  ctx.session.get = () => new Promise((resolve) => gates.push(resolve))
  const race = createHandlers(ctx, { engine: async () => null })
  const first = race.keyOf("ses_x")
  const second = race.keyOf("ses_x") // a concurrent cold lookup
  await flush()
  assert.equal(gates.length, 2)
  gates[1]("not an object") // the later lookup is malformed
  assert.equal(await second, null)
  gates[0]({ id: "ses_x" }) // the earlier lookup verifies a root
  assert.deepEqual(await first, { session: "ses_x" })
  assert.deepEqual(await race.keyOf("ses_x"), { session: "ses_x" }, "the verified key is cached")
  assert.equal(gates.length, 2, "and served without another lookup")
})

test("racing cold lookups that both verify keep the first cached value", async () => {
  const ctx = fakeCtx()
  const gates = []
  ctx.session.get = () => new Promise((resolve) => gates.push(resolve))
  const race = createHandlers(ctx, { engine: async () => null })
  const first = race.keyOf("ses_x")
  const second = race.keyOf("ses_x")
  await flush()
  gates[1]({ id: "ses_x" }) // the second lookup verifies a root first
  assert.deepEqual(await second, { session: "ses_x" })
  gates[0]({ id: "ses_x", parentID: "ses_p" }) // the first now sees a parent: a different answer
  await flush()
  gates[2]({ id: "ses_p" }) // the hop to the parent verifies a different root
  assert.deepEqual(await first, { session: "ses_x" }, "the cached value wins over the later, different one")
  assert.deepEqual(await race.keyOf("ses_x"), { session: "ses_x" })
})

// ---- compaction -------------------------------------------------------------------------------------------

test("compaction maps: session.compaction.started sends compacted with the child key", async () => {
  const sessions = { ses_child: { id: "ses_child", parentID: "ses_root" } }
  const events = [
    { type: "session.compaction.delta", data: { sessionID: "ses_child" } },
    { type: "session.compaction.started", data: { sessionID: "ses_child", reason: "manual", inputID: "i" } },
    { type: "session.compaction.ended", data: { sessionID: "ses_child" } },
  ]
  const { spawn } = await boot({ sessions, events })
  await spawn.called(1)
  assert.deepEqual(spawn.events(), [
    { event: "compacted", session: "ses_root", agent: "ses_child", cwd: DIR },
  ])
})

test("compaction of a root session sends the session key only", async () => {
  const { spawn } = await boot({
    events: [{ type: "session.compaction.started", data: { sessionID: "ses_root" } }],
  })
  await spawn.called(1)
  assert.deepEqual(spawn.events(), [{ event: "compacted", session: "ses_root", cwd: DIR }])
})

test("a compaction event after a stream failure is processed (rejected iterator, then a good stream)", async () => {
  const { spawn, sleep, ctx } = await boot({
    streams: [
      async function* () {
        throw new Error("stream broke")
      },
      async function* (signal) {
        yield { type: "session.compaction.started", data: { sessionID: "ses_root" } }
        yield* quietStream(signal)
      },
    ],
  })
  await spawn.called(1)
  assert.deepEqual(spawn.events(), [{ event: "compacted", session: "ses_root", cwd: DIR }])
  assert.deepEqual(sleep.delays, [1000])
  assert.equal(ctx.subscribeCalls, 2)
})

test("a subscription that throws synchronously is retried", async () => {
  const { spawn, sleep } = await boot({
    streams: [
      () => {
        throw new Error("no stream")
      },
      async function* (signal) {
        yield { type: "session.compaction.started", data: { sessionID: "ses_root" } }
        yield* quietStream(signal)
      },
    ],
  })
  await spawn.called(1)
  assert.deepEqual(sleep.delays, [1000])
})

test("a stream that ends on its own is subscribed again", async () => {
  const { spawn, sleep } = await boot({
    streams: [
      async function* () {},
      async function* (signal) {
        yield { type: "session.compaction.started", data: { sessionID: "ses_root" } }
        yield* quietStream(signal)
      },
    ],
  })
  await spawn.called(1)
  assert.deepEqual(sleep.delays, [1000])
})

test("the watcher backs off 1s, 2s, 4s ... capped at 30s, and stops on abort", async () => {
  const controller = new AbortController()
  const delays = []
  const ctx = fakeCtx({
    streams: Array.from({ length: 20 }, () => () => {
      throw new Error("down")
    }),
  })
  const sleep = async (ms) => {
    delays.push(ms)
    if (delays.length === 8) controller.abort()
  }
  const handlers = createHandlers(ctx, { engine: async () => null, sleep })
  await handlers.watch(controller.signal)
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000])
  assert.equal(ctx.subscribeCalls, 8)
})

test("the backoff starts over once an event arrives", async () => {
  const controller = new AbortController()
  const delays = []
  const failing = () => {
    throw new Error("down")
  }
  const ctx = fakeCtx({
    streams: [
      failing,
      failing,
      async function* () {
        yield { type: "other", data: {} }
        throw new Error("broke after an event")
      },
      failing,
    ],
  })
  const sleep = async (ms) => {
    delays.push(ms)
    if (delays.length === 4) controller.abort()
  }
  await createHandlers(ctx, { engine: async () => null, sleep }).watch(controller.signal)
  assert.deepEqual(delays, [1000, 2000, 1000, 2000])
})

test("a failing event handler does not end the stream", async () => {
  const bad = { type: "session.compaction.started" }
  Object.defineProperty(bad, "data", {
    get() {
      throw new Error("bad event")
    },
  })
  const { spawn } = await boot({
    events: [bad, { type: "session.compaction.started", data: { sessionID: "ses_root" } }],
  })
  await spawn.called(1)
  assert.equal(spawn.events()[0].event, "compacted")
})

test("setup returns an async disposer that aborts the stream, ends the watcher and disposes the hooks", async () => {
  const { ctx, dispose, spawn } = await boot({
    events: [{ type: "session.compaction.started", data: { sessionID: "ses_root" } }],
  })
  assert.equal(typeof dispose, "function")
  await spawn.called(1)
  const [signal] = ctx.signals
  assert.equal(signal.aborted, false)
  await dispose()
  assert.equal(signal.aborted, true)
  assert.deepEqual(ctx.disposed, ["session.prompt", "execute.after", "execute.before"])
  const calls = ctx.subscribeCalls
  await flush()
  assert.equal(ctx.subscribeCalls, calls, "no re-subscription after the disposer")
})

test("the disposer ends a watcher that has already re-subscribed after a failure", async () => {
  const { ctx, dispose, sleep } = await boot({
    streams: [
      () => {
        throw new Error("down")
      },
    ],
  })
  await flush()
  assert.deepEqual(sleep.delays, [1000])
  assert.equal(ctx.subscribeCalls, 2) // the second subscription is parked on its quiet stream
  await dispose()
  assert.equal(ctx.signals[1].aborted, true)
  assert.equal(ctx.subscribeCalls, 2)
})

// ---- prompt -----------------------------------------------------------------------------------------------

test("prompt registers: setup registers all three hooks and the event stream", async () => {
  const { hooks } = await boot()
  assert.equal(typeof hooks["execute.before"], "function")
  assert.equal(typeof hooks["execute.after"], "function")
  assert.equal(typeof hooks["session.prompt"], "function")
})

test("a /opsx:apply prompt sends a prompt event and injects the engine text as a synthetic message", async () => {
  const { hooks, spawn, ctx } = await boot({ script: answer("context", "APPLY RULES", [entry("r.md", 6)]) })
  await hooks["session.prompt"]({
    sessionID: "ses_a",
    messageID: "msg_9",
    prompt: { text: "/opsx:apply my-change", files: [] },
    delivery: "immediate",
  })
  assert.deepEqual(spawn.events(), [
    { event: "prompt", session: "ses_a", cwd: DIR, prompt: "/opsx:apply my-change" },
  ])
  assert.equal(ctx.synthetics.length, 1)
  const [message] = ctx.synthetics
  assert.equal(message.sessionID, "ses_a")
  assert.equal(message.text, "APPLY RULES")
  assert.equal(message.resume, false)
  assert.match(message.id, /^msg_[0-9a-zA-Z_]+$/)
})

test("synthetic message ids are unique", async () => {
  const { hooks, ctx } = await boot({ script: answer("context", "T", [entry("r.md", 1)]) })
  for (let index = 0; index < 5; index += 1) {
    await hooks["session.prompt"]({ sessionID: "s", prompt: { text: "/x", files: [] } })
  }
  assert.equal(new Set(ctx.synthetics.map((message) => message.id)).size, 5)
})

test("a prompt that is a pass injects nothing", async () => {
  const { hooks, ctx } = await boot({ script: { code: 0, stdout: "" } })
  await hooks["session.prompt"]({ sessionID: "s", prompt: { text: "hello", files: [] } })
  assert.deepEqual(ctx.synthetics, [])
})

test("a prompt hook failure injects the notice", async () => {
  const { hooks, ctx } = await boot({ script: { code: 1, stdout: "" } })
  await hooks["session.prompt"]({ sessionID: "s", prompt: { text: "hello", files: [] } })
  assert.equal(ctx.synthetics.length, 1)
  assert.equal(ctx.synthetics[0].text, FAIL_NOTICE_TEXT)
  assert.equal(ctx.synthetics[0].resume, false)
})

test("a rejected synthetic injection voids each entry and does not throw", async () => {
  const { hooks, spawn } = await boot({
    script: (call) =>
      call.event && call.event.event === "void"
        ? { code: 0, stdout: "" }
        : answer("context", "T", [entry("a.md", 11), entry("b.md", 12)]),
    syntheticError: new Error("host refused"),
  })
  await hooks["session.prompt"]({ sessionID: "s", prompt: { text: "/x", files: [] } })
  assert.deepEqual(spawn.events().slice(1), [
    { event: "void", session: "s", cwd: DIR, write_ids: [11] },
    { event: "void", session: "s", cwd: DIR, write_ids: [12] },
  ])
})

test("the injection switch also fails the prompt injection", async () => {
  const { hooks, ctx, spawn } = await boot({
    script: (call) =>
      call.event && call.event.event === "void"
        ? { code: 0, stdout: "" }
        : answer("context", "T", [entry("a.md", 21)]),
    env: { VELES_AGENT_RULES_FAIL_INJECTION: "1" },
  })
  await hooks["session.prompt"]({ sessionID: "s", prompt: { text: "/x", files: [] } })
  assert.deepEqual(ctx.synthetics, [])
  assert.deepEqual(spawn.events().map((event) => event.event), ["prompt", "void"])
})

test("a prompt without text sends nothing", async () => {
  const { hooks, spawn } = await boot()
  await hooks["session.prompt"]({ sessionID: "s", prompt: { files: [] } })
  assert.equal(spawn.calls.length, 0)
})

// ---- module shape -----------------------------------------------------------------------------------------

test("the default export is a V2 plugin { id, setup }", () => {
  assert.equal(typeof plugin.id, "string")
  assert.equal(typeof plugin.setup, "function")
  assert.equal(typeof createHandlers, "function")
})

// ---- real engine round trip -------------------------------------------------------------------------------

const python = spawnSync("python3", ["--version"], { encoding: "utf8" })
const havePython = !python.error && python.status === 0

test(
  "real engine: the plain wire format round-trips through the actual script",
  { skip: havePython ? false : "python3 is not available on this machine" },
  async () => {
    const repo = fs.mkdtempSync(path.join(os.tmpdir(), "veles-hooked-rules-"))
    try {
      const git = (...args) => {
        const run = spawnSync("git", args, { cwd: repo, encoding: "utf8" })
        assert.equal(run.status, 0, run.stderr)
      }
      git("init", "-q")
      fs.mkdirSync(path.join(repo, ".agents", "hooked-rules"), { recursive: true })
      fs.writeFileSync(
        path.join(repo, ".agents", "hooked-rules", "stub.md"),
        [
          "---",
          'pointer: "Read the stub rule."',
          'skills: ["opsx:apply"]',
          'gates: ["^git commit"]',
          "---",
          "STUB RULE BODY",
          "",
        ].join("\n"),
      )
      // The default spawn and the real engine; the host context and the kill timer are fakes.
      const fake = fakeTimers()
      const timerOptions = { setTimer: fake.setTimer, clearTimer: fake.clearTimer }
      const ctx = fakeCtx()
      ctx.location.directory = repo
      await createPlugin({ env: {}, ...timerOptions }).setup(ctx)

      // A gated command is denied with the rule text ...
      await assert.rejects(ctx.hooks["execute.before"](shellBefore("git commit -m x")), (error) => {
        assert.match(error.message, /STUB RULE BODY/)
        return true
      })
      // ... the skill moment delivers the rule through the output append ...
      const skill = {
        tool: "skill",
        sessionID: "ses_a",
        input: { id: "opsx:apply" },
        status: "completed",
        result: { content: [], output: {}, metadata: {} },
      }
      await ctx.hooks["execute.after"](skill)
      assert.equal(skill.result.content.length, 1)
      assert.match(skill.result.content[0].text, /STUB RULE BODY/)
      // ... and the gate now passes.
      assert.equal(await ctx.hooks["execute.before"](shellBefore("git commit -m x")), undefined)

      // A voided delivery makes the next gated call denied again, so the void wire format round-trips.
      const failing = fakeCtx()
      failing.location.directory = repo
      await createPlugin({ env: { VELES_AGENT_RULES_FAIL_INJECTION: "1" }, ...timerOptions }).setup(failing)
      const other = { ...skill, sessionID: "ses_b", result: { content: [], output: {}, metadata: {} } }
      await failing.hooks["execute.after"](other)
      assert.deepEqual(other.result.content, [])
      await assert.rejects(failing.hooks["execute.before"](shellBefore("git commit -m x", "ses_b")), /STUB RULE BODY/)
    } finally {
      fs.rmSync(repo, { recursive: true, force: true })
    }
  },
)
