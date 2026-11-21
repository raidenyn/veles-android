// OpenCode V2 plugin that delivers the hooked rules (.agents/hooked-rules/) through the shared engine,
// scripts/agent_rules.py. The engine decides; this file only maps OpenCode hooks to engine events and
// engine decisions back to host actions. It never blocks a call because of its own failure.
//
// Plain ESM, no dependencies and no package.json: OpenCode loads the directory and finds this index.js.
import { spawn as nodeSpawn } from "node:child_process"
import { randomBytes } from "node:crypto"
import path from "node:path"
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
export const HANDLER_BUDGET_MS = 5000 // one bound for a whole hook call: lookups, engine runs, injection, voids
export const MAX_STDOUT_BYTES = 1024 * 1024 // engine output above this is a failure

// The verifier slot (verify.py --hook opencode): resolved from this file like ENGINE_PATH, never from the
// session directory, so the active checkout's launcher and watchdog are always the plugin's own.
export const VERIFY_PATH = fileURLToPath(new URL("../verify.py", import.meta.url))
export const VERIFY_BUDGET_MS = 1800000 // one verifier run: the task gate's own deadline plus its margin
export const VERIFY_KILL_GRACE_MS = 5000 // between SIGTERM and SIGKILL on the launcher

const PASS = Object.freeze({ decision: "pass", text: "", entries: Object.freeze([]) })
const VERIFIER_PASS = Object.freeze({ kind: "pass", text: "" })
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
 * `{decision: "context"|"deny"|"block", text: string, entries: [{rule: [string, string], write_id: uint}]}`.
 * `kind` is the engine event the response answers; `block` is valid only at a `stop` event. Zero bytes of
 * stdout is the normal empty pass; whitespace-only output is not. A deny or block must carry the text the
 * model is shown: one with empty or blank text is malformed, so the call passes.
 */
function decode(bytes, kind) {
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
  if (value.decision !== "context" && value.decision !== "deny" && value.decision !== "block") return null
  if (value.decision === "block" && kind !== "stop") return null
  if (!isString(value.text) || !Array.isArray(value.entries)) return null
  if (value.decision !== "context" && value.text.trim() === "") return null
  if (!value.entries.every(validEntry)) return null
  return { decision: value.decision, text: value.text, entries: value.entries }
}

/**
 * Run the engine for one plain event. Resolves with `{decision, text, entries}`, a pass when the engine
 * printed nothing and exited 0, or `null` for any infrastructure failure (spawn error, timeout, an aborted
 * `signal`, nonzero exit, more than `MAX_STDOUT_BYTES` of output, undecodable output). Never rejects.
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
    signal = null,
    maxStdoutBytes = MAX_STDOUT_BYTES,
  } = {},
) {
  return new Promise((resolve) => {
    if (signal && signal.aborted) return resolve(null) // the caller's budget is already spent
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
      if (signal) signal.removeEventListener("abort", onAbort)
      resolve(value)
    }
    // Any transport failure: the call passes (null) at once, then the child is cleaned up.
    const fail = () => {
      finish(null)
      startCleanup()
    }
    function onAbort() {
      fail()
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
    if (signal) signal.addEventListener("abort", onAbort, { once: true })
    const chunks = []
    let size = 0
    if (child.stdout) {
      child.stdout.on("data", (chunk) => {
        if (settled) return
        const bytes = Buffer.from(chunk)
        size += bytes.length
        if (size > maxStdoutBytes) return fail() // a runaway child: stop reading and kill it
        chunks.push(bytes)
      })
    }
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
      finish(decode(Buffer.concat(chunks), event && event.event))
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

/**
 * The verifier hook's answer: `{kind: "block", text}` for `{"decision":"block","reason":string}`,
 * `{kind: "notice", text}` for `{"systemMessage":string}`, else the empty pass. Empty, whitespace-only,
 * malformed, non-object and unknown-shaped output is a pass: the verifier is fail-open, and a block or
 * notice must carry the text the session is shown, so one with empty or blank text is ignored.
 */
