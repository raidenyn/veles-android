// OpenCode V2 plugin that delivers the hooked rules (.agents/hooked-rules/) through the shared engine,
// scripts/agent_rules.py. The engine decides; this file only maps OpenCode hooks to engine events and
// engine decisions back to host actions. It never blocks a call because of its own failure.
//
// Plain ESM, no dependencies and no package.json: OpenCode loads the directory and finds this index.js.
import { spawn as nodeSpawn } from "node:child_process"
import { randomBytes } from "node:crypto"
import { fileURLToPath } from "node:url"

// Resolved from this file, never from the session directory: the session may be any checkout.
export const ENGINE_PATH = fileURLToPath(new URL("../agent_rules.py", import.meta.url))
export const NOTICE =
  "Rule hooks failed: read the hooked rules listed in AGENTS.md by hand at the moments they name."
export const TIMEOUT_MS = 5000
export const KILL_ATTEMPTS = 5 // SIGKILL attempts on a child that must not outlive its call
export const CLEANUP_MS = 1000 // between SIGKILL attempts
export const DISPOSE_BOUND_MS = 1000 // how long the disposer waits for the watcher loop
export const FAIL_INJECTION_VARIABLE = "VELES_AGENT_RULES_FAIL_INJECTION"

const PASS = Object.freeze({ decision: "pass", text: "", entries: Object.freeze([]) })
const MAX_PARENT_DEPTH = 16
const BACKOFF_START_MS = 1000
const BACKOFF_CAP_MS = 30000

function isString(value) {
  return typeof value === "string"
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function hasExactKeys(value, keys) {
  const own = Object.keys(value).sort()
  return own.length === keys.length && own.every((key, index) => key === keys[index])
}

function validEntry(item) {
  return (
    isPlainObject(item) &&
    hasExactKeys(item, ["rule", "write_id"]) &&
    Array.isArray(item.rule) &&
    item.rule.length === 2 &&
    item.rule.every(isString) &&
    Number.isSafeInteger(item.write_id) &&
    item.write_id >= 0
  )
}

/**
 * The decoded engine response, or null when the bytes are not exactly the plain contract:
 * `{decision: "context"|"deny", text: string, entries: [{rule: [string, string], write_id: uint}]}`.
 * Zero bytes of stdout is the normal empty pass; whitespace-only output is not.
 */
function decode(bytes) {
  if (bytes.length === 0) return PASS
  let text
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes)
  } catch {
    return null
  }
  let value
  try {
    value = JSON.parse(text)
  } catch {
    return null
  }
  if (!isPlainObject(value) || !hasExactKeys(value, ["decision", "entries", "text"])) return null
  if (value.decision !== "context" && value.decision !== "deny") return null
  if (!isString(value.text) || !Array.isArray(value.entries)) return null
  if (!value.entries.every(validEntry)) return null
  return { decision: value.decision, text: value.text, entries: value.entries }
}

/**
 * Run the engine for one plain event. Resolves with `{decision, text, entries}`, a pass when the engine
 * printed nothing and exited 0, or `null` for any infrastructure failure (spawn error, timeout, nonzero
 * exit, undecodable output). Never rejects.
 *
 * `spawn`, `setTimer` and `clearTimer` are injectable so tests drive it without a clock or a real child.
 */
export function runEngine(
  event,
  {
    spawn = nodeSpawn,
    timeoutMs = TIMEOUT_MS,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
    engine = ENGINE_PATH,
    cleanupMs = CLEANUP_MS,
  } = {},
) {
  return new Promise((resolve) => {
    let settled = false
    let timer
    let child
    let exited = false
    let cleaning = false
    let cleanupTimer
    let attempts = 0
    const stopCleanup = () => {
      if (cleanupTimer !== undefined) clearTimer(cleanupTimer)
      cleanupTimer = undefined
    }
    // Child cleanup is independent of the fail-open result: SIGKILL again every `cleanupMs` until the child
    // has exited or the attempts run out. A false return or a throw is a failed attempt.
    const attemptKill = () => {
      cleanupTimer = undefined
      if (exited) return
      attempts += 1
      try {
        child.kill("SIGKILL")
      } catch {
        // a failed attempt; the next one follows
      }
      if (exited || attempts >= KILL_ATTEMPTS) return
      cleanupTimer = setTimer(attemptKill, cleanupMs)
      if (cleanupTimer && typeof cleanupTimer.unref === "function") cleanupTimer.unref()
    }
    const startCleanup = () => {
      if (cleaning || !child || exited) return
      cleaning = true // a synchronous 'error' from kill() re-enters fail(); this keeps it to one cleanup
      attemptKill()
    }
    const finish = (value) => {
      if (settled) return
      settled = true
      if (timer !== undefined) clearTimer(timer)
      resolve(value)
    }
    // Any transport failure: the call passes (null) at once, then the child is cleaned up.
    const fail = () => {
      finish(null)
      startCleanup()
    }
    try {
      child = spawn("python3", ["-I", engine, "hook", "--format", "plain"], {
        cwd: event.cwd,
        stdio: ["pipe", "pipe", "ignore"],
        windowsHide: true,
      })
    } catch {
      finish(null)
      return
    }
    timer = setTimer(fail, timeoutMs)
    const chunks = []
    if (child.stdout) child.stdout.on("data", (chunk) => chunks.push(Buffer.from(chunk)))
    child.on("error", fail)
    child.on("exit", () => {
      exited = true
      stopCleanup()
    })
    child.on("close", (code) => {
      exited = true
      stopCleanup()
      if (settled) return
      if (code !== 0) return finish(null)
      finish(decode(Buffer.concat(chunks)))
    })
    try {
      if (!child.stdin) return fail()
      // A failed write means the engine never saw the event: not a pass, whatever the exit code says.
      child.stdin.on("error", fail)
      child.stdin.end(JSON.stringify(event))
    } catch {
      fail()
    }
  })
}

