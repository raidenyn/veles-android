# OpenCode and the `opencode-rules` plugin

OpenCode has no native path-scoped rules, so scoped rules reach it only through the third-party
`opencode-rules` plugin declared in `opencode.json`. The plugin loads `additionalRuleDirectories` (today
`.agents/rules`) and delivers a rule only when the files the agent reads match its `globs`; `repo-*` rules
are always on. The root `AGENTS.md` and every node's `## Rules` list keep the files discoverable if the
plugin is not active.

## Install

The plugin is configured once, in `opencode.json`, at the range pinned there; OpenCode installs a missing
configured plugin in the background. There is no separate install command, and the version must not be
repeated in any other document — `opencode.json` is the single source. The root `AGENTS.md` and
`CONTRIBUTING.md` state that the plugin is required; the range itself lives only in `opencode.json`.

## Check and update

`opencode plugin list` shows what is installed; `opencode plugin update` updates an installed package
within the pinned range. Widen the range only by editing `opencode.json` in a reviewed change; the
agent-file check rejects an unpinned or unbounded entry.

## Observed caveats

- **Two instances**: if `opencode-rules` is also enabled in a user-level OpenCode config, two plugin
  instances load and project rules are not delivered. Configure it once — remove or disable the duplicate
  global entry.
- **Repeated read** (`opencode-rules` 2.0.0): reading the same file a second time in one session can drop
  the scoped rule that the first read delivered, so a rule that applied earlier can stop applying. If a
  scoped rule appears to vanish, read the rule files listed in the node's `## Rules` section by hand; the
  always-on `repo-*` rules are unaffected.
- **Plugin unavailable**: with an offline cache miss, an install failure or the plugin disabled, OpenCode
  still starts; scoped rules are simply not delivered, and the root `AGENTS.md` pointer plus each node's
  listed rule files keep them discoverable. This degraded state is supported, not a repository error.

## The verify hook

At a root session's turn end (`session.execution.succeeded`), the plugin starts
`python3 -I <plugin checkout>/scripts/verify.py --hook opencode` with
`{"session_id": <session id>, "cwd": <session directory>}` on stdin and a budget of 1,800,000 ms. The
verifier runs in its own per-session slot, independent of the engine's `stop` call: the engine's stop
gate keeps its 5-second budget and its own resume, at every turn end, and neither waits for the
other. The engine stop is unchanged.

A verifier block is sent as its own `synthetic({resume: true})`; a `systemMessage` as a
`synthetic({resume: false})`; empty, malformed or oversized output is ignored. A turn end that
arrives while the verifier slot is busy starts no second run, and the running one still resumes the
session when it finishes, even if the user sent a new message meanwhile. On budget expiry or plugin
dispose the plugin sends SIGTERM to the launcher; on SIGTERM the launcher stops its dispatched child
and exits 0, and after a 5-second grace the plugin sends SIGKILL. No fallback queue was added.

Spike S1 (OpenCode 2.3.2) observed interleaved delivery: a `resume: true` synthetic admitted while a
user turn runs is not rejected and not parked. OpenCode admits it as a `steer` inbox item and
delivers it at the next step boundary of that execution, which then starts another step. In the runs
where it landed before the turn's final step, the model answered the block instead of the user's
request; when it landed during the final step, the user's answer was produced first and the turn
was extended. That is the owner's accepted trade-off; the block is not lost.

## Untrusted checkouts

Any agent configuration in a checkout (`opencode.json`, `.claude/settings.json`, auto-discovered plugins
and more) can make your tool install and run code, and this repository's hooks run a script on every
shell command. Review an untrusted PR from a trusted checkout (its diff, not its working tree) or with a
file viewer that is not an agent. Run an agent in it only after disabling all of that tool's project
configuration; removing the known entries is not enough, since the checkout can add others.
