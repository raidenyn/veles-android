// Generated from src/hook-runtime/claude.mts by scripts/build.mts. Do not edit.
// Claude Code entry: one command for every hook event (design D3). It reads the hook payload from
// stdin, dispatches it to the installed handlers and prints the host's hook JSON. It exits 0 on
// every path: a hook never blocks the host on its own failure.
import { spawn as spawnChild } from 'node:child_process';
import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { capabilityNotice, HOST_CAPABILITIES } from './lib/capabilities.mjs';
import { CLAUDE_EVENTS, HOST, toEvent, toOutput } from './lib/claude-adapter.mjs';
import { createRuntime, failureNotice, loadHandlers, OUTPUT_MS, writeTrace } from './lib/runtime.mjs';

const HOST_MARGIN_MS = 7000; // work deadline = host deadline - 7 s (cleanup 6 s + output 1 s)
const GIT_TIMEOUT_MS = 2000;
const errText = (err) => (err instanceof Error ? err.message : String(err));

// spawn(argv, {cwd, env, timeoutMs}) -> Promise<{code, signal, stdout, stderr, pgid}>
export function defaultSpawn(argv, { cwd, env, timeoutMs } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawnChild(argv[0], argv.slice(1), {
      cwd,
      env: env ? { ...process.env, ...env } : process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    const t = timeoutMs ? setTimeout(() => child.kill('SIGKILL'), timeoutMs) : null;
    child.stdout.on('data', (d) => {
      stdout += d;
    });
    child.stderr.on('data', (d) => {
      stderr += d;
    });
    child.on('error', (err) => {
      if (t) clearTimeout(t);
      reject(err);
    });
    child.on('close', (code, signal) => {
      if (t) clearTimeout(t);
      resolve({ code, signal, stdout, stderr, pgid: child.pid });
    });
  });
}

// CLAUDE_PROJECT_DIR, else the top level of the git checkout around payload.cwd, else that folder.
async function findRepoRoot(payload, { cwd, env, spawn }) {
  if (env.CLAUDE_PROJECT_DIR) return env.CLAUDE_PROJECT_DIR;
  const start = typeof payload?.cwd === 'string' && payload.cwd ? payload.cwd : cwd;
  try {
    const r = await spawn(['git', 'rev-parse', '--show-toplevel'], { cwd: start, timeoutMs: GIT_TIMEOUT_MS });
    const top = r.code === 0 ? r.stdout.trim() : '';
    if (top) return top;
  } catch {}
  return start;
}

// Resolves once fn() settles or the deadline passes, whichever is first; never rejects.
function bounded(fn, until) {
  return () =>
    new Promise((resolve) => {
      const t = setTimeout(resolve, Math.max(0, until - Date.now()));
      Promise.resolve()
        .then(fn)
        .catch(() => {})
        .finally(() => {
          clearTimeout(t);
          resolve();
        });
    });
}

const parse = (text) => {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
};

// main({stdin, cwd, env, spawn, now, capabilities}) -> {text, ack, release, cleanupDeadline}. `ack`
// is called once `text` was written, `release` when it could not be; both are bounded by
// cleanupDeadline.
export async function main({
  stdin,
  cwd = process.cwd(),
  env = process.env,
  spawn = defaultSpawn,
  now = Date.now,
  capabilities = HOST_CAPABILITIES,
} = {}) {
  const start = now();
  const empty = { text: '', ack: null, release: null, cleanupDeadline: start };
  const payload = parse(String(stdin ?? ''));
  const name = payload?.hook_event_name;
  if (!Object.hasOwn(CLAUDE_EVENTS, name ?? '')) return empty;
  const hostDeadline = start + CLAUDE_EVENTS[name] * 1000;
  const cleanupDeadline = hostDeadline - OUTPUT_MS;
  let merged;
  let type = 'event';
  try {
    const repoRoot = await findRepoRoot(payload, { cwd, env, spawn });
    const mapped = toEvent(payload, repoRoot);
    if (!mapped) return empty;
    type = mapped.event.type;
    const { handlers, problems } = await loadHandlers(repoRoot);
    const handlersLoadedAt = now();
    const runtime = createRuntime({ repoRoot, host: HOST, handlers, problems, now });
    merged = await runtime.dispatch(mapped.event, {
      sessionId: mapped.sessionId,
      agentId: mapped.agentId,
      hostDeadline,
      workDeadline: hostDeadline - HOST_MARGIN_MS,
    });
    writeTrace(
      repoRoot,
      {
        event: type,
        processStart: performance.timeOrigin,
        receivedAt: start,
        handlersLoadedAt,
        settledAt: now(),
        outcomes: merged.outcomes ?? {},
      },
      env,
    );
    // The capability check runs at session.start and adds a context line when something is missing.
    const missing = type === 'session.start' ? capabilityNotice(HOST, handlers, capabilities) : null;
    if (missing) merged = { ...merged, notices: [...(merged.notices ?? []), missing] };
    const out = toOutput(name, merged);
    const text = out ? JSON.stringify(out) : '';
    const written = out && typeof out.reason === 'string' && out.decision === 'block';
    if (!written && merged.release) await bounded(merged.release, cleanupDeadline)();
    return {
      text,
      ack: written && merged.ack ? bounded(merged.ack, cleanupDeadline) : null,
      release: written && merged.release ? bounded(merged.release, cleanupDeadline) : null,
      cleanupDeadline,
    };
  } catch (err) {
    if (merged?.release) await bounded(merged.release, cleanupDeadline)();
    const out = toOutput(name, { notices: [failureNotice('hook-runtime', type, errText(err))] });
    return { ...empty, text: out ? JSON.stringify(out) : '', cleanupDeadline };
  }
}

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString('utf8');
}

async function runAsScript() {
  let result = { text: '', ack: null, release: null };
  try {
    result = await main({ stdin: await readStdin() });
  } catch {}
  const finish = async (err) => {
    try {
      if (err) await result.release?.();
      else await result.ack?.();
    } catch {}
    process.exit(0);
  };
  if (!result.text) return finish();
  try {
    process.stdout.write(result.text, (err) => {
      finish(err);
    });
  } catch (err) {
    finish(err);
  }
}

const isScript = (() => {
  try {
    return process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href;
  } catch {
    return false;
  }
})();

if (isScript) {
  process.on('uncaughtException', () => process.exit(0));
  process.on('unhandledRejection', () => process.exit(0));
  runAsScript();
}
