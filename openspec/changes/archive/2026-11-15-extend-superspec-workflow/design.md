## Context

The brainstorm (approved, reviewed by two models) fixes *what* the workflow does; this document fixes *how*
it is built. Facts about the repository and the tools that the design rests on, each verified in this
checkout:

- **Generated skills cannot carry the change.** `.agents/skills/openspec-*` are overwritten by
  `openspec update` (`docs/AGENTS.md`). They follow the schema and the project config; the verify skill
  runs its own procedure and never fetches the schema's `verify` instruction; the archive skill moves the
  change directory and fails if the target exists; `openspec-ff-change` treats existing files as done.
- **`openspec/config.yaml` has two supported hook points** (CLI project-config schema): `rules`, a map from
  artifact id to a list of strings, returned as data with each artifact's instructions; and `operations`,
  with only `apply` and `archive`, each holding a `guidance` list. Neither is overwritten by a schema
  reinstall.
- **The schema fork** (`openspec/schemas/superspec/`, v4) owns the artifact instructions, the top-level
  `apply:` block, the `verify` and `finalize` instructions and `templates/finalize.md`, whose "Next step"
  text orders closeout as archive, push, then merge.
- **The agent-file check** (`docs/checks/check_agent_files.py`) already validates any `repo-*` rule
  generically: flat name, known scope, restricted frontmatter (`trigger: always_on` and nothing else for
  `repo-*`), a 24,000-byte cap, and that the rule is listed in the root `AGENTS.md`. It needs no change for
  new rule names; the only rule it names is a CODEOWNERS sample. The `docs` CI node runs it plus its
  stdlib unit tests and installs nothing, so it cannot run the OpenSpec CLI.
- **The board** the maintainer uses has a table view ("Execution order") with **no sort field, no filter
  and no grouping**, and its Status field has Backlog, Ready, In Progress, In Review, Blocked and Done.
  The GraphQL `ProjectV2.items` connection defaults to `orderBy: {field: POSITION, direction: ASC}`
  (verified by schema introspection), i.e. the project's manual item order; on this board it lists the
  completed tasks first, then the next task. A view's own sort fields and filter are exposed by
  `ProjectV2View.sortByFields` and `.filter`.
- **Always-on rules cost context in every session**; the existing four are about 0.3 KB each.

## Goals / Non-Goals

**Goals:**
- Build the four rules, the procedure doc, the `config.yaml` hooks, the schema fork edits, the capability
  specs and the `agent-guidance-layout` delta so an agent that follows them reproduces the brainstorm's
  behavior without guessing.
- Keep every rule tiny; put every changeable fact (model names) in exactly one file.
- Make every loop bounded and every outward-facing write idempotent and ordered so a failure leaves a
  retryable state.

**Non-Goals:**
- No change to generated skills or `superpowers:*` skills; no hooks or CI enforcement of reviews; no
  tracker other than GitHub; no model switching; no change to implementation review.

## Decisions

### D1. Who owns what (single source per fact)

| Fact | Single owner |
|---|---|
| Reviewer models, efforts, presets and their order | `repo-review-models.md` |
| Non-negotiable review, apply-stop, intake and closeout steps | the three other rules |
| Procedures: reviewer brief, caps and passes, hashing, escalation template, next-task algorithm, `tracker.md` format, closeout checklist, monitoring | `docs/superspec-workflow.md` |
| "Run the gate after this artifact" triggers | `openspec/config.yaml` `rules` |
| Apply precondition, verify check, finalize hand-off | `openspec/schemas/superspec/` |
| Normative requirements (testable) | the three new capability specs and the layout delta |

Rules never repeat procedure text: each states invariants and the heading of the procedure-doc section it
sends the agent to. **Why:** a rule read alone must be safe, and the detail must not be paid for in every
session. **Alternative rejected:** all text inside the rules (too large for always-on); all text in the
procedure doc with one pointer rule (an agent could skip the doc; the user chose root rules for policy).

### D2. Rule anatomy

