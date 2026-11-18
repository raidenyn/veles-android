## 1. Site guard (test first)

- [x] 1.1 Create `docs/checks/test_check_site.py` with a temporary-git-repository fixture builder (`git init` and `git add` only, no commit, run with `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1` so user config and hooks cannot interfere; `git ls-files -c` needs only the index) and write failing tests for the published-set computation: tracked files minus `git ls-files -c -i --exclude-from=.mintignore`; Mintlify's default ignores dropped unless an exact `!/<path>` line re-includes that path (fixtures: `!/CONTRIBUTING.md` re-included, a `.github/` file with no re-include dropped); untracked files unpublished (spec: *Published-set computation*; design D6)
- [x] 1.2 Implement `docs/checks/check_site.py` (standard library, Python 3.8+, `--root`, `severity path: message`, exit non-zero on errors and zero on warnings only) with the published-set computation until 1.1 passes (spec: *Site guard output*)
- [x] 1.3 Add positive and negative tests, then the code, for each structural error: missing or invalid `docs.json`; navigation page with no published `.md`/`.mdx`; page listed twice; import in a published `.mdx` page pointing at a missing file; wrapper import not matching its file name; wrapper missing from navigation; logo or favicon not published; `.mintignore` line outside the four forms of design D1 (spec: *Structural errors*, *Allowlist format*)
- [x] 1.4 Add positive and negative tests, then the code, for the MDX hazard scan: fenced code skipped (backtick and tilde fences), inline code spans matched per paragraph including spans across lines, HTML comment and `<` followed by a non-space character as errors in `.md` pages and imported files, only HTML comments in `.mdx` pages, imported files scanned as `.md` (spec: *MDX hazard errors*)
- [x] 1.5 Add positive and negative tests, then the code, for the warnings: capability folder without a wrapper; brace outside code in a `.md` page or imported file; plus the exit-code tests: warnings only prints one line per warning and exits zero, any error exits non-zero (spec: *Guard warnings*, *Wrapper lifecycle*, *Site guard output*)
- [x] 1.6 Calibrate the build-breaking rules against Mintlify: in a scratch copy, run `npx mint@4.2.1199 validate` on one fixture per MDX hazard rule and per build-breaking structural rule (invalid `docs.json`, missing navigation page, missing import) and confirm each is rejected; confirm a brace-only fixture is accepted (keeping the brace rule a warning); narrow any hazard rule that flags text Mintlify accepts, never editing specs to pass. The repository-policy rules (duplicate page, wrapper not in navigation, wrapper name mismatch, `.mintignore` form, logo or favicon unpublished) are not calibrated: Mintlify may accept them, and the spec requires them anyway (design D6)
- [x] 1.7 Run `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` with the local Python and with Python 3.8 (`uv run --python 3.8 python -m unittest discover -s docs/checks -p 'test_*.py' -v`); all pass

## 2. Allowlist and site configuration

- [x] 2.1 Copy `site/veles-icon.png` to `docs/assets/veles-icon.png`
- [x] 2.2 Write the root `.mintignore` exactly as design D1 lists it and `git add` it with the icon; confirm with `git ls-files` minus `git ls-files -c -i --exclude-from=.mintignore` that the published set equals the spec's *Published set* (except the files added or moved later into `docs/user/`, `docs/dev/` and `docs/assets/`, including the guide moved in 3.1)
- [x] 2.3 Write the root `docs.json`: `$schema`, `name` "Veles", `theme` "mint", colours `#1e6b47`/`#8fdbb0`/`#1e6b47`, logo and favicon `docs/assets/veles-icon.png`, `integrations.telemetry.enabled: false`, navbar links to the repository and its Releases page, and `navigation.products` Users and Developers with the groups of design D2 as an empty skeleton (product and group entries only; 4.5 and 5.5 add the pages) (spec: *Site configuration*, *Site branding*, *Two products*)

## 3. Existing content

