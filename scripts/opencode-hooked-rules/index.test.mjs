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
  HANDLER_BUDGET_MS,
  KILL_ATTEMPTS,
  MAX_STDOUT_BYTES,
  NOTICE,
  VERIFY_BUDGET_MS,
  VERIFY_KILL_GRACE_MS,
  VERIFY_PATH,
  createHandlers,
  createPlugin,
  runEngine,
  runVerifier,
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
      for (const timer of timers.slice()) {
        if (timer.cleared || timer.fired) continue
        timer.fired = true
        timer.fn()
      }
    },
    live() {
      return timers.filter((timer) => !timer.cleared && !timer.fired)
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
 *   {kill}           (child, attempt) => result of kill(): false, a throw, or a synchronous "error" event
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
      // `{kill: (child, attempt) => ...}` scripts a kill that returns false, throws or emits an error
      return response && response.kill ? response.kill(child, child.signals.length) : true
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

const NEVER = Symbol("never answers")

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
function fakeCtx({
  sessions = {},
  events = [],
  streams = null,
  syntheticError = null,
  syntheticNever = false,
  disposeFails = null,
} = {}) {
  const hooks = {}
  const queue = streams
    ? streams.slice()
    : [
        async function* (signal) {
          yield* events
          yield* quietStream(signal)
        },
      ]
  const disposeOf = (name) => {
    if (name === disposeFails) throw new Error(`cannot dispose ${name}`)
    ctx.disposed.push(name)
  }
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
        return { dispose: async () => disposeOf(name) }
      },
    },
    session: {
      hook: async (name, handler) => {
        hooks[`session.${name}`] = handler
        return { dispose: async () => disposeOf(`session.${name}`) }
      },
      get: ({ sessionID }) => {
        if (sessions[sessionID] === NEVER) return new Promise(() => {}) // a lookup that never answers
        return Promise.resolve(sessionID in sessions ? sessions[sessionID] : { id: sessionID })
      },
      synthetic: async (message) => {
        if (syntheticError) throw syntheticError
        ctx.synthetics.push(message)
        if (syntheticNever) await new Promise(() => {}) // the host never answers
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
async function boot({
  script,
  sessions,
  events,
  streams,
  env = {},
  syntheticError,
  syntheticNever,
  disposeFails,
} = {}) {
  const timers = fakeTimers()
  const spawn = fakeSpawn(script ?? { code: 0, stdout: "" })
  const sleep = fakeSleep()
  const ctx = fakeCtx({ sessions, events, streams, syntheticError, syntheticNever, disposeFails })
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
  assert.equal(timers.timers[0].cleared, true, "the deadline timer is cleared once settled")
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

test("runEngine: a deny with empty or blank text is malformed (null)", async () => {
  for (const text of ["", " ", "\n\t"]) {
    const timers = fakeTimers()
    const result = await runEngine(
      { event: "tool-before", session: "s", cwd: DIR },
      { spawn: fakeSpawn(answer("deny", text, [entry("a.md", 1)])), setTimer: timers.setTimer, clearTimer: timers.clearTimer },
    )
    assert.equal(result, null, JSON.stringify(text))
  }
})

test("execute.before: a deny with empty text does not stop the call", async () => {
  const { hooks } = await boot({ script: answer("deny", "", []) })
  assert.equal(await hooks["execute.before"](shellBefore("git commit")), undefined)
})

test("runEngine: stdout over the cap is null and the child is killed", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ code: 0, stdout: Buffer.alloc(MAX_STDOUT_BYTES + 1, 0x20) })
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.equal(result, null)
  assert.deepEqual(spawn.calls[0].child.signals, ["SIGKILL"])
  assert.equal(MAX_STDOUT_BYTES, 1024 * 1024)
})

test("runEngine: stdout of exactly the cap still decodes", async () => {
  const timers = fakeTimers()
  const body = JSON.stringify({ decision: "deny", text: "x", entries: [] })
  const stdout = Buffer.concat([Buffer.from(body), Buffer.alloc(MAX_STDOUT_BYTES - body.length, 0x20)])
  assert.equal(stdout.length, MAX_STDOUT_BYTES)
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn: fakeSpawn({ code: 0, stdout }), setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.deepEqual(result, { decision: "deny", text: "x", entries: [] })
})

test("runEngine: an abort signal ends a hanging run with null and kills the child", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ hang: true })
  const controller = new AbortController()
  const pending = runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer, signal: controller.signal },
  )
  await spawn.called(1)
  controller.abort()
  assert.equal(await pending, null)
  assert.deepEqual(spawn.calls[0].child.signals, ["SIGKILL"])
  assert.equal(timers.timers[0].cleared, true)
})

test("runEngine: an already aborted signal spawns nothing", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ code: 0, stdout: "" })
  const controller = new AbortController()
  controller.abort()
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer, signal: controller.signal },
  )
  assert.equal(result, null)
  assert.equal(spawn.calls.length, 0)
})

/** A valid deny response whose `text` holds an invalid UTF-8 byte (0xff). */
const BAD_UTF8_DENY = Buffer.concat([
  Buffer.from('{"decision":"deny","text":"a'),
  Buffer.from([0xff]),
  Buffer.from('b","entries":[]}'),
])

test("the invalid UTF-8 fixture would parse under a lenient decoder", () => {
  const lenient = JSON.parse(new TextDecoder("utf-8").decode(BAD_UTF8_DENY))
  assert.equal(lenient.decision, "deny")
  assert.throws(() => new TextDecoder("utf-8", { fatal: true }).decode(BAD_UTF8_DENY))
})

