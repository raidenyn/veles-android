## Design Summary

Add one always-on root rule, `.agents/rules/repo-real-device-testing.md`, that makes agents plan and run
real-environment tests whenever a change touches behaviour, instead of stopping at unit tests:

- **Android logic** (code under `android/`, not docs, comments, formatting or other minor edits): run on a real
  device or an emulator.
- **Web-extension logic** (not docs or other minor edits): run in a real Chrome with the extension loaded.
- **Shared or connecting logic** (`rust/`, `protocol/`, `native-bridge/`): test it through each consumer it
  affects (Android, Chrome).
- **More than one component affected:** also cover the end-to-end scenario across them.
- The test run is **planned** (in the change's plan or the PR notes) and **executed** before the work is called
  done; the result states what ran and on what. An unavailable device or browser is reported, never claimed as
  tested.

The rule is short (well under the 2 KB target) and carries no procedure detail.

## Alternatives Considered

### Alternative A: One root rule (chosen)
- **Approach**: a single `repo-*` always-on rule listing the three triggers.
- **Pros**: matches the request ("root level rule"), one place, always in context, no per-node duplication.
- **Cons**: costs always-on context (small); cannot name node-specific commands.
- **Why not chosen**: n/a, chosen.

### Alternative B: Per-node rules
- **Approach**: a node rule in `android/` and `web-extension/` plus a root rule only for cross-component work.
- **Pros**: node rules load only where relevant; can hold node commands.
- **Cons**: splits one policy across three files; the cross-component case still needs a root rule; the request
  asked for a root rule.
- **Why not chosen**: more files for the same policy.

### Alternative C: Extend `repo-pre-push-checks.md`
- **Approach**: add the device-testing lines to the existing pre-push rule.
- **Pros**: no new file.
- **Cons**: breaks the one-concern-per-rule convention; the pre-push rule is about running listed checks, not
  about planning real-environment tests; the layout spec lists it as its own concern.
- **Why not chosen**: violates one concern per rule.

## Agreed Approach

Alternative A: one new `repo-*` rule file, listed in the root `AGENTS.md`, with the `agent-guidance-layout`
requirement updated from eight to nine initial `repo-*` rules. A small new capability spec states the rule's
behaviour so it is verifiable.

## Key Decisions

- Name `repo-real-device-testing.md`; frontmatter `trigger: always_on` only, as every `repo-*` rule.
- "Logic" means behaviour-affecting code; docs, comments, formatting, renames without behaviour change and
  similar minor edits are exempt.
- `rust/`, `protocol/` and `native-bridge/` are covered through their consumers, since they have no device of
  their own; their own unit and conformance checks stay in their node `AGENTS.md`.
- No tool or command is named (device tools differ per agent). Procedures to run on a device or in Chrome are out of scope; `android/AGENTS.md` has the instrumented-test commands and the agent uses whatever device or browser tooling its environment provides.
- No change to `check_agent_files.py`: it already validates any `repo-*` rule and its listing.

## Open Questions

None.
