# Mintlify Documentation Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish one Mintlify site (Users and Developers products) from a root `docs.json` and an allowlist `.mintignore`, with a standard-library CI guard that keeps the site's build from breaking.

**Architecture:** Mintlify builds every `.md`/`.mdx` file under the repository root that `.mintignore` does not ignore. The allowlist publishes new pages in `docs/user/` and `docs/dev/`, the curated `docs/` pages in place, `BUILD.md`, `CONTRIBUTING.md` and `openspec/specs/`. One `.mdx` wrapper per capability gives each spec a title. `docs/checks/check_site.py` recomputes the published set with git's own ignore matching and fails on the error classes that break the build or the layout rules.

**Tech Stack:** Mintlify (`docs.json`, MDX), Python 3.8+ standard library, git, GitHub Actions, Node's built-in test runner for the workflow contracts.

**Spec:** `openspec/changes/onboard-mintlify-docs/specs/documentation-site/spec.md`, the deltas `specs/component-versioning/spec.md` and `specs/ci-pipeline/spec.md`, and `design.md` (D1-D7) in the same folder. Task numbers from `tasks.md` are cited as (t1.1) and so on.

## Global Constraints

- The guard is standard-library Python 3.8+ only; no new CI job and no Node in the `docs` node.
- Local Mintlify CLI is pinned: `npx mint@4.2.1199` (never `latest`).
- Never edit anything under `openspec/specs/` or the rest of `openspec/` for the site.
- `docs.json`: `name` "Veles", `theme` "mint", colours `primary` `#1e6b47`, `light` `#8fdbb0`, `dark` `#1e6b47`, logo and favicon `docs/assets/veles-icon.png`, `integrations.telemetry.enabled: false`.
- `.mintignore` lines: first `/*`, then only `!/path`, `/folder/*` or `!/folder/path` (blank lines and `#` comments allowed).
- Published `.md` files contain no HTML comment and no `<` followed by a non-space character outside code; `docs/user/sensitive-notifications.md` stays plain Markdown with no components.
- Absolute links use `https://github.com/raidenyn/veles-android/blob/master/<path>`.
- Unit tests never sleep or read the clock (`.agents/rules/repo-no-time-based-tests.md`).
- Every commit subject is a Conventional Commit; these changes are not user-visible app features, so use `docs`, `test`, `ci` or `chore` with scope `docs`, never `feat`.
- No file under `docs/checks/` may contain the text `npx` (code, comments, strings or test data): `verify/check-remote-code.mjs` scans that folder for the supply-chain node and rejects it. Refer to "the Mintlify CLI" there instead.
- Finding format, as in the agent-file check: `severity path: message`, where a line-specific finding uses `path:line` as its path.

## Review Focus

- A `.mintignore` saved with CRLF line endings or trailing spaces must be read as the same lines, not rejected as unsupported forms (test in Task 2).
- A fenced code block indented inside a list item (up to three spaces) must still be masked; the specs use list-nested code (test in Task 3).
- An unmatched backtick in a paragraph must not mask the rest of the file, so later hazards are still found (test in Task 3).
- A path containing a space or non-ASCII characters must be handled, which needs `-z` parsing of every git call (test in Task 1).
- Navigation nests groups inside group `pages` and products may hold `pages` directly; every page string at any depth must be collected (test in Task 2).

---

### Task 1: Guard skeleton and published set (t1.1, t1.2)

**Files:**
- Create: `docs/checks/check_site.py`
- Create: `docs/checks/test_check_site.py`

