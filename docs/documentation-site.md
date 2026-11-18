# Documentation site

How the Mintlify documentation site is built and kept healthy. Referenced from `docs/AGENTS.md`.

The Mintlify site is built from the repository root: `docs.json` is its configuration and the root
`.mintignore` is an allowlist that decides which files it publishes. Layout: `docs/user/` (the Users
product), `docs/dev/` (the Developers product, with thin pages that summarise or link to files kept
elsewhere), `docs/assets/`, and the curated pages published in place (`BUILD.md`, `docs/ci.md`,
`docs/releasing.md`, `docs/reproducible-builds.md`, `docs/superspec-workflow.md`, `docs/decisions/`,
`docs/rfcs/` and `openspec/specs/`). The Overview page follows the root node map.

- **Allowlist rule:** the first `.mintignore` pattern is `/*`; every other line is `!/path`,
  `/folder/*` or `!/folder/path`. A page inside an already re-included folder (`docs/user/`,
  `docs/dev/`) needs only a navigation entry in `docs.json`; a file or folder outside them also needs
  a re-include line. Anything not re-included stays private.
- **Default ignores win:** Mintlify's built-in default ignore list (`README.md`, `CONTRIBUTING.md`, ...)
  beats a re-include, so such a file cannot be published through `.mintignore`; link to it on GitHub
  instead, as `docs/dev/contributing.mdx` does for `CONTRIBUTING.md`.
- **Wrapper rule:** each capability under `openspec/specs/` needs a wrapper page in `docs/dev/specs/<capability>.mdx` and a
  navigation entry. When a capability is removed or renamed, remove or rename its wrapper in the same
  change.
- **MDX constraint:** published Markdown files and specs are parsed as MDX. Bare `<`, `{`, `}` and
  similar text outside code spans and fences can break the build; write them inside backticks.
- **The guard sees only tracked files:** `git add` a new page before running it.

```bash
python3 docs/checks/check_site.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

The guard is standard-library Python and runs in CI as the second step of `docs / agent-files`. A
local preview or full validation is optional: `npx mint@4.2.1199 validate`. Owner-side hosting settings
are in `docs/mintlify-owner-setup.md` (unpublished).

