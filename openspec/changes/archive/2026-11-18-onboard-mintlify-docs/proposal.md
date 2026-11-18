## Why

Veles has no navigable documentation: users get a README and one guide, and contributors get a `docs/` folder
that mixes curated references with working records. The Mintlify GitHub app is already connected to this
repository, and its deployment check fails on pull requests because no `docs.json` exists. Publishing one
Mintlify site with a user guide and a developer section gives both audiences a readable home, built from the
Markdown that already lives in the repository, including the living requirements in `openspec/specs/`.

## What Changes

- **One Mintlify site, two products.** A root `docs.json` defines the site (name, Veles branding from the landing
  page: `#1e6b47` light, `#8fdbb0` dark and the app icon; telemetry off) with two navigation products: Users and Developers.
- **An allowlist decides what is published.** A root `.mintignore` ignores everything and re-includes only the
  published files, so internal working records in `docs/`, archived OpenSpec changes and the rest of the
  monorepo never reach the site. No existing file moves except the user guide below.
- **User pages** in `docs/user/`: Welcome, Install, Grant notification access, Android 15+ sensitive
  notifications (moved from `docs/sensitive-notifications.md`, with the `README.md` and `BUILD.md` links
  updated) and Privacy and offline, which claims nothing about the hosted site beyond telemetry being off.
- **Developer pages** in `docs/dev/`: Overview, Architecture, Build and Contributing (importing `BUILD.md`
  and `CONTRIBUTING.md`, whose relative links become absolute GitHub links; if such an import does not
  resolve, a short page linking to the file on GitHub), an Engineering reference group
  for the curated `docs/` pages published in place, and one titled wrapper page per capability in
  `docs/dev/specs/` that imports `openspec/specs/<capability>/spec.md` unchanged. This change's own
  capability, `documentation-site`, exists in `openspec/specs/` only after the closeout's archive, so its
  wrapper and navigation entry are added in the closeout's documentation review, after the archive.
- **A site guard in CI.** A standard-library Python check runs as a step in the existing `docs` node: it fails
  on missing navigation pages, broken wrapper imports, wrappers missing from navigation and MDX syntax that
  breaks the Mintlify build, and warns about a capability without a wrapper.
- **Ownership and guidance.** The new root files join the `docs` CI node; `.mintignore` gets a CODEOWNERS
  entry; `docs/AGENTS.md` and `docs/ci.md` describe the site, and `docs/mintlify-owner-setup.md` records the
  dashboard settings.

Out of scope: reorganising the `docs/` folder (a separate follow-up change), pairing and external-device user
guides, a custom domain and paid Mintlify features. The landing page in `site/` is unchanged.

## Capabilities

### New Capabilities

- `documentation-site`: the published Mintlify site — its configuration, the Users and Developers products,
  the publication allowlist, the user and developer page sets, the capability wrappers, telemetry off, and the
  site guard that keeps the build from breaking, the CODEOWNERS entry for `.mintignore`, and the
  `docs/AGENTS.md` guidance (including that removing or renaming a capability updates its wrapper in the same
  change).

### Modified Capabilities

- `component-versioning`: the `docs` node's enumerated paths gain `docs.json` and `.mintignore`.
- `ci-pipeline`: the `docs` node's `agent-files` check also runs the site guard.

## Impact

- **New files**: `docs.json`, `.mintignore`, `docs/user/*`, `docs/dev/*` (including 28 wrappers),
  `docs/checks/check_site.py` and its tests, `docs/mintlify-owner-setup.md`.
- **Edited files**: `BUILD.md` and `CONTRIBUTING.md` (relative links only), `README.md` (guide links),
  `.github/components.json`, `.github/CODEOWNERS` (entry and header comment), `.github/workflows/check-docs.yml`,
  `verify/test/workflow-contracts.test.mjs`, `docs/AGENTS.md`, `docs/ci.md`, the RFC (minimal parse fix) and any
  published page whose relative links fail `mint broken-links`.
- **Moved file**: `docs/sensitive-notifications.md` to `docs/user/`.
- **CI**: editing `check-docs.yml` runs the `docs`, `tooling`, `installer` and `supply-chain` nodes (its
  `global-overrides` entry); `docs/reproducible-builds.md`
  is also a `native-host` ci-path.
- **External**: the Mintlify project's docs directory must stay the repository root on `master` (owner
  action). Two results can only be checked on the deployed site and are reported, not assumed: whether the
  Mintlify PR check turns green on the Free plan, and whether the hosted build applies `.mintignore` as the
  local CLI does (sampled unpublished URLs must return Not Found). No app, extension, Rust or protocol code
  changes.
