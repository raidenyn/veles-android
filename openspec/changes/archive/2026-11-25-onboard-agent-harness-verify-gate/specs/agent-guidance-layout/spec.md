## MODIFIED Requirements

### Requirement: OpenCode scoped rules use a range-bounded, allow-listed plugin
`opencode.json` SHALL contain only the keys `$schema` and `plugins`, and `plugins` SHALL contain exactly three entries: the local hooked-rules plugin in `scripts/opencode-hooked-rules/` (declared with the local-entry syntax that the `hooked-rule-delivery` verification records), the agent harness hook runtime's local plugin `./.agents/harness/hook-runtime/opencode`, and the package `opencode-rules` constrained to the supported range `>=2.0.0 <3` (a caret or inequality range that admits no version below 2.0.0 or at 3.0.0 and above), never `latest`, `*`, an exact pin, or any range crossing the major bound, with `additionalRuleDirectories` containing `.agents/rules`. The root `AGENTS.md` and `CONTRIBUTING.md` MUST state that OpenCode users need the `opencode-rules` plugin at the range pinned in `opencode.json` for scoped rules to be delivered, without repeating the range. `docs/AGENTS.md` MUST state the same requirement in one sentence and link `docs/opencode-rules.md`, which MUST hold the install, update and untrusted-checkout guidance without repeating the range. The qualified plugin range and OpenCode version MUST be recorded where the system-level qualification keeps them; the plugin's installation, package-integrity and transitive-dependency policy and per-version regression tests are outside this change. A `.github/CODEOWNERS` entry MUST name an owner for `opencode.json`, `.claude/`, `.mcp.json` when present, `.agents/`, `**/AGENTS.md`, `/docs/superspec-workflow.md`, `/docs/agent-guidance.md`, `/docs/openspec-workflow.md`, `/docs/opencode-rules.md`, `/openspec/config.yaml`, `/openspec/schemas/`, `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/` (the instruction files agents follow and the code their tools run automatically; the entries for `/docs/superspec-workflow.md`, `/docs/agent-guidance.md`, `/docs/openspec-workflow.md`, `/docs/opencode-rules.md`, `/openspec/config.yaml`, `/openspec/schemas/`, `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/` MUST name the same owner as the existing agent-guidance entries, which review checks and the agent-file check does not), and contributor guidance MUST tell reviewers not to launch agents in an untrusted PR checkout, or to disable project configuration, before reading those files. If the plugin cannot be used, the root `AGENTS.md` and the node files MUST still make the rule files discoverable.

#### Scenario: Plugin requirement documented
- **WHEN** the root `AGENTS.md` or `CONTRIBUTING.md` does not mention `opencode-rules` as an OpenCode requirement, or `docs/AGENTS.md` lacks the one-sentence statement and link, or `docs/opencode-rules.md` lacks the install, update and untrusted-checkout guidance
- **THEN** the agent-file check or review fails, and no document repeats the pinned range

#### Scenario: Supported range accepted
- **WHEN** `opencode.json` lists `opencode-rules@>=2.0.0 <3` or `opencode-rules@^2.0.0`, the local hooked-rules plugin and the hook runtime's plugin, and nothing else
- **THEN** the agent-file check passes

#### Scenario: Unpinned or out-of-range plugin
- **WHEN** `opencode.json` lists `opencode-rules@latest`, `opencode-rules@*`, an exact pin, or a range that admits a version below 2.0.0 or at 3.0.0 and above
- **THEN** the agent-file check fails

#### Scenario: Additional plugin or key
- **WHEN** `opencode.json` adds a plugin other than `opencode-rules`, the local hooked-rules plugin and the hook runtime's plugin, an MCP server, a command or any key other than `$schema` and `plugins`
- **THEN** the agent-file check fails

#### Scenario: Rule directory configured
- **WHEN** `opencode.json` omits `.agents/rules` from `additionalRuleDirectories`
- **THEN** the agent-file check fails

#### Scenario: Code-owner coverage
- **WHEN** `.github/CODEOWNERS` lacks an entry for `opencode.json`, `.claude/`, `.agents/`, `**/AGENTS.md`, `/docs/superspec-workflow.md`, `/docs/agent-guidance.md`, `/docs/openspec-workflow.md`, `/docs/opencode-rules.md`, `/openspec/config.yaml`, `/openspec/schemas/`, `/scripts/agent_rules.py` or `/scripts/opencode-hooked-rules/`
- **THEN** the agent-file check fails

#### Scenario: Plugin unavailable
- **WHEN** OpenCode starts with the plugin unavailable (offline with an empty plugin cache, an install failure or the plugin disabled)
- **THEN** OpenCode still starts, scoped rules are not delivered, and an agent can still find them through the root `AGENTS.md` and the node files' read-the-rule instructions, which the verification record shows