**Interfaces:**
- Produces, in `check_site.py`:
  - `Finding = collections.namedtuple("Finding", "severity path message")`
  - `DEFAULT_IGNORES: tuple` = `(".git", ".github", ".claude", ".agents", ".idea", ".vscode", ".cursor", ".mintlify", "node_modules", "README.md", "LICENSE.md", "CHANGELOG.md", "CONTRIBUTING.md")`; following gitignore semantics for a slash-less pattern, an entry matches a path when any of its segments equals the entry (folders such as `.github` or `node_modules` at any depth, and file names such as `README.md` in any folder).
  - `_git(root: str, *args: str) -> str` (same contract as `check_agent_files._git`: raises `RuntimeError` on a non-zero exit).
  - `reincluded_paths(mintignore_text: str) -> set` — the exact paths of `!/path` lines, without the leading `!/` and trailing `/` (lines stripped of `\r` and trailing whitespace).
  - `published_files(root: str) -> list` — sorted: `git ls-files -z` minus `git ls-files -z -c -i --exclude-from=.mintignore`, minus `DEFAULT_IGNORES` matches unless the path itself is in `reincluded_paths` (exact path only; a re-included parent folder does not lift a default ignore below it). It calls `git ls-files` first, so a non-repository root raises `RuntimeError`; then, if `.mintignore` is missing, it returns `[]` without calling `--exclude-from` (git fails on a missing exclude file), and Task 2 reports the missing file.
  - `check_repo(root: str) -> list` of `Finding` (Tasks 2-4 add to it).
  - `main(argv=None) -> int`: `--root` (default `.`); prints each finding as `"%s %s: %s"`; returns 1 if any finding is `error`, else 0; an `OSError` or `RuntimeError` prints one `error <root>: <message>` line and returns 1.
- Produces, in `test_check_site.py`: `make_repo(files: dict, untracked: dict = None) -> str`, which writes the files into a fresh temporary folder, runs `git init -q` and `git add -A` (no commit) with `env` adding `GIT_CONFIG_GLOBAL=os.devnull`, `GIT_CONFIG_NOSYSTEM=1`, then writes `untracked` files; tests clean up with `addCleanup(shutil.rmtree, path)`.

- [ ] **Step 1: Write the failing tests** in `class PublishedSetTests(unittest.TestCase)`:
  - `test_allowlist_publishes_only_reincluded`: `.mintignore` `/*\n!/docs.json\n!/docs/\n/docs/*\n!/docs/user/\n`, files `docs.json`, `docs/user/a.md`, `docs/otp-1.md`, `AGENTS.md` → `published_files` == `["docs.json", "docs/user/a.md"]`.
  - `test_untracked_page_is_unpublished`: `docs/user/new.md` passed as `untracked` → not in the result.
  - `test_default_ignore_dropped_without_reinclude`: `/*\n!/docs/\n` with `docs/node_modules/x.md` and `docs/README.md` → neither published, although git does not ignore them. (This replaces the `.github/` fixture named in tasks t1.1: under `/*` git already ignores `.github/`, so that fixture could not show the default ignore at work.)
  - `test_default_ignore_kept_with_exact_reinclude`: `/*\n!/CONTRIBUTING.md\n` with `CONTRIBUTING.md` → published.
  - `test_path_with_space_and_unicode` (Review Focus): `/*\n!/docs/\n` with `docs/my page é.md` → published as that exact string.
  - `test_main_outside_git_repository`: a temporary folder that is not a git repository but holds a `.mintignore` → `main(["--root", path])` returns 1 and prints one line starting with `error ` that contains the git failure text.
- [ ] **Step 2: Run** `python3 -m unittest docs/checks/test_check_site.py -v` — expected: errors, `No module named 'check_site'`.
- [ ] **Step 3: Implement** the interfaces above in `docs/checks/check_site.py` with the module docstring naming the spec, `argparse`, and `if __name__ == "__main__": sys.exit(main())`.
- [ ] **Step 4: Run** the same command — expected: all `PublishedSetTests` pass.
- [ ] **Step 5: Commit**

```bash
git add docs/checks/check_site.py docs/checks/test_check_site.py
git commit -m "test(docs): add the documentation site guard's published-set computation"
```

### Task 2: Structural errors (t1.3)

**Files:**
- Modify: `docs/checks/check_site.py`, `docs/checks/test_check_site.py`

