---
pointer: "Every commit subject and PR title is a Conventional Commit `type(scope): description`; the type guide is delivered when you apply or finalize a change and before `git commit`, `gh pr create` or `gh pr edit`; read this file at those moments if it was not."
skills:
  - "openspec-apply-change"
  - "opsx:apply"
commands:
  - "\\bopenspec\\s+instructions\\s+finalize\\b"
gates:
  - "\\bgit\\b[^;&|\\n]*?\\scommit\\b"
  - "\\bgh\\s+pr\\s+(create|edit)\\b"
---
# Commit and PR types

Write every commit subject as a Conventional Commit: `type(scope): description`.
Release tooling derives versions and changelogs from these subjects, so a non-conforming
subject breaks a release. PR titles follow the same format. The scope and a `!` before the
colon are optional (`type(scope)!: description`); `scripts/commitlint.py` is the only validator.

Choose the type:

- `feat` — new behaviour a user of a shipped product can see (the Android app, the web extension, the native host, the installer). Bumps the minor version of the touched components.
- `fix` — corrects wrong behaviour in a shipped product. Bumps the patch version of the touched components.
- `perf` — a shipped product gets faster or smaller with no behaviour change. Bumps the patch version of the touched components.
- `refactor` — restructures product code with no behaviour change.
- `test` — only adds or changes tests.
- `docs` — Markdown and documentation, including agent rules, skills, `AGENTS.md` and `openspec/`.
- `build` — Gradle, dependencies (including dev dependencies), toolchains, and release packaging such as signing and git-cliff config.
- `ci` — GitHub workflows, including release and CD workflows, and the scripts only CI runs.
- `style` — formatting only.
- `revert` — undoes an earlier commit.
- `chore` — only when nothing above fits.

Use `!` or a `BREAKING CHANGE:` footer only with `feat`: any `!` bumps the major version of the
touched components, and major bumps are reserved for features.

A mixed change takes the type of its purpose, not of most of its files: a user-visible feature
with its tests and docs is `feat`, and new CI tooling with its docs is `ci`.
