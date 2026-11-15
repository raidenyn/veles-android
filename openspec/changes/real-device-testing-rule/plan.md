# Real-Device Testing Rule Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development
> to implement this plan task-by-task.

**Goal:** Add one always-on root rule that requires planning and running tests on real devices, emulators or real Chrome when behaviour-affecting logic changes.

**Architecture:** A single `repo-*` rule file in `.agents/rules/`, listed in the root `AGENTS.md`; behaviour is specified in the `real-device-testing` spec and the `agent-guidance-layout` count update. No code or check-script change.

**Tech Stack:** Markdown rule files, `docs/checks/check_agent_files.py`, OpenSpec CLI.

---

## Task 1: The rule file

- [ ] **Step 1:** Create `.agents/rules/repo-real-device-testing.md` with exactly:

```markdown
---
trigger: always_on
---

Unit tests are not enough: plan real-environment tests with the change and run them before calling it done.

- **Android logic** (code in `android/`, not docs, comments or formatting): run it on a real device or an emulator.
- **Web-extension logic**: run it in a real Chrome with the extension loaded.
- **Shared logic** (`rust/`, `protocol/`, `native-bridge/`): run it through each Android or Chrome consumer it affects.
- **More than one component affected**: also cover the end-to-end scenario across them.
- **Exempt**: docs, comments, formatting and other edits with no behaviour change.

State what ran and on what in the PR note, or the completion report if there is no PR. If a device or browser is unavailable, say so and ask; never report the change as tested.
```

- [ ] **Step 2:** Check size: `wc -c .agents/rules/repo-real-device-testing.md` is under 2048.

## Task 2: List the rule

- [ ] **Step 1:** In root `AGENTS.md`, add `.agents/rules/repo-real-device-testing.md`, to the `## Rules` list after `repo-conventional-commits.md`, keeping the file within its line cap.
- [ ] **Step 2:** Run `python3 docs/checks/check_agent_files.py --root .`; expect no findings.

## Task 3: Verify

- [ ] **Step 1:** Run `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`; expect all pass.
- [ ] **Step 2:** Run `openspec validate real-device-testing-rule --strict`; expect valid. Then inspect the rule text against each requirement and scenario of `specs/real-device-testing/spec.md`.
- [ ] **Step 3:** Commit `docs(rules): add the real-device testing rule` with the change files.
