## Context

The Mintlify GitHub app is connected to this repository (Mintlify project `veles-android`) and reports a failing
"Mintlify Deployment" check on pull requests because no `docs.json` exists. Mintlify builds a site from the
`.md`/`.mdx` files under the folder that holds `docs.json`, renders them as written (it does not rewrite them),
and hosts the result; Git stays the source of truth.

Facts this design rests on, verified locally with the `mint` CLI (full `mint validate` builds and `mint dev`)
on copies of the repository's files, and from Mintlify's documentation (see `brainstorm.md`, *Observed facts*):

- Every `.md`/`.mdx` file under the root is built and served, listed in navigation or not (an unlisted page
  returned HTTP 200 in `mint dev`). Only `.mintignore` removes a file.
- `.md` is parsed as MDX. An HTML comment, a bare `<tag>`, `<5` or `<=` outside code fails the build.
- Navigation cannot point outside the root (`../openspec/...` is rejected).
- A root `docs.json` plus a gitignore-style allowlist `.mintignore` builds the 28 `openspec/specs/` files and
  skips the 21 files that fail to parse.
- A page whose file name is `spec.md` is titled "Spec". A wrapper `.mdx` with a `title` and an `import` of the
  spec renders the full spec under that title, and the imported file is not served as a page of its own.
- Navigation `products` give each product its own navigation and a product switcher; no plan restriction is
  documented. `integrations.telemetry.enabled: false` disables visitor telemetry.

Constraints: `openspec/` structure is not touched; no new CI job (a step in the existing `docs` node is
allowed); the `docs/` folder reorganisation is a separate change; the `docs` CI node installs nothing and runs
only the standard-library Python on the runner.

## Goals / Non-Goals

**Goals:**

- One public Mintlify site with a Users product and a Developers product.
- Publish exactly the intended files: new user and developer pages, the curated `docs/` references, `BUILD.md`,
  `CONTRIBUTING.md` and `openspec/specs/` — nothing else from the monorepo.
- Readable titles for every capability spec without editing `openspec/`.
- A CI guard in the `docs` node that fails before Mintlify would, for every error class seen locally.

**Non-Goals:**

- Reorganising or renaming existing `docs/` files (follow-up change), beyond moving the one user guide.
- Pairing and external-device user guides, a custom domain, paid Mintlify features, the AI assistant setup.
- Running the Mintlify CLI in CI or adding Node to the `docs` node.
- Changing the landing page in `site/` or its GitHub Pages deployment.

## Decisions

### D1. Root `docs.json` with an allowlist `.mintignore`

`docs.json` and `.mintignore` sit at the repository root, the only placement that can publish `openspec/specs/`
in place. `.mintignore` uses only these forms: `/*` to ignore everything, `!/<path>` to re-include a top-level
file or folder, `/<folder>/*` to ignore a re-included folder's contents, and `!/<folder>/<path>` to re-include
inside it. The initial list:

```text
/*
!/docs.json
!/BUILD.md
!/CONTRIBUTING.md
!/docs/
/docs/*
!/docs/user/
!/docs/dev/
!/docs/assets/
!/docs/ci.md
!/docs/releasing.md
!/docs/reproducible-builds.md
!/docs/superspec-workflow.md
!/docs/decisions/
!/docs/rfcs/
!/openspec/
/openspec/*
!/openspec/specs/
```

Future edits to `.mintignore` MUST stay within these four forms: the guard (D6) computes the published set
with git's gitignore matching as a stand-in for Mintlify's, and only these forms were verified to behave the
same in the Mintlify CLI. The guard rejects any other line (comments and blank lines aside).

Alternatives: a subfolder root with specs copied in (two copies, drift on every archive) and a root
`docs.json` with a denylist (every new internal file would be published by default). The allowlist fails safe:
a new file is unpublished until someone adds it.

### D2. One site, two products

`navigation.products` holds two entries:

- **Users** (`docs/user/`): Welcome (`index.mdx`), Install (`install.mdx`), Grant notification access
  (`notification-access.mdx`), Sensitive notifications (Android 15+, `sensitive-notifications.md`), Privacy and
  offline (`privacy.mdx`). The site's home page is expected to be `docs/user/index`, the first page of the
  first product; the post-deployment spot check confirms what `/` serves.
- **Developers** (`docs/dev/`): a "Start here" group (Overview `index.mdx`, Architecture `architecture.mdx`,
  Build `build.mdx`, Contributing `contributing.mdx`), an
  "Engineering reference" group (`docs/ci`, `docs/releasing`, `docs/reproducible-builds`,
  `docs/superspec-workflow`, the two decision records and the RFC), and a "Specifications" group of the
  capability wrappers, split into sub-groups:
  - Android app: `android-standalone-build`, `ble-sharing-availability`, `sharing-card-copy`;
  - Pairing and trust: `spake2-enrollment-exchange`, `pairing-window-admission`, `protected-record-session`,
    `reconnect-authentication`, `peer-revocation`, `mutual-peer-removal`, `trust-activation-recovery`,
    `trust-store-failure-recovery`, `synthetic-session-probe`, `pairing-debug-harness`;
  - Gate B testing: `gate-b-evidence-matrix`, `gate-b-history-fixture`, `gate-b-journey-harness`;
  - Build, CI and release: `ci-pipeline`, `component-versioning`, `release-publishing`,
    `pr-last-green-selection`, `commit-conventions`;
  - Contributor workflow: `agent-guidance-layout`, `openspec-skills-delivery`, `superspec-review-gate`,
    `superspec-closeout`, `superspec-tracker-intake`, `github-project-automation`, `real-device-testing`.

  A wrapper's title is the capability name in sentence case with acronyms kept (`ci-pipeline` → "CI pipeline",
  `spake2-enrollment-exchange` → "SPAKE2 enrollment exchange").

`docs.json` also sets `name` "Veles", `theme` "mint", `colors` (`primary` `#1e6b47`, `light` `#8fdbb0`,
`dark` `#1e6b47`), `logo` and `favicon` from `docs/assets/veles-icon.png` (a copy of `site/veles-icon.png`),
`integrations.telemetry.enabled: false`, and navbar links to the GitHub repository and to its Releases page (all components; the repository has three
tag prefixes, so no single "latest release" link).
Alternatives: tabs (one shared sidebar search scope, weaker separation) and two sites (rejected by the user).

### D3. Capability wrappers

Each capability gets `docs/dev/specs/<capability>.mdx`:

```mdx
---
title: "<Readable capability name>"
description: "<one line from the spec's Purpose>"
---

import Spec from "/openspec/specs/<capability>/spec.md";

<Spec />
```

The file name equals the capability folder name; the import path is derived from it. The spec's own first
heading (`# <capability> Specification`) renders below the page title; this duplication is accepted. A spec
without a wrapper is still published raw (titled "Spec", not in navigation) because `openspec/specs/` is
re-included; that is the documented interim state for a newly archived capability.

### D4. Single-source developer pages

- **Build** and **Contributing** are wrappers that import `/BUILD.md` and `/CONTRIBUTING.md`. Every
  relative link in those files (today `BUILD.md` lines 16, 63, 84, 112, 124 and `CONTRIBUTING.md` lines 95-97)
  becomes an absolute `https://github.com/raidenyn/veles-android/blob/master/...` link, which works on GitHub
  and on the site.
  `CONTRIBUTING.md` is on Mintlify's default ignore list; if the `!/CONTRIBUTING.md` re-include does not make
  the import resolve (locally or hosted), the fallback is a short page that summarises and links to the file
  on GitHub.
- **Overview** is written for people: what Veles is, the components (Android app, web extension, Rust core,
  protocol, native bridge, installers, verifier, tooling) and where each lives, drawn from the root
  `AGENTS.md` node map. **Architecture** draws on `CONTRIBUTING.md` "How Veles Works" and the node
  `AGENTS.md` files. Both are hand-written; `docs/AGENTS.md` asks for the Overview to be updated when the node
  map changes.
