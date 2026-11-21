# scripts/AGENTS.md

Release tooling, Conventional Commits checks and the component map. Stdlib-Python suites live in
`scripts/tests/`; the pinned git-cliff and gitleaks live in `scripts/release-tools.toml`. Workflow ownership
and git hooks: `docs/ci.md`; versions, tags and the release runbook: `docs/releasing.md`.

## Build & Test

Run from the repository root:

```bash
./gradlew installGitCliff      # pinned, SHA-256-verified git-cliff -> build/release-tools/
./gradlew installGitleaks      # pinned, SHA-256-verified gitleaks -> build/release-tools/
python3 -I scripts/commitlint.py --message "feat(app): add thing"   # --file PATH
python3 -I scripts/components.py changed --base <sha>
python3 -I scripts/verify.py              # task gate: affected nodes' checks and related tests
python3 -I scripts/verify.py --branch     # branch gate before a push: all of affected nodes' tests
python3 -I scripts/last_green.py --pr <n> --head <sha> --head-repo <owner/name> --head-ref <ref>
python3 -I scripts/project_flow.py --help   # check, sync, next, start, followup
```

`scriptsTest` depends on `installGitCliff` and `installGitleaks`. `project_flow.py` exits 0 on success, 2
on usage, 1 on a `gh` error and 3 on a named stop; setup and board work: `docs/github-project-setup.md`
and the `github-project` skill.

Guidance-only changes are typed `docs` so they do not bump a release component; the full commit type
and bump table is in `docs/releasing.md`.

## Rules

- `.agents/rules/scripts-verify-commands.md` — read `docs/verify.md` before editing `scripts/verify-commands.json`.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