let counter = 0
/** A unique `msg_` id without a clock: a module counter plus random hex. */
function messageId() {
  counter += 1
  return `msg_${counter.toString(16).padStart(4, "0")}${randomBytes(8).toString("hex")}`
}

/**
 * The hook handlers for one host context. `engine(event)` runs one engine event; `env` is read for the
 * test-only injection switch; `sleep(ms, signal)` paces the event-stream re-subscription.
 */
export function createHandlers(
  ctx,
  { engine = runEngine, env = process.env, sleep = abortableSleep } = {},
) {
  const directory = ctx.location.directory
  const keys = new Map()

  /**
   * `{session}` for a root session; `{session: root, agent: child}` for a subagent's child session; `null`
   * when the parent chain cannot be verified (a malformed result, a cycle, the depth cap or a rejected
   * lookup). Only a chain that reached a verified root is cached, and a cached key is never replaced.
   */
  async function keyOf(sessionID) {
    if (!isString(sessionID) || sessionID === "") return null
    if (keys.has(sessionID)) return keys.get(sessionID)
    try {
      const visited = new Set([sessionID])
      let current = sessionID
      for (let depth = 0; depth < MAX_PARENT_DEPTH; depth += 1) {
        const info = await ctx.session.get({ sessionID: current })
        if (!isPlainObject(info) || info.id !== current) return null // a record of another session is not this one
        const parent = info.parentID
        if (parent === undefined || parent === null) {
          const key = current === sessionID ? { session: sessionID } : { session: current, agent: sessionID }
          if (!keys.has(sessionID)) keys.set(sessionID, key)
          return keys.get(sessionID)
        }
        if (!isString(parent) || parent === "" || visited.has(parent)) return null
        visited.add(parent)
        current = parent
      }
    } catch {
      return null
    }
    return null // the depth cap was reached without a root
  }

  async function send(event) {
    try {
      return await engine(event)
    } catch {
      return null
    }
  }

  /** One `void` per rule entry, each carrying its own write id; failures here are swallowed. */
  async function voidEntries(key, entries) {
    for (const item of entries) {
      await send({ event: "void", ...key, cwd: directory, write_ids: [item.write_id] })
    }
  }

  /** Run `inject(text)`; if the host injection throws, void what the engine recorded. */
  async function deliver(key, response, inject) {
    if (response === null) return notify(inject)
    if (response.decision !== "context") return
    if (response.text === "" && response.entries.length === 0) return
    try {
      // Every context response with entries goes through the real injection, whatever the text length, so
      // a recorded credit is voided when the injection (or its channel) fails.
      if (env[FAIL_INJECTION_VARIABLE] === "1") throw new Error(`${FAIL_INJECTION_VARIABLE}=1`)
      await inject(response.text) // as it is, even when empty: the entries were recorded for it
    } catch {
      await voidEntries(key, response.entries)
    }
  }

  /** The read-by-hand notice through a channel; no channel left means the call simply passes. */
  async function notify(inject) {
    try {
      await inject(NOTICE)
    } catch {
      // nothing more to do
    }
  }

  async function before(input) {
    if (!input || input.tool !== "shell") return
    const command = input.input && input.input.command
    if (!isString(command)) return
    const key = await keyOf(input.sessionID)
    if (key === null) return // an unverified key is never recorded under; execute.before has no channel
    const response = await send({ event: "tool-before", ...key, cwd: directory, command })
    // Only a decision of exactly "deny" stops the call; the message is what the model sees.
    if (response !== null && response.decision === "deny") throw new Error(response.text)
  }

  function afterEvent(input) {
    const args = input.input || {}
    if (input.tool === "shell") {
      if (!isString(args.command)) return null
      const ok =
        input.status === "completed" &&
        Boolean(input.result && input.result.metadata) &&
        input.result.metadata.exit === 0
      return { event: "tool-after", tool: "shell", command: args.command, ok }
    }
    if (input.status !== "completed") return null
    if (input.tool === "skill" && isString(args.id)) {
      return { event: "tool-after", tool: "skill", skill: args.id }
    }
    if (input.tool === "read" && isString(args.path)) {
      return { event: "tool-after", tool: "read", path: args.path }
    }
    return null
  }

  async function after(input) {
    if (!input) return
    const partial = afterEvent(input)
    if (partial === null) return
    const append = async (text) => {
      // Mutate the result in place: OpenCode keeps the appended text in the transcript.
      const content = input.result && input.result.content
      if (!Array.isArray(content)) throw new Error("the tool result has no content array to append to")
      content.push({ type: "text", text })
    }
    const key = await keyOf(input.sessionID)
    if (key === null) return notify(append)
    const response = await send({ ...partial, ...key, cwd: directory })
    await deliver(key, response, append)
  }

  async function prompt(input) {
    const text = input && input.prompt && input.prompt.text
    if (!isString(text)) return
    const inject = (body) =>
      // resume:false: the synthetic message alone must not start a run.
      ctx.session.synthetic({ sessionID: input.sessionID, id: messageId(), text: body, resume: false })
    const key = await keyOf(input.sessionID)
    if (key === null) return notify(inject)
    const response = await send({ event: "prompt", ...key, cwd: directory, prompt: text })
    await deliver(key, response, inject)
  }

  async function onEvent(event) {
    if (!event || event.type !== "session.compaction.started") return
    const sessionID = event.data && event.data.sessionID
    if (!isString(sessionID)) return
    const key = await keyOf(sessionID)
    if (key === null) return // no channel, and no verified key to re-arm
    await send({ event: "compacted", ...key, cwd: directory })
  }

  /**
   * Consume the host event stream until `signal` aborts; never rejects. A stream that fails or ends is
   * subscribed again after `sleep(delay, signal)`, the delay doubling from 1 s up to 30 s and starting over
   * once an event arrives. One failing event never ends the stream.
   */
  async function watch(signal) {
    let delay = BACKOFF_START_MS
    while (!signal.aborted) {
      try {
        for await (const event of ctx.event.subscribe({ signal })) {
          if (signal.aborted) return
          delay = BACKOFF_START_MS
          try {
            await onEvent(event)
          } catch {
            // one bad event must not end the stream
          }
        }
      } catch {
        // a rejected subscription or iterator: subscribe again below
      }
      if (signal.aborted) return
      try {
        await sleep(delay, signal)
      } catch {
        // a failing sleep still backs off through the loop
      }
      delay = Math.min(delay * 2, BACKOFF_CAP_MS)
    }
  }

  return { keyOf, before, after, prompt, onEvent, watch }
}

