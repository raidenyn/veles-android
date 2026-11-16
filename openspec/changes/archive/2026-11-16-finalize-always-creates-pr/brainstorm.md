# Brainstorm

## Design Summary

Superspec's canonical finalize will always open the code-review pull request. When the feature branch has no
open pull request, finalize creates one with a Conventional Commit title derived from `proposal.md` and the
existing standard body, without asking the user and without a skip choice. A spec pre-review PR that already
exists is reused. Finalize then confirms the PR is open, writes the receipt with the true `pr-created` or `pr-updated` outcome,
records the PR in `tracker.md` for tracked work, sets the card In Review, and only then hands off to the
closeout.

If PR creation or the open-PR confirmation fails, finalize reports the failure and stops: it records no
outcome and the closeout does not start. A local merge remains available only through the explicit
escape-hatch path when the user asks for it.

## Alternatives Considered

### Alternative A: Keep the prompt, default to "Yes"

- **Approach**: leave finalize's PR step as a question but preselect creating the PR.
- **Pros**: smallest wording change; the user can still decline.
- **Cons**: the run still stops for approval, and an agent can still skip or misread the default.
- **Why not chosen**: it does not satisfy "always create" or "do not stop".

### Alternative B: Rephrase the existing closeout rule and make finalize's PR step mandatory (chosen)

- **Approach**: rephrase the closeout rule's start so the PR is always created and confirmed, rewrite the
  finalize instruction's PR step as a non-interactive creation step, and align the procedure, schema
  documentation and capabilities spec.
- **Pros**: one policy owner; no new always-on rule; the behavior lives where the finalize-to-closeout
  handoff already lives; the rule stays short.
- **Cons**: the superspec schema is a third-party fork, so its edits must be listed as fork divergences to
  survive a reinstall.
- **Why chosen**: it matches the repository's existing structure and the user's preference for modifying the
  existing rule.

### Alternative C: Add a new always-on rule file

- **Approach**: add `.agents/rules/repo-superspec-finalize-pr.md`, list it in `AGENTS.md`, and still edit the
  schema instruction.
- **Pros**: an explicit rule name that is easy to find.
- **Cons**: duplicates the closeout rule's handoff, adds another file, list entry and byte budget, and creates
  two places that can drift.
- **Why not chosen**: the closeout rule already owns the finalize handoff, so a second rule would split one
  concern.

## Agreed Approach

Alternative B. The closeout rule's **Start** bullet states that finalize always creates the pull request when
none exists, never asks and never skips, and reuses an existing pre-review PR. The superspec finalize
instruction's step 11 becomes "create the PR when none exists" instead of "offer to open a PR"; its outcome
recording uses `pr-created` or `pr-updated` truthfully, and a failed creation or confirmation stops finalize.
The closeout starts only after finalize has created and confirmed the open PR, written the receipt with the
true outcome, recorded the PR in `tracker.md` for tracked work and set the card In Review. Writing the receipt
after confirmation means a failed creation leaves no successful outcome recorded on the branch.

## Key Decisions

- **Automatic, mandatory PR**: canonical finalize creates the PR without a prompt; "skip PR creation" is
  removed from the canonical path.
- **Reuse**: an existing spec pre-review PR is updated, never duplicated.
- **Title and body**: keep the current derivation, a Conventional Commit title and the standard body.
- **Fail-closed**: a PR that cannot be created or confirmed is reported; no outcome is recorded and no
  closeout starts.
- **Receipt after confirmation**: `finalize.md` is written and committed only once the PR is confirmed open,
  so a failed creation cannot leave a successful outcome on the branch.
- **Tracked work**: the PR is recorded in `tracker.md` and the card moves to In Review only after the open PR
  is confirmed.
- **Escape hatch**: `merge-locally`, `kept-as-is` and `discarded` remain available only when the user
  explicitly chooses a non-canonical flow; "no PR yet" is not one of them.
- **Documentation consistency**: the closeout rule, capabilities spec, procedure, schema instruction, finalize
  template, integration and README docs, and the fork-divergence list in `docs/AGENTS.md` all state the same
  behavior.
- **Rule budget**: the closeout rule stays under its 4,096-byte cap.

## Open Questions

None.
