# superspec-review-gate Specification

## Purpose
This capability defines how every superspec planning file, and the documentation produced at closeout, is reviewed by an independent subagent before it is used: the single reviewer roster, the ask-first step, the bounded review loop, escalation to the user, content-bound review records, the verify audit, the stop before apply and the schema fork's divergences.

## Requirements

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

### Requirement: A review pass is bounded
The agent SHALL fix or rebut every critical and high finding and have the reviewer re-review, until the reviewer approves with no open critical or high finding. Medium and low findings MUST be recorded with a disposition (fixed, rebutted, or deferred with a reason) and MUST NOT block approval. A critical or high rebuttal MUST give a reason, is judged by the reviewer in the next round and consumes a round; a minor rebuttal or deferral MUST be recorded and MUST NOT open a round. A **review pass** is one loop for one artifact and one reviewer: it begins when that reviewer's review opens, continues through every re-review made in response to its findings without resetting the counter, and ends at that reviewer's approval or at escalation. A pass MUST be capped at 5 rounds per reviewer. During a pass, a triggering fix opens a new round and continues the same reviewer's counter; a non-triggering fix MUST NOT open a round and MUST carry the note *Only significant fixes reopen review* requires.

#### Scenario: Approval with medium findings
- **WHEN** a reviewer approves and leaves medium and low findings
- **THEN** each has a recorded disposition and the artifact is approved

#### Scenario: Rebuttal
- **WHEN** the agent rebuts a high finding
- **THEN** it gives a reason, the reviewer judges it in the next round and that round counts toward the cap

#### Scenario: Minor fixes
- **WHEN** up to five minor findings have been fixed since the last completed review
- **THEN** no new round opens, the dispositions are recorded, and the approval hash is re-pointed with a note

#### Scenario: Sixth minor fix
- **WHEN** the sixth minor finding is fixed since the last completed review
- **THEN** a new round opens

#### Scenario: Cap reached
- **WHEN** a reviewer has not approved after 5 rounds of a pass
- **THEN** the agent stops and escalates to the user

### Requirement: Two reviewers run in order with a bounded confirmation
With a two-reviewer preset the first reviewer MUST approve before the second starts. If the second reviewer's accepted fixes are triggering (a critical or high fix or rebuttal, or more than five minor fixes since the last completed review), the first reviewer MUST run one confirmation pass capped at 2 rounds; non-triggering fixes are covered by the second reviewer's approving round and the first reviewer's record MUST be re-pointed with a note. At most one confirmation pass MUST run per two-reviewer cycle, and if it demands a critical or high fix the agent MUST escalate instead of reopening the second reviewer. A consistency revalidation after a triggering upstream edit is its own pass, capped at 2 rounds per reviewer.

#### Scenario: Sequential reviewers
- **WHEN** the preset is a two-reviewer preset
- **THEN** the second reviewer starts only after the first has approved

#### Scenario: Normative edits after the second reviewer
- **WHEN** the second reviewer's accepted fixes are triggering
- **THEN** the first reviewer runs one confirmation pass of at most 2 rounds, and a demand for a critical or high fix escalates to the user

#### Scenario: Wording-only edits
- **WHEN** the second reviewer's accepted fixes are non-triggering
- **THEN** its approving round covers them, no confirmation pass runs, and the first reviewer's record is re-pointed with a note

### Requirement: Escalation at a cap explains the problem to a human
When a cap is reached or a confirmation pass demands a critical or high fix, the agent SHALL stop and write an explanation in plain language that states what the artifact is trying to do, the unresolved finding in ordinary words, what the reviewer argues, what the agent argues, the options with a recommendation, and the decision it needs, and MUST then wait for the user.

#### Scenario: Escalation text
- **WHEN** the agent escalates
- **THEN** the message contains the six parts (review-only check: it is written in plain language without unexplained jargon)

#### Scenario: No silent continuation
- **WHEN** a cap is reached
- **THEN** the agent does not start another round before the user answers

