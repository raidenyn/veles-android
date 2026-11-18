## ADDED Requirements

### Requirement: Commit and PR type guidance
The repository SHALL give agents a type guide for commit subjects and PR titles in
`.agents/hooked-rules/repo-commit-types.md`, delivered as the `hooked-rule-delivery` capability defines.

The guide is advisory. `scripts/commitlint.py` stays the only validator, and neither the type list nor the release
bump rules change.

**Format.** The guide MUST keep the format rule: every commit subject and PR title is a Conventional Commit
`type(scope)!: subject`, with scope and `!` optional, as the "Commit header format" requirement defines.

**Types.** It MUST give one line per accepted type:

- `feat` — new behaviour a user of a shipped product can see (the Android app, the web extension, the native host,
  the installer). Bumps the minor version of the touched components.
- `fix` — corrects wrong behaviour in a shipped product. Bumps the patch version of the touched components.
- `perf` — a shipped product gets faster or smaller with no behaviour change. Bumps the patch version of the
  touched components.
- `refactor` — restructures product code with no behaviour change.
- `test` — only adds or changes tests.
- `docs` — Markdown and documentation, including agent rules, skills, `AGENTS.md` and `openspec/`.
- `build` — Gradle, dependencies (including dev dependencies), toolchains, and release packaging such as signing and
  git-cliff config.
- `ci` — GitHub workflows, including release and CD workflows, and the scripts only CI runs.
- `style` — formatting only.
- `revert` — undoes an earlier commit.
- `chore` — only when nothing above fits.

**Bump and mixed changes.**

- The guide MUST state that `!` and a `BREAKING CHANGE:` footer are used only with `feat`, because any `!` bumps
  the major version of the touched components and major bumps are reserved for features.
- It MUST state that a mixed change takes the type of its purpose, not of most of its files.

**Triggers.** The rule's gates MUST be `git commit`, `gh pr create` and `gh pr edit`. Its moments MUST be the
`openspec-apply-change` and `opsx:apply` skills and the command `openspec instructions finalize`.

**Related guidance.** `scripts/AGENTS.md` MUST say that guidance-only changes are typed `docs`.

#### Scenario: Agent guidance change
- **WHEN** an agent reads the guide before committing a new rule file
- **THEN** the guide directs it to `docs`, not `feat` or `chore`

#### Scenario: CI tooling change
- **WHEN** an agent reads the guide before committing a workflow change with its docs
- **THEN** the guide directs it to `ci`

#### Scenario: Breaking marker
- **WHEN** an agent reads the guide before committing a breaking change
- **THEN** the guide directs it to use `!` or a `BREAKING CHANGE:` footer only on a `feat` commit

#### Scenario: User-visible feature with tests
- **WHEN** an agent commits a new Android screen together with its tests and docs
- **THEN** the guide directs it to `feat`

#### Scenario: Every type covered
- **WHEN** the guide is compared with the type list in `scripts/commitlint.py`
- **THEN** each of the 11 types has exactly one guide line