**Interfaces:**
- Consumes: `Finding`, `published_files`, `_git`, `make_repo` (Task 1).
- Produces:
  - `MINTIGNORE_FORMS`: three compiled patterns `^/\*$` (first line only), `^!/[^*!\s]+/?$`, `^/[^*!\s]+/\*$`.
  - `check_mintignore(root: str) -> list` — error `.mintignore: is missing` when absent; otherwise strips `\r` and trailing whitespace per line, skips blank and `#` lines, and reports `.mintignore:N: line "<text>" is not an allowed form`, where the first non-blank, non-comment line must be `/*` and every later one must match the other two forms.
  - `nav_pages(navigation: dict) -> list` — every string that appears as an item of a `pages` list, at any depth, in order; it recurses only through the values of the keys `pages`, `groups`, `products`, `tabs`, `anchors`, `dropdowns` and into dict items of those lists, so `icon`, `group`, `product` and other string values are never collected.
  - `IMPORT_RE = re.compile(r'^import\s+\w+\s+from\s+["\']([^"\']+)["\'];?\s*$', re.M)`; `imports(text: str) -> list`.
  - `check_structure(root: str, published: list) -> list` — the errors of spec *Structural errors*: `docs.json` missing or invalid JSON; a nav page with no published `<page>.md` or `<page>.mdx`; a page listed twice; an import in a published `.mdx` that does not start with `/` (error: imports must be root-relative) or resolves (leading `/` stripped) to no tracked file; a `docs/dev/specs/X.mdx` with no import or whose only import is not `/openspec/specs/X/spec.md`; a wrapper (any `docs/dev/specs/*.mdx`) not in `nav_pages`; `logo`/`favicon` (string, or the `light`/`dark` values of a logo object, each with any leading `/` stripped) not in `published`.

- [ ] **Step 1: Write the failing tests** in `class StructureTests` (one positive and one negative each; a shared `base_site()` fixture returns a valid tree with `.mintignore` `/*`, `!/docs.json`, `!/docs/`, `/docs/*`, `!/docs/user/`, `!/docs/dev/`, `!/docs/assets/`, `!/openspec/`, `/openspec/*`, `!/openspec/specs/`, plus a `docs.json` whose navigation lists `docs/user/a` and `docs/dev/specs/x` and whose `logo` and `favicon` are `docs/assets/veles-icon.png`, the user page `docs/user/a.md`, one wrapper `docs/dev/specs/x.mdx` importing `/openspec/specs/x/spec.md`, the spec and the icon, and asserts `check_repo` gives `[]`):
  - `test_missing_docs_json`, `test_invalid_docs_json` → error path `docs.json`.
  - `test_nav_page_without_file`, `test_nav_page_unpublished` (file exists but ignored) → error naming the page.
  - `test_duplicate_nav_page` → error naming the page.
  - `test_import_missing_file` → error on the `.mdx` path naming the import.
  - `test_wrapper_import_mismatch` (`docs/dev/specs/x.mdx` imports `/openspec/specs/y/spec.md`) → error.
  - `test_wrapper_not_in_nav` → error.
  - `test_logo_unpublished` → error naming `logo`; `test_favicon_unpublished` → error naming `favicon`; `test_logo_object_light_dark` (`"logo": {"light": ..., "dark": ...}`, both published) → none, and with `dark` unpublished → error naming `logo.dark`.
  - `test_wrapper_without_import` and `test_relative_import` (`./x.md`) → one error each.
  - `test_missing_mintignore` (`base_site()` with `.mintignore` removed) → `check_repo` returns only `.mintignore: is missing`, and `main` prints exactly one line and returns 1.
  - `test_mintignore_bad_form` (`docs/**` as line 2) → error `.mintignore:2`; `test_mintignore_first_line_not_root` (`# comment` then `!/docs/`) → error `.mintignore:2`.
  - `test_mintignore_crlf_and_trailing_space` (Review Focus): the base `.mintignore` with `\r\n` endings and a trailing space → no finding.
  - `test_nav_walks_nested_groups_and_product_pages` (Review Focus): a product with direct `pages` and a group whose `pages` hold a nested group → `nav_pages` returns all three page strings.
