## Design Summary

The closeout's green gate takes its required-check inventory from **rulesets only**. Classic branch
protection is defined as ignored, and the documentation names the retired classic contexts
(`lint-check`, `unit-tests`, `instrumented-tests`) that stay ignored even if a classic rule still lists
them.

Today the rule tells the agent to read both sources and to treat an unreadable classic endpoint as an
unestablished inventory: "stop and ask the user". In this repository that fires on every pull request.
`gh api repos/raidenyn/veles-android/rules/branches/master` reports one repository ruleset (id
26950949) requiring `pr-title` and `ci-ok`, while
`gh api repos/raidenyn/veles-android/branches/master/protection` returns `404 Not Found` because the
token is not a repository admin (`permissions.admin: false`). A 404 there cannot distinguish "no classic
protection" from "no permission to read it", so the rule escalates forever. The ruleset answers the
question completely; the classic read adds nothing but the escalation.

## Alternatives Considered

### Alternative A: Rulesets only, classic ignored by name (chosen)
- **Approach**: the required set is the `required_status_checks` rule's contexts from
  `gh api repos/<owner>/<repository>/rules/branches/<base>`. No classic protection endpoint is called.
  The docs list the retired classic contexts documented in the 2026-07-01 CI plan — `lint-check`,
  `unit-tests`, `instrumented-tests` — as ignored even when a classic rule exists. The list is that
  plan's, not a claim to have enumerated every protection this repository ever had.
- **Pros**: one source, one read, no ambiguity to escalate. A missing classic permission stops being a
  closeout blocker. The ignored names are documented, so the recurring question ("is the inventory just
  `pr-title` and `ci-ok`, or does classic add checks?") has a written answer.
- **Cons**: a repository that still enforces classic protection without rulesets would have its required
  set under-read; this repository has a ruleset, and the rule says so.
- **Why chosen**: it matches the request ("monitor only rulesets") and removes the escalation that
  produced the question in the first place.

### Alternative B: Read classic, ignore its contents
- **Approach**: still call the protection endpoint for visibility, but never let it change the required
  set and never stop for its 404.
- **Pros**: the agent can report what classic says if the user asks.
- **Cons**: a read whose result is discarded invites the same confusion, and it still costs a permission
  check per base branch; the 404 keeps looking like a failure in transcripts.
- **Why not chosen**: the read has no consumer. Nothing in the closeout uses the classic response.

### Alternative C: Union of both sources minus the three retired names
- **Approach**: keep the current union and subtract `lint-check`, `unit-tests`, `instrumented-tests`.
- **Pros**: still catches a classic rule that requires a live check the ruleset does not.
- **Cons**: keeps the unreadable-source escalation, so the recurring question survives; and the ignore
  list becomes a filter the agent must apply to an inventory it cannot read.
- **Why not chosen**: it treats the symptom. The question the user keeps fielding comes from the read,
  not from the names.

## Agreed Approach

Alternative A. The change touches three places, which must stay consistent:

1. `docs/superspec-workflow.md`, *Required checks and green*: replace the two-source discovery with the
   ruleset read, state that classic protection is ignored, name the ignored contexts, and keep the
   fail-closed rule scoped to the ruleset read (unreadable response or empty context list). The polling
   cycle table's ruleset row drops the classic endpoint.
2. `.agents/rules/repo-superspec-closeout.md`, the *Green is fail-closed* bullet: say the inventory is
   the ruleset's, and that classic protection and its retired contexts are ignored.
3. `openspec/specs/superspec-closeout/spec.md` through a delta: rewrite *Green means required checks
   succeeded on the current head* to name rulesets as the only source, keep every fail-closed clause,
   and replace the "required checks unreadable" scenario's trigger with an unreadable ruleset response.

## Key Decisions

- **Rulesets are the only source.** `gh api repos/<owner>/<repository>/rules/branches/<base>` is the
  whole inventory. Its `required_status_checks` rules contribute their contexts; several rules are
  combined by union.
- **Classic protection is ignored, not merged.** No `branches/<base>/protection` call, and a 404 from it
  is not evidence of anything.
- **The ignored list is explicit**: `lint-check`, `unit-tests`, `instrumented-tests` — the retired
  standalone check contexts from `docs/superpowers/plans/2026-07-01-github-actions-ci.md` Task 9, which
  predate rulesets. The *jobs* with those names still exist inside the node workflows
  (`check-android.yml` runs `unit-tests` and `instrumented-tests`), but they report as
  `<node> / <check>` and only feed `ci-ok`; a bare classic context of the same name never matches them,
  so a classic rule naming the bare contexts can never report a conclusion and would hang green forever.
- **Fail-closed survives.** A missing, pending or unknown check result is not green and the agent keeps
  waiting. An unreadable ruleset response, a ruleset with no `required_status_checks` rule, and an empty
  context list mean the inventory is not established: the agent stops and asks the user, per the existing
  spec, rather than waiting on a set it cannot name.
- **One read per base branch.** The inventory is read once per base branch and reused across polling
  cycles, as the polling table already says.

## Open Questions

None.