test("runEngine: invalid UTF-8 and a byte order mark are null", async () => {
  for (const stdout of [
    BAD_UTF8_DENY,
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
  assert.equal(timers.timers[0].cleared, true)
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
  assert.equal(timers.timers[0].cleared, true)
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
    BAD_UTF8_DENY,
  ]) {
    const { hooks } = await boot({ script: { code: 0, stdout } })
    assert.equal(await hooks["execute.before"](shellBefore("git commit")), undefined)
  }
})

test("execute.after: whitespace-only or invalid UTF-8 output appends the notice", async () => {
  for (const stdout of ["\n", BAD_UTF8_DENY]) {
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

test("an empty-text context with entries goes through the real append and voids nothing on success", async () => {
  const { hooks, spawn } = await boot({ script: answer("context", "", [entry("a.md", 33)]) })
  const input = shellAfter("x")
  await hooks["execute.after"](input)
  assert.deepEqual(input.result.content, [{ type: "text", text: "" }], "the text is passed as it is")
  assert.deepEqual(spawn.events().map((event) => event.event), ["tool-after"])
})

test("an empty-text context with entries and no result.content voids every entry", async () => {
  const { hooks, spawn } = await boot({
    script: (call) =>
      call.event && call.event.event === "void"
        ? { code: 0, stdout: "" }
        : answer("context", "", [entry("a.md", 41), entry("b.md", 42)]),
  })
  const input = { ...shellAfter("x"), result: { metadata: { exit: 0 } } }
  await hooks["execute.after"](input)
  assert.deepEqual(spawn.events().slice(1), [
    { event: "void", session: "ses_a", cwd: DIR, write_ids: [41] },
    { event: "void", session: "ses_a", cwd: DIR, write_ids: [42] },
  ])
})

test("an empty-text context with entries and a rejecting synthetic voids every entry", async () => {
  const { hooks, spawn } = await boot({
    script: (call) =>
      call.event && call.event.event === "void"
        ? { code: 0, stdout: "" }
        : answer("context", "", [entry("a.md", 43)]),
    syntheticError: new Error("host refused"),
  })
  await hooks["session.prompt"]({ sessionID: "ses_a", prompt: { text: "/x", files: [] } })
  assert.deepEqual(spawn.events().map((event) => event.event), ["prompt", "void"])
  assert.deepEqual(spawn.events()[1].write_ids, [43])
})

test("an empty-text context with entries and a working synthetic injects the text as it is", async () => {
  const { hooks, spawn, ctx } = await boot({ script: answer("context", "", [entry("a.md", 44)]) })
  await hooks["session.prompt"]({ sessionID: "ses_a", prompt: { text: "/x", files: [] } })
  assert.equal(ctx.synthetics.length, 1)
  assert.equal(ctx.synthetics[0].text, "")
  assert.deepEqual(spawn.events().map((event) => event.event), ["prompt"])
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
  "a first hop with no id": { ses_a: {} },
  "a first hop with a numeric id": { ses_a: { id: 5 } },
  "a first hop with another session's id": { ses_a: { id: "ses_other" } },
  "a first hop with another session's record that names a parent": {
    ses_a: { id: "ses_other", parentID: "ses_r" },
  },
  "an ancestor with no id": { ses_a: { id: "ses_a", parentID: "ses_b" }, ses_b: {} },
  "an ancestor with another id": {
    ses_a: { id: "ses_a", parentID: "ses_b" },
    ses_b: { id: "ses_zzz" },
  },
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

test("the disposer disposes the hooks and resolves (bounded) when next() never settles and ignores the signal", async () => {
  const stuck = () => ({ [Symbol.asyncIterator]: () => ({ next: () => new Promise(() => {}) }) })
  const { ctx, dispose, timers } = await boot({ streams: [stuck] })
  await flush()
  let done = false
  const disposing = dispose().then(() => {
    done = true
  })
  await flush()
  assert.equal(ctx.signals[0].aborted, true)
  assert.deepEqual(ctx.disposed, ["session.prompt", "execute.after", "execute.before"])
  assert.equal(done, false, "waiting for the bounded timeout, not for the loop")
  const [bound] = timers.live()
  assert.equal(bound.ms, 1000)
  timers.fire()
  await disposing
  assert.equal(done, true)
})

test("the disposer resolves when an in-flight compaction lookup never answers", async () => {
  const { ctx, dispose, timers } = await boot({
    sessions: { ses_stuck: NEVER },
    events: [{ type: "session.compaction.started", data: { sessionID: "ses_stuck" } }],
  })
  await flush()
  assert.equal(timers.live().length, 1, "the compaction's budget timer")
  // The abort ends the stuck lookup's wait at once, so the disposer needs no timer to finish.
  await dispose()
  assert.deepEqual(ctx.disposed, ["session.prompt", "execute.after", "execute.before"])
  assert.equal(timers.live().length, 0)
})

test("the disposer clears its bound when the loop ends first", async () => {
  const { dispose, timers } = await boot()
  await flush()
  await dispose()
  assert.equal(timers.live().length, 0)
})

test("the disposer disposes every hook even when one dispose rejects", async () => {
  const { ctx, dispose } = await boot({ disposeFails: "execute.after" })
  await flush()
  await dispose()
  assert.deepEqual(ctx.disposed, ["session.prompt", "execute.before"])
})

// ---- engine process cleanup ---------------------------------------------------------------------------------

async function failedRun(script) {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ stdinError: true, hang: true, ...script })
  const result = await runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  return { result, timers, child: spawn.calls[0].child }
}

test("cleanup: a kill that returns false is retried until the child exits", async () => {
  const { result, timers, child } = await failedRun({ kill: (_child, attempt) => attempt >= 3 })
  assert.equal(result, null, "the result is settled at once")
  assert.equal(child.signals.length, 1)
  assert.equal(timers.live().length, 1, "an independent cleanup timer stays")
  timers.fire()
  assert.equal(child.signals.length, 2)
  timers.fire()
  assert.equal(child.signals.length, 3)
  child.emit("exit", null, "SIGKILL")
  assert.equal(timers.live().length, 0, "the child's exit clears the cleanup timer")
  timers.fire()
  assert.equal(child.signals.length, 3)
})

test("cleanup: a kill that throws is caught and retried", async () => {
  const { result, timers, child } = await failedRun({
    kill: () => {
      throw new Error("EPERM")
    },
  })
  assert.equal(result, null)
  assert.equal(child.signals.length, 1)
  timers.fire()
  assert.equal(child.signals.length, 2)
})

test("cleanup: an error event emitted synchronously from kill() does not re-enter or double-settle", async () => {
  const { result, timers, child } = await failedRun({
    kill: (self) => {
      self.emit("error", new Error("kill failed"))
      return true
    },
  })
  assert.equal(result, null)
  assert.equal(child.signals.length, 1, "no recursion: one attempt so far")
  timers.fire()
  assert.equal(child.signals.length, 2)
})

test("cleanup: attempts are bounded and the cleanup timer ends with them", async () => {
  const { timers, child } = await failedRun({ kill: () => false })
  for (let round = 0; round < 20; round += 1) timers.fire()
  assert.equal(child.signals.length, KILL_ATTEMPTS)
  assert.equal(timers.live().length, 0)
})

test("cleanup: a child that already exited is not killed again", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ hang: true })
  const pending = runEngine(
    { event: "prompt", session: "s", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  await spawn.called(1)
  spawn.calls[0].child.emit("exit", 1, null)
  spawn.calls[0].child.emit("close", 1, null)
  assert.equal(await pending, null)
  assert.deepEqual(spawn.calls[0].child.signals, [])
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

// ---- bounded host awaits (one budget per hook call) ---------------------------------------------------------

/** A promise with its resolve function, for host calls a test answers by hand. */
function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

/** Whether `promise` has settled after the pending microtasks ran. */
async function settled(promise) {
  let done = false
  promise.then(
    () => {
      done = true
    },
    () => {
      done = true
    },
  )
  await flush()
  return done
}

/** The budget timer of the first hook call: it is set before any engine run of that call. */
const budgetTimer = (timers) => timers.timers[0]

test("the handler budget is 5 seconds, one timer per hook call, cleared when the call ends", async () => {
  const { hooks, timers } = await boot({ script: answer("context", "T", [entry("a.md", 1)]) })
  await hooks["execute.after"](shellAfter("x"))
  assert.equal(HANDLER_BUDGET_MS, 5000)
  assert.deepEqual(timers.timers.map((timer) => timer.ms), [HANDLER_BUDGET_MS, 5000], "the budget, then the run")
  assert.equal(timers.live().length, 0)
})

test("budget: a session lookup that never settles lets execute.before proceed when the budget fires", async () => {
  const { hooks, spawn, timers } = await boot({ sessions: { ses_a: NEVER } })
  const pending = hooks["execute.before"](shellBefore("git commit"))
  assert.equal(await settled(pending), false, "held only until the budget")
  timers.fire()
  assert.equal(await pending, undefined)
  assert.equal(spawn.calls.length, 0)
})

test("budget: a session lookup that never settles gives execute.after the notice when the budget fires", async () => {
  const { hooks, spawn, timers } = await boot({ sessions: { ses_a: NEVER } })
  const input = shellAfter("openspec archive x")
  const pending = hooks["execute.after"](input)
  assert.equal(await settled(pending), false)
  timers.fire()
  await pending
  assert.deepEqual(input.result.content, [{ type: "text", text: FAIL_NOTICE_TEXT }])
  assert.equal(spawn.calls.length, 0)
})

test("budget: a session lookup that never settles lets the prompt proceed when the budget fires", async () => {
  const { hooks, spawn, timers, ctx } = await boot({ sessions: { ses_a: NEVER } })
  const pending = hooks["session.prompt"]({ sessionID: "ses_a", prompt: { text: "/x", files: [] } })
  assert.equal(await settled(pending), false)
  timers.fire()
  await pending
  assert.equal(spawn.calls.length, 0)
  assert.deepEqual(ctx.synthetics.map((message) => message.text), [FAIL_NOTICE_TEXT])
})

test("budget: a late lookup answer is ignored for the call that gave up on it", async () => {
  const { hooks, spawn, timers, ctx } = await boot()
  const lookup = deferred()
  ctx.session.get = () => lookup.promise
  const pending = hooks["execute.before"](shellBefore("git commit"))
  await flush()
  timers.fire()
  assert.equal(await pending, undefined)
  lookup.resolve({ id: "ses_a" })
  await flush()
  assert.equal(spawn.calls.length, 0, "no engine run after the call already proceeded")
})

test("budget: a synthetic injection that never settles lets the prompt proceed and voids nothing", async () => {
  const { hooks, spawn, timers, ctx } = await boot({
    script: answer("context", "RULES", [entry("a.md", 3)]),
    syntheticNever: true,
  })
  const pending = hooks["session.prompt"]({ sessionID: "ses_a", prompt: { text: "/x", files: [] } })
  await spawn.called(1)
  assert.equal(await settled(pending), false)
  timers.fire()
  await pending
  assert.equal(ctx.synthetics.length, 1)
  // The injection's outcome is unknown, so the credit stays (a documented stale delivery).
  assert.deepEqual(spawn.events().map((event) => event.event), ["prompt"])
})

test("budget: the notice's synthetic injection never holds the prompt past the budget", async () => {
  const { hooks, timers } = await boot({ script: { code: 1, stdout: "" }, syntheticNever: true })
  const pending = hooks["session.prompt"]({ sessionID: "ses_a", prompt: { text: "/x", files: [] } })
  assert.equal(await settled(pending), false)
  timers.fire()
  await pending
})

test("budget: voids share the remaining budget; a hanging void ends the call and no later void starts", async () => {
  let spawned = 0
  const { hooks, spawn, timers } = await boot({
    // `call.event` is parsed only after spawn, so the script answers by call order: the voids hang.
    script: () => {
      spawned += 1
      return spawned === 1 ? answer("context", "T", [entry("a.md", 7), entry("b.md", 9)]) : { hang: true }
    },
    env: { VELES_AGENT_RULES_FAIL_INJECTION: "1" },
  })
  const pending = hooks["execute.after"](shellAfter("openspec archive x"))
  await spawn.called(2)
  assert.equal(await settled(pending), false)
  // One budget, the tool-after run and the first void's run: no fresh budget per void.
  assert.equal(timers.timers.length, 3)
  // Fire only the budget: the void's own 5-second engine timer is not what ends the call.
  budgetTimer(timers).fn()
  await pending
  assert.deepEqual(spawn.events().map((event) => event.event), ["tool-after", "void"])
  assert.deepEqual(spawn.calls[1].child.signals, ["SIGKILL"], "the abandoned void is killed")
})

test("budget: a hanging engine run is cut by the budget, not by a fresh timer per run", async () => {
  const { hooks, spawn, timers } = await boot({ script: { hang: true } })
  const pending = hooks["execute.before"](shellBefore("git commit"))
  await spawn.called(1)
  budgetTimer(timers).fn()
  assert.equal(await pending, undefined)
  assert.deepEqual(spawn.calls[0].child.signals, ["SIGKILL"])
})

// ---- turn end (stop) ----------------------------------------------------------------------------------------

const succeeded = (sessionID) => ({ type: "session.execution.succeeded", data: { sessionID } })
const interrupted = (sessionID) => ({ type: "session.execution.interrupted", data: { sessionID, reason: "user" } })
const failed = (sessionID) => ({ type: "session.execution.failed", data: { sessionID, error: {} } })
const stopEntry = (change, writeId) => ({ rule: ["stop", change], write_id: writeId })
const STREAM_SIGNAL = new AbortController().signal

/**
 * Handlers over the fake host and a fake engine, for the turn-end tests: `script` answers every engine run,
 * so a script that answers by call order counts the spawns itself (the event is parsed only after spawn).
 */
function turnEnd({ script, sessions, syntheticError, syntheticNever, env = {}, verifier } = {}) {
  const timers = fakeTimers()
  const spawn = fakeSpawn(script ?? { code: 0, stdout: "" })
  const ctx = fakeCtx({ sessions, syntheticError, syntheticNever })
  const handlers = createHandlers(ctx, {
    engine: (event, options) =>
      runEngine(event, { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer, ...options }),
    // The verifier slot starts at every root turn end; a pass fake keeps these engine tests to one spawn.
    verifier: verifier ?? (async () => ({ kind: "pass", text: "" })),
    env,
    sleep: async () => {},
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })
  return { ctx, spawn, timers, handlers }
}

test("turn end: succeeded on a root session sends stop and resumes with the block text", async () => {
  const { ctx, spawn, handlers } = turnEnd({
    script: answer("block", "STOP TEXT", [stopEntry("demo", 7)]),
  })
  await handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  assert.equal(await settled(spawn.called(1)), true, "the stop event was sent")
  await flush()
  assert.deepEqual(spawn.events(), [{ event: "stop", session: "ses_a", cwd: DIR }])
  assert.equal(ctx.synthetics.length, 1)
  assert.equal(ctx.synthetics[0].sessionID, "ses_a")
  assert.equal(ctx.synthetics[0].text, "STOP TEXT")
  assert.equal(ctx.synthetics[0].resume, true, "the prompt resumes the session")
  assert.match(ctx.synthetics[0].id, /^msg_[0-9a-zA-Z_]+$/)
  assert.deepEqual(spawn.events().map((event) => event.event), ["stop"], "a delivered block voids nothing")
})

test("turn end: succeeded on a child session sends nothing", async () => {
  const sessions = { ses_child: { id: "ses_child", parentID: "ses_root" }, ses_root: { id: "ses_root" } }
  const { ctx, spawn, handlers } = turnEnd({ sessions })
  await handlers.onEvent(succeeded("ses_child"), STREAM_SIGNAL)
  await flush()
  assert.equal(spawn.calls.length, 0)
  assert.deepEqual(ctx.synthetics, [])
})

test("turn end: a session get failure sends nothing", async () => {
  const { ctx, spawn, handlers } = turnEnd()
  ctx.session.get = async () => {
    throw new Error("gone")
  }
  await handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  await flush()
  assert.equal(spawn.calls.length, 0)
  assert.deepEqual(ctx.synthetics, [])
})

test("turn end: a session lookup that never answers sends nothing", async () => {
  const { ctx, spawn, timers, handlers } = turnEnd({ sessions: { ses_a: NEVER } })
  await handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  await flush()
  timers.fire()
  await flush()
  assert.equal(spawn.calls.length, 0)
  assert.deepEqual(ctx.synthetics, [])
})

test("turn end: a second success while one is in flight sends nothing", async () => {
  const { ctx, spawn, handlers } = turnEnd({ script: { hang: true } })
  const first = handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  assert.equal(await settled(spawn.called(1)), true, "the stop event was sent")
  assert.equal(await settled(first), true, "the event stream is not held by the turn-end call")
  const second = handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  assert.equal(await settled(second), true)
  await flush()
  assert.equal(spawn.calls.length, 1)
  assert.deepEqual(ctx.synthetics, [])
})

test("turn end: interrupted sends nothing", async () => {
  const { ctx, spawn, handlers } = turnEnd()
  await handlers.onEvent(interrupted("ses_a"), STREAM_SIGNAL)
  await flush()
  assert.equal(spawn.calls.length, 0)
  assert.deepEqual(ctx.synthetics, [])
})

test("turn end: failed sends nothing", async () => {
  const { ctx, spawn, handlers } = turnEnd()
  await handlers.onEvent(failed("ses_a"), STREAM_SIGNAL)
  await flush()
  assert.equal(spawn.calls.length, 0)
  assert.deepEqual(ctx.synthetics, [])
})

test("turn end: a failed synthetic prompt voids the stop entry", async () => {
  let spawned = 0
  const { ctx, spawn, handlers } = turnEnd({
    script: () => {
      spawned += 1
      return spawned === 1 ? answer("block", "STOP TEXT", [stopEntry("demo", 7)]) : { code: 0, stdout: "" }
    },
    syntheticError: new Error("host refused"),
  })
  await handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  assert.equal(await settled(spawn.called(2)), true, "both engine calls were made")
  await flush()
  assert.deepEqual(ctx.synthetics, [])
  assert.deepEqual(spawn.events(), [
    { event: "stop", session: "ses_a", cwd: DIR },
    { event: "void", session: "ses_a", cwd: DIR, write_ids: [7] },
  ])
})

test("turn end: a context response at a stop moment is ignored", async () => {
  const { ctx, spawn, handlers } = turnEnd({ script: answer("context", FAIL_NOTICE_TEXT) })
  await handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  assert.equal(await settled(spawn.called(1)), true, "the stop event was sent")
  await flush()
  assert.deepEqual(spawn.events().map((event) => event.event), ["stop"])
  assert.deepEqual(ctx.synthetics, [], "the engine-failure notice does not resume the session")
})

test("turn end: an engine timeout at a stop moment does nothing", async () => {
  const { ctx, spawn, timers, handlers } = turnEnd({ script: { hang: true } })
  await handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  assert.equal(await settled(spawn.called(1)), true, "the stop event was sent")
  timers.fire()
  await flush()
  assert.equal(spawn.calls.length, 1)
  assert.deepEqual(spawn.events().map((event) => event.event), ["stop"])
  assert.deepEqual(ctx.synthetics, [])
})

test("turn end: a missing python at a stop moment does nothing", async () => {
  const { ctx, spawn, handlers } = turnEnd({
    script: { error: Object.assign(new Error("spawn python3 ENOENT"), { code: "ENOENT" }) },
  })
  await handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  assert.equal(await settled(spawn.called(1)), true, "the stop event was sent")
  await flush()
  assert.deepEqual(spawn.events().map((event) => event.event), ["stop"])
  assert.deepEqual(ctx.synthetics, [])
})

test("a block for a non-stop event is rejected as a malformed response", async () => {
  for (const kind of ["prompt", "tool-before", "tool-after", "compacted", "void"]) {
    const timers = fakeTimers()
    const result = await runEngine(
      { event: kind, session: "s", cwd: DIR },
      {
        spawn: fakeSpawn(answer("block", "TEXT", [stopEntry("demo", 1)])),
        setTimer: timers.setTimer,
        clearTimer: timers.clearTimer,
      },
    )
    assert.equal(result, null, kind)
  }
  const timers = fakeTimers()
  const result = await runEngine(
    { event: "stop", session: "s", cwd: DIR },
    {
      spawn: fakeSpawn(answer("block", "TEXT", [stopEntry("demo", 1)])),
      setTimer: timers.setTimer,
      clearTimer: timers.clearTimer,
    },
  )
  assert.deepEqual(result, { decision: "block", text: "TEXT", entries: [stopEntry("demo", 1)] })
})

test("turn end: a second success after the first budget ended sends stop", async () => {
  let spawned = 0
  const { ctx, spawn, timers, handlers } = turnEnd({
    script: () => {
      spawned += 1
      return spawned === 1 ? { hang: true } : answer("block", "SECOND", [stopEntry("demo", 2)])
    },
  })
  await handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  assert.equal(await settled(spawn.called(1)), true, "the stop event was sent")
  timers.fire() // the first call's budget ends, so its slot is free again
  await flush()
  await handlers.onEvent(succeeded("ses_a"), STREAM_SIGNAL)
  assert.equal(await settled(spawn.called(2)), true, "both engine calls were made")
  await flush()
  assert.deepEqual(spawn.events().map((event) => event.event), ["stop", "stop"])
  assert.deepEqual(ctx.synthetics.map((message) => [message.text, message.resume]), [["SECOND", true]])
})

// ---- the verifier slot (verify.py --hook opencode) ----------------------------------------------------------

/** Whether a fake spawn call is the verifier rather than the engine. */
const isVerify = (call) => call.args[2] === "--hook"
const blockJson = (reason) => JSON.stringify({ decision: "block", reason })
const noticeJson = (text) => JSON.stringify({ systemMessage: text })
/** Script answers each call by argv: the verifier gets `stdout`, the engine prints nothing. */
const verifyPrints = (stdout) => (call) => (isVerify(call) ? { code: 0, stdout } : { code: 0, stdout: "" })

/** Script: the verifier child never finishes, the engine prints nothing. */
const hangingVerifier = (call) => (isVerify(call) ? { hang: true } : { code: 0, stdout: "" })

test("verifier: a block resumes with resume:true", async () => {
  const { ctx, spawn } = await boot({
    events: [succeeded("ses_a")],
    script: verifyPrints(blockJson("VERIFY: fix the lint error")),
  })
  assert.equal(await settled(spawn.called(2)), true, "the engine stop and the verifier both ran")
  await flush()
  assert.deepEqual(
    ctx.synthetics.map((message) => [message.sessionID, message.text, message.resume]),
    [["ses_a", "VERIFY: fix the lint error", true]],
  )
  assert.match(ctx.synthetics[0].id, /^msg_[0-9a-zA-Z_]+$/)
})

test("verifier: a systemMessage is sent with resume:false", async () => {
  const { ctx, spawn } = await boot({
    events: [succeeded("ses_a")],
    script: verifyPrints(noticeJson("VERIFY: the branch pass is stale")),
  })
  assert.equal(await settled(spawn.called(2)), true)
  await flush()
  assert.deepEqual(
    ctx.synthetics.map((message) => [message.sessionID, message.text, message.resume]),
    [["ses_a", "VERIFY: the branch pass is stale", false]],
  )
})

test("verifier: empty, malformed or oversized output sends nothing", async () => {
  const outputs = [
    "",
    "not json",
    "[]",
    "null",
    '"text"',
    " ",
    '{"decision":"block"}',
    '{"decision":"block","reason":""}',
    '{"reason":"x"}',
    '{"decision":"allow","reason":"x"}',
    '{"decision":"block","reason":"x","extra":1}',
    '{"decision":"block","reason":5}',
    '{"systemMessage":""}',
    '{"systemMessage":" "}',
    '{"systemMessage":5}',
    '{"systemMessage":{"text":"x"}}',
    '{"systemMessage":"x","decision":"block","reason":"y"}',
    Buffer.alloc(MAX_STDOUT_BYTES + 1, 0x20),
  ]
  for (const stdout of outputs) {
    const { ctx, spawn } = await boot({ events: [succeeded("ses_a")], script: verifyPrints(stdout) })
    assert.equal(await settled(spawn.called(2)), true, "both slots ran")
    await flush()
    assert.deepEqual(ctx.synthetics, [], JSON.stringify(String(stdout)).slice(0, 120))
  }
})

test("verifier: truncated JSON is ignored", async () => {
  const { ctx, spawn } = await boot({
    events: [succeeded("ses_a")],
    script: verifyPrints('{"decision":"block","reason":"FIX'),
  })
  assert.equal(await settled(spawn.called(2)), true)
  await flush()
  assert.deepEqual(ctx.synthetics, [])
  assert.equal(spawn.calls.filter(isVerify).length, 1, "the verifier still ran and was ignored")
})

test("verifier: budget expiry sends SIGTERM, then SIGKILL after 5000 ms, and sends nothing", async () => {
  const { ctx, spawn, timers } = await boot({
    events: [succeeded("ses_a")],
    script: hangingVerifier,
  })
  assert.equal(await settled(spawn.called(2)), true)
  await flush()
  const verifier = spawn.calls.find(isVerify).child
  const budget = timers.timers.find((timer) => timer.ms === VERIFY_BUDGET_MS && !timer.cleared && !timer.fired)
  assert.ok(budget, "the verifier has its own budget timer")
  budget.fn()
  await flush()
  assert.deepEqual(verifier.signals, ["SIGTERM"], "the launcher is stopped first")
  assert.deepEqual(ctx.synthetics, [], "an expired run sends nothing")
  const grace = timers.timers.find(
    (timer) => timer.ms === VERIFY_KILL_GRACE_MS && !timer.cleared && !timer.fired,
  )
  assert.ok(grace, "a SIGKILL follows after the grace")
  grace.fn()
  await flush()
  assert.deepEqual(verifier.signals, ["SIGTERM", "SIGKILL"])
  assert.deepEqual(ctx.synthetics, [])
})

test("verifier: dispose stops a running verifier", async () => {
  const { ctx, spawn, dispose } = await boot({
    events: [succeeded("ses_a")],
    script: hangingVerifier,
  })
  assert.equal(await settled(spawn.called(2)), true)
  await flush()
  const verifier = spawn.calls.find(isVerify).child
  const disposing = dispose()
  await flush()
  assert.deepEqual(verifier.signals, ["SIGTERM"], "the launcher is stopped first")
  assert.equal(await settled(disposing), true, "the disposer stays bounded and resolves")
  assert.deepEqual(ctx.synthetics, [], "a disposed run sends nothing")
})

test("verifier: a turn end while one runs still sends the engine stop and starts no second verifier", async () => {
  let release
  const gate = new Promise((resolve) => {
    release = resolve
  })
  const streams = [
    async function* (signal) {
      yield succeeded("ses_a")
      await gate
      yield succeeded("ses_a")
      yield* quietStream(signal)
    },
  ]
  const { ctx, spawn } = await boot({ streams, script: hangingVerifier })
  assert.equal(await settled(spawn.called(2)), true, "the first turn end: engine stop and verifier")
  await flush()
  release()
  assert.equal(await settled(spawn.called(3)), true, "the second turn end still sends the engine stop")
  await flush()
  assert.equal(spawn.calls.filter(isVerify).length, 1, "no second verifier run")
  assert.equal(spawn.calls.filter((call) => !isVerify(call)).length, 2, "both engine stops were sent")
  assert.deepEqual(ctx.synthetics, [])
})

test("verifier: engine and verifier blocks are two separate resumes", async () => {
  const { ctx, spawn } = await boot({
    events: [succeeded("ses_a")],
    script: (call) =>
      isVerify(call) ? { hang: true } : answer("block", "STOP TEXT", []),
  })
  assert.equal(await settled(spawn.called(2)), true)
  await flush()
  assert.deepEqual(
    ctx.synthetics.map((message) => [message.text, message.resume]),
    [["STOP TEXT", true]],
    "the engine's resume is sent within its own budget",
  )
  const verifier = spawn.calls.find(isVerify).child
  verifier.stdout.emit("data", Buffer.from(blockJson("VERIFY TEXT")))
  verifier.emit("close", 0, null)
  await flush()
  assert.deepEqual(
    ctx.synthetics.map((message) => [message.text, message.resume]),
    [
      ["STOP TEXT", true],
      ["VERIFY TEXT", true],
    ],
    "the verifier's resume is sent separately when its run ends",
  )
  assert.notEqual(ctx.synthetics[0].id, ctx.synthetics[1].id, "two distinct messages")
})

test("verifier: child sessions and interrupted or failed executions start nothing", async () => {
  let verifications = 0
  const sessions = { ses_child: { id: "ses_child", parentID: "ses_root" }, ses_root: { id: "ses_root" } }
  const { ctx, spawn, handlers } = turnEnd({
    sessions,
    verifier: async () => {
      verifications += 1
      return { kind: "pass", text: "" }
    },
  })
  await handlers.onEvent(interrupted("ses_a"), STREAM_SIGNAL)
  await handlers.onEvent(failed("ses_a"), STREAM_SIGNAL)
  await handlers.onEvent(succeeded("ses_child"), STREAM_SIGNAL)
  await flush()
  assert.equal(verifications, 0)
  assert.equal(spawn.calls.length, 0)
  assert.deepEqual(ctx.synthetics, [])
})

test("verifier: runVerifier sends {session_id, cwd} on stdin to python3 -I <VERIFY_PATH> --hook opencode", async () => {
  const timers = fakeTimers()
  const spawn = fakeSpawn({ code: 0, stdout: "" })
  const result = await runVerifier(
    { session_id: "ses_a", cwd: DIR },
    { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
  )
  assert.deepEqual(result, { kind: "pass", text: "" })
  const [call] = spawn.calls
  assert.equal(call.command, "python3")
  assert.deepEqual(call.args, ["-I", VERIFY_PATH, "--hook", "opencode"])
  assert.equal(call.options.cwd, DIR)
  assert.deepEqual(call.event, { session_id: "ses_a", cwd: DIR })
  assert.equal(VERIFY_BUDGET_MS, 1800000)
  assert.equal(VERIFY_KILL_GRACE_MS, 5000)
  assert.equal(timers.timers.length, 1)
  assert.equal(timers.timers[0].ms, VERIFY_BUDGET_MS)
  assert.equal(timers.timers[0].cleared, true, "the budget timer is cleared when the child exits")
})

test("the verifier path is resolved from the plugin directory, not from the cwd", () => {
  const expected = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "verify.py")
  assert.equal(VERIFY_PATH, expected)
  assert.ok(fs.existsSync(VERIFY_PATH))
})

test("verifier: a missing python or a spawn throw is a silent pass", async () => {
  for (const script of [
    { throws: new Error("boom") },
    { error: Object.assign(new Error("spawn python3 ENOENT"), { code: "ENOENT" }) },
  ]) {
    const timers = fakeTimers()
    const spawn = fakeSpawn(script)
    const result = await runVerifier(
      { session_id: "ses_a", cwd: DIR },
      { spawn, setTimer: timers.setTimer, clearTimer: timers.clearTimer },
    )
    assert.deepEqual(result, { kind: "pass", text: "" })
    assert.ok(
      timers.timers.every((timer) => timer.cleared),
      "a spawn that throws creates no live timer",
    )
  }
})

// ---- the session's own checkout ------------------------------------------------------------------------------

const located = (id, directory, extra = {}) => ({ id, ...extra, location: { directory } })

test("cwd: the engine runs in the event session's directory, not the plugin's", async () => {
  const { hooks, spawn } = await boot({
    script: (call) =>
      call.event && call.event.event === "void"
        ? { code: 0, stdout: "" }
        : answer("context", "T", [entry("a.md", 5)]),
    sessions: { ses_a: located("ses_a", "/other/checkout") },
    env: { VELES_AGENT_RULES_FAIL_INJECTION: "1" },
  })
  await hooks["execute.after"](shellAfter("openspec archive x"))
  assert.deepEqual(spawn.events().map((event) => event.cwd), ["/other/checkout", "/other/checkout"])
  assert.deepEqual(spawn.calls.map((call) => call.options.cwd), ["/other/checkout", "/other/checkout"])
})

test("cwd: a child session uses its own directory, else its root's", async () => {
  const { hooks, spawn } = await boot({
    sessions: {
      ses_c1: located("ses_c1", "/child/tree", { parentID: "ses_r" }),
      ses_c2: { id: "ses_c2", parentID: "ses_r" },
      ses_r: located("ses_r", "/root/tree"),
    },
  })
  await hooks["execute.before"](shellBefore("git status", "ses_c1"))
  await hooks["execute.before"](shellBefore("git status", "ses_c2"))
  assert.deepEqual(spawn.events().map((event) => event.cwd), ["/child/tree", "/root/tree"])
})

test("cwd: an absent or invalid session location falls back to the plugin directory", async () => {
  for (const location of [undefined, null, "dir", {}, { directory: "" }, { directory: 5 }, { directory: "relative/dir" }, { directory: "/a\0b" }]) {
    const sessions = { ses_a: location === undefined ? { id: "ses_a" } : { id: "ses_a", location } }
    const { hooks, spawn } = await boot({ sessions })
    await hooks["execute.before"](shellBefore("git status"))
    assert.equal(spawn.events()[0].cwd, DIR, JSON.stringify(location))
  }
})

test("cwd: the directory is cached with the verified key", async () => {
  const { hooks, spawn, ctx } = await boot({ sessions: { ses_a: located("ses_a", "/first") } })
  await hooks["execute.before"](shellBefore("git status"))
  let gets = 0
  ctx.session.get = async ({ sessionID }) => {
    gets += 1
    return located(sessionID, "/second")
  }
  await hooks["execute.before"](shellBefore("git status"))
  assert.equal(gets, 0)
  assert.deepEqual(spawn.events().map((event) => event.cwd), ["/first", "/first"])
})

test("cwd: a compaction runs in the compacting session's directory", async () => {
  const { spawn } = await boot({
    sessions: { ses_root: located("ses_root", "/compacting/tree") },
    events: [{ type: "session.compaction.started", data: { sessionID: "ses_root" } }],
  })
  await spawn.called(1)
  assert.deepEqual(spawn.events(), [{ event: "compacted", session: "ses_root", cwd: "/compacting/tree" }])
})

// ---- compaction ordering and the disposed watcher -------------------------------------------------------------

/** Handlers over a scripted engine: each call is recorded and answered by hand through `answers`. */
function scriptedHandlers(ctx, timers) {
  const calls = []
  const engine = (event) => {
    const answer = deferred()
    calls.push({ event, answer })
    return answer.promise
  }
  const handlers = createHandlers(ctx, {
    engine,
    env: {},
    sleep: async () => {},
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })
  return { handlers, calls }
}

test("ordering: an observed compaction is sent before the next gate of that session", async () => {
  const timers = fakeTimers()
  const ctx = fakeCtx()
  const { handlers, calls } = scriptedHandlers(ctx, timers)
  const signal = new AbortController().signal
  const compaction = handlers.onEvent({ type: "session.compaction.started", data: { sessionID: "ses_a" } }, signal)
  const gate = handlers.before(shellBefore("git commit"))
  await flush()
  assert.deepEqual(calls.map((call) => call.event.event), ["compacted"], "the gate waits for the compaction")
  calls[0].answer.resolve({ decision: "pass", text: "", entries: [] })
  await compaction
  await flush()
  assert.deepEqual(calls.map((call) => call.event.event), ["compacted", "tool-before"])
  calls[1].answer.resolve({ decision: "pass", text: "", entries: [] })
  assert.equal(await gate, undefined)
})

test("ordering: a root compaction is awaited by a subagent's gate and moment as well", async () => {
  const timers = fakeTimers()
  const ctx = fakeCtx({ sessions: { ses_c: { id: "ses_c", parentID: "ses_r" } } })
  const { handlers, calls } = scriptedHandlers(ctx, timers)
  const signal = new AbortController().signal
  handlers.onEvent({ type: "session.compaction.started", data: { sessionID: "ses_r" } }, signal)
  const gate = handlers.before(shellBefore("git commit", "ses_c"))
  const moment = handlers.after(shellAfter("openspec archive x", { sessionID: "ses_c" }))
  await flush()
  assert.deepEqual(calls.map((call) => call.event.event), ["compacted"])
  calls[0].answer.resolve({ decision: "pass", text: "", entries: [] })
  await flush()
  assert.deepEqual(calls.map((call) => call.event.event).sort(), ["compacted", "tool-after", "tool-before"])
  for (const call of calls.slice(1)) call.answer.resolve({ decision: "pass", text: "", entries: [] })
  await gate
  await moment
})

test("ordering: a compaction that never finishes holds the gate only until the gate's budget", async () => {
  const timers = fakeTimers()
  const ctx = fakeCtx()
  const { handlers, calls } = scriptedHandlers(ctx, timers)
  handlers.onEvent({ type: "session.compaction.started", data: { sessionID: "ses_a" } }, new AbortController().signal)
  await flush()
  const gate = handlers.before(shellBefore("git commit"))
  await flush()
  assert.equal(await settled(gate), false)
  const gateBudget = timers.live().at(-1)
  gateBudget.fn()
  assert.equal(await gate, undefined, "the call proceeds")
  assert.deepEqual(calls.map((call) => call.event.event), ["compacted"])
})

test("ordering: a compaction of another session does not hold the gate", async () => {
  const timers = fakeTimers()
  const ctx = fakeCtx()
  const { handlers, calls } = scriptedHandlers(ctx, timers)
  handlers.onEvent({ type: "session.compaction.started", data: { sessionID: "ses_other" } }, new AbortController().signal)
  const gate = handlers.before(shellBefore("git commit"))
  await flush()
  assert.deepEqual(calls.map((call) => call.event.event).sort(), ["compacted", "tool-before"])
  for (const call of calls) call.answer.resolve({ decision: "pass", text: "", entries: [] })
  await gate
})

test("disposed: a watcher whose lookup answers after the disposer sends no late compaction", async () => {
  const lookup = deferred()
  const { ctx, spawn, dispose } = await boot({
    events: [{ type: "session.compaction.started", data: { sessionID: "ses_late" } }],
  })
  ctx.session.get = () => lookup.promise
  await flush()
  await dispose()
  lookup.resolve({ id: "ses_late" })
  await flush()
  assert.equal(spawn.calls.length, 0)
})

test("disposed: an event handed to onEvent after the abort is ignored", async () => {
  const timers = fakeTimers()
  const ctx = fakeCtx()
  const { handlers, calls } = scriptedHandlers(ctx, timers)
  const controller = new AbortController()
  controller.abort()
  await handlers.onEvent({ type: "session.compaction.started", data: { sessionID: "ses_a" } }, controller.signal)
  await flush()
  assert.equal(calls.length, 0)
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
