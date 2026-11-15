# superspec-review-gate Specification

## Purpose
This capability defines how every superspec planning file, and the documentation produced at closeout, is reviewed by an independent subagent before it is used: the single reviewer roster, the ask-first step, the bounded review loop, escalation to the user, content-bound review records, the verify audit, the stop before apply and the schema fork's divergences.

## Requirements

### Requirement: The reviewer roster is the single source of models
`.agents/rules/repo-review-models.md` SHALL hold a table of reviewers (label, model name, effort) and a list of presets, each preset an ordered list of reviewer labels. The roster MUST define at least two reviewers, a single-reviewer preset for each of them, and a two-reviewer preset that runs them in order (first, then second). The roster MUST state the capability a reviewer needs (a read-only subagent on the named model at the named effort). No other workflow instruction file SHALL name a model, namely the other rules in `.agents/rules/`, `openspec/schemas/`, `openspec/config.yaml`, `openspec/specs/`, `docs/superspec-workflow.md` and `docs/AGENTS.md`; they MUST refer to "the roster's presets" and to the "first" and "second" reviewer of a preset. `openspec/changes/**` and historical evidence, investigations and records elsewhere under `docs/` are outside this restriction. An agent MUST NOT substitute another model for an unavailable one: it MUST stop and tell the user. With a two-reviewer preset and one reviewer unavailable it MUST stop and ask, and MAY run the other reviewer alone only if the user says so.

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

### Requirement: The agent asks for the reviewer preset first
Before the first review of a session the agent SHALL ask the user to choose a roster preset or a custom model, listing the presets from the roster. A custom model MUST apply to that session only and MUST NOT be added to the roster or to any rule, schema or config, nor stored as a default; the review records of that session MUST identify it with the label `custom` (`custom-1`, `custom-2` and so on when there are several) and name the model in the record header. The choice MUST NOT be remembered across sessions: a session that continues an existing change MUST ask again. The agent MAY escalate a single-reviewer choice to a two-reviewer preset for a complicated artifact, stating why, and MUST NOT downgrade the user's choice.

#### Scenario: First review of a session
- **WHEN** a session is about to run its first review
- **THEN** the agent asks which preset or custom model to use before reviewing anything

#### Scenario: Continuing a change in a new session
- **WHEN** a new session continues a change whose earlier session chose a preset
- **THEN** the agent asks again and does not reuse the earlier choice

#### Scenario: Custom model
- **WHEN** the user names a custom model
- **THEN** it is used for that session, the roster and rules are unchanged, and the session's review records name it in their headers under the label `custom` (numbered when there are several)

#### Scenario: Escalation to a two-reviewer preset
- **WHEN** the user chose a single-reviewer preset and an artifact is complicated
- **THEN** the agent may add the second reviewer after stating the reason, and never removes a reviewer the user chose

### Requirement: Every planning file is reviewed before it is used
The agent SHALL have an independent, read-only subagent review each planning file (brainstorm, proposal, design, specs, tasks, plan) and the documentation produced at closeout, and MUST NOT present a file to the user as final or approved, or read it as input for a later artifact, until it has an approved review record for its current content; the interactive brainstorm dialogue and an escalation MAY show unapproved content to the user. The gate applies to the files a phase produces or finds, so it MUST cover a `design.md` pre-filled by brainstorming, files written through `openspec-ff-change` or `openspec-propose`, and any planning file with no approved record. The rule file MUST NOT depend on the `openspec/config.yaml` hook alone. Implementation review is outside this gate and MUST keep superspec's standard single review subagent. The reviewer brief MUST give the reviewer the artifact, the change directory and the upstream artifacts, and ask whether the artifact satisfies its inputs, what a builder would still need to know and what is wrong or risky; the reviewer MUST grade each finding critical, high, medium or low and give a verdict of approve or changes-required.

