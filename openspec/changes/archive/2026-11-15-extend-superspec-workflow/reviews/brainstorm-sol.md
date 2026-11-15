# Review record: brainstorm — reviewer Sol (GPT-6.1-Sol, medium)

Preset: both (first Sol, second Opus).
Upstream inputs: none (brainstorm is the first planning file).

Hand-run under the procedure this change defines (dogfooding). Cap: 5 rounds per reviewer.

## Round 1 — verdict: changes-required (4 high, 3 medium)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | Requiring a merged PR before archive contradicts the finalize closeout (archive → push → merge). | **Fixed.** Verified in `templates/finalize.md`. Done is no longer tied to archive; it is a post-merge reconciliation step (set when the merge is confirmed, or by the next intake). Archive order is untouched. |
| 2 | high | "Next task after the last closed item" is not well defined (no-view URL, grouped views, ties, no Done items, last item Done, pagination, closed vs Done). | **Fixed.** Exact ordering source, completion predicate, boundary cases and a stop-and-ask rule added. |
| 3 | high | Per-artifact hooks can miss a design pre-filled by brainstorming; ff treats existing files as done; CLI returns rules as data. | **Fixed.** Gate now applies to every planning file a phase produces or finds (including pre-filled `design.md` and artifacts with no review record) before any consumer reads it; explicit ff/propose coverage. Verified: `openspec instructions` returns rules as separate data. |
| 4 | high | Approval not tied to the artifact's final revision; stale approvals after later edits or upstream changes; round accounting across sessions. | **Fixed.** Approval records carry the artifact's sha256; any later edit reopens review (wording-only edits update the hash with a note); upstream changes invalidate downstream approvals; round counters live in the record and survive sessions; exceeding a cap escalates. |
| 5 | medium | Issue-only intake cannot identify the board (zero or many projects, unqualified number). | **Fixed.** Repo resolved explicitly, memberships discovered, ask on several, no project means no board writes; association persisted before the first write. |
| 6 | medium | "In Review when a PR exists" misfires with a planning pre-review PR. | **Fixed.** In Review is set only by a successful finalize outcome (pr-created / pr-updated); other outcomes defined. |
| 7 | medium | Claim that verify sees missing review records has no verify change behind it. | **Fixed.** An instruction-level review-records check is added to the schema's verify step (in scope), and the apply precondition checks the plan's approval record. |

## Round 2 — verdict: changes-required (4 high, 1 medium)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | Ordinary task completion (apply ticks `tasks.md` checkboxes) invalidates approvals and burns rounds. | **Fixed.** The approval hash for `tasks.md` is computed over the content with checkbox state normalized, so ticking boxes never invalidates it; any other edit does. |
| 2 | high | `pr-updated` does not prove a PR exists; finalize can push without a PR. | **Fixed.** In Review requires a successful finalize **and** a confirmed open PR URL (`gh pr view`); a push without a PR stays In Progress. |
| 3 | high | Post-merge reconciliation has no authoritative PR association and "any item" exceeds the write scope. | **Fixed.** Finalize persists the implementation PR in the tracker block; reconciliation covers only items recorded in tracker blocks of this repo's active and archived changes, via those PRs; ambiguity asks; the write authorization is stated explicitly. |
| 4 | high | Direct `openspec-verify-change` bypasses the schema's verify instruction, so the audit is skipped. | **Fixed.** The always-on gate rule requires the review-record audit whenever verification runs, however it is invoked; the schema verify check is reinforcement. Verified: the generated verify skill follows its own procedure. |
| 5 | medium | Upstream invalidation has no durable revalidation contract. | **Fixed.** Each approval records the hashes of the upstream artifacts it was judged against; any mismatch is stale; an unchanged downstream file is revalidated only by a reviewer round; apply and verify check the hashes. |

## Round 3 — verdict: changes-required (1 high)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | Finalize adding the implementation PR to the brainstorm's tracker block would invalidate the approved planning chain after verification. | **Fixed.** The association moves to a separate unreviewed `tracker.md` in the change directory (not a planning artifact, in no approval hash). The brainstorm template edit is dropped. |

## Round 4 — verdict: approve (no findings)

