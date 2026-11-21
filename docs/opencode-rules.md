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

## The verify Stop hook

Claude Code's `Stop` event also runs `python3 -I scripts/verify.py --hook claude` (the task gate over the
affected nodes). The plugin gives OpenCode the same rule delivery and the same gates, but not that hook:
OpenCode has no equivalent yet, so an OpenCode agent runs `python3 -I scripts/verify.py` itself before
calling the work done.

## Untrusted checkouts

Any agent configuration in a checkout (`opencode.json`, `.claude/settings.json`, auto-discovered plugins
and more) can make your tool install and run code, and this repository's hooks run a script on every
shell command. Review an untrusted PR from a trusted checkout (its diff, not its working tree) or with a
file viewer that is not an agent. Run an agent in it only after disabling all of that tool's project
configuration; removing the known entries is not enough, since the checkout can add others.
