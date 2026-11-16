## 1. Replace the two-source discovery in the workflow procedure

- [ ] 1.1 In `docs/superspec-workflow.md`, *Required checks and green*: drop the classic
      `branches/<base>/protection/required_status_checks` command from the discovery block, leaving the
      rules `gh api repos/<owner>/<repository>/rules/branches/<base>` read as the whole inventory.
- [ ] 1.2 In the same section, replace the "union of every source" paragraph with the ruleset-only rule: the
      contexts of every `required_status_checks` rule in the response, combined by union, read once per base
      branch; classic branch protection is not read, is ignored when it exists, and a 404 from it is not
      evidence either way.
- [ ] 1.3 In the same section, name the ignored classic contexts `lint-check`, `unit-tests` and
      `instrumented-tests`, and say why: the repository's CI reports those checks as `<node> / <check>`, so a
      classic rule naming the bare contexts cannot be satisfied, while a ruleset naming one of them still
      counts.
- [ ] 1.4 Rewrite the fail-closed paragraph so its ask trigger is the ruleset response: unreadable response,
      no `required_status_checks` rule, or an empty context list stops the closeout to ask the user; a missing,
      pending or unknown check result keeps it waiting.
- [ ] 1.5 In *One polling cycle*, replace the required-checks row's "rulesets and the classic protection
      endpoint, combined into one required set" with the ruleset response alone, keeping "read once per base
      branch".

## 2. Update the closeout rule

- [ ] 2.1 In `.agents/rules/repo-superspec-closeout.md`, the *Green is fail-closed* bullet: state that the
      inventory is the base branch's ruleset response, that classic branch protection and its retired contexts
      are ignored, and that an unreadable or empty ruleset response is not green.
- [ ] 2.2 Check the whole rule file stays within the 4,096-byte `repo-*` cap and that its frontmatter is
      unchanged.

## 3. Verify

- [ ] 3.1 Run `grep -rn "protection/required_status_checks\|classic branch protection" docs .agents` and confirm
      every remaining hit states the ignore rule rather than a read of the endpoint.
- [ ] 3.2 Run `python3 docs/checks/check_agent_files.py --root .` and
      `python3 -m unittest discover -s docs/checks -p 'test_*.py'` (the `docs` node checks).
- [ ] 3.3 Run `openspec validate ruleset-only-required-checks --strict` and confirm it is valid.
- [ ] 3.4 Run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and run the
      checks of every node it selects.