/** Resolves after `ms`, or as soon as `signal` aborts. The only real timer in this file. */
function abortableSleep(ms, signal) {
  return new Promise((resolve) => {
    if (signal && signal.aborted) return resolve()
    const done = () => {
      clearTimeout(timer)
      if (signal) signal.removeEventListener("abort", done)
      resolve()
    }
    const timer = setTimeout(done, ms)
    if (signal) signal.addEventListener("abort", done, { once: true })
  })
}

/**
 * The plugin object; the options are the injectable seams (`spawn`, timers, `env`, `sleep`) the tests use.
 * `setup` returns an async disposer, like `opencode-rules`: it aborts the event stream, waits for the
 * watcher to stop and disposes the hook registrations.
 */
export function createPlugin({
  spawn,
  setTimer,
  clearTimer,
  env = process.env,
  sleep,
  disposeBoundMs = DISPOSE_BOUND_MS,
} = {}) {
  const runtime = {}
  if (spawn) runtime.spawn = spawn
  if (setTimer) runtime.setTimer = setTimer
  if (clearTimer) runtime.clearTimer = clearTimer
  return {
    id: "veles-hooked-rules",
    async setup(ctx) {
      const handlers = createHandlers(ctx, {
        engine: (event) => runEngine(event, runtime),
        env,
        sleep,
      })
      const registrations = []
      registrations.push(await ctx.tool.hook("execute.before", handlers.before))
      registrations.push(await ctx.tool.hook("execute.after", handlers.after))
      registrations.push(await ctx.session.hook("prompt", handlers.prompt))
      const controller = new AbortController()
      const watcher = handlers.watch(controller.signal)
      return async () => {
        // First stop the stream, then release the hooks (never dependent on the loop), and only then wait
        // for the loop, and only up to a bound: a `next()` that ignores the signal or a lookup that never
        // answers must not hold the disposer.
        controller.abort()
        for (const registration of registrations.reverse()) {
          try {
            if (registration && typeof registration.dispose === "function") await registration.dispose()
          } catch {
            // a registration that cannot be disposed is already gone
          }
        }
        const timers = { set: runtime.setTimer || setTimeout, clear: runtime.clearTimer || clearTimeout }
        await new Promise((resolve) => {
          const bound = timers.set(resolve, disposeBoundMs)
          watcher.then(() => {
            timers.clear(bound)
            resolve()
          })
        })
      }
    },
  }
}

export default createPlugin()