- [x] 3.1 `git mv docs/sensitive-notifications.md docs/user/sensitive-notifications.md`, keep it plain Markdown, and update the links in `README.md` (lines 56, 66) and `BUILD.md` (line 84) (spec: *Sensitive-notifications guide*). In `BUILD.md` the link becomes absolute in 3.2, which the spec's *Absolute links to unpublished files* allows
- [x] 3.2 Turn every relative link in `BUILD.md` (lines 16, 63, 84, 112, 124) and `CONTRIBUTING.md` (lines 95-97) into an absolute `https://github.com/raidenyn/veles-android/blob/master/...` link; change nothing else in those files (spec: *Absolute links to unpublished files*)
- [x] 3.3 Escape the RFC's `<1 KiB` (`docs/rfcs/2026-08-21-otp-external-devices.md` line 2663) with the minimal edit that makes it parse

## 4. User pages

- [x] 4.1 Write `docs/user/index.mdx` (Welcome: what Veles does and for whom, from `README.md` About and the landing page's problem and free sections)
- [x] 4.2 Write `docs/user/install.mdx` (download from Releases, install with Android 13+, from `README.md` Quick Start and `BUILD.md` "Installing on a Device")
- [x] 4.3 Write `docs/user/notification-access.mdx` (granting notification access and what the app then shows), taking the screen names and button labels from the app's string resources and permissions screen in `android/app/src/main/`, with `README.md` Quick Start as the outline
- [x] 4.4 Write `docs/user/privacy.mdx` (no internet permission, no telemetry, OTPs parsed on the phone; that codes leave the phone only over Bluetooth to a computer the user has paired, when the user turns sharing on, in one sentence with no steps (a privacy fact the spec's *Privacy wording* requires for accuracy, beyond design D5's summary, and not a pairing guide); separately, the site is hosted by Mintlify with telemetry off and nothing more is claimed), confirming every claim against `android/app/src/main/AndroidManifest.xml` and the sharing code (spec: *Privacy wording*)
- [x] 4.5 List the five pages in the Users product; no pairing guide (spec: *User page set*)

## 5. Developer pages

- [x] 5.1 Write `docs/dev/index.mdx` (Overview: the components and where each lives, written for people from the root `AGENTS.md` node map)
- [x] 5.2 Write `docs/dev/architecture.mdx` from `CONTRIBUTING.md` "How Veles Works" and the node `AGENTS.md` files
- [x] 5.3 Write `docs/dev/build.mdx` and `docs/dev/contributing.mdx` importing `/BUILD.md` and `/CONTRIBUTING.md`; if an import does not resolve in `mint dev`, use the fallback page and drop the file from `.mintignore` (spec: *Build page import and Contributing summary*)
- [x] 5.4 Write the 28 wrappers `docs/dev/specs/CAPABILITY.mdx` per design D3 (title rule, description from the Purpose or by hand where it is `TBD`)
- [x] 5.5 Fill the Developers product: "Start here", "Engineering reference" (`docs/ci`, `docs/releasing`, `docs/reproducible-builds`, `docs/superspec-workflow`, both decision records, the RFC) and "Specifications" with the five sub-groups of design D2 (spec: *Developer page set*)
- [x] 5.6 `git add` every new page, wrapper and asset; run `npx mint@4.2.1199 broken-links` from the root; turn each reported link, and each link in any published page (old or new) to an unpublished file, into an absolute GitHub link (design D4). Files under `openspec/specs/` are never edited (none has a relative link today); if one is reported, stop and report it instead

## 6. CI, ownership and guidance

- [x] 6.1 Add `docs.json` and `.mintignore` to the `docs` node paths in `.github/components.json`, and update the path tuple in `test_docs_only_change_selects_only_docs` in `scripts/tests/test_components.py` (lines 240-249); run `python3 -I scripts/components.py check` and the components tests (spec delta: component-versioning *Documentation node*)
- [x] 6.2 Add `/.mintignore` and `/docs.json` with the existing owner to `.github/CODEOWNERS` and widen its header comment (spec: *Allowlist ownership*)
- [x] 6.3 Add the step `python3 docs/checks/check_site.py --root .` to the `agent-files` job of `.github/workflows/check-docs.yml` after the agent-file check, and update the workflow's header comment to name the site guard
- [x] 6.4 Update `verify/test/workflow-contracts.test.mjs`: the docs-node `run:` count from 2 to 3 (line 3701), a new assertion that `agent-files` runs exactly the guard command, the `/exactly the agent-file check/` messages in both mutation tests (line 3744 and the chained-install one at 3733), the test's title and messages that say it runs only the agent-file check, and two new mutation tests that must throw: the guard step removed, and its arguments changed (for example `--root docs`); run the workflow contract suite (spec delta: ci-pipeline *Component-tree CI*)
- [x] 6.5 Add the "Documentation site" section to `docs/AGENTS.md` (layout, allowlist rule and forms, wrapper rule with removal and renaming, MDX constraint for published Markdown and specs, tracked files only, guard commands, `npx mint@4.2.1199 validate`, Overview follows the node map) and the guard step to `docs/ci.md` (spec: *Site guidance in AGENTS.md*, *Site guidance document*; see the As built note)
- [x] 6.6 Write `docs/mintlify-owner-setup.md` (unpublished): docs directory = repository root, branch `master`, how to read the "Mintlify Deployment" check (spec: *Hosting settings*)

## 7. Verification

- [x] 7.1 Confirm `git status` shows no untracked file under `docs/`, `docs.json`, `.mintignore` or `docs/checks/`, then run the guard on the repository: no error and no warning (all 28 capabilities wrapped)
- [x] 7.2 Run `python3 docs/checks/check_agent_files.py --root .` and the `docs/checks` unit tests
- [x] 7.3 Run `npx mint@4.2.1199 validate` and `npx mint@4.2.1199 broken-links` from the root: both clean; re-read `docs.json` against the *Site configuration* and *Site branding* scenarios (name, theme, telemetry off, colours, logo, favicon, navbar links)
- [x] 7.4 Run `npx mint@4.2.1199 dev` and check: the product switcher shows Users and Developers; the logo and navbar links render; `/` serves the Welcome page; a wrapper (`docs/dev/specs/ci-pipeline`) is titled "CI pipeline" with the full spec text; the Build and Contributing pages show the imported files; an `otp-*` page, a `docs/superpowers/` file, an `openspec/changes/` file and `AGENTS.md` return Not Found
- [x] 7.5 Run the pre-push checks: `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and the checks in the `AGENTS.md` of every node it selects

Closeout notes (not apply tasks): the pull request reports the "Mintlify Deployment" check result as it is; after the archive creates `openspec/specs/documentation-site/spec.md`, the closeout's documentation review adds its wrapper and navigation entry; after merge, the deployed site gets the spot check of 7.4 and the result is reported.

## As built (deviations from the approved task text)

- 1.1: the default-ignore fixture changed twice: first to `docs/node_modules/x.md` and `docs/README.md` under `!/docs/` (the `.github/` fixture could not show the default ignore at work), then, after calibration, the exact re-include test was flipped: a default-ignored file such as `CONTRIBUTING.md` stays unpublished even with `!/CONTRIBUTING.md`.
- 1.3: imports are resolved against the published set, not the tracked files, so an import of an unpublished file is an error.
- 1.6: calibration found that `{a: 1}`, `{a b}` and `{{x}}` fail the Mintlify build while `{x}`, `{}` and a lone `}` pass. The brace rule stays a warning (the spec fixes it); promoting expression-like braces to an error is a follow-up change.
- 4.4: the privacy page does not say codes leave the phone over Bluetooth, because this build sends no codes (`SessionHost.kt` `Deliver -> Unit`; the app says "does not share OTPs yet"). It says Veles sends codes nowhere in this version and that Bluetooth sharing with a computer is still in development.
- 5.3: the fallback was taken for Contributing: Mintlify's default ignore list wins over `!/CONTRIBUTING.md`, so `docs/dev/contributing.mdx` is a summary page linking to the file on GitHub, and `.mintignore` has no `!/CONTRIBUTING.md` line.
- 5.4: all 28 wrapper descriptions were written by hand.
- 5.6: two RFC links (the 2026-08-11 design files) are pinned to commit `f5ec4afb` because those files do not exist on `master`.
- 7.4: the Contributing page check is its summary and GitHub link, not an imported text.
- 6.5: master capped every `AGENTS.md` at 40 lines while this change was in review, so `docs/AGENTS.md` keeps only a short "Documentation site" pointer and the full section lives in the new `docs/documentation-site.md`.
