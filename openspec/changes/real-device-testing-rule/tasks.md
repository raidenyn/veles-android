## 1. Rule

- [x] 1.1 Add `.agents/rules/repo-real-device-testing.md` (`trigger: always_on`, under 2 KB) stating the Android, web-extension, shared-logic and multi-component triggers, the docs/minor exemption, the plan-then-execute requirement and the report-never-fake requirement
- [x] 1.2 List the rule in the `## Rules` section of the root `AGENTS.md`

## 2. Verify

- [x] 2.1 Run `python3 docs/checks/check_agent_files.py --root .` and `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`
- [x] 2.2 Inspect the rule against every requirement and scenario of `specs/real-device-testing/spec.md` (each trigger, exemption, report-and-ask, no tools named)
- [x] 2.3 Run `openspec validate real-device-testing-rule --strict`
