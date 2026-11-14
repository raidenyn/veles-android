# Review record: brainstorm — reviewer Opus (Opus-5.5, medium)

Preset: both (first Sol, second Opus).
Upstream inputs: none (brainstorm is the first planning file).

Hand-run under the procedure this change defines (dogfooding). Runs second, after Sol approved (Sol record:
`brainstorm-sol.md`). Cap: 5 rounds per reviewer per review pass.

## Round 1 — verdict: changes-required (2 high, 4 medium, 3 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | Round cap counted over a change's whole life; upstream invalidation burns it in normal use. | **Fixed.** The cap is per review pass (one artifact, one reviewer, one loop); confirmation and upstream-revalidation passes are capped at 2. |
| 2 | high | The hashing procedure is not precise enough for two agents to compute the same value; the typo exception lets the author clear the reopen rule alone; `specs` is several files. | **Fixed.** Hash = sha256 of exact bytes; one defined `tasks.md` checkbox normalization; one hash per spec file; no recomputing to silence an audit; cosmetic exception must be confirmed by a reviewer round. |
| 3 | medium | Always-on context cost (~10-15 KB) and rules holding several concerns. | **Partly accepted.** Kept three always-on rules (the agreed approach; intake and gate must be on in any session) but added a 4 KB budget per rule with overflow into a `docs/` procedure the rule points to. Not adopting a single pointer rule: the user chose root rules for policy. |
| 4 | medium | Gate rule would name models, breaking the one-file anti-goal. | **Fixed.** Presets are ordered in the roster; other files say "the roster's presets" and "first/second reviewer". A grep assertion is left as an open question for the plan. |
| 5 | medium | GitHub's API does not give a view's item order; many views filter out Done so "last completed" never exists. | **Fixed.** Completion judged over all items ignoring the view filter; the view's filter only narrows candidates when it is a simple field match; multi-key sort/grouping means ask. Builder verifies the exact API fields. |
| 6 | medium | "Tool-neutral" hides that a GPT reviewer needs a non-native orchestrator; unclear `both` with one reviewer unavailable. | **Fixed.** Roster states the needed capability; unavailable means stop; with `both` and one unavailable the agent stops and asks, running the other alone only if the user says so. |
| 7 | low | Apply confirmation "in the current session" breaks when a model switch starts a new session. | **Fixed.** A fresh session whose user states a coding model counts; otherwise ask once. |
| 8 | low | Resumed intake trips its own In Progress check. | **Fixed.** In Progress plus an active change's `tracker.md` is a resume. |
| 9 | low | Done reconciliation scans all archived changes every intake. | **Fixed.** Scan only `tracker.md` files not yet marked Done; write Done back into `tracker.md`. |

## Round 2 — verdict: changes-required (1 high, 3 medium, 3 low)

Round-1 dispositions confirmed as reflected; finding 3 (always-on cost) accepted as no longer a real problem.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| H1 | high | Ticking `plan.md` checkboxes during apply invalidates the plan's approval (normalization covered `tasks.md` only). | **Fixed.** Checkbox normalization applies to every planning file. |
| M1 | medium | "Review opened for this content" reads as if each fix opens a new pass and resets the cap. | **Fixed.** A pass begins when the reviewer's review opens, continues through all edits made in response, and ends only at approval or escalation. |
| M2 | medium | Confirmation rule contradicts the reopen rule; passes can chain without limit under `both`. | **Fixed.** The second reviewer's approving round is the cosmetic confirmation; at most one confirmation pass per `both` cycle; if it demands normative edits, escalate instead of reopening. |
| M3 | medium | Hash procedure not portable (GNU `sed -E \s`, `sha256sum`, CRLF). | **Fixed.** One normative `python3 -I` procedure with LF normalization and a POSIX-style regex, given in the rule text. |
| L1 | low | Nothing enforces the 4 KB budget. | **Accepted as open question** next to the model-name grep assertion. |
| L2 | low | A confirmed cosmetic upstream edit still makes all downstream approvals stale. | **Fixed.** Downstream records are re-pointed to the new upstream hash citing the confirming round. |
| L3 | low | Sol's approval is stale after normative edits. | **Process point, agreed.** Sol runs a confirmation pass (cap 2) after Opus approves, as the design requires. |

## Round 3 — verdict: approve (1 medium, 3 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | medium | The reopen rule and the confirmation-pass rule conflict inside a `both` cycle. | **Fixed.** Edits made after a review cycle is complete reopen review; edits made during an active `both` cycle follow the confirmation-pass rule only. |
| 2 | low | Key Decisions still carried the old hash rule. | **Fixed.** Updated to LF and checkbox normalization in every planning file via the one procedure. |
| 3 | low | Scope of "model names in one file" unclear. | **Fixed.** Scope is the rules, schema, config, specs and docs; `openspec/changes/**` excluded. |
| 4 | low | Which reviewer runs the short re-check passes under `both` is unstated. | **Fixed.** Each reviewer holding an approval runs it, in preset order, each capped at 2 rounds. |