function decodeVerifier(bytes) {
  if (bytes.length === 0) return VERIFIER_PASS
  let text
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes)
  } catch {
    return VERIFIER_PASS
  }
  let value
  try {
    value = JSON.parse(text)
  } catch {
    return VERIFIER_PASS // a truncated or malformed document is ignored
  }
  if (!isPlainObject(value)) return VERIFIER_PASS
  if (hasExactKeys(value, ["decision", "reason"])) {
    if (value.decision !== "block" || !isString(value.reason) || value.reason.trim() === "") {
      return VERIFIER_PASS
    }
    return { kind: "block", text: value.reason }
  }
  if (hasExactKeys(value, ["systemMessage"])) {
    if (!isString(value.systemMessage) || value.systemMessage.trim() === "") return VERIFIER_PASS
    return { kind: "notice", text: value.systemMessage }
  }
  return VERIFIER_PASS
}

/**
 * Run the verifier hook once at a turn end: `python3 -I <plugin checkout>/scripts/verify.py --hook opencode`
 * with `{session_id, cwd}` on stdin. Resolves with `{kind: "block"|"notice"|"pass", text}` and never rejects.
 *
 * Empty, malformed, non-object or oversized stdout is the empty pass. When `signal` aborts or the budget
 * expires the launcher gets SIGTERM (its own handler stops the dispatched child, design D4), a SIGKILL
 * follows after `graceMs` unless the child exited first, and the run passes with no message: an expired or
 * disposed run is not a verification result, so nothing is sent.
 *
 * `spawn`, `setTimer` and `clearTimer` are injectable so tests drive it without a clock or a real child.
 */
