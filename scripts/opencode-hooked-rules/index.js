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
export const FAIL_INJECTION_VARIABLE = "VELES_AGENT_RULES_FAIL_INJECTION"

const PASS = Object.freeze({ decision: "pass", text: "", entries: Object.freeze([]) })
const MAX_PARENT_DEPTH = 32

function isString(value) {
  return typeof value === "string"
}

/** The decoded engine response when it has the plain contract's shape, otherwise null. */
function decode(stdout) {
  if (stdout.trim() === "") return PASS // exit 0 and nothing printed: a normal pass
  let value
  try {
    value = JSON.parse(stdout)
  } catch {
    return null
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null
  if (value.decision !== "context" && value.decision !== "deny") return null
  if (!isString(value.text) || !Array.isArray(value.entries)) return null
  for (const item of value.entries) {
    if (item === null || typeof item !== "object" || !Number.isInteger(item.write_id)) return null
  }
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
  } = {},
) {
  return new Promise((resolve) => {
    let settled = false
    let timer
    let child
    const finish = (value) => {
      if (settled) return
      settled = true
      if (timer !== undefined) clearTimer(timer)
      resolve(value)
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
    timer = setTimer(() => {
      try {
        child.kill()
      } catch {
        // already gone
      }
      finish(null)
    }, timeoutMs)
    const chunks = []
    if (child.stdout) child.stdout.on("data", (chunk) => chunks.push(Buffer.from(chunk)))
    child.on("error", () => finish(null))
    child.on("close", (code) => {
      if (code !== 0) return finish(null)
      finish(decode(Buffer.concat(chunks).toString("utf8")))
    })
    try {
      if (child.stdin) {
        child.stdin.on("error", () => {}) // an early exit closes the pipe; the close event decides
        child.stdin.end(JSON.stringify(event))
      }
    } catch {
      finish(null)
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
 * test-only injection switch.
 */
export function createHandlers(ctx, { engine = runEngine, env = process.env } = {}) {
  const directory = ctx.location.directory
  const keys = new Map()

  /** `{session}` for a root session; `{session: root, agent: child}` for a subagent's child session. */
  async function keyOf(sessionID) {
    if (keys.has(sessionID)) return keys.get(sessionID)
    let key = { session: sessionID }
    try {
      let current = sessionID
      let parent = null
      for (let depth = 0; depth < MAX_PARENT_DEPTH; depth += 1) {
        const info = await ctx.session.get({ sessionID: current })
        if (!info || !isString(info.parentID) || info.parentID === "") break
        parent = info.parentID
        current = parent
      }
      if (parent !== null) key = { session: current, agent: sessionID }
    } catch {
      return key // not cached: the lookup may work next time
    }
    keys.set(sessionID, key)
    return key
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
    if (response === null) {
      try {
        await inject(NOTICE)
      } catch {
        // no channel left: the call already passes
      }
      return
    }
    if (response.decision !== "context" || response.text === "") return
    try {
      if (env[FAIL_INJECTION_VARIABLE] === "1") throw new Error(`${FAIL_INJECTION_VARIABLE}=1`)
      await inject(response.text)
    } catch {
      await voidEntries(key, response.entries)
    }
  }

  async function before(input) {
    if (!input || input.tool !== "shell") return
    const command = input.input && input.input.command
    if (!isString(command)) return
    const key = await keyOf(input.sessionID)
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
    const key = await keyOf(input.sessionID)
    const response = await send({ ...partial, ...key, cwd: directory })
    await deliver(key, response, async (text) => {
      // Mutate the result in place: OpenCode keeps the appended text in the transcript.
      const content = input.result && input.result.content
      if (!Array.isArray(content)) throw new Error("the tool result has no content array to append to")
      content.push({ type: "text", text })
    })
  }

  async function prompt(input) {
    const text = input && input.prompt && input.prompt.text
    if (!isString(text)) return
    const key = await keyOf(input.sessionID)
    const response = await send({ event: "prompt", ...key, cwd: directory, prompt: text })
    await deliver(key, response, (body) =>
      // resume:false: the synthetic message alone must not start a run.
      ctx.session.synthetic({ sessionID: input.sessionID, id: messageId(), text: body, resume: false }),
    )
  }

  async function onEvent(event) {
    if (!event || event.type !== "session.compaction.started") return
    const sessionID = event.data && event.data.sessionID
    if (!isString(sessionID)) return
    const key = await keyOf(sessionID)
    await send({ event: "compacted", ...key, cwd: directory })
  }

  /** Consume the host event stream until it ends; never rejects. */
  async function watch(signal) {
    try {
      for await (const event of ctx.event.subscribe({ signal })) {
        try {
          await onEvent(event)
        } catch {
          // one bad event must not end the stream
        }
      }
    } catch {
      // no event stream: compaction re-arm is lost, the pointer block still names the rules
    }
  }

  return { keyOf, before, after, prompt, onEvent, watch }
}

/** The plugin object; `options` are the injectable seams (`spawn`, timers, `env`) used by the tests. */
export function createPlugin({ spawn, setTimer, clearTimer, env = process.env } = {}) {
  const runtime = {}
  if (spawn) runtime.spawn = spawn
  if (setTimer) runtime.setTimer = setTimer
  if (clearTimer) runtime.clearTimer = clearTimer
  return {
    id: "veles-hooked-rules",
    async setup(ctx) {
      const handlers = createHandlers(ctx, { engine: (event) => runEngine(event, runtime), env })
      await ctx.tool.hook("execute.before", handlers.before)
      await ctx.tool.hook("execute.after", handlers.after)
      await ctx.session.hook("prompt", handlers.prompt)
      void handlers.watch(new AbortController().signal)
    },
  }
}

export default createPlugin()
