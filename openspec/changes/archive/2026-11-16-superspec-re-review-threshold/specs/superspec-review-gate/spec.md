## MODIFIED Requirements

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

### Requirement: Escalation at a cap explains the problem to a human
When a cap is reached or a confirmation pass demands a critical or high fix, the agent SHALL stop and write an explanation in plain language that states what the artifact is trying to do, the unresolved finding in ordinary words, what the reviewer argues, what the agent argues, the options with a recommendation, and the decision it needs, and MUST then wait for the user.

#### Scenario: Escalation text
- **WHEN** the agent escalates
- **THEN** the message contains the six parts (review-only check: it is written in plain language without unexplained jargon)

#### Scenario: No silent continuation
- **WHEN** a cap is reached
- **THEN** the agent does not start another round before the user answers

### Requirement: The review-records audit runs whenever verification runs
Whenever verification is performed, however it is invoked, the agent SHALL audit that every planning file has an approved record from every reviewer listed in the record header's preset, with a current hash, upstream hashes covering the full upstream set, and no open critical or high finding. A hash re-pointed after a non-triggering edit MUST carry the note naming the fixes; a re-point without that note, or a triggering edit that was not re-reviewed, MUST be FAIL. Missing records, stale hashes, a smaller upstream set, a missing reviewer and unresolved critical or high findings MUST be FAIL. The `verify` instruction of the schema MUST carry the same check as a sixth check, `templates/verify.md` MUST have a "Review records" section for it (artifact, reviewer, hash state, open critical or high findings), and the schema text that counts the checks MUST say six.

#### Scenario: Direct verify invocation
- **WHEN** `openspec-verify-change` runs without fetching the schema's verify instruction
- **THEN** the audit still runs because the always-on rule requires it

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

### Requirement: The schema fork carries the gate and records its divergences
The superspec schema fork SHALL stay at version 5 in `schema.yaml` and its description history, in its README and INTEGRATION version mentions and in the `(schema version v5)` text of `docs/AGENTS.md`. `docs/AGENTS.md` MUST list every divergence from upstream (the apply precondition including the note condition, the sixth verify check and its template section including the note condition, the finalize hand-off and next-step text) so a reinstall can re-apply them, and `grep -rn '/opsx:' openspec/schemas` MUST still return no hits. The generated `openspec-*` skills and the `superpowers:*` skills MUST remain unmodified.

#### Scenario: Divergences documented
- **WHEN** the schema is reinstalled from upstream
- **THEN** `docs/AGENTS.md` lists each edit to re-apply, including the note condition in the apply pre-flight and the sixth verify check

#### Scenario: Skills untouched
- **WHEN** this change's diff is inspected
- **THEN** it touches no file under `.agents/skills/` or `.claude/skills/`

## RENAMED Requirements

- FROM: `### Requirement: Edits and upstream changes reopen review`
- TO: `### Requirement: Only significant fixes reopen review`