### Requirement: Review records bind approvals to content
Each review SHALL be recorded in `reviews/<artifact>-<reviewer-label>.md` of the change directory (the archived change's directory for closeout documentation, where the artifact id is `closeout-docs`, the header's upstream set is `none` and the hashes are keyed by the repo-relative path of each reviewed file), committed with the change. A record MUST have a header with the artifact, the reviewer, the session's preset as the ordered list of reviewers, the cap and the hashes of the upstream files it was judged against, then one section per pass with one table per round (severity, finding, disposition) and, for an approval, an `Approved content: <artifact> sha256 <hash>` line. A record MAY re-point its `Approved content` line or an upstream hash in its header only after a non-triggering edit, and each re-pointed hash MUST carry a note naming the fixes immediately after it; an untouched hash needs no note. The hash MUST be the sha256 of the file content after converting CR LF and CR to LF and rewriting the checkbox state of list items (`- [ ]`, `- [x]`, `- [X]`, and the same with a `*` marker, at the start of a line after optional indentation) to `[ ]`, computed by the single procedure given in `docs/superspec-workflow.md`, so that ticking checkboxes during apply never invalidates an approval while any change to other text does. The `specs` artifact MUST have one hash per spec file, keyed by its path relative to the change directory. **Upstream** means every planning file earlier in the order brainstorm, proposal, design, specs, tasks, plan.

#### Scenario: Ticking tasks during apply
- **WHEN** apply changes `- [ ]` to `- [x]` in `tasks.md` or `plan.md`
- **THEN** the recorded hash still matches and the approval stays valid

#### Scenario: Editing a task's text
- **WHEN** the text of a task changes after approval without an allowed noted re-point
- **THEN** the hash no longer matches and the approval is stale

#### Scenario: Non-triggering task-text fix
- **WHEN** a task's text is corrected as a non-triggering fix
- **THEN** the record re-points the hash with a note naming the fix and the approval stays valid

#### Scenario: Several spec files
- **WHEN** a change has two spec files
- **THEN** the record holds one hash per file, keyed by its relative path

#### Scenario: Portable computation
- **WHEN** two agents compute the hash on Linux, macOS or Windows with Python 3
- **THEN** both get the same value for the same file

### Requirement: Only significant fixes reopen review
An edit made after an artifact's review cycle is complete SHALL reopen review for every reviewer that approved it, in preset order, as a new pass, only when it is triggering: a critical or high fix or rebuttal, more than five minor findings (medium, low or cosmetic) fixed since the last completed review, or a normative edit that is not a finding fix. **Cosmetic** means a wording or formatting change that does not change meaning. A non-triggering edit — up to five minor fixes, a cosmetic edit, or a minor rebuttal or deferral — MUST NOT reopen review; the agent SHALL record it and re-point the affected `Approved content` and upstream header hashes with a note naming the fixes. The count MUST be per artifact since the last completed review (every reviewer the preset lists approved); an intermediate round or one reviewer's approval MUST NOT reset it. During an active pass a triggering fix MUST continue the same reviewer's counter. An upstream edit follows the same threshold: a triggering one makes the downstream approvals stale and each approving reviewer MAY revalidate it in preset order, capped at 2 rounds each, while the author alone MUST NOT; a non-triggering one only re-points the downstream records with a note. The agent MUST NOT rewrite a recorded hash to hide a triggering edit.

#### Scenario: Substantive edit after approval
- **WHEN** an approved design receives a critical or high fix or another triggering edit
- **THEN** every reviewer that approved it reviews again, in preset order

#### Scenario: Five minor fixes
- **WHEN** five minor findings are fixed after approval
- **THEN** no re-review runs, the dispositions are recorded, and the hashes are re-pointed with a note

#### Scenario: Sixth minor fix
- **WHEN** a sixth minor finding is fixed since the last completed review
- **THEN** every approving reviewer reviews again, in preset order

#### Scenario: Cosmetic edit
- **WHEN** a typo is fixed after approval
- **THEN** no re-review runs and the affected hash is re-pointed with a note

#### Scenario: Normative edit not from findings
- **WHEN** approved normative content is rewritten without a finding fix
- **THEN** every approving reviewer reviews again, in preset order