Approved content: brainstorm.md sha256 866942ec893c5cd7c478386300ae3d85ef912c62c40b135d69d37221866e153b
Rounds used: 4 of 5.

## Confirmation pass (cap 2), round 1 — verdict: approve (no findings)

Run after Opus's accepted normative edits (cap per pass, hashing, confirmation rules, next-task rules).
Approved content: brainstorm.md sha256 a9dbc78e4c6988ba36cb96749f7bf4b64df25763bbd599a5f0a27faf0a81ff4f (computed over raw bytes; the LF/checkbox-normalized procedure is defined by the design but not yet built)
Rounds used in this pass: 1 of 2.

---

# Review pass 2 (reopened after user feedback)

Earlier approvals are stale: the user added the closeout flow (monitor PR to green, sync and archive, doc
review with the same models, push, comments, merge, then Done), asked for much smaller rules, and limited the
reviewer models to superspec artifacts (not implementation). The document was substantially rewritten.
New pass, new counter (cap 5 for Sol, then 5 for Opus, then at most one confirmation pass for Sol).

## Pass 2, Round 1 — verdict: changes-required (2 high, 3 medium)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | Post-archive pushes restart the non-repeatable archive (verified: the archive skill moves the change and fails if the target exists; sync works on active changes). | **Fixed.** Sync and archive run exactly once; later pushes return only to babysitting and comments; post-archive behavior changes edit the synced main specs and archived copy directly, reviewed as documentation, no second sync. |
| 2 | high | Closeout gated on "only approval remains" never triggers if approval is early or not required; an external merge can skip closeout. | **Fixed.** Readiness is required checks green, regardless of approval state; Done requires steps 3-5 completed; an early external merge or a closed PR is reported and nothing is marked Done. |
| 3 | medium | Reading the procedure doc is not a mandatory entry gate. | **Fixed.** Each rule with procedure detail opens with a sentence requiring the named section to be read before acting. |
| 4 | medium | "Required checks green" lacks a fail-closed contract per revision. | **Fixed.** Discover required checks, need success evidence for the current head commit, missing/pending/unknown/undiscoverable is not green, an empty list is never green; queries left to the builder. |
| 5 | medium | Writing Done into `tracker.md` after merge has no durable commit path. | **Fixed.** No post-merge write: the fallback is stateless (In Review items whose archived change's `tracker.md` records a merged PR); the PR is committed to `tracker.md` before the merge; writes are idempotent. |

## Pass 2, Round 2 — verdict: changes-required (1 high, 1 medium)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | If the Status write succeeds but closing the issue fails, the item leaves the In Review set and is never retried. | **Fixed.** Close the issue first, then set Done last; the item stays In Review (eligible for the fallback) until both succeed. |
| 2 | medium | Untracked PRs cannot enter the closeout (trigger needed tracker.md and In Review). | **Fixed.** The closeout triggers on a successful finalize with a confirmed open PR, tracked or not; only the board, issue and `tracker.md` steps (7 and 8) are conditional on tracked work. |

## Pass 2, Round 3 — verdict: approve (no findings)

Approved content: brainstorm.md sha256 ed0e54a63791200585f4cb28c9b8c85350dcd13aa99032be5bb2bcd6e94c8959 (raw bytes). Rounds used in pass 2: 3 of 5.

## Pass 2, confirmation pass (cap 2), round 1 — verdict: approve (no findings)

Run after Opus's accepted normative edits. Approved content: brainstorm.md sha256 4cd9831d18034e5c6f1593a630d425a4ae89d0d74c2b8f81f2867ff3e3e3898d (raw bytes).

---

# Review pass 3 (reopened after two user decisions)

User decisions: the CI fix cap is 5 attempts per distinct failure (was 3), and the agent never stops
waiting: it stays in monitoring until the PR is merged or closed or the user says to stop (the earlier
"may stop waiting" permission is removed). Earlier approvals are stale; new pass, new counter.

## Pass 3, Round 1 — verdict: approve (no findings)

Approved content: brainstorm.md sha256 3980c59def517b25ab5a23a05f2e45f0b31b41b43ebfe3d0ca0de5ca34ade4a8 (raw bytes).

## Pass 3, confirmation pass, round 1 — verdict: approve (no findings)

Approved content: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661 (raw bytes).
