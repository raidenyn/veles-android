## Why

The closeout's green gate reads required checks from rulesets **and** classic branch protection, and treats
an unreadable classic endpoint as an unestablished inventory that must be answered by the user. In this
repository the token is not a repo admin, so `branches/master/protection` answers `404 Not Found`, which the
rule forbids interpreting as "no protection". Every pull request in closeout therefore stops on the same
question — "is the required set just the ruleset's `pr-title` and `ci-ok`, or does classic add checks?" — and
the agent cannot answer it from the API. Ignoring classic protection is a deliberate monitoring choice: it does
not claim classic protection is absent, only that the ruleset's contexts are the ones the closeout watches.
Reading both sources is what costs a user round trip per PR, and that is what this change removes.

## What Changes

**Required-check discovery**
- From: the union of every ruleset's `required_status_checks` contexts and classic branch protection's
  `contexts`, with an unreadable source or an unestablished inventory stopping the closeout to ask the user.
- To: the ruleset response is the whole inventory
  (`gh api repos/<owner>/<repository>/rules/branches/<base>`). Classic branch protection is never read and is
  ignored even when it exists; the documentation names the retired classic contexts `lint-check`,
  `unit-tests`, `instrumented-tests` as ignored.
- Reason: one readable, authoritative source, and the ambiguity that forced the recurring question disappears.
- Impact: non-breaking for the gate's outcome — the intended required set for this repository (`pr-title` and
  `ci-ok`, as `docs/ci.md` and the ruleset record) is unchanged — and breaking for anyone who relied on a
  classic-only protection being merged in.

**Fail-closed boundary**
- From: an unreadable source at either endpoint and an empty list are not green.
- To: an unreadable ruleset response, a ruleset with no `required_status_checks` rule and an empty context
  list are not green and the agent asks the user; a missing, pending or unknown check result keeps it waiting.
- Reason: keeps the fail-closed guarantee while giving it one source to be fail-closed about.
- Impact: non-breaking; the guarantee is preserved, its trigger narrows.

**Where the rule lives**
- From: the two-source discovery and its union wording in `docs/superspec-workflow.md` (*Required checks and
  green* and the polling table), the fail-closed bullet in `.agents/rules/repo-superspec-closeout.md`, and the
  corresponding requirement in `openspec/specs/superspec-closeout/spec.md`.
- To: the same three places state the ruleset-only rule and the ignored classic contexts.
- Reason: the rule and its cited procedure must not disagree.
- Impact: non-breaking.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `superspec-closeout`: requirement *Green means required checks succeeded on the current head* now names
  rulesets as the only source of required checks and defines classic branch protection and its retired
  contexts as ignored, with the fail-closed trigger moved to the ruleset response.

## Impact

- `docs/superspec-workflow.md`: *Required checks and green*, and the required-checks row of the *One polling
  cycle* table.
- `.agents/rules/repo-superspec-closeout.md`: the *Green is fail-closed* bullet.
- `openspec/specs/superspec-closeout/spec.md`: one requirement and its scenarios.
- No code, workflow, dependency or API change; no repository permission change.
