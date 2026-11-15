## Context

`.agents/rules/` holds one-concern `repo-*` always-on rules (4,096-byte cap, 2 KB target) listed in the root
`AGENTS.md`. Node `AGENTS.md` files hold build and test commands (`android/AGENTS.md` includes instrumented tests; `web-extension/AGENTS.md` has no real-Chrome procedure); nothing says when a real environment is mandatory.

## Goals / Non-Goals

**Goals:**
- One short always-on rule that states when to plan and run real-device, emulator or real-Chrome tests, and the
  end-to-end expectation for multi-component changes.
- Make the claim verifiable: the PR note (or the completion report when there is no PR) states what ran and on what.

**Non-Goals:**
- Naming tools or commands, provisioning devices, or CI enforcement.
- Changing the agent-file check or any node file.

## Decisions

- **One root rule** (see brainstorm, Alternative A): matches the request and the one-concern convention.
- **Exemption by kind of change**, not by size: docs, comments, formatting and other edits with no behaviour
  change need no device run.
- **Shared logic through consumers**: `rust/`, `protocol/`, `native-bridge/` are exercised via the Android app
  and/or Chrome they serve, plus their own node checks.
- **Reporting over blocking**: when no device or Chrome is available the agent says so and asks; it never
  reports the change as tested. Reporting does not itself satisfy the execution requirement. This keeps the rule honest without making unavailable hardware a dead end.
- **Spec delta** raises the `repo-*` count from eight to nine; the check script needs no change because it
  validates any listed `repo-*` file.

## Risks / Trade-offs

- [Always-on context grows] → the rule stays under 2 KB.
- [Agents skip the run] → instruction-level only, like the other `repo-*` rules; the PR note makes a skip visible.
- ["Logic" is fuzzy] → the rule gives the exemption list (docs, comments, formatting, no-behaviour changes).