- Curated `docs/` pages are published unchanged except: the RFC gets the minimal escape that makes it parse,
  and a relative link to an unpublished file, or one `mint broken-links` reports, becomes an absolute GitHub
  link.

### D5. User pages

Written as `.mdx` from `README.md`, the landing page (problem, privacy, free, adb guide) and the existing
guide, which moves to `docs/user/sensitive-notifications.md` and stays plain Markdown with no Mintlify
components, so it still renders on GitHub; `README.md` (lines 56, 66) and `BUILD.md` (line 84) link to that
GitHub path. The other user pages are `.mdx` and may use components. Privacy and offline
describes the app (no internet permission, no telemetry, on-device processing) and states separately that the
documentation site is hosted by Mintlify with telemetry turned off, claiming nothing more about the site.
Pairing with a computer is not documented.

### D6. Site guard `docs/checks/check_site.py`

A standard-library Python 3.8+ script with the same finding format as the agent-file check
(`severity path: message`), exit non-zero on errors and zero with only warnings, `--root` argument.

- **Published set**: tracked files (`git ls-files -z`) minus those `git ls-files -z -c -i
  --exclude-from=.mintignore` reports, so gitignore matching is git's own, not a reimplementation; minus
  Mintlify's documented default ignores (`.git`, `.github`, `.claude`, `.agents`, `.idea`, `.vscode`,
  `.cursor`, `.mintlify`, `node_modules`, `README.md`, `LICENSE.md`, `CHANGELOG.md`, `CONTRIBUTING.md`)
  unless `.mintignore` re-includes the path explicitly (assumed to override the default; the import fallback
  in D4 and the deployed spot check cover the case where it does not). Pages are the `.md`/`.mdx` files in
  that set. Only tracked files are seen, so a new page must be `git add`ed before the guard sees it.
- **Imports**: every `import ... from "<path>"` in a published `.mdx` page (the spec wrappers and the Build
  and Contributing pages alike) is resolved from the root; the imported file is scanned as a `.md` page.
- **Errors**:
  - `docs.json` is missing or not valid JSON, or a navigation page has no published `.md`/`.mdx` file;
  - a page listed twice in navigation;
  - an import in a published `.mdx` page that points at a missing file, or a wrapper in `docs/dev/specs/`
    whose import does not match its file name;
  - a wrapper not listed in navigation;
  - `logo`/`favicon` paths that are not published files;
  - a `.mintignore` line outside the four forms of D1;
  - MDX hazards in every published `.md` page and every imported file: outside fenced code blocks and inline
    code spans, an HTML comment `<!--`, or a `<` followed by a non-space character. Code spans are matched per
    paragraph, as in CommonMark (a backtick run closed by an equal run, possibly on a later line of the same
    paragraph), so the multi-line spans in `docs/ci.md`, `docs/reproducible-builds.md` and
    `docs/superspec-workflow.md` are not flagged. In `.mdx` pages, only `<!--` outside code is an error
    (components and expressions are intended).
- **Warnings**: a capability folder in `openspec/specs/` with no wrapper; a `{` or `}` outside code in a `.md`
  page or imported file (seen to parse in `mint validate` but may be evaluated as an expression; promoted to an
  error only if calibration shows the build rejects it). This narrows the brainstorm's *Site guard*, which
  listed braces among the failing hazards, because no observed build failed on a brace.

Calibration: the guard is run against the current published set; it must report no error for any of the 28
current specs unchanged, and every rule must correspond to input `mint validate` rejects. A rule that flags
text Mintlify accepts is narrowed, not worked around by editing specs.

Alternatives: running `mint validate` in CI (needs Node and a network install in a node that installs
nothing, and the user chose no new CI job) and relying only on the Mintlify check (may stay red on the Free
plan).

### D7. CI and ownership