#### Scenario: Design pre-filled by brainstorming
- **WHEN** brainstorming leaves a `design.md` with content
- **THEN** it is reviewed before the tasks artifact reads it

#### Scenario: Fast-forward or propose
- **WHEN** `openspec-ff-change` or `openspec-propose` writes several artifacts in a row
- **THEN** each is reviewed and approved before the next one reads it

#### Scenario: File found without a record
- **WHEN** a planning file exists and has no approved review record
- **THEN** it is reviewed before anything uses it

#### Scenario: Implementation
- **WHEN** an implementation task is reviewed during apply
- **THEN** superspec's standard single review subagent is used and the roster is not consulted

### Requirement: openspec/config.yaml carries thin hooks
`openspec/config.yaml` SHALL carry a per-artifact rule for each of the six planning artifacts (brainstorm, proposal, design, specs, tasks, plan) that runs the review gate after the artifact is written, an `operations.apply.guidance` entry that points to the apply stop, and an `operations.archive.guidance` entry that points to the closeout's archive-once procedure. The hooks MUST name no model and no project, and MUST survive a reinstall of the schema.

#### Scenario: Per-artifact hooks
- **WHEN** `openspec instructions <artifact>` is run for any of the six planning artifacts
- **THEN** the returned rules include the review-gate hook

#### Scenario: Operation guidance
- **WHEN** the apply and archive operations read the project config
- **THEN** apply's guidance points to the apply stop and archive's guidance points to the closeout

#### Scenario: Schema reinstall
- **WHEN** the superspec schema is reinstalled from upstream
- **THEN** the hooks in `openspec/config.yaml` are unchanged

### Requirement: A review pass is bounded
The agent SHALL fix or rebut every critical and high finding and have the reviewer re-review, until the reviewer approves with no open critical or high finding. Medium and low findings MUST be recorded with a disposition (fixed, rebutted, or deferred with a reason) and MUST NOT block approval. A rebuttal MUST give a reason, is judged by the reviewer in the next round and consumes a round. A **review pass** is one loop for one artifact and one reviewer: it begins when that reviewer's review opens, continues through every edit made in response to its findings without resetting the counter, and ends at that reviewer's approval or at escalation. A pass MUST be capped at 5 rounds per reviewer.

#### Scenario: Approval with medium findings
- **WHEN** a reviewer approves and leaves medium and low findings
- **THEN** each has a recorded disposition and the artifact is approved

#### Scenario: Rebuttal
- **WHEN** the agent rebuts a high finding
- **THEN** it gives a reason, the reviewer judges it in the next round and that round counts toward the cap

#### Scenario: Cap reached
- **WHEN** a reviewer has not approved after 5 rounds of a pass
- **THEN** the agent stops and escalates to the user

### Requirement: Two reviewers run in order with a bounded confirmation
With a two-reviewer preset the first reviewer MUST approve before the second starts. If the second reviewer's accepted edits change normative content, the first reviewer MUST run one confirmation pass capped at 2 rounds; wording-only edits are confirmed by the second reviewer's approving round. At most one confirmation pass MUST run per two-reviewer cycle, and if it demands normative edits the agent MUST escalate instead of reopening the second reviewer. A consistency revalidation after an upstream change is its own pass, capped at 2 rounds per reviewer.

#### Scenario: Sequential reviewers
- **WHEN** the preset is a two-reviewer preset
- **THEN** the second reviewer starts only after the first has approved

#### Scenario: Normative edits after the second reviewer
- **WHEN** the second reviewer's accepted edits change normative content
- **THEN** the first reviewer runs one confirmation pass of at most 2 rounds, and a demand for further normative edits escalates to the user

#### Scenario: Wording-only edits
- **WHEN** the second reviewer's accepted edits are wording only
- **THEN** its approving round confirms them and no confirmation pass runs