Each rule is `trigger: always_on` frontmatter only, then: one sentence "Before doing X, read section Y of
`docs/superspec-workflow.md`", then terse imperative invariants (a short list). Budget: target 2 KB, hard
cap **4,096 bytes for every `repo-*` rule** (the agent-file check gets this one new assertion; the 24,000
cap stays for node rules). **Why a check change:** the budget is the agreed protection against context
bloat and is trivially testable; **why only this one:** the other candidate assertions (model names only in
the roster, procedure-doc pointer present) need parsing of free text and are covered by a plan verification
step instead (a grep for the roster's model names over the workflow instruction files only: the other `.agents/rules/`, `openspec/schemas/`, `openspec/config.yaml`, `openspec/specs/`, `docs/superspec-workflow.md` and `docs/AGENTS.md`; historical evidence and records elsewhere under `docs/` and `openspec/changes/**` name models legitimately and are out of scope).
**Consequences to carry out:** the layout delta changes the 24,000-byte sentence and its size scenario
(`agent-guidance-layout/spec.md`) to "24,000 for node rules, 4,096 for `repo-*` rules"; the existing
boundary tests that use repo-scoped fixtures at exactly 24,000 bytes (`test_rule_size_boundaries`,
`test_rule_over_24000_bytes_fails`, `test_oversized_rule_skips_content_parsing`, `sized_rule()`) move to a
node-scoped fixture, and new repo-rule tests cover 4,096 (passes) and 4,097 (fails); the size bullet under
"Adding a rule or a node" in `docs/AGENTS.md` is updated. **A dedicated task (user decision) reviews those
existing size-test fixtures and compacts them** while they move to node-scoped fixtures: one shared
fixture builder and table-driven boundary cases for both caps, instead of one near-duplicate test per
boundary, with no loss of coverage (each enforced invariant keeps a positive and a negative case). The tests assert
exactly one finding, so the shared node fixture also lists its rule in the node's `AGENTS.md`; otherwise the
"not listed by its node" finding would fail them. Rule files list in the root `AGENTS.md` `## Rules` sentence; the root file stays under its 60-line soft cap (it is 45 lines today).

### D3. Roster format

`repo-review-models.md` holds a small Markdown table of reviewers (`label | model | effort`) and a list of
presets, each an ordered list of reviewer labels (`both` is Sol then Opus). It states the capability needed
(a read-only subagent on the named model at the named effort) and the stop behaviors (cannot start the
model: stop and tell the user, never substitute; with a two-reviewer preset and one unavailable: stop and
ask). Nothing parses the table; the agent reads it. Changing a model is a one-line edit.

### D4. Procedure doc layout

`docs/superspec-workflow.md`, headings that the rules cite by name (not by line): *Reviewer brief*, *Review
passes and caps*, *Revision binding*, *Escalation*, *Next task*, *tracker.md*, *Closeout*, *Monitoring*. It
is documentation of the `docs` node. It names no model and no project.

### D5. Review records and revision binding

- **Location and names:** `reviews/<artifact>-<reviewer-label>.md` in the change directory; for `specs`,
  one section per spec file keyed by its path; closeout doc reviews go in the archived change's `reviews/`.
- **Structure:** a header (artifact, reviewer, the session's preset as the ordered reviewer list, cap,
  upstream hashes the review was judged against), then
  one section per pass with one table per round (severity, finding, disposition) and, for an approval, an
  `Approved content: <artifact> sha256 <hash>` line; the round tables give the round count. This change's
  own `reviews/*.md` files are the worked example.
- **Hash:** sha256 of the file's UTF-8 content after converting CR LF and CR to LF and rewriting the checkbox
  state of `- [ ]`, `- [x]`, `- [X]` and the same with a `*` marker, at the start of a line after optional
  indentation, to `[ ]` (the planning templates use `- [ ]` only; other markers are not normalized and a
  change to them is a real edit). One normative command goes in the procedure doc:

  ```
  python3 -I -c "import hashlib,re,sys;t=open(sys.argv[1],'rb').read().decode('utf-8').replace('\r\n','\n').replace('\r','\n');t=re.sub(r'(?m)^([ \t]*[-*] )\[[xX ]\]',r'\1[ ]',t);print(hashlib.sha256(t.encode('utf-8')).hexdigest())" FILE
  ```

  It was run against this change's `brainstorm.md` and `proposal.md` and reproduces the hashes already
  recorded in their review files (neither has checkbox list items), so the existing records stay valid
  under the final procedure. **Why Python:** the repo's tooling already requires `python3` and the docs
  node runs plain Python; `sha256sum` and GNU `sed -E '\s'` are not portable to macOS or Windows.
- **Upstream** means every planning file earlier in the order brainstorm, proposal, design, specs, tasks,
  plan (this is stricter than the schema's dependency graph, where `design` feeds nothing, so a design
  edit also makes later approvals stale). A record whose upstream set is smaller fails the audit.
- **Who must approve:** the record header lists the reviewers of the preset the session chose; the audit
  requires an approved, current record from **every listed reviewer**, so a session that stopped after the
  first reviewer approved is caught in a later session.
- **Staleness and passes:** the procedure doc's *Review passes and caps* section is a compact decision
  table, checked once against the brainstorm in the plan:

  | Event | Reviewed by | Cap | Recorded |
  |---|---|---|---|
  | A planning file is first written | each preset reviewer, in order, one after another | 5 rounds each | a pass section and an approval line with the hash |
  | Edits made in response to findings during a pass | the same reviewer | same counter, no reset | rounds in the pass |
  | The second reviewer's accepted edits change normative content | first reviewer, one confirmation pass | 2; if it demands normative edits, escalate | confirmation pass |
  | The second reviewer's accepted edits are wording only | the second reviewer's approving round confirms | none extra | note "cosmetic, confirmed in round N" |
  | Normative edit after the cycle is complete | every approving reviewer, in order, new pass | 5 each | new pass |
  | Edit after the cycle claimed cosmetic | one reviewer round confirms | 1 | hash re-pointed with a note |
  | An upstream hash no longer matches | each approving reviewer, in order, consistency check | 2 each | new upstream hashes |
  | An upstream edit confirmed cosmetic | none | none | downstream records re-pointed, citing the round |
  | Any cap reached | stop; the escalation template goes to the user | none | the escalation |

  The audit (rule plus schema verify check) recomputes hashes with the command above and compares; it never
  rewrites a recorded hash.

### D6. `config.yaml` hooks

```yaml
rules:
  brainstorm: ["After writing, run the review gate in .agents/rules/repo-superspec-review-gate.md."]
  # same one-line rule for proposal, design, specs, tasks, plan
operations:
  apply:   { guidance: ["Do not start until the review gate's apply stop is satisfied."] }
  archive: { guidance: ["Archive runs once, in the closeout; see .agents/rules/repo-superspec-closeout.md."] }
```

Because the CLI returns `rules` as data and `openspec-ff-change` skips existing files, the gate rule
itself (not only the hook) says that a file is reviewed when it is produced or found without an approved
record. Hooks are reinforcement; the rule is the contract. The existing commented example blocks in the
file are left in place.

### D7. Schema fork edits (v4 to v5)

1. `apply:` block: a new first step "Pre-flight 0a": refuse to start unless the plan has an approval from
   every reviewer listed in its record header, current (the plan's own hash and every recorded upstream hash
   recomputed and matching, and the recorded upstream set being the full set D5 defines), and the user confirmed a coding model (a fresh session whose
   user states they are on a coding model counts). The existing step 0 (commit artifacts) follows.
2. `verify` instruction: a sixth check, the review-records audit (every planning file has an approved,
   current record; unresolved critical or high findings, missing records and stale hashes are FAIL), with
   the same text as the rule. `templates/verify.md` gets a "6. Review records" section (artifact, reviewer,
   hash state, open critical or high findings) so the audit result has a home, and the schema text that
   says "5 checks" says 6.
3. `finalize` instruction: after the push, confirm an open PR (`gh pr view`), record it in `tracker.md`
   (commit and push that edit as part of the PR), set In Review for tracked work, then hand off to the
   closeout rule. `templates/finalize.md` "Next step" for `pr-created` and `pr-updated` becomes: monitor to
   green, sync and archive once, review the docs, push, wait for approval and merge; for `merge-locally`
   the sync, archive and doc review run after the local merge and the agent asks the user before any push to the default branch; if the push is rejected (for example by branch protection) it reports it, does not retry or bypass, and offers a follow-up PR, and if the user declines the push the issue stays open and the card In Progress with a follow-up PR offered; Done only follows a successful push; `discarded` reports and asks
   what the status becomes; `kept-as-is` leaves the card In Progress.
4. `schema.yaml` `version: 5` and its description's version history, the README and INTEGRATION version
   mentions, the "(schema version v4)" text in `docs/AGENTS.md`, and a divergence list there ("The
   superspec schema fork") so a reinstall can re-apply them; the existing
   `grep -rn '/opsx:' openspec/schemas` check still applies.

### D8. Next-task algorithm (intake)

Inputs: a project URL (optionally a view URL) or an issue reference. Steps, using `gh api graphql`:

1. Resolve the project and read **all** items with their Status and the issue state, paging until
   `hasNextPage` is false; a partial read is a failure.
2. **Order** (as the brainstorm decided). A view URL with sort fields: sort by them in the configured
   direction. A view with **no sort**, or a project URL with no view: the project's item order (the default
   `POSITION, ASC` of the items connection, which is the manual order the board shows). Several sort keys,
   grouping, or an order that cannot be recovered: ask the user to name the task. Never substitute another
   order.
3. **Completed** is judged over all items, ignoring any view filter: Status option named Done, or the issue
   closed (a draft or pull-request item has no issue and completes only by Status). Because completion is
   an OR, only the **last completed item** can make the two signals matter: if they disagree for it
   (Status Done with an open issue, or a closed issue that is not Done), report and ask; a disagreement for
   any other item never blocks pickup.
4. **Successor:** the first item after the last completed one; with no completed item, a tie that makes the
   last completed item or the task ambiguous, or the last completed item being last: stop and report (a tie
   elsewhere does not block). When the view's filter is a single simple field match the task is the first
   such item that matches it, and with no later item matching, stop and report; any other filter: ask. This
   refines the brainstorm: with completion as an OR, a disagreement or a tie away from the boundary cannot
   change the result, so it does not block.
5. Check the dependencies listed in the issue body and that the task is not In Progress unless it is the
   resume of an active change (a `tracker.md` records it). Announce the choice (and the previous completed
   task) before creating the change; follow the issue's links for context.

Issue-only intake resolves the repository, lists the issue's project memberships (`projectItems`), uses the
only one, asks on several and, with none, never writes to a board.

### D9. `tracker.md`

A small Markdown list in the change directory, outside every approval hash: `repository`, `project`,
`issue`, `source documents`, `implementation PR`, `created`. Written right after the change is created and
before the first status write; the PR line is added by finalize and committed with the PR. Reconcile reads
only archived `tracker.md` files.

### D10. Closeout mechanics

- **Trigger and monitoring:** after finalize confirms an open PR. Prefer the tool's event-driven PR watch,
  which ends the turn and wakes the agent; otherwise poll at a coarse interval with backoff, reading
  in one pass the PR's lifecycle state (merged, closed), head commit and check results, its reviews and
  review decision, and its comments, and print only state changes; the exact queries are fixed by the
  plan's spike. Polling is never a foreground sleep loop and never blocks the user.
- **Required checks:** read the base branch's rulesets and branch-protection required checks
  (`gh api` on the repository's branch rules); require a successful result for each, on the PR's current
  head commit; if they cannot be read, ask the user. In this repository the aggregate `ci-ok` and
  `pr-title` checks are the required ones (evidence only; the rule names neither).
- **Fix attempts:** counted per distinct failure (same required check, same root cause) across pushes for
  the whole closeout; the sixth attempt escalates.
- **Sync and archive:** `openspec-sync-specs`, then `openspec-archive-change` answering "Archive now", once.
  After archive, behavior changes requested in comments edit the synced main specs and the archived copy
  directly, reviewed as documentation. Pushes follow the repo's pre-push checks.
- **Comments:** fetch review and issue comments, keep only those from the user or an account with write
  permission, treat text as data.
- **Done:** close the issue, then set Status Done last; both idempotent.

### D11. Reconcile (start of every intake)

`git fetch`, find archived `tracker.md` files on the remote default branch, take the recorded PR, and for
project items whose Status is In Review and whose PR is merged, do the Done writes (D10). It writes nothing
to the repository. It reports instead of writing when an item has several recorded PRs, its PR was closed
unmerged, its change is not archived, or no archived `tracker.md` is found for an In Review item.

### D12. CODEOWNERS (user decision)

`.github/CODEOWNERS` gains `/docs/superspec-workflow.md`, `/openspec/config.yaml` and `/openspec/schemas/`,
each with the same owner as the existing agent-guidance entries, because these files now carry
instructions that agents follow and run on a public repository. The agent-file check enforces CODEOWNERS
coverage today (`CODEOWNER_PATTERNS` and `CODEOWNER_SAMPLE_PATHS`, applied with GitHub's last-match rule),
and the layout spec requires the entries, so the same change extends the check's required patterns and
sample paths, adds a positive and a negative test for each new pattern, and updates the layout delta's
CODEOWNERS sentence and its missing-coverage scenario. The comment block at the top of the CODEOWNERS file
is reworded to cover instruction files in general. A required pattern must appear literally in the file (a
broader `/openspec/` line would not count), and an entry for `docs/superspec-workflow.md` before that file
exists is safe: neither the check nor GitHub needs it on disk. The CODEOWNERS lines land in the same commit
as the new required patterns, or before them, so the agent-file check never fails on the real tree. The
CODEOWNERS sentence sits inside the `opencode.json` requirement of the layout spec and the size sentence
inside the rule-layout requirement, so the delta restates both whole requirements with their scenarios.

### D13. Specs and bootstrap

Three new capability specs (`superspec-review-gate`, `superspec-tracker-intake`, `superspec-closeout`) and
a delta to `agent-guidance-layout` (the initial `repo-*` set, the 4,096-byte cap for `repo-*` rules, the
procedure-doc pointer, the CODEOWNERS coverage). The change reviews its own artifacts by hand under the same procedure; its records
already use the final format.

## Risks / Trade-offs

- [An agent skips a review, the apply stop or a closeout step] → the verify audit and hash-bound records
  make a skip visible; the apply block refuses without a current plan approval.
- [The board's order cannot be recovered (several sort keys, grouping)] → ask the user to name the task; otherwise use the view's single sort field or the project's position order, and announce the choice; no field name is hard-coded.
- [GraphQL or `gh` output changes] → queries are listed in the procedure doc, not in rules; the plan
  includes a spike that runs them read-only against a real board.
- [Required checks cannot be discovered (no permission)] → fail closed and ask.
- [Reviewer cost and latency with two models] → the preset is chosen per session; the caps bound the cost.
- [Long monitoring sessions] → event-driven watch, state-change-only reporting, stateless reconcile.
- [Rules drift past the budget over time] → the 4,096-byte assertion in the agent-file check.
- [A schema reinstall restores upstream text] → divergences are listed in `docs/AGENTS.md`; hooks that live
  in `config.yaml` and the rules survive.

## Migration Plan

One PR on this branch. No data migration: the rules apply to new and continuing changes; archived changes
are untouched. Order: roster and rules, procedure doc, `config.yaml`, schema, specs, docs, then the layout
delta and the check assertion with their tests; before each push run the component selection
(`python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`) and the checks in
the `AGENTS.md` of every selected node (touching `docs/checks/` also selects the `supply-chain` node,
whose `verify/AGENTS.md` lists its checks), plus the agent-file check, its tests and
`openspec validate --all` locally. Rollback is a revert of the PR (the rules are plain
text; nothing outside the repository changes).

## Open Questions

- Exact GraphQL queries and field names (view `sortByFields`, `projectItems`, branch rules) are fixed by a
  read-only spike in the plan, not here.
- Final wording of each rule within its budget.
- The plan includes a step that runs `openspec validate --all` with a `tracker.md` and `reviews/` present
  (`reviews/` is already tolerated: validation reports only the missing-delta error).