- [ ] **Step 2: Run** `python3 -m unittest docs/checks/test_check_site.py -v` — expected: the new tests fail.
- [ ] **Step 3: Implement** the interfaces above and call `check_mintignore` and `check_structure` from `check_repo`. When `.mintignore` is missing, `check_repo` returns that single finding and runs no other check (every page would otherwise also be reported unpublished).
- [ ] **Step 4: Run** the same command — expected: all pass.
- [ ] **Step 5: Commit** — `git add docs/checks/check_site.py docs/checks/test_check_site.py && git commit -m "test(docs): check documentation site structure and allowlist forms"`.

### Task 3: MDX hazard scan (t1.4)

**Files:**
- Modify: `docs/checks/check_site.py`, `docs/checks/test_check_site.py`

**Interfaces:**
- Consumes: `Finding`, `published_files`, `imports` (Tasks 1-2).
- Produces:
  - `mask_code(text: str) -> str` — same length and line count as `text`, with code replaced by spaces: (1) fenced blocks opened by a line of up to three spaces then at least three backticks or tildes, closed by a line with the same character and at least the same count, or by the end of the file; (2) then, per paragraph (runs of lines separated by blank lines), each backtick run of length n is paired with the next run of exactly length n in the same paragraph and the span between them, delimiters included, is masked; an unpaired run is left as literal text.
  - `scan_hazards(path: str, text: str, mdx: bool) -> list` — on `mask_code(text)`: error `path:N: HTML comment breaks the MDX build` for `<!--`; for `.md` (not `mdx`) also error `path:N: "<" followed by "c" breaks the MDX build` for each `<` followed by a non-whitespace character, except a `<` that starts `<!--` (already reported once); warning `path:N: brace may be read as an MDX expression`, at most one per line, for `{` or `}` in `.md`.
  - `check_hazards(root, published) -> list` — runs `scan_hazards` on every published `.md` (`mdx=False`), every published `.mdx` (`mdx=True`), and every file imported by a published `.mdx` (`mdx=False`), each file once.

