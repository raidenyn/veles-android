## ADDED Requirements

### Requirement: The review-gate and roster rules are hook-delivered
The review-gate rule SHALL be `.agents/hooked-rules/repo-superspec-review-gate.md` and the roster
`.agents/hooked-rules/repo-review-models.md` (capability `hooked-rule-delivery`).

- The review-gate rule MUST be delivered at the fast-forward, propose, new-change, continue, apply, verify and
  archive skills, at `openspec new change`, and at `openspec instructions` for each planning artifact
  (brainstorm, proposal, design, specs, tasks, plan).
- The roster MUST be delivered at the fast-forward, propose, new-change, continue and archive skills and at the
  same two command patterns.
- Each pointer MUST keep its fact always on: for the review gate, that every planning file and the closeout docs
  are reviewed, the preset is asked first and the workflow stops after the plan; for the roster, that models and
  presets live only in the roster and no model is substituted.
- The `openspec/config.yaml` hooks and the schema MUST cite the rules by their `.agents/hooked-rules/` paths.

#### Scenario: Fast-forward starts
- **WHEN** an agent invokes `openspec-ff-change`
- **THEN** the review-gate rule and the roster are added to its context

#### Scenario: Planning instructions
- **WHEN** `openspec instructions brainstorm --change x` succeeds in a context that has not received the rules from that trigger
- **THEN** both rules are added to that call's output

#### Scenario: Verify audits without the roster
- **WHEN** the agent invokes `openspec-verify-change`
- **THEN** the review-gate rule is added and the roster is not

## MODIFIED Requirements

### Requirement: The reviewer roster is the single source of models
`.agents/hooked-rules/repo-review-models.md` SHALL hold a table of reviewers (label, model name, effort) and a list of presets, each preset an ordered list of reviewer labels. The roster MUST define at least two reviewers, a single-reviewer preset for each of them, and a two-reviewer preset that runs them in order (first, then second). The roster MUST state the capability a reviewer needs (a read-only subagent on the named model at the named effort). No other workflow instruction file SHALL name a model, namely the rules in `.agents/rules/`, the other rules in `.agents/hooked-rules/`, `openspec/schemas/`, `openspec/config.yaml`, `openspec/specs/`, `docs/superspec-workflow.md`, `docs/AGENTS.md`, `docs/agent-guidance.md`, `docs/openspec-workflow.md`, `docs/opencode-rules.md` and `docs/hook-delivered-rules.md`; they MUST refer to "the roster's presets" and to the "first" and "second" reviewer of a preset. `openspec/changes/**` and historical evidence, investigations and records elsewhere under `docs/` are outside this restriction. An agent MUST NOT substitute another model for an unavailable one: it MUST stop and tell the user. With a two-reviewer preset and one reviewer unavailable it MUST stop and ask, and MAY run the other reviewer alone only if the user says so.

#### Scenario: A model changes
- **WHEN** a model name in the roster is replaced by a newer one
- **THEN** no other rule, schema, config, spec or docs file needs an edit

#### Scenario: A model name appears elsewhere
- **WHEN** a roster model name appears in a workflow instruction file listed above other than the roster
- **THEN** the plan's verification step (a grep over exactly those paths) fails and the occurrence is replaced by a reference to the roster

#### Scenario: A model name in another hooked rule
- **WHEN** `.agents/hooked-rules/repo-superspec-review-gate.md` names a roster model
- **THEN** the verification grep fails

#### Scenario: Historical documents
- **WHEN** a historical document under `docs/` outside the listed files names a model
- **THEN** the verification step does not flag it

#### Scenario: Unavailable model
- **WHEN** the agent's tool cannot start a roster model
- **THEN** the agent stops, tells the user and does not use a different model

#### Scenario: Two-reviewer preset with one reviewer unavailable
- **WHEN** the session's preset is a two-reviewer preset and the second reviewer cannot be started
- **THEN** the agent stops and asks, and runs the first reviewer alone only after the user says so

### Requirement: openspec/config.yaml carries thin hooks
`openspec/config.yaml` SHALL carry a per-artifact rule for each of the six planning artifacts (brainstorm, proposal, design, specs, tasks, plan) that runs the review gate after the artifact is written, citing `.agents/hooked-rules/repo-superspec-review-gate.md`; an `operations.apply.guidance` list with one entry that points to the apply stop and a separate entry, owned by capability `superspec-closeout`, that tells the agent to run verify and then finalize without asking once every task is done; and an `operations.archive.guidance` entry that points to the closeout's archive-once procedure. The hooks MUST name no model and no project, and MUST survive a reinstall of the schema.

#### Scenario: Per-artifact hooks
- **WHEN** `openspec instructions <artifact>` is run for any of the six planning artifacts
- **THEN** the returned rules include the review-gate hook with the `.agents/hooked-rules/` path

#### Scenario: Operation guidance
- **WHEN** the apply and archive operations read the project config
- **THEN** apply's guidance points to the apply stop and to running verify and finalize without asking, and archive's guidance points to the closeout

#### Scenario: Schema reinstall
- **WHEN** the superspec schema is reinstalled from upstream
- **THEN** the hooks in `openspec/config.yaml` are unchanged

### Requirement: The review-records audit runs whenever verification runs
Whenever verification is performed, however it is invoked, the agent SHALL audit that every planning file has an approved record from every reviewer listed in the record header's preset, with a current hash, upstream hashes covering the full upstream set, and no open critical or high finding. A hash re-pointed after a non-triggering edit MUST carry the note naming the fixes; a re-point without that note, or a triggering edit that was not re-reviewed, MUST be FAIL. Missing records, stale hashes, a smaller upstream set, a missing reviewer and unresolved critical or high findings MUST be FAIL. The `verify` instruction of the schema MUST carry the same check as a sixth check, `templates/verify.md` MUST have a "Review records" section for it (artifact, reviewer, hash state, open critical or high findings), and the schema text that counts the checks MUST say six.

#### Scenario: Direct verify invocation
- **WHEN** `openspec-verify-change` runs without fetching the schema's verify instruction
- **THEN** the audit still runs because the review-gate rule, delivered at the verify skill (or read by hand from its pointer), requires it

#### Scenario: One reviewer missing
- **WHEN** a session using a two-reviewer preset stopped after the first reviewer approved
- **THEN** the audit in a later session reports FAIL for the missing second reviewer

#### Scenario: Stale hash
- **WHEN** a planning file differs from its approved hash and the change is not a noted non-triggering fix
- **THEN** the audit reports FAIL naming the file

#### Scenario: Noted re-point
- **WHEN** a record's hash was re-pointed after a non-triggering edit with a note naming the fixes
- **THEN** the audit passes

#### Scenario: Unnoted re-point
- **WHEN** a recorded hash was changed without the required note
- **THEN** the audit reports FAIL
