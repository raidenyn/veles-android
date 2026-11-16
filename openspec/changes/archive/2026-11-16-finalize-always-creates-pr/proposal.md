## Why

Superspec's finalize step currently asks whether to open a code-review pull request and allows skipping it. An
agent can therefore finish implementation, stop for an approval nobody required, and leave the branch with no
PR; the closeout, which begins from an open PR, then cannot start. Making PR creation unconditional removes
that stop and lets every canonical change flow from finalize into the closeout.

## What Changes

**PR creation at finalize**

- From: finalize offers an interactive choice to create the PR, lets the user skip it, and skips silently in a
  non-interactive session.
- To: finalize always creates the PR when the feature branch has none, reusing an existing spec pre-review PR,
  with no prompt and no skip option.
- Reason: the user wants PR creation to be the default and the run to continue without an approval stop.
- Impact: non-breaking for the closeout; removes the optional no-PR canonical variant.

**Receipt and handoff order**

- From: the receipt is written and committed with a successful outcome before the PR is created or confirmed.
- To: the PR is created and confirmed open first; only then is `finalize.md` written with the true
  `pr-created` or `pr-updated` outcome, the PR recorded in `tracker.md` for tracked work, the card set In
  Review, and the closeout handed off. A creation or confirmation failure stops finalize with no outcome.
- Reason: a failed creation must not leave a successful receipt, and the closeout must never start without an
  open PR.
- Impact: reorders finalize steps; no new workflow step.

**Local merge and escape-hatch outcomes**

- From: a local merge is a normal outcome when no PR exists.
- To: `merge-locally`, `kept-as-is` and `discarded` remain only for a non-canonical flow the user explicitly
  chooses; missing a PR is never a reason to take them.
- Reason: the canonical path always ends in a PR.
- Impact: narrows the escape hatch; no removal.

**Documentation consistency**

- From: the closeout rule, capabilities spec, procedure, schema instruction, template, README, integration
  guide and fork-divergence list describe the optional prompt and no-PR variant.
- To: all of them state the same mandatory, non-interactive PR creation and fail-closed handoff.
- Reason: the schema is a third-party fork whose edits must be re-applied after a reinstall.
- Impact: documentation and YAML only; no code, tests or dependencies.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `superspec-closeout`: the closeout-start requirement now requires finalize to create the PR when none exists,
  confirm it, and stop with no outcome on failure; the tracker and In Review steps follow confirmation.

## Impact

- `.agents/rules/repo-superspec-closeout.md`
- `openspec/changes/finalize-always-creates-pr/specs/superspec-closeout/spec.md` (delta)
- `openspec/specs/superspec-closeout/spec.md` (updated by the closeout sync)
- `docs/superspec-workflow.md`
- `docs/AGENTS.md` (fork-divergence list)
- `openspec/schemas/superspec/schema.yaml` (finalize instruction and version description)
- `openspec/schemas/superspec/templates/finalize.md`
- `openspec/schemas/superspec/README.md`
- `openspec/schemas/superspec/INTEGRATION.md`
- No code, tests, dependencies or CI changes; the rule stays under its 4,096-byte cap.
