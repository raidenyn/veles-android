## 1. Rule file

- [x] 1.1 Rename `.agents/rules/macos-test-app-tcc.md` to `.agents/rules/native-bridge-test-app-tcc.md` with `git mv`, keeping the body unchanged.
- [x] 1.2 Set its frontmatter to `trigger: glob` with `globs` and `paths` each exactly `["native-bridge/installer/macos/**", "native-bridge/host/**"]`.
- [x] 1.3 In the rule body, keep the statement that the rule applies to every macOS app under test, including the `native-bridge/host/` test bundle, and add no new normative content.

## 2. Node guidance

- [x] 2.1 In `native-bridge/AGENTS.md`, replace `None.` under `## Rules` with the pointer to `.agents/rules/native-bridge-test-app-tcc.md`.
- [x] 2.2 In `native-bridge/installer/macos/AGENTS.md`, update the prose pointer to the new rule path and remove the rule bullet from `## Rules`, leaving the signing-identity section canonical.
- [x] 2.3 In `native-bridge/host/AGENTS.md`, keep the pointer to `native-bridge/installer/macos/AGENTS.md`, name the new rule path, and remove the claim that the macOS scope does not deliver it under `native-bridge/host/`.

## 3. Agent-file check and layout documentation

- [x] 3.1 In `docs/checks/check_agent_files.py`, change `_check_rule_frontmatter` to accept identical, non-empty `globs`/`paths` lists whose patterns each sit under the rule's scope folder; brace expansion, non-lists, differing lists and out-of-scope patterns still fail.
- [x] 3.2 In `docs/checks/test_check_agent_files.py`, update the changed-error tests and add one test that accepts `native-bridge/installer/macos/**` plus `native-bridge/host/**` under the `native-bridge` scope and one that rejects the host pattern under the `macos` scope.
- [x] 3.3 In `docs/AGENTS.md`, describe the multi-pattern rule (identical lists under the scope, filename names the narrowest common parent, exact consuming patterns for sibling nodes).

## 4. Verification

- [x] 4.1 Run `python3 docs/checks/check_agent_files.py --root .`; expect exit 0 (warnings allowed).
- [x] 4.2 Run `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`; expect all tests to pass.
- [x] 4.3 Run `openspec validate --all`; expect every item valid.
- [x] 4.4 Grep the live files for `macos-test-app-tcc`, excluding `.git/` and `openspec/changes/` (the change history legitimately quotes the old name); expect only the not-yet-synced main spec that the closeout updates.
- [x] 4.5 Inspect `git status --short`; expect the rename plus the edited `AGENTS.md`, check and test files and the change directory only.
