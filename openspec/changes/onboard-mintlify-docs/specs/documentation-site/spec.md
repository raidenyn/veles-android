## ADDED Requirements

### Requirement: Site configuration
The repository SHALL hold one Mintlify site configuration, `docs.json`, at the repository root. It MUST name
the site "Veles", use the `mint` theme and set `integrations.telemetry.enabled` to `false`.

#### Scenario: Telemetry is off
- **WHEN** `docs.json` is read
- **THEN** it names the site "Veles", uses the `mint` theme and `integrations.telemetry.enabled` is `false`

### Requirement: Site branding
`docs.json` SHALL set the colours `primary` `#1e6b47`, `light` `#8fdbb0` and `dark` `#1e6b47`, use
`docs/assets/veles-icon.png` (a copy of `site/veles-icon.png`) as logo and favicon, and link the navbar to the
GitHub repository and to its Releases page.

#### Scenario: Branding matches the landing page
- **WHEN** `docs.json` is read
- **THEN** its colours are `#1e6b47` and `#8fdbb0`, its logo and favicon resolve to the published app icon, and the navbar links to the repository and its Releases page

### Requirement: Hosting settings
The Mintlify project SHALL build from the repository root on the `master` branch, and the unpublished owner
note `docs/mintlify-owner-setup.md` MUST record these settings and how to read the deployment check.

#### Scenario: Owner checks the dashboard
- **WHEN** the owner follows `docs/mintlify-owner-setup.md`
- **THEN** it names the repository root as the docs directory and `master` as the branch

### Requirement: Two products
The site navigation SHALL consist of exactly two products: **Users**, whose pages live in `docs/user/`, and
**Developers**, whose pages live in `docs/dev/` plus the curated pages published in place. Every navigation
page MUST belong to exactly one product and MUST be listed once.

#### Scenario: A visitor opens the user guide
- **WHEN** a visitor opens a page of the Users product
- **THEN** the sidebar shows only Users pages, and the product switcher offers Developers

#### Scenario: Duplicate navigation entry
- **WHEN** a page is listed twice in `docs.json` navigation
- **THEN** the site guard reports an error naming the page

### Requirement: Allowlist format
The root `.mintignore` SHALL be an allowlist whose first pattern is `/*`. Every other non-blank, non-comment
line MUST have one of the forms `!/path`, `/folder/*` or `!/folder/path`.

#### Scenario: Unsupported pattern
- **WHEN** `.mintignore` contains a line that is not one of the allowed forms
- **THEN** the site guard reports an error naming the line

### Requirement: Published set
The published files SHALL be exactly, apart from Mintlify's default ignores, `docs.json`, `BUILD.md`, the files under
`docs/user/`, `docs/dev/` and `docs/assets/`, the pages `docs/ci.md`, `docs/releasing.md`,
`docs/reproducible-builds.md` and `docs/superspec-workflow.md`, the files under `docs/decisions/` and
`docs/rfcs/`, and the files under `openspec/specs/`. Every other file MUST stay unpublished until the
allowlist names it.

#### Scenario: Internal records stay off the site
- **WHEN** the site is deployed
- **THEN** requests for an `otp-*` page in `docs/`, a file under `docs/superpowers/`, a file under `openspec/changes/` and `AGENTS.md` return Not Found

#### Scenario: A new internal note
- **WHEN** a contributor adds a new Markdown file directly under `docs/`
- **THEN** it is not published, because no allowlist line names it

### Requirement: Default ignores
A file on Mintlify's default ignore list, such as `CONTRIBUTING.md`, SHALL NOT be treated as published, because
Mintlify ignores it even when `.mintignore` re-includes it.

#### Scenario: Contributing guide cannot be imported
- **WHEN** `.mintignore` contains `!/CONTRIBUTING.md` and a page imports `/CONTRIBUTING.md`
- **THEN** the site guard reports an error naming the import

### Requirement: User page set
The Users product SHALL contain the pages Welcome (`docs/user/index.mdx`), Install (`install.mdx`), Grant
notification access (`notification-access.mdx`), Sensitive notifications (Android 15+,
`sensitive-notifications.md`) and Privacy and offline (`privacy.mdx`), all in `docs/user/`, and MUST NOT document pairing with a
computer.