#### Scenario: Active pass
- **WHEN** a critical or high finding is fixed during an active pass
- **THEN** the same reviewer continues the same counter and no new pass opens

#### Scenario: Cosmetic edit judged substantive
- **WHEN** an edit claimed cosmetic is found to be triggering
- **THEN** it is treated as a triggering edit and every approving reviewer reviews again

#### Scenario: Upstream change
- **WHEN** the brainstorm receives a triggering edit after the tasks were approved
- **THEN** the tasks approval is stale and each approving reviewer revalidates it, capped at 2 rounds each

#### Scenario: Non-triggering upstream edit
- **WHEN** an upstream typo is fixed after the downstream artifact was approved
- **THEN** the downstream records are re-pointed with a note and no re-review runs

#### Scenario: Silencing an audit
- **WHEN** an agent rewrites a recorded hash to hide a triggering edit
- **THEN** the audit's result is invalid and the change is treated as unreviewed

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

### Requirement: The workflow stops before apply
After the plan has an approved review record the agent SHALL stop, tell the user to switch to a coding model and wait for explicit confirmation before starting `openspec-apply-change`. A fresh session in which the user starts apply and says they are on a coding model MUST count as the confirmation; otherwise the agent MUST ask once. `openspec-ff-change` and `openspec-propose` MUST end at the plan for the same reason. The `apply:` block of the superspec schema MUST start with a precondition that refuses to proceed unless the plan has an approved record from every reviewer listed in its header, **current** meaning that the plan's own hash and every upstream hash recorded in the approval match the files on disk and the recorded upstream set is the full upstream set, and the user confirmed a coding model. A hash re-pointed for a non-triggering edit with its note is current; a triggering edit that was not re-reviewed MUST refuse the start.

#### Scenario: Plan approved
- **WHEN** the plan passes its review
- **THEN** the agent asks the user to switch to a coding model and does not start apply

#### Scenario: New session for apply
- **WHEN** the user starts apply in a new session and says they are on a coding model
- **THEN** apply may start without asking again

#### Scenario: Stale plan
- **WHEN** the plan was edited after approval without a re-review or an allowed noted re-point
- **THEN** the apply precondition refuses to proceed

#### Scenario: Stale upstream
- **WHEN** the brainstorm or design was edited after the plan was approved with a triggering edit and the plan itself is unchanged
- **THEN** the apply precondition refuses to proceed until the plan is revalidated

#### Scenario: Noted non-triggering upstream edit
- **WHEN** the brainstorm or design was edited with a non-triggering fix and the plan's record was re-pointed with a note
- **THEN** the apply precondition accepts the current hashes

#### Scenario: Missing second reviewer
- **WHEN** the plan has an approved record from only the first reviewer of a two-reviewer preset
- **THEN** the apply precondition refuses to proceed

#### Scenario: Fast-forward
- **WHEN** `openspec-ff-change` finishes the plan
- **THEN** it stops there and asks for the model switch

### Requirement: The schema fork carries the gate and records its divergences
The superspec schema fork SHALL stay at version 5 in `schema.yaml` and its description history, in its README and INTEGRATION version mentions and in the `(schema version v5)` text of `docs/AGENTS.md`. `docs/openspec-workflow.md` MUST list every divergence from upstream (the apply precondition including the note condition, the sixth verify check and its template section including the note condition, the finalize hand-off and next-step text) so a reinstall can re-apply them, and MUST name `docs/AGENTS.md` explicitly where the version mention lives rather than saying "this file"; `grep -rn '/opsx:' openspec/schemas` MUST still return no hits. The generated `openspec-*` skills and the `superpowers:*` skills MUST remain unmodified.

#### Scenario: Divergences documented
- **WHEN** the schema is reinstalled from upstream
- **THEN** `docs/openspec-workflow.md` lists each edit to re-apply, including the note condition in the apply pre-flight and the sixth verify check, and names `docs/AGENTS.md` as the home of the version mention

#### Scenario: Skills untouched
- **WHEN** this change's diff is inspected
- **THEN** it touches no file under `.agents/skills/` or `.claude/skills/`

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
