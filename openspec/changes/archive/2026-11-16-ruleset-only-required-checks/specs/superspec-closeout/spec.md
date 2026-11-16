## MODIFIED Requirements

### Requirement: Green means required checks succeeded on the current head
The agent SHALL discover the required checks of the PR's base branch from the ruleset response for that branch
(`gh api repos/<owner>/<repository>/rules/branches/<base>`), taking the contexts of every
`required_status_checks` rule it reports and combining them by union, read once per base branch and reused
across polling cycles. Classic branch protection MUST NOT be read and MUST NOT contribute to the required set:
it is ignored even when it exists, and a `404 Not Found` from `/branches/<base>/protection` MUST NOT stop the
closeout or be treated as evidence in either direction. The retired classic contexts `lint-check`,
`unit-tests` and `instrumented-tests` MUST be ignored even when a classic rule lists them, because the
repository's current CI reports those checks as `<node> / <check>` and never as the bare context, so a classic
rule naming them cannot be satisfied; a ruleset that names one of them still counts by the union rule. The
agent SHALL treat the PR as green only when each
required check has a successful result for the PR's current head commit. A missing, pending, unknown or
undiscoverable check and an empty list of checks MUST NOT count as green; the agent MUST keep waiting or stop
and ask, and MUST ask the user when the ruleset response cannot be read, reports no `required_status_checks`
rule, or reports an empty context list.

#### Scenario: Pending check
- **WHEN** one required check is still running
- **THEN** the PR is not green

#### Scenario: New push
- **WHEN** a new commit is pushed to the PR
- **THEN** results for the previous head commit no longer count

#### Scenario: Required checks unreadable
- **WHEN** the ruleset response for the base branch cannot be read
- **THEN** the agent asks the user and does not treat the PR as green

#### Scenario: No required-status-checks rule
- **WHEN** the ruleset response reports no `required_status_checks` rule or an empty context list
- **THEN** the agent asks the user and does not treat the PR as green

#### Scenario: Classic protection exists
- **WHEN** the ruleset inventory is readable and non-empty, and classic branch protection on the base branch
  names contexts, including `lint-check`, `unit-tests` or `instrumented-tests`
- **THEN** those contexts are ignored, the ruleset contexts are the whole inventory, and the closeout does not
  stop to ask because of classic protection alone

#### Scenario: Empty list
- **WHEN** no checks are reported
- **THEN** the PR is not green