#### Scenario: User navigation
- **WHEN** the Users product is opened
- **THEN** it lists exactly these five pages and no pairing guide

### Requirement: Sensitive-notifications guide
The sensitive-notifications guide SHALL live at `docs/user/sensitive-notifications.md` as plain Markdown
without Mintlify components, so that it also renders on GitHub, and every repository link to the guide MUST
point to that path.

#### Scenario: Guide moved without broken links
- **WHEN** the change is applied
- **THEN** `docs/sensitive-notifications.md` no longer exists and the links in `README.md` and `BUILD.md` point to `docs/user/sensitive-notifications.md`

### Requirement: Privacy wording
The Privacy and offline page SHALL describe the app's offline guarantee and MUST state separately that the
documentation site is hosted by Mintlify with telemetry turned off, claiming nothing else about the site.

#### Scenario: Privacy page wording
- **WHEN** the Privacy and offline page is read
- **THEN** it states the app has no internet permission and no telemetry, and that the site is hosted by Mintlify with telemetry off, and makes no other claim about the site

### Requirement: Developer page set
The Developers product SHALL contain a "Start here" group (Overview, Architecture, Build, Contributing), an
"Engineering reference" group with the curated `docs/` pages, the decision records and the RFC, and a
"Specifications" group of capability wrappers.

#### Scenario: Developer navigation
- **WHEN** the Developers product is opened
- **THEN** it shows the three groups, and the Specifications group lists one page per wrapped capability

### Requirement: Build page import and Contributing summary
The Build page SHALL import `BUILD.md` rather than copy it. The Contributing page MUST summarise
`CONTRIBUTING.md` and link to the file on GitHub, because Mintlify's default ignore list always applies to
`CONTRIBUTING.md` and an import of it does not resolve. Any other import that does not resolve MUST likewise be
replaced by a summary page that links to the file on GitHub, and the allowlist and the published set then drop
that file.

#### Scenario: Build guide has one source
- **WHEN** `BUILD.md` changes
- **THEN** the site's Build page shows the change without any other file being edited

#### Scenario: Contributing guide is linked, not imported
- **WHEN** a visitor opens the Contributing page
- **THEN** it shows a short summary and a link to `CONTRIBUTING.md` on GitHub

### Requirement: Absolute links to unpublished files
Every relative link in `BUILD.md` and `CONTRIBUTING.md`, and every link in a published page that points to an
unpublished file or that `mint broken-links` reports, SHALL be an absolute GitHub link to the `master` branch, or to the commit that holds the file when the file
exists only at that commit.

#### Scenario: Link to an unpublished file
- **WHEN** a published page links to a file the allowlist does not publish
- **THEN** the link is an absolute GitHub link, not a relative one

### Requirement: Capability wrapper pages
Each capability folder CAPABILITY in `openspec/specs/` SHALL have, except as *Wrapper lifecycle* permits,
a page `docs/dev/specs/CAPABILITY.mdx` listed in the Specifications group that sets a `title` (sentence case,
acronyms kept) and a one-line `description` of the capability written for the page and renders
exactly `/openspec/specs/CAPABILITY/spec.md`, imported. `openspec/` files MUST NOT be edited for the site.

#### Scenario: Readable spec title
- **WHEN** a visitor opens the wrapper for `ci-pipeline`
- **THEN** the page is titled "CI pipeline" and shows the full text of `openspec/specs/ci-pipeline/spec.md`

### Requirement: Wrapper lifecycle
A change that removes or renames a capability SHALL delete or rename its wrapper and navigation entry in the
same change. A newly archived capability MAY lack a wrapper until a later change adds it. The wrapper for this
change's own capability, `documentation-site`, MUST be added in the closeout's documentation review, after the
archive creates the spec.

#### Scenario: Newly archived capability
- **WHEN** an archive adds a capability folder that has no wrapper yet
- **THEN** the site guard reports a warning naming the capability, and the docs check still passes

#### Scenario: Removed capability
- **WHEN** a capability folder is removed but its wrapper remains
- **THEN** the site guard reports an error naming the wrapper's missing import

### Requirement: Site guard output
`docs/checks/check_site.py` SHALL be a standard-library Python 3.8+ script, run with `--root`, that prints one
finding per line as `severity path: message`, exits non-zero when any error is found and zero when only
warnings are found.