- [ ] **Step 1: Write the failing tests** in `class HazardTests` (strings fed to `scan_hazards` directly):
  - `test_html_comment_in_md` → one error at line 2; `test_html_comment_in_code_fence` → none.
  - `test_bare_tag_in_md` (`Read <dir>/AGENTS.md`) → error naming `<` and `d`; `test_less_than_space_ok` (`a < b`) → none.
  - `test_autolink_flagged` (`<https://x.y>`) → error.
  - `test_tag_in_inline_code` (`` `<dir>` ``) → none.
  - `test_code_span_across_lines`: a paragraph whose span opens on line 1 and closes on line 2 around `<node>` → none.
  - `test_double_backtick_span` (``` `` a ` <b> `` ```) → none.
  - `test_indented_fence_in_list` (Review Focus): `- item\n\n  ```\n  <x>\n  ```\n` → none.
  - `test_unmatched_backtick_does_not_hide_later_hazard` (Review Focus): paragraph 1 has a lone backtick, paragraph 3 has `<x>` → one error at paragraph 3's line.
  - `test_tilde_fence` → none inside.
  - `test_mdx_allows_components` (`<Spec />` with `mdx=True`) → none; `test_mdx_comment_flagged` → error.
  - `test_brace_warning_in_md` (`a {x} b`) → exactly one warning, no error; `test_brace_in_code_ok` → none.
  - `test_imported_file_scanned`: via `make_repo`, a wrapper importing a spec containing `<!-- x -->` → an error on the spec's path.
- [ ] **Step 2: Run** the suite — expected: the new tests fail.
- [ ] **Step 3: Implement** `mask_code`, `scan_hazards` and `check_hazards`; call `check_hazards` from `check_repo`.
- [ ] **Step 4: Run** the suite — expected: all pass.
- [ ] **Step 5: Commit** — `test(docs): scan published Markdown for MDX build hazards`.

### Task 4: Warnings, exit codes and calibration (t1.5, t1.6, t1.7)

**Files:**
- Modify: `docs/checks/check_site.py`, `docs/checks/test_check_site.py`

**Interfaces:**
- Produces: `check_wrapper_coverage(root, published) -> list` — a warning `docs/dev/specs/X.mdx: capability "X" has no wrapper page` for each folder `openspec/specs/X/` holding a published `spec.md` without a tracked `docs/dev/specs/X.mdx`.

- [ ] **Step 1: Write the failing tests** in `class OutputTests`:
  - `test_missing_wrapper_is_warning`: base site plus `openspec/specs/z/spec.md` → exactly one `warning` finding naming `z`.
  - `test_all_wrapped_no_warning` → `[]`.
  - `test_main_warnings_only_exits_zero`: same tree, `main(["--root", path])` returns 0 and prints exactly one line starting with `warning `.
  - `test_main_error_exits_nonzero`: base site with a duplicated nav page → returns 1.
- [ ] **Step 2: Run** the suite — expected: the new tests fail. **Step 3: Implement** and wire into `check_repo`. **Step 4: Run** — all pass.
- [ ] **Step 5: Calibrate** in a scratch folder outside the repository: a minimal `docs.json` and one fixture page per hazard (HTML comment, `<dir>`, `<5`, `<https://x>`, an indented fence, a two-line code span) plus one per build-breaking structural rule (invalid `docs.json`, missing nav page, missing import); run `npx mint@4.2.1199 validate` per fixture. Expected: every hazard and build-breaking fixture fails, and the fenced and spanned ones pass; a brace-only page passes (brace stays a warning). If Mintlify accepts a fixture the guard rejects, narrow that hazard rule and its test; do not calibrate the policy rules (duplicate page, wrapper not in nav, wrapper name mismatch, `.mintignore` form, unpublished logo). Record the outcomes in the commit body.
- [ ] **Step 6: Run** `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` and `uv run --python 3.8 python -m unittest discover -s docs/checks -p 'test_*.py' -v` — expected: all pass on both.
- [ ] **Step 7: Commit** — `test(docs): warn about unwrapped capabilities and calibrate the site guard`.

### Task 5: Allowlist, configuration and existing content (t2.1-t2.3, t3.1-t3.3)

**Files:**
- Create: `docs/assets/veles-icon.png` (copy of `site/veles-icon.png`), `.mintignore`, `docs.json`
- Move: `docs/sensitive-notifications.md` → `docs/user/sensitive-notifications.md`
- Modify: `README.md:56,66`, `BUILD.md:16,63,84,112,124`, `CONTRIBUTING.md:95-97`, `docs/rfcs/2026-08-21-otp-external-devices.md:2663`

**Interfaces:**
- Produces: the published set and the `docs.json` skeleton that Tasks 6-7 fill: `navigation.products` = Users (`icon` "book-open", groups "Get started" and "Privacy") and Developers (`icon` "code", groups "Start here", "Engineering reference", "Specifications" whose `pages` are the five sub-groups "Android app", "Pairing and trust", "Gate B testing", "Build, CI and release", "Contributor workflow"); `navbar.links` = GitHub repository and `https://github.com/raidenyn/veles-android/releases`.

- [ ] **Step 1:** Copy the icon; write `.mintignore` exactly as design D1; `git add` both.
- [ ] **Step 2: Verify:** `comm -23 <(git ls-files | sort) <(git ls-files -c -i --exclude-from=.mintignore | sort)` — expected: `BUILD.md`, `CONTRIBUTING.md`, `docs/assets/veles-icon.png`, `docs/ci.md`, both `docs/decisions/` files, `docs/releasing.md`, `docs/reproducible-builds.md`, the RFC, `docs/superspec-workflow.md`, and `openspec/specs/` (28 `spec.md` and `.gitkeep`).
- [ ] **Step 3:** Write `docs.json` per Global Constraints and the skeleton above, with `"$schema": "https://mintlify.com/docs.json"`; groups may be empty only until Tasks 6-7.
- [ ] **Step 4:** `git mv` the guide; point `README.md` lines 56 and 66 at `docs/user/sensitive-notifications.md`.
- [ ] **Step 5:** Rewrite every relative link in `BUILD.md` (16, 63, 84, 112, 124; line 84 to the moved guide) and `CONTRIBUTING.md` (95-97) as absolute GitHub links; `git diff BUILD.md CONTRIBUTING.md` shows only link targets changed.
- [ ] **Step 6:** In the RFC line 2663 put `<1 KiB` in a code span (`` `<1 KiB` ``); no other edit.
- [ ] **Step 7: Commit** — `docs(docs): add the documentation site allowlist and configuration`.

### Task 6: User pages (t4.1-t4.5)

**Files:**
- Create: `docs/user/index.mdx`, `docs/user/install.mdx`, `docs/user/notification-access.mdx`, `docs/user/privacy.mdx`
- Modify: `docs.json` (Users groups)

**Interfaces:**
- Consumes: the Users skeleton (Task 5). Each page has frontmatter `title` and `description`.

- [ ] **Step 1:** `index.mdx` "Welcome to Veles": what it does (the before/after example from `README.md` About), who it is for, free and open source (landing page sections problem and free), and cards to Install and Privacy.
- [ ] **Step 2:** `install.mdx` "Install Veles": Android 13 or later; download the APK from the Releases page; install by opening the APK or `adb install -r <apk>` in a code block; then go to notification access.
- [ ] **Step 3:** `notification-access.mdx` "Grant notification access": the steps and labels taken from the app's string resources and permissions screen under `android/app/src/main/`; a callout pointing Android 15+ users to the sensitive-notifications guide.
- [ ] **Step 4:** `privacy.mdx` "Privacy and offline": no internet permission and no telemetry (checked against `android/app/src/main/AndroidManifest.xml`); OTPs parsed on the phone; one sentence, no steps, that codes leave the phone only over Bluetooth to a computer the user has paired, when the user turns sharing on (checked against the sharing code); separately, that this site is hosted by Mintlify with telemetry turned off, and nothing else about the site.
- [ ] **Step 5:** Users groups: "Get started" = `docs/user/index`, `docs/user/install`, `docs/user/notification-access`, `docs/user/sensitive-notifications`; "Privacy" = `docs/user/privacy`. No pairing page.
- [ ] **Step 6:** `git add docs/user docs.json`; run `python3 docs/checks/check_site.py --root .` — expected: no error about Users pages.
- [ ] **Step 7: Commit** — `docs(docs): add the user guide pages`.

### Task 7: Developer pages, wrappers and links (t5.1-t5.6)

**Files:**
- Create: `docs/dev/index.mdx`, `docs/dev/architecture.mdx`, `docs/dev/build.mdx`, `docs/dev/contributing.mdx`, 28 × `docs/dev/specs/<capability>.mdx`
- Modify: `docs.json` (Developers groups); any published page `mint broken-links` reports

**Interfaces:**
- Consumes: the Developers skeleton (Task 5); wrapper shape of design D3:

```mdx
---
title: "CI pipeline"
description: "..."
---

import Spec from "/openspec/specs/ci-pipeline/spec.md";

<Spec />
```

- [ ] **Step 1:** `index.mdx` "Overview": the components and their folders from the root `AGENTS.md` node map, in plain language, with a pointer to Architecture.
- [ ] **Step 2:** `architecture.mdx` "Architecture": from `CONTRIBUTING.md` "How Veles Works" and the node `AGENTS.md` files; a Mermaid diagram of notification → parser → re-posted notification, and of the optional Bluetooth path to the extension and native host.
- [ ] **Step 3:** `build.mdx` (`import Build from "/BUILD.md";`) and `contributing.mdx` (`import Contributing from "/CONTRIBUTING.md";`); run `npx mint@4.2.1199 dev`; if either page renders empty or errors, replace it with a summary page linking to the file on GitHub and remove that file's `!/` line from `.mintignore`.
- [ ] **Step 4:** The 28 wrappers, titles exactly: agent-guidance-layout "Agent guidance layout"; android-standalone-build "Android standalone build"; ble-sharing-availability "BLE sharing availability"; ci-pipeline "CI pipeline"; commit-conventions "Commit conventions"; component-versioning "Component versioning"; gate-b-evidence-matrix "Gate B evidence matrix"; gate-b-history-fixture "Gate B history fixture"; gate-b-journey-harness "Gate B journey harness"; github-project-automation "GitHub Project automation"; mutual-peer-removal "Mutual peer removal"; openspec-skills-delivery "OpenSpec skills delivery"; pairing-debug-harness "Pairing debug harness"; pairing-window-admission "Pairing window admission"; peer-revocation "Peer revocation"; pr-last-green-selection "PR last-green selection"; protected-record-session "Protected record session"; real-device-testing "Real-device testing"; reconnect-authentication "Reconnect authentication"; release-publishing "Release publishing"; sharing-card-copy "Sharing card copy"; spake2-enrollment-exchange "SPAKE2 enrollment exchange"; superspec-closeout "Superspec closeout"; superspec-review-gate "Superspec review gate"; superspec-tracker-intake "Superspec tracker intake"; synthetic-session-probe "Synthetic session probe"; trust-activation-recovery "Trust activation recovery"; trust-store-failure-recovery "Trust store failure recovery". `description` = the spec's Purpose shortened to one line, written by hand where it is `TBD`.
- [ ] **Step 5:** Developers groups: "Start here" = `docs/dev/index`, `docs/dev/architecture`, `docs/dev/build`, `docs/dev/contributing`; "Engineering reference" = `docs/ci`, `docs/releasing`, `docs/reproducible-builds`, `docs/superspec-workflow`, `docs/decisions/2026-11-08-self-signed-completion-identity`, `docs/decisions/2026-11-09-self-signed-macos-acceptance`, `docs/rfcs/2026-08-21-otp-external-devices`; "Specifications" sub-groups with the capabilities of design D2.
- [ ] **Step 6:** `git add docs/dev docs.json`; run `npx mint@4.2.1199 broken-links`; rewrite each reported link and each link to an unpublished file in any published page as an absolute GitHub link. If a file under `openspec/specs/` is reported, stop and report it. Re-run until clean.
- [ ] **Step 7:** Run `python3 docs/checks/check_site.py --root .` — expected: exit 0 with no output.
- [ ] **Step 8: Commit** — `docs(docs): add developer pages and capability spec wrappers`.

### Task 8: CI, ownership and guidance (t6.1-t6.6)

**Files:**
- Modify: `.github/components.json` (docs node paths), `scripts/tests/test_components.py:237-250`, `.github/CODEOWNERS`, `.github/workflows/check-docs.yml`, `verify/test/workflow-contracts.test.mjs:3700-3746`, `docs/AGENTS.md`, `docs/ci.md`
- Create: `docs/mintlify-owner-setup.md`

**Interfaces:**
- Consumes: the guard command `python3 docs/checks/check_site.py --root .`.

- [ ] **Step 1: Write the failing contract tests** in `verify/test/workflow-contracts.test.mjs`: in `assertDocsCheckWorkflow` change the count to `assert.equal(runs.length, 3, `${name} must run exactly the agent-file check, the site guard and their tests`)` and add `assert.match(jobs['agent-files'], /^      - run: python3 docs\/checks\/check_site\.py --root \.$/m, `${name} must run the documentation site guard from the repository root`)`; retitle the test "docs workflow runs only the agent-file check, the site guard and their tests from the allow-list"; change both `/exactly the agent-file check/` expectations (chained-install, dropped-tests) to `/exactly the agent-file check, the site guard/`; add mutations `droppedGuard` (remove the guard `run:` line; expects `/exactly the agent-file check, the site guard/`) and `guardArgs` (`source.replace('python3 docs/checks/check_site.py --root .', 'python3 docs/checks/check_site.py --root docs')`; expects `/must run the documentation site guard/`), each `assert.throws`.
- [ ] **Step 2: Run** `node --test verify/test/workflow-contracts.test.mjs` — expected: the docs workflow test fails (2 runs, no guard).
- [ ] **Step 3:** Add `      - run: python3 docs/checks/check_site.py --root .` after the agent-file step in `check-docs.yml`; name the site guard in its header comment.
- [ ] **Step 4: Run** the contract suite — expected: pass.
- [ ] **Step 5:** Add `"docs.json"` and `".mintignore"` to the `docs` node `paths` in `.github/components.json` and to the tuple in `test_docs_only_change_selects_only_docs`; run `python3 -I scripts/components.py check` and `python3 -m unittest scripts/tests/test_components.py -v` — expected: pass.
- [ ] **Step 6:** In `.github/CODEOWNERS` add `/docs.json` and `/.mintignore` with `@raidenyn`, and extend the header comment with "and the files that decide what the documentation site publishes"; run `python3 docs/checks/check_agent_files.py --root .` — expected: no finding.
- [ ] **Step 7:** Add a `## Documentation site` section to `docs/AGENTS.md` covering exactly the items of spec *Site guidance*; add the guard to the `docs / agent-files` row in `docs/ci.md`; write `docs/mintlify-owner-setup.md` (docs directory = repository root, branch `master`, the "Mintlify Deployment" check is not required and preview deployments need a paid plan). Confirm the owner note is not published (`git ls-files -c -i --exclude-from=.mintignore` lists it).
- [ ] **Step 8: Commit** — `ci(docs): run the documentation site guard in the docs node`.

### Task 9: Verification (t7.1-t7.5)

**Files:** none changed unless a check fails.

- [ ] **Step 1:** `git status --short` shows no untracked file under `docs/`, `docs/checks/`, `docs.json`, `.mintignore`; `python3 docs/checks/check_site.py --root .` — expected: exit 0, no output.
- [ ] **Step 2:** `python3 docs/checks/check_agent_files.py --root .` and `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` — expected: clean and all pass.
- [ ] **Step 3:** `npx mint@4.2.1199 validate` and `npx mint@4.2.1199 broken-links` — expected: both report success; read `docs.json` against Global Constraints.
- [ ] **Step 4:** `npx mint@4.2.1199 dev --no-open --port 3917`, then with `curl`: `/` serves Welcome; `/docs/dev/specs/ci-pipeline` has `<title>CI pipeline` and the text "ci-pipeline Specification"; `/docs/dev/build` contains "Prerequisites"; `/docs/dev/contributing` contains "How Veles Works"; `/docs/otp-06-transport`, `/docs/superpowers/plans/2026-07-07-hilt-di`, `/openspec/changes/archive/2026-11-18-clarify-sharing-card-not-running/proposal` and `/AGENTS` return 404. In a browser view, the product switcher shows Users and Developers, and the logo and navbar links render. Stop the server.
- [ ] **Step 5:** `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`, then run the checks in the `AGENTS.md` of every node it selects (expected `docs`, `tooling`, `installer`, `supply-chain`; before Task 8 adds `docs.json` and `.mintignore` to the map they are unowned and select every node); run `grep -rn npx docs/checks` (expected: no output) and report any check that cannot run locally.
- [ ] **Step 6:** Record the results in `apply.md`. Closeout, not apply: report the "Mintlify Deployment" check as it is; after the archive, add the `documentation-site` wrapper and nav entry in the documentation review; after merge, repeat Step 4's URL checks on the deployed site.
