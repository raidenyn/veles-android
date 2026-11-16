## 1. Rule file

- [x] 1.1 Rename `.agents/rules/macos-test-app-tcc.md` to `.agents/rules/native-bridge-test-app-tcc.md` with `git mv`, keeping the body unchanged.
- [x] 1.2 Set its frontmatter to `trigger: glob` with `globs: ["native-bridge/**"]` and `paths: ["native-bridge/**"]`.
- [x] 1.3 In the rule body, keep the statement that the rule applies to every macOS app under test, including the `native-bridge/host/` test bundle, and add no new normative content.

## 2. Node guidance

- [x] 2.1 In `native-bridge/AGENTS.md`, replace `None.` under `## Rules` with the pointer to `.agents/rules/native-bridge-test-app-tcc.md`.
- [x] 2.2 In `native-bridge/installer/macos/AGENTS.md`, update the prose pointer to the new rule path and remove the rule bullet from `## Rules`, leaving the signing-identity section canonical.
- [x] 2.3 In `native-bridge/host/AGENTS.md`, keep the pointer to `native-bridge/installer/macos/AGENTS.md`, name the new rule path, and remove the claim that the macOS scope does not deliver it under `native-bridge/host/`.

## 3. Verification

- [x] 3.1 Run `python3 docs/checks/check_agent_files.py --root .`; expect exit 0 (warnings allowed).
- [x] 3.2 Run `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`; expect all tests to pass.
- [x] 3.3 Run `openspec validate --all`; expect every item valid.
- [x] 3.4 Grep the live files (outside `openspec/changes/archive/`, which keeps history) for `macos-test-app-tcc`; expect no hits outside the archived changes and the main spec that the closeout syncs.
- [x] 3.5 Inspect `git status --short`; expect the rename plus the three edited `AGENTS.md` files and the change directory only.