- `check-docs.yml` `agent-files` job gains a step `python3 docs/checks/check_site.py --root .`. In
  `verify/test/workflow-contracts.test.mjs` the existing `python3 docs/checks/` allow-list prefix already
  permits it; the edits are the docs-node assertion that counts the `run:` steps (from 2 to 3, line 3701), a
  new assertion that the `agent-files` job runs exactly `python3 docs/checks/check_site.py --root .`, and the
  matching messages, including the expected `/exactly the agent-file check/` message in both mutation tests
  (the chained-install one and the dropped-tests one at line 3744). The tests (`docs/checks/test_check_site.py`)
  run in the existing `unit-tests` job through its `discover` pattern.
- `.github/components.json`: `docs.json` and `.mintignore` join the `docs` node's enumerated paths.
- `.github/CODEOWNERS`: `/.mintignore` and `/docs.json` with the existing owner; the header comment is widened
  to "and the files that decide what the documentation site publishes".
- `docs/AGENTS.md` gains a "Documentation site" section: layout, the allowlist rule, the wrapper rule (add a
  wrapper and navigation entry for a new capability; delete or rename it with the capability in the same
  change), the MDX authoring constraint for published `.md` files and specs, the guard commands, and the
  optional local preview. `docs/ci.md` lists the new step. `docs/mintlify-owner-setup.md` (unpublished)
  records the dashboard settings: docs directory = repository root, branch `master`, and how to read the
  deployment check.
- Local validation is optional: `npx mint@4.2.1199 validate` and `npx mint@4.2.1199 broken-links` (released
  2026-11-03, the newest release at least two weeks old when this design was written; the author's checks ran
  the then-latest release, so implementation re-runs them on the pinned one).

## Risks / Trade-offs

- [The hosted build may treat `.mintignore` or imports differently from the local CLI] → After deployment,
  request a sample of unpublished paths (an `otp-*` page, `docs/superpowers/...`, an archived change,
  `AGENTS.md`) and expect Not Found; request a wrapper page and the Contributing page and expect their imported
  text; request `/` and record which page it serves. Report the result.
- [The Mintlify PR check may stay red on the Free plan (previews are paid-only)] → The site guard is the CI gate;
  the check's result is reported, never masked. It is not a required check.
- [The guard's hazard rules are heuristics] → Calibrated against `mint validate`; stricter-than-Mintlify rules are
  narrowed; the unverified brace rule is a warning; unit tests cover each rule with a positive and a negative
  case. Today's published set has one real hazard, the RFC's `<1 KiB` (line 2663), which D4 escapes.
- [Every future spec becomes MDX-constrained] → Stated in `docs/AGENTS.md`; the guard reports the exact line;
  today's 28 specs already comply.
- [Newly archived capabilities appear only as raw "Spec" pages until wrapped] → The guard warns, naming the
  missing wrapper; the closeout's documentation review is the natural moment to add it.
- [Absolute GitHub links leave the site for pages the site also publishes] → Accepted for this version; site
  routes can replace them in the `docs/` reorganisation change.
- [Overview and Architecture can drift from the node map] → Short pages; `docs/AGENTS.md` names the update duty.
- [Branch and directory are dashboard settings outside the repository] → `docs/mintlify-owner-setup.md`; the
  owner confirms them.

## Migration Plan

1. Land the change on a branch; the PR runs the `docs` node (with the guard) and the Mintlify check.
2. Owner confirms the dashboard settings (`docs/mintlify-owner-setup.md`).
3. After merge to `master`, Mintlify deploys production; run the published-set spot check from *Risks*.
4. At closeout, after the archive creates `openspec/specs/documentation-site/spec.md`, add its wrapper and
   navigation entry in the documentation review.

Rollback: revert the commit. With `docs.json` gone the Mintlify check returns to its current failing state;
nothing else depends on the site.

## Open Questions

- Whether the Mintlify PR check turns green on the Free plan — answered by this change's pull request.
- Whether Mintlify resolves relative `x.md` links between published pages — answered by `mint broken-links`
  during implementation; failures become absolute GitHub links (D4). This replaces the brainstorm's "site
  routes" for this version, keeping one link rule for every published file; site routes can come with the
  `docs/` reorganisation.