### Requirement: Escalation at a cap explains the problem to a human
When a cap is reached or a confirmation pass demands normative edits, the agent SHALL stop and write an explanation in plain language that states what the artifact is trying to do, the unresolved finding in ordinary words, what the reviewer argues, what the agent argues, the options with a recommendation, and the decision it needs, and MUST then wait for the user.

#### Scenario: Escalation text
- **WHEN** the agent escalates
- **THEN** the message contains the six parts (review-only check: it is written in plain language without unexplained jargon)

#### Scenario: No silent continuation
- **WHEN** a cap is reached
- **THEN** the agent does not start another round before the user answers

### Requirement: Review records bind approvals to content
Each review SHALL be recorded in `reviews/<artifact>-<reviewer-label>.md` of the change directory (the archived change's directory for closeout documentation, where the artifact id is `closeout-docs`, the header's upstream set is `none` and the hashes are keyed by the repo-relative path of each reviewed file), committed with the change. A record MUST have a header with the artifact, the reviewer, the session's preset as the ordered list of reviewers, the cap and the hashes of the upstream files it was judged against, then one section per pass with one table per round (severity, finding, disposition) and, for an approval, an `Approved content: <artifact> sha256 <hash>` line. The hash MUST be the sha256 of the file content after converting CR LF and CR to LF and rewriting the checkbox state of list items (`- [ ]`, `- [x]`, `- [X]`, and the same with a `*` marker, at the start of a line after optional indentation) to `[ ]`, computed by the single procedure given in `docs/superspec-workflow.md`, so that ticking checkboxes during apply never invalidates an approval while any change to other text does. The `specs` artifact MUST have one hash per spec file, keyed by its path relative to the change directory. **Upstream** means every planning file earlier in the order brainstorm, proposal, design, specs, tasks, plan.

#### Scenario: Ticking tasks during apply
- **WHEN** apply changes `- [ ]` to `- [x]` in `tasks.md` or `plan.md`
- **THEN** the recorded hash still matches and the approval stays valid

#### Scenario: Editing a task's text
- **WHEN** the text of a task changes after approval
- **THEN** the hash no longer matches and the approval is stale

#### Scenario: Several spec files
- **WHEN** a change has two spec files
- **THEN** the record holds one hash per file, keyed by its relative path

#### Scenario: Portable computation
- **WHEN** two agents compute the hash on Linux, macOS or Windows with Python 3
- **THEN** both get the same value for the same file

### Requirement: Edits and upstream changes reopen review
An edit made after an artifact's review cycle is complete SHALL reopen review for every reviewer that approved it, in preset order, as a new pass; an edit the author believes cosmetic MUST be confirmed cosmetic by a reviewer round (capped at 1) before the new hash is accepted, and the record MUST note the confirming round; if that round finds the edit is not cosmetic it MUST be treated as a substantive edit and open a new pass for every approving reviewer. An edit made during an active two-reviewer cycle MUST follow the confirmation rule and nothing else. When an upstream hash no longer matches the hash recorded in an approval, that approval MUST be stale, and only a reviewer round (each approving reviewer in preset order, capped at 2 rounds each) MAY revalidate it; the author alone MUST NOT. If a reviewer round confirms an upstream edit was cosmetic, the downstream records SHALL be re-pointed to the new upstream hash with a note citing that round and no new pass. The agent MUST NOT recompute or rewrite a recorded hash to silence an audit.

#### Scenario: Substantive edit after approval
- **WHEN** an approved design is edited substantively
- **THEN** every reviewer that approved it reviews again, in order

#### Scenario: Cosmetic edit
- **WHEN** the author fixes a typo after approval
- **THEN** one reviewer round confirms it is cosmetic and the record notes that round

#### Scenario: Cosmetic edit judged substantive
- **WHEN** the confirming reviewer round finds a claimed typo fix changes meaning
- **THEN** it is treated as a substantive edit and every approving reviewer reviews again

#### Scenario: Upstream change
- **WHEN** the brainstorm changes after the tasks were approved
- **THEN** the tasks approval is stale and a consistency revalidation by each approving reviewer is required

