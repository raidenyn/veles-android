## MODIFIED Requirements

### Requirement: The reviewer roster is the single source of models
`.agents/rules/repo-review-models.md` SHALL hold a table of reviewers (label, model name, effort) and a list of presets, each preset an ordered list of reviewer labels. The roster MUST define at least two reviewers, a single-reviewer preset for each of them, and a two-reviewer preset that runs them in order (first, then second). The roster MUST state the capability a reviewer needs (a read-only subagent on the named model at the named effort). No other workflow instruction file SHALL name a model, namely the other rules in `.agents/rules/`, `openspec/schemas/`, `openspec/config.yaml`, `openspec/specs/`, `docs/superspec-workflow.md`, `docs/AGENTS.md`, `docs/agent-guidance.md`, `docs/openspec-workflow.md` and `docs/opencode-rules.md`; they MUST refer to "the roster's presets" and to the "first" and "second" reviewer of a preset. `openspec/changes/**` and historical evidence, investigations and records elsewhere under `docs/` are outside this restriction. An agent MUST NOT substitute another model for an unavailable one: it MUST stop and tell the user. With a two-reviewer preset and one reviewer unavailable it MUST stop and ask, and MAY run the other reviewer alone only if the user says so.

#### Scenario: A model changes
- **WHEN** a model name in the roster is replaced by a newer one
- **THEN** no other rule, schema, config, spec or docs file needs an edit

#### Scenario: A model name appears elsewhere
- **WHEN** a roster model name appears in a workflow instruction file listed above other than the roster
- **THEN** the plan's verification step (a grep over exactly those paths) fails and the occurrence is replaced by a reference to the roster

#### Scenario: Historical documents
- **WHEN** a historical document under `docs/` outside the listed files names a model
- **THEN** the verification step does not flag it

#### Scenario: Unavailable model
- **WHEN** the agent's tool cannot start a roster model
- **THEN** the agent stops, tells the user and does not use a different model

#### Scenario: Two-reviewer preset with one reviewer unavailable
- **WHEN** the session's preset is a two-reviewer preset and the second reviewer cannot be started
- **THEN** the agent stops and asks, and runs the first reviewer alone only after the user says so

### Requirement: The schema fork carries the gate and records its divergences
The superspec schema fork SHALL stay at version 5 in `schema.yaml` and its description history, in its README and INTEGRATION version mentions and in the `(schema version v5)` text of `docs/AGENTS.md`. `docs/openspec-workflow.md` MUST list every divergence from upstream (the apply precondition including the note condition, the sixth verify check and its template section including the note condition, the finalize hand-off and next-step text) so a reinstall can re-apply them, and MUST name `docs/AGENTS.md` explicitly where the version mention lives rather than saying "this file"; `grep -rn '/opsx:' openspec/schemas` MUST still return no hits. The generated `openspec-*` skills and the `superpowers:*` skills MUST remain unmodified.

#### Scenario: Divergences documented
- **WHEN** the schema is reinstalled from upstream
- **THEN** `docs/openspec-workflow.md` lists each edit to re-apply, including the note condition in the apply pre-flight and the sixth verify check, and names `docs/AGENTS.md` as the home of the version mention

#### Scenario: Skills untouched
- **WHEN** this change's diff is inspected
- **THEN** it touches no file under `.agents/skills/` or `.claude/skills/`