#### Scenario: Warnings only
- **WHEN** the only finding is a capability without a wrapper
- **THEN** the guard prints one warning line and exits zero

### Requirement: Published-set computation
The site guard SHALL compute the published set as the tracked files minus those git reports as ignored by
`.mintignore` (`git ls-files -c -i --exclude-from=.mintignore`), minus Mintlify's default ignores (`.git`,
`.github`, `.claude`, `.agents`, `.idea`, `.vscode`, `.cursor`, `.mintlify`, `node_modules`, `README.md`,
`LICENSE.md`, `CHANGELOG.md`, `CONTRIBUTING.md`), which always apply and which `.mintignore` cannot
re-include.

#### Scenario: Untracked page
- **WHEN** a page file exists but is not tracked by git
- **THEN** the guard treats it as unpublished

### Requirement: Structural errors
The site guard SHALL report as errors: a missing or invalid `docs.json`; a navigation page with no published
`.md` or `.mdx` file; a page listed twice; an import in a published `.mdx` page that points at a missing or
unpublished file; a wrapper whose import does not match its file name; a wrapper missing from navigation; a logo or
favicon that is not a published file; and a `.mintignore` line outside the allowed forms.

#### Scenario: Missing navigation page
- **WHEN** `docs.json` lists a page whose file is missing or unpublished
- **THEN** the guard reports an error naming the page and exits non-zero

### Requirement: MDX hazard errors
In every published `.md` page and every imported file, the site guard SHALL report as an error an HTML comment
or a `<` followed by a non-space character outside fenced code blocks and inline code spans, matching code
spans per paragraph. In published `.mdx` pages only an HTML comment outside code MUST be an error.

#### Scenario: A spec that would break the build
- **WHEN** a published spec contains an HTML comment or a bare tag such as `<dir>` outside code
- **THEN** the guard reports an error with the file and line, and exits non-zero

#### Scenario: Code spread over two lines
- **WHEN** an inline code span that contains a `<` starts on one line and closes on the next line of the same paragraph
- **THEN** the guard reports nothing for it

### Requirement: Guard warnings
The site guard SHALL report as warnings a capability folder without a wrapper and a brace outside code in a
`.md` page or imported file.

#### Scenario: Brace in a spec
- **WHEN** a published spec contains a brace outside code
- **THEN** the guard reports a warning with the file and line and exits zero

### Requirement: Guard tests and calibration
Every guard rule SHALL have a positive and a negative unit test in `docs/checks/test_check_site.py`, and the
guard MUST report no error on the repository after this change, with all 28 existing specs unchanged.

#### Scenario: Clean tree
- **WHEN** the guard runs on the repository after this change
- **THEN** it reports no error

### Requirement: Allowlist ownership
`.github/CODEOWNERS` SHALL name the existing owner for `/.mintignore` and `/docs.json`, and its header comment
MUST say it also covers the files that decide what the documentation site publishes.

#### Scenario: Allowlist edit needs the owner
- **WHEN** a pull request changes `.mintignore`
- **THEN** GitHub requests review from the owner named in CODEOWNERS

### Requirement: Site guidance in AGENTS.md
`docs/AGENTS.md` SHALL have a short "Documentation site" section that names the wrapper rule and the guard
command and points to `docs/documentation-site.md`, because every `AGENTS.md` is capped at 40 lines.
`docs/ci.md` MUST list the guard step.

#### Scenario: Agent guidance names the wrapper rule
- **WHEN** an agent reads `docs/AGENTS.md` before adding a capability
- **THEN** it finds that a new capability needs a wrapper page, and the link to `docs/documentation-site.md`, which also states that a removed capability needs its wrapper removed

### Requirement: Site guidance document
`docs/documentation-site.md` SHALL state the layout, the allowlist rule and its forms, the default-ignore rule,
the wrapper rule with removal and renaming, the MDX constraint for published Markdown and specs, that the guard
sees only tracked files, the guard commands, the optional `npx mint@4.2.1199 validate`, and that the Overview
page follows the root node map. It MUST NOT be published.

#### Scenario: Guidance document stays private
- **WHEN** the published set is computed
- **THEN** `docs/documentation-site.md` is not in it