#### Scenario: Silencing an audit
- **WHEN** an agent rewrites a recorded hash to match an edited file
- **THEN** the audit's result is invalid and the change is treated as unreviewed

### Requirement: The review-records audit runs whenever verification runs
Whenever verification is performed, however it is invoked, the agent SHALL audit that every planning file has an approved record from every reviewer listed in the record header's preset, with a current hash, upstream hashes covering the full upstream set, and no open critical or high finding. Missing records, stale hashes, a smaller upstream set, a missing reviewer and unresolved critical or high findings MUST be FAIL. The `verify` instruction of the schema MUST carry the same check as a sixth check, `templates/verify.md` MUST have a "Review records" section for it (artifact, reviewer, hash state, open critical or high findings), and the schema text that counts the checks MUST say six.

#### Scenario: Direct verify invocation
- **WHEN** `openspec-verify-change` runs without fetching the schema's verify instruction
- **THEN** the audit still runs because the always-on rule requires it

#### Scenario: One reviewer missing
- **WHEN** a session using a two-reviewer preset stopped after the first reviewer approved
- **THEN** the audit in a later session reports FAIL for the missing second reviewer

#### Scenario: Stale hash
- **WHEN** a planning file differs from its approved hash
- **THEN** the audit reports FAIL naming the file

### Requirement: The workflow stops before apply
After the plan has an approved review record the agent SHALL stop, tell the user to switch to a coding model and wait for explicit confirmation before starting `openspec-apply-change`. A fresh session in which the user starts apply and says they are on a coding model MUST count as the confirmation; otherwise the agent MUST ask once. `openspec-ff-change` and `openspec-propose` MUST end at the plan for the same reason. The `apply:` block of the superspec schema MUST start with a precondition that refuses to proceed unless the plan has an approved record from every reviewer listed in its header, **current** meaning that the plan's own hash and every upstream hash recorded in the approval match the files on disk and the recorded upstream set is the full upstream set, and the user confirmed a coding model.

#### Scenario: Plan approved
- **WHEN** the plan passes its review
- **THEN** the agent asks the user to switch to a coding model and does not start apply

#### Scenario: New session for apply
- **WHEN** the user starts apply in a new session and says they are on a coding model
- **THEN** apply may start without asking again

#### Scenario: Stale plan
- **WHEN** the plan was edited after approval
- **THEN** the apply precondition refuses to proceed

#### Scenario: Stale upstream
- **WHEN** the brainstorm or design was edited after the plan was approved and the plan itself is unchanged
- **THEN** the apply precondition refuses to proceed until the plan is revalidated

#### Scenario: Missing second reviewer
- **WHEN** the plan has an approved record from only the first reviewer of a two-reviewer preset
- **THEN** the apply precondition refuses to proceed

#### Scenario: Fast-forward
- **WHEN** `openspec-ff-change` finishes the plan
- **THEN** it stops there and asks for the model switch

### Requirement: The schema fork carries the gate and records its divergences
The superspec schema fork SHALL be bumped to version 5 in `schema.yaml` and its description history, in its README and INTEGRATION version mentions and in the "(schema version v4)" text of `docs/AGENTS.md`. `docs/AGENTS.md` MUST list every divergence from upstream (the apply precondition, the sixth verify check and its template section, the finalize hand-off and next-step text) so a reinstall can re-apply them, and `grep -rn '/opsx:' openspec/schemas` MUST still return no hits. The generated `openspec-*` skills and the `superpowers:*` skills MUST remain unmodified.

#### Scenario: Divergences documented
- **WHEN** the schema is reinstalled from upstream
- **THEN** `docs/AGENTS.md` lists each edit to re-apply

#### Scenario: Skills untouched
- **WHEN** this change's diff is inspected
- **THEN** it touches no file under `.agents/skills/` or `.claude/skills/`