Opus approved. The four clarifications above are Opus's own suggestions and were applied after its approval;
the Sol confirmation pass reviews the final text. Rounds used: 3 of 5.

Final state: brainstorm.md sha256 a9dbc78e4c6988ba36cb96749f7bf4b64df25763bbd599a5f0a27faf0a81ff4f confirmed by Sol's confirmation pass (approve). Both reviewers approved.

---

# Review pass 2 (reopened after user feedback)

Earlier approval is stale (substantial rewrite after user feedback: closeout flow, small rules, reviewer
models limited to superspec artifacts). Opus runs after Sol approves pass 2.

## Pass 2, Round 1 — verdict: changes-required (1 high, 2 medium, 4 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | The closeout acts on PR comments from anyone; on a public repo a stranger could steer what the agent pushes. | **Fixed.** Act only on comments from the user or write-access accounts; comment text is data, never instructions; others' requests are reported to the user; the agent's own and bot status comments are skipped. |
| 2 | medium | Tracked work that ends without a PR (`merge-locally`, `discarded`, `kept-as-is`) never reaches the right status. | **Fixed.** Tracker rule: `merge-locally` closes the issue then sets Done once pushed; `discarded` reports and asks; `kept-as-is` stays In Progress. |
| 3 | medium | The two big rules will not fit their 2 KB target; the closeout only matters at one point yet is always-on. | **Fixed.** Rule 4 is now a one-line order plus invariants; the ordered checklist lives in the procedure doc; the reconcile step moved into the intake rule. The closeout stays always-on (it has to fire in any session that pushes a PR). |
| 4 | low | No home or hash for closeout doc-review records after archive moves the directory. | **Fixed.** Records go in the archived change's `reviews/` directory bound to main-spec hashes; the verify audit does not cover them. |
| 5 | low | Running sync then archive is redundant; the archive skill prompts after a separate sync. | **Fixed.** Procedure: sync once; run `openspec-sync-specs` first and answer "Archive now", or let archive sync inline. |
| 6 | low | The fallback may read "archived" from a stale local checkout. | **Fixed.** Read the archive from the remote default branch after a fetch. |
| 7 | low | Waiting for approval and merge has no end inside the session. | **Fixed.** The agent may stop waiting once checks are green and every comment is handled, and tells the user the card stays In Review for the next intake's reconcile. |

## Pass 2, Round 2 — verdict: approve (1 medium, 5 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| M1 | medium | `merge-locally` would set Done without sync, archive or doc review. | **Fixed.** For `merge-locally` the sync, archive (once) and doc review run on the default branch after the local merge and push; only then close the issue and set Done. Schema finalize "Next step" text updated to match. |
| L1 | low | "Merged earlier by someone else" has no reference point (the agent never merges). | **Fixed.** Reworded to "merged before sync, archive and doc review were completed". |
| L2 | low | The stop-waiting permission could fire before the archive. | **Fixed.** Only once the post-archive push is green and comments are handled. |
| L3 | low | The gate's record path does not cover archived changes. | **Fixed.** The gate says the change's `reviews/` directory, or the archived change's for closeout docs. |
| L4 | low | Key Decisions contradicts rule 4 on when the closeout starts. | **Fixed.** Reworded: sync and archive start when required checks are green. |
| L5 | low | Finalize "Next step" text skips the doc review. | **Fixed.** Doc review inserted before push. |

Final state of pass 2: brainstorm.md sha256 4cd9831d18034e5c6f1593a630d425a4ae89d0d74c2b8f81f2867ff3e3e3898d, approved by Opus (pass 2, 2 rounds) and confirmed by Sol
(pass 2: 3 rounds plus 1 confirmation round). Both reviewers approved.

---

# Review pass 3 (reopened after two user decisions)

Earlier approval is stale (CI fix cap 3 to 5; the agent keeps waiting until merged or closed). Opus runs
after Sol approves pass 3.

## Pass 3, Round 1 — verdict: approve (2 medium, 3 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | medium | Monitoring mechanism undefined; an endless wait could lock up or bloat the session. | **Fixed.** Procedure doc: event-driven watch preferred; else coarse polling with backoff, report state changes only, never block the user or sleep in a foreground loop. |
| 2 | medium | The user gets no signal about the open-ended wait. | **Fixed.** On entering monitoring the agent says what it waits for and that the user can tell it to stop; then it reports only state changes. |
| 3 | low | A user "stop" is not spelled out. | **Fixed.** Handled like a session end: report state; the next intake's reconcile finishes the card or reports it. |
| 4 | low | "Distinct CI failure" undefined; new failures could reset the cap. | **Fixed.** Same required check failing for the same root cause is one failure; the counter persists across pushes for the whole closeout. |
| 5 | low | A second session's reconcile can overlap a monitoring session. | **Fixed.** Expected; writes are idempotent; already-closed/Done counts as success. |

Final state of pass 3: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661, approved by Opus (1 round) and confirmed by Sol (pass 3 round 1 plus confirmation round 1). Both reviewers approved.

Approved content: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661 (format normalization of the earlier "Final state" line; no new review)