export function runVerifier(
  event,
  {
    spawn = nodeSpawn,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
    signal = null,
    budgetMs = VERIFY_BUDGET_MS,
    graceMs = VERIFY_KILL_GRACE_MS,
    maxStdoutBytes = MAX_STDOUT_BYTES,
  } = {},
) {
  return new Promise((resolve) => {
    if (signal && signal.aborted) return resolve(VERIFIER_PASS) // the plugin is already disposing
    let settled = false
    let child
    let exited = false
    let budgetTimer
    let graceTimer
    const stopGrace = () => {
      if (graceTimer !== undefined) clearTimer(graceTimer)
      graceTimer = undefined
    }
    const finish = (value) => {
      if (settled) return
      settled = true
      if (budgetTimer !== undefined) clearTimer(budgetTimer)
      if (signal) signal.removeEventListener("abort", onAbort)
      resolve(value)
    }
    // SIGKILL only when SIGTERM did not end the launcher within the grace.
    const killAfterGrace = () => {
      graceTimer = undefined
      if (exited) return
      try {
        child.kill("SIGKILL")
      } catch {
        // the child is already gone
      }
    }
    // Budget expiry, abort, or a broken stdin: no message, stop the launcher, and arm the grace.
    const stop = () => {
      if (settled) return
      finish(VERIFIER_PASS)
      if (!child || exited) return
      try {
        child.kill("SIGTERM")
      } catch {
        // a child that is already gone needs no signal
      }
      graceTimer = setTimer(killAfterGrace, graceMs)
      if (graceTimer && typeof graceTimer.unref === "function") graceTimer.unref()
    }
    function onAbort() {
      stop()
    }
    try {
      child = spawn("python3", ["-I", VERIFY_PATH, "--hook", "opencode"], {
        cwd: event.cwd,
        stdio: ["pipe", "pipe", "ignore"],
        windowsHide: true,
      })
    } catch {
      finish(VERIFIER_PASS)
      return
    }
    budgetTimer = setTimer(stop, budgetMs)
    if (budgetTimer && typeof budgetTimer.unref === "function") budgetTimer.unref()
    if (signal) signal.addEventListener("abort", onAbort, { once: true })
    const chunks = []
    let size = 0
    let oversized = false
    if (child.stdout) {
      child.stdout.on("data", (chunk) => {
        if (settled || oversized) return
        const bytes = Buffer.from(chunk)
        size += bytes.length
        if (size > maxStdoutBytes) {
          oversized = true // stop storing: an oversized document is ignored, whatever it holds
          chunks.length = 0
          return
        }
        chunks.push(bytes)
      })
    }
    child.on("error", () => {
      exited = true
      stopGrace()
      finish(VERIFIER_PASS)
    })
    child.on("exit", () => {
      exited = true
      stopGrace() // the launcher ended within the grace: no SIGKILL
    })
    child.on("close", (code) => {
      exited = true
      stopGrace()
      if (settled) return
      if (code !== 0 || oversized) return finish(VERIFIER_PASS)
      finish(decodeVerifier(Buffer.concat(chunks)))
    })
    try {
      if (!child.stdin) return stop()
      // A failed write means the hook never saw the event: stop it and pass silently.
      child.stdin.on("error", stop)
      child.stdin.end(JSON.stringify(event))
    } catch {
      stop()
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
 * The directory in a session record's `location`, or null when it is absent or not a usable absolute
 * path. Observed on OpenCode 2.3.2: `ctx.session.get()` returns `location: {directory}`.
 */
function locationOf(info) {
  const location = info.location
  if (!isPlainObject(location)) return null
  const directory = location.directory
  if (!isString(directory) || directory === "" || directory.includes("\0")) return null
  return path.isAbsolute(directory) ? directory : null
}

/**
 * One time bound for a whole hook call. `settle(promise)` resolves with `{status: "ok", value}`,
 * `{status: "error"}` or `{status: "expired"}` and never rejects: when the budget runs out (its one timer
 * fires, or `parent` aborts) every pending `settle` resolves as expired at once, and late results are
 * ignored. `signal` aborts at the same moment, which kills an engine child still running.
 */
function startBudget(ms, setTimer, clearTimer, parent) {
  const controller = new AbortController()
  const expire = () => controller.abort()
  const expired = new Promise((resolve) =>
    controller.signal.addEventListener("abort", () => resolve({ status: "expired" }), { once: true }),
  )
  const timer = setTimer(expire, ms)
  if (timer && typeof timer.unref === "function") timer.unref()
  if (parent) {
    if (parent.aborted) expire()
    else parent.addEventListener("abort", expire, { once: true })
  }
  return {
    signal: controller.signal,
    get expired() {
      return controller.signal.aborted
    },
    settle(promise) {
      const outcome = Promise.resolve(promise).then(
        (value) => ({ status: "ok", value }),
        () => ({ status: "error" }),
      )
      return Promise.race([outcome, expired])
    },
    end() {
      clearTimer(timer)
      if (parent) parent.removeEventListener("abort", expire)
      expire() // anything still waiting on this budget gives up now
    },
  }
}

/** Calls `inject(text)` and returns its promise; a synchronous throw becomes a rejection. */
function invoke(inject, text) {
  return new Promise((resolve) => resolve(inject(text)))
}

/**
 * The hook handlers for one host context. `engine(event, {signal})` runs one engine event; `env` is read
 * for the test-only injection switch; `sleep(ms, signal)` paces the event-stream re-subscription;
 * `setTimer`, `clearTimer` and `budgetMs` bound each hook call (`HANDLER_BUDGET_MS`).
 */
export function createHandlers(
  ctx,
  {
    engine = runEngine,
    verifier = runVerifier,
    env = process.env,
    sleep = abortableSleep,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
    budgetMs = HANDLER_BUDGET_MS,
  } = {},
) {
  const directory = ctx.location.directory
  const contexts = new Map() // session id -> {key, cwd}, only for verified chains
  const compactions = new Map() // session id -> the promise of its observed, unfinished compaction
  const stopsInFlight = new Set() // session ids whose turn end is still being handled
  const verifiesInFlight = new Map() // session id -> {controller, promise}: the separate verifier slot
  let closed = false // set by dispose: no verifier starts or resumes after it

  const budget = (parent) => startBudget(budgetMs, setTimer, clearTimer, parent)

  /**
   * `{key, cwd}` for a session, or `null` when the parent chain cannot be verified (a malformed result, a
   * cycle, the depth cap or a rejected lookup). The key is `{session}` for a root session and
   * `{session: root, agent: child}` for a subagent's child session. `cwd` is the event session's own
   * directory, else its root's, else the plugin's (`ctx.location.directory`): the engine must decide in the
   * checkout the session works in. Only a chain that reached a verified root is cached, and a cached value
   * is never replaced.
   */
  async function resolveSession(sessionID) {
    if (!isString(sessionID) || sessionID === "") return null
    if (contexts.has(sessionID)) return contexts.get(sessionID)
    try {
      const visited = new Set([sessionID])
      let current = sessionID
      let own = null
      for (let depth = 0; depth < MAX_PARENT_DEPTH; depth += 1) {
        const info = await ctx.session.get({ sessionID: current })
        if (!isPlainObject(info) || info.id !== current) return null // a record of another session is not this one
        if (current === sessionID) own = locationOf(info)
        const parent = info.parentID
        if (parent === undefined || parent === null) {
          const key = current === sessionID ? { session: sessionID } : { session: current, agent: sessionID }
          const cwd = own || locationOf(info) || directory
          if (!contexts.has(sessionID)) contexts.set(sessionID, { key, cwd })
          return contexts.get(sessionID)
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

  /** The verified key of a session, or null (see `resolveSession`). */
  async function keyOf(sessionID) {
    const resolved = await resolveSession(sessionID)
    return resolved === null ? null : resolved.key
  }

  /** The verified `{key, cwd}`, or null when unverified or when the lookup outlived the budget. */
  async function lookup(sessionID, bound) {
    const outcome = await bound.settle(resolveSession(sessionID))
    return outcome.status === "ok" ? outcome.value : null
  }

  /**
   * Wait, within the budget, for an observed compaction of this session or of its root, so a re-arm the
   * host already announced reaches the engine before this call's event. False when the budget ran out.
   */
  async function afterCompactions(sessionID, key, bound) {
    const pending = [compactions.get(sessionID), compactions.get(key.session)].filter(Boolean)
    if (pending.length === 0) return !bound.expired
    const outcome = await bound.settle(Promise.all(pending))
    return outcome.status !== "expired"
  }

  /** One engine run inside the budget; null for any failure, including a spent budget. */
  async function send(event, bound) {
    if (bound.expired) return null
    const outcome = await bound.settle(engine(event, { signal: bound.signal }))
    return outcome.status === "ok" ? outcome.value : null
  }

  /** One `void` per rule entry, each with its own write id, while the call's budget lasts. */
  async function voidEntries(resolved, entries, bound) {
    for (const item of entries) {
      if (bound.expired) return
      await send({ event: "void", ...resolved.key, cwd: resolved.cwd, write_ids: [item.write_id] }, bound)
    }
  }

  /**
   * Run `inject(text)`; if the host injection fails, void what the engine recorded. An injection that
   * outlives the budget has an unknown outcome, so nothing is voided (a documented stale delivery).
   */
  async function deliver(resolved, response, inject, bound) {
    if (response === null) return notify(inject, bound)
    if (response.decision !== "context") return
    if (response.text === "" && response.entries.length === 0) return
    let outcome
    if (env[FAIL_INJECTION_VARIABLE] === "1") {
      outcome = { status: "error" } // the test-only switch fails every injection
    } else {
      // Every context response with entries goes through the real injection, whatever the text length,
      // so a recorded credit is voided when the injection (or its channel) fails.
      outcome = await bound.settle(invoke(inject, response.text))
    }
    if (outcome.status === "error") await voidEntries(resolved, response.entries, bound)
  }

  /** The read-by-hand notice through a channel, waited for only within the budget. */
  async function notify(inject, bound) {
    await bound.settle(invoke(inject, NOTICE))
  }

  async function before(input) {
    if (!input || input.tool !== "shell") return
    const command = input.input && input.input.command
    if (!isString(command)) return
    const bound = budget()
    let response = null
    try {
      const resolved = await lookup(input.sessionID, bound)
      if (resolved === null) return // an unverified key is never recorded under; execute.before has no channel
      if (!(await afterCompactions(input.sessionID, resolved.key, bound))) return
      response = await send({ event: "tool-before", ...resolved.key, cwd: resolved.cwd, command }, bound)
    } finally {
      bound.end()
    }
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
    const append = (text) => {
      // Mutate the result in place: OpenCode keeps the appended text in the transcript.
      const content = input.result && input.result.content
      if (!Array.isArray(content)) throw new Error("the tool result has no content array to append to")
      content.push({ type: "text", text })
    }
    const bound = budget()
    try {
      const resolved = await lookup(input.sessionID, bound)
      if (resolved === null) return await notify(append, bound)
      if (!(await afterCompactions(input.sessionID, resolved.key, bound))) return await notify(append, bound)
      const response = await send({ ...partial, ...resolved.key, cwd: resolved.cwd }, bound)
      await deliver(resolved, response, append, bound)
    } finally {
      bound.end()
    }
  }

  async function prompt(input) {
    const text = input && input.prompt && input.prompt.text
    if (!isString(text)) return
    const inject = (body) =>
      // resume:false: the synthetic message alone must not start a run.
      ctx.session.synthetic({ sessionID: input.sessionID, id: messageId(), text: body, resume: false })
    const bound = budget()
    try {
      const resolved = await lookup(input.sessionID, bound)
      if (resolved === null) return await notify(inject, bound)
      if (!(await afterCompactions(input.sessionID, resolved.key, bound))) return await notify(inject, bound)
      const response = await send({ event: "prompt", ...resolved.key, cwd: resolved.cwd, prompt: text }, bound)
      await deliver(resolved, response, inject, bound)
    } finally {
      bound.end()
    }
  }

  /** Send one observed compaction, within its own budget; nothing once `signal` (the watcher) aborted. */
  async function compact(sessionID, signal) {
    if (signal && signal.aborted) return
    const bound = budget(signal)
    try {
      const resolved = await lookup(sessionID, bound)
      // No channel, and no verified key to re-arm; an aborted watcher sends nothing late.
      if (resolved === null || bound.expired) return
      await send({ event: "compacted", ...resolved.key, cwd: resolved.cwd }, bound)
    } catch {
      // a compaction is best effort
    } finally {
      bound.end()
    }
  }

  /**
   * Handle one turn end (`session.execution.succeeded`) of a root session inside its own budget: send
   * `stop`, and on a `block` resume the session with the engine text through a synthetic prompt.
   * Nothing at all is done for a child session (an unverified chain is not a turn end either), when the
   * session record cannot be read, when the engine fails or the response is not a `block` (a `context`
   * is the engine-failure notice, and a stop moment has no notice channel), or when the budget runs out.
   * A failed resume voids the stop record, so a later stop in the same phase can remind again.
   */
  async function stopTurn(sessionID, signal) {
    const bound = budget(signal)
    const release = () => stopsInFlight.delete(sessionID)
    // The one-in-flight slot lasts for this call's budget, not for the resume promise: a resume that
    // never settles must not keep a later turn end from sending.
    bound.signal.addEventListener("abort", release, { once: true })
    try {
      const resolved = await lookup(sessionID, bound)
      if (resolved === null) return
      if (resolved.key.agent !== undefined) return // a child session's turn end is not a stop moment
      const response = await send({ event: "stop", ...resolved.key, cwd: resolved.cwd }, bound)
      if (response === null || response.decision !== "block") return
      let outcome
      if (env[FAIL_INJECTION_VARIABLE] === "1") {
        outcome = { status: "error" } // the test-only switch fails every injection
      } else {
        // resume:true continues the same session with the block text.
        outcome = await bound.settle(
          invoke(
            (text) => ctx.session.synthetic({ sessionID, id: messageId(), text, resume: true }),
            response.text,
          ),
        )
      }
      if (outcome.status === "error") await voidEntries(resolved, response.entries, bound)
    } catch {
      // a stop moment is best effort: nothing is retried and nothing is reported
    } finally {
      bound.end()
      release()
    }
  }

  /**
   * One verifier run for one turn end, in its own per-session slot (design D5). The verified cwd and the
   * root check come from `resolveSession`: a child session's turn end verifies nothing. A block resumes the
   * session with the verifier text, a notice is a message that starts no run, and a pass sends nothing; a
   * failed synthetic prompt is caught, so the watcher and the slot are never held by the host. Nothing is
   * sent after dispose or after the watcher aborted.
   */
  async function verifyTurn(sessionID, signal) {
    const resolved = await resolveSession(sessionID)
    if (signal.aborted || closed || resolved === null) return
    if (resolved.key.agent !== undefined) return // a child session's turn end starts no verifier
    const response = await verifier({ session_id: sessionID, cwd: resolved.cwd }, { signal })
    if (signal.aborted || closed) return
    if (!isPlainObject(response) || !isString(response.text) || response.text === "") return
    // Each result is its own synthetic prompt; the host's answer is not awaited, so a resume that never
    // settles cannot keep this session's verifier slot busy.
    const resume = response.kind === "block" ? true : response.kind === "notice" ? false : null
    if (resume === null) return
    const message = { sessionID, id: messageId(), text: response.text, resume }
    invoke(() => ctx.session.synthetic(message)).catch(() => {
      // a failed resume or notice never reports and never holds the stream
    })
  }

  /**
   * Claim a session's verifier slot and start one run, unless one is already in flight (a later turn end
   * starts no second run). The slot is released when the run settles, never by a timer, so a resume that
   * never settles does not keep it: the run's own budget bounds the launcher, not the slot.
   */
  function startVerifier(sessionID, signal) {
    if (closed || verifiesInFlight.has(sessionID) || (signal && signal.aborted)) return
    const controller = new AbortController()
    const slot = { controller, promise: null }
    verifiesInFlight.set(sessionID, slot)
    const abort = () => controller.abort()
    if (signal) signal.addEventListener("abort", abort, { once: true })
    slot.promise = verifyTurn(sessionID, controller.signal)
      .catch(() => {
        // a verifier run never reports and never holds the stream
      })
      .finally(() => {
        if (signal) signal.removeEventListener("abort", abort)
        if (verifiesInFlight.get(sessionID) === slot) verifiesInFlight.delete(sessionID)
      })
  }

  /**
   * Stop every in-flight verifier (design D5): the abort sends SIGTERM to each launcher, which stops its
   * dispatched child, and arms the SIGKILL grace; nothing more is sent or started. Resolves as soon as the
   * runs settle, bounded so a lookup or host call that ignores the abort cannot hold the disposer.
   */
  async function dispose() {
    closed = true
    const pending = []
    for (const slot of verifiesInFlight.values()) {
      try {
        slot.controller.abort()
      } catch {
        // an aborted controller is already stopped
      }
      if (slot.promise) pending.push(slot.promise)
    }
    if (pending.length === 0) return
    await new Promise((resolve) => {
      const bound = setTimer(resolve, DISPOSE_BOUND_MS)
      if (bound && typeof bound.unref === "function") bound.unref()
      Promise.all(pending.map((promise) => Promise.resolve(promise).catch(() => {}))).then(() => {
        clearTimer(bound)
        resolve()
      })
    })
  }

  /**
   * Handle one host event. A `session.execution.succeeded` is a turn end: its work starts detached, one
   * call in flight per session, each bounded by its own budget, so the event stream is never held.
   * `session.execution.interrupted` and `session.execution.failed` are not turn ends and are ignored, as
   * is every other event except a compaction. A `session.compaction.started` is registered at once as
   * pending for its session, so a gate or moment of that session (or of a child of it) that arrives
   * meanwhile waits for it.
   *
   * The engine stop and the verifier run are two independent slots: a busy engine stop never suppresses
   * the verifier, and a long verifier run never suppresses a later turn end's engine stop.
   */
  function onEvent(event, signal) {
    if (!event || (signal && signal.aborted)) return Promise.resolve()
    if (event.type === "session.execution.succeeded") {
      const sessionID = event.data && event.data.sessionID
      if (!isString(sessionID) || sessionID === "") return Promise.resolve()
      if (!stopsInFlight.has(sessionID)) {
        stopsInFlight.add(sessionID)
        stopTurn(sessionID, signal).catch(() => {
          // a detached stop call never reports and never holds the stream
        })
      }
      startVerifier(sessionID, signal)
      return Promise.resolve()
    }
    if (event.type !== "session.compaction.started") return Promise.resolve()
    const sessionID = event.data && event.data.sessionID
    if (!isString(sessionID) || sessionID === "") return Promise.resolve()
    const previous = compactions.get(sessionID) || Promise.resolve()
    const run = previous.then(() => compact(sessionID, signal))
    compactions.set(sessionID, run)
    run.then(() => {
      if (compactions.get(sessionID) === run) compactions.delete(sessionID)
    })
    return run
  }

  /**
   * Consume the host event stream until `signal` aborts; never rejects. A stream that fails or ends is
   * subscribed again after `sleep(delay, signal)`, the delay doubling from 1 s up to 30 s and starting over
   * once an event arrives. One failing event never ends the stream, and no event is acted on after the abort.
   */
  async function watch(signal) {
    let delay = BACKOFF_START_MS
    while (!signal.aborted) {
      try {
        for await (const event of ctx.event.subscribe({ signal })) {
          if (signal.aborted) return
          delay = BACKOFF_START_MS
          try {
            await onEvent(event, signal)
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

  return { keyOf, before, after, prompt, onEvent, watch, dispose }
}

/** Resolves after `ms`, or as soon as `signal` aborts. */
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
 * `setup` returns an async disposer, like `opencode-rules`: it aborts the event stream, stops an in-flight
 * verifier, disposes the hook registrations and then waits for the watcher, each step bounded.
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
  const timers = { set: runtime.setTimer || setTimeout, clear: runtime.clearTimer || clearTimeout }
  return {
    id: "veles-hooked-rules",
    async setup(ctx) {
      const handlers = createHandlers(ctx, {
        engine: (event, options) => runEngine(event, { ...runtime, ...options }),
        verifier: (event, options) => runVerifier(event, { ...runtime, ...options }),
        env,
        sleep,
        setTimer: timers.set,
        clearTimer: timers.clear,
      })
      const registrations = []
      registrations.push(await ctx.tool.hook("execute.before", handlers.before))
      registrations.push(await ctx.tool.hook("execute.after", handlers.after))
      registrations.push(await ctx.session.hook("prompt", handlers.prompt))
      const controller = new AbortController()
      const watcher = handlers.watch(controller.signal)
      return async () => {
        // First stop the stream, then release the hooks (never dependent on the loop), stop a verifier
        // still running, and only then wait for the loop, and only up to a bound: a `next()` that ignores
        // the signal must not hold the disposer. The abort also ends an in-flight compaction at once and
        // keeps it from sending late, and it aborts every verifier slot with the same signal.
        controller.abort()
        for (const registration of registrations.reverse()) {
          try {
            if (registration && typeof registration.dispose === "function") await registration.dispose()
          } catch {
            // a registration that cannot be disposed is already gone
          }
        }
        try {
          await handlers.dispose() // bounded inside; a verifier run resolves at once on the abort
        } catch {
          // a verifier slot that cannot be stopped is already gone
        }
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
