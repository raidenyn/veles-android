## MODIFIED Requirements

### Requirement: Fail open
The engine and both adapters SHALL never block a tool call because of their own failure.

**Failures covered.** An internal error, a lock timeout, a 5-second hook timeout, or a host failure, cancellation or
timeout after the engine's write.

**Behaviour.**

- The call MUST pass.
- Where a channel exists, the agent MUST be told to read the hooked rules listed in `AGENTS.md` by hand. The
  OpenCode plugin MUST send that notice itself when Python is missing or the engine times out.
- In Claude Code, a missing Python MUST leave only the pointer, and this MUST be documented.

**Stale credit.** It is an accepted residual and MUST be documented: until the context's next re-arm, later gated
calls of that rule in that context may pass without the guide.

**OpenCode `void`.** When the OpenCode plugin's own injection fails, it MUST send one `void` per rule entry in that
response, each with the entry's write id. The engine MUST remove an entry only if it still carries exactly that
write id.

**Documentation.**

- `docs/AGENTS.md` MUST gain a short hook-delivered rules section limited to what an agent needs on every
  checkout: how to add a rule (location, naming, the grammar pointer and the `pointers --write` step), the
  supported configuration, the root 40-line cap and that each rule adds one pointer line, and a link to
  `docs/hook-delivered-rules.md`.
- `docs/opencode-rules.md` MUST warn that the repository configuration (`.claude/settings.json`,
  `opencode.json`) can make an agent tool install and run code and that the hooks run a script on every
  shell command; `CONTRIBUTING.md` MUST cross-link the warning, and `docs/AGENTS.md` MUST link that page.
- `docs/hook-delivered-rules.md` MUST describe:
  - adding a rule, with the grammar;
  - delivery, gates, pending entries and re-arm;
  - the supported configuration;
  - each degraded mode and what the agent sees in it;
  - the stale-credit and parallel-batch residuals;
  - the verified tools and versions.

#### Scenario: Corrupt state
- **WHEN** the session file contains invalid JSON and the agent runs `git commit`
- **THEN** the call passes and the agent receives the read-by-hand notice

#### Scenario: Forced failure after the write
- **WHEN** a test-only environment switch makes `hook` fail after writing
- **THEN** the tool call proceeds on both hosts, and the stale credit is observed for the first and a second later gated call

#### Scenario: Late void
- **WHEN** a sibling call rewrote the entry before the plugin's `void` for an older write id arrives
- **THEN** `void` changes nothing
