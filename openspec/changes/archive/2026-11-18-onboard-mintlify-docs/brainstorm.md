## Design Summary

Publish one Mintlify documentation site for Veles with two audiences, built from Markdown that stays in
this repository:

- **Users** — how to install, set up and use Veles (download, notification access, the Android 15+
  sensitive-notifications setup, privacy and the offline guarantee).
- **Developers and contributors** — how Veles is built: architecture, build, CI, releasing, the workflow,
  and the living requirements in `openspec/specs/` as reference pages.

The site is one Mintlify project with one `docs.json`. The two audiences are separated with Mintlify's
navigation `products` (a product switcher, each product with its own menu), so a visitor sees either the
user guide or the developer docs, never one long mixed sidebar.

`docs.json` and a `.mintignore` sit at the repository root, because Mintlify cannot reference files outside
the folder that holds `docs.json` and `openspec/specs/` must be published in place, without copying or
editing it. The `.mintignore` is an allowlist: it ignores everything and re-includes only the published
folders. Apart from the one user guide that moves into the user section, no existing file moves: the allowlist publishes the curated `docs/` pages in place and leaves the
working records unpublished where they are. Reorganising the `docs/` folder is a separate follow-up change.

Mintlify renders the Markdown as written; it does not rewrite or summarise it. User pages therefore have to be
written for users; specs appear as normative reference text.

### Why

The repository has no user documentation beyond the README and one guide, and no navigable developer
documentation: `docs/` mixes curated references with working records (task runbooks, evidence, superpowers
plans). The Mintlify GitHub app is already connected to this repository (project `veles-android`) and its
"Mintlify Deployment" check fails on pull requests because no `docs.json` exists.

### Observed facts this design rests on

Verified locally with the `mint` CLI (`mint validate`, full build) on copies of the repository's files:

- Mintlify builds and serves every `.md`/`.mdx` file under its root whether or not navigation lists it. With
  no exclusions, 594 candidate files were built and 21 failed to parse (archived OpenSpec changes,
  `docs/superpowers/`, evidence notes, five `docs/otp-*`/setup files, the RFC and one protocol review).
- `.md` is parsed as MDX: an HTML comment `<!-- -->` or a bare `<dir>` outside a code span fails the build.
- A navigation entry pointing outside the root (`../openspec/...`) is rejected (`missing-nav-page`).
- All 28 files in `openspec/specs/` build unchanged.
- A root `docs.json` with a `.mintignore` of `/*`, then `!/docs.json`, `!/<folder>/` re-includes, then
  `/openspec/*` and `!/openspec/specs/`, builds cleanly: the 21 failing files are skipped. Without the
  `.mintignore` the same tree fails with 21 errors.

From Mintlify's documentation (read through its MCP server):

- Navigation `products` give each product its own navigation and a product switcher.
- `integrations.telemetry.enabled: false` disables visitor telemetry for the whole site.
- Preview deployments need a paid plan; custom domains are available on the Free plan.
- `hidden: true` pages stay reachable by URL; only `.mintignore` removes a file from the site.

## Alternatives Considered

### Alternative A: Two Mintlify sites (one project per audience)

- **Approach**: a user site from a `user-docs/` subfolder and a developer site from the repository root with
  its own `.mintignore`, each a separate Mintlify project with its own URL.
- **Pros**: fully standalone — separate search, AI assistant index, `llms.txt` and domain per audience.
- **Cons**: a second Mintlify project, whose availability on the current plan and binding to the same
  repository are unconfirmed; two configurations to keep consistent (theme, branding, telemetry).
- **Why not chosen**: the user chose one site; the two-product layout gives separated navigation with one
  project and no plan risk.

### Alternative B: Mintlify Knowledge base for developer docs

- **Approach**: developer documentation as a Mintlify knowledge-base project.
- **Pros**: built for specs, runbooks and onboarding.
- **Cons**: readable only by members of the Mintlify organisation.
- **Why not chosen**: Veles is open source; contributors outside the organisation must be able to read it.

### Alternative C: Docs root in a subfolder, specs copied in

- **Approach**: `docs.json` in a subfolder (for example `docs/`), with `openspec/specs/` copied into it by a
  sync script.
- **Pros**: nothing Mintlify-related at the repository root.
- **Cons**: two copies of every spec and a sync step that can drift; the copy must be regenerated on every
  archive.
- **Why not chosen**: the root allowlist publishes the specs in place with no copy and no drift.

## Agreed Approach

One site, two products (Users, Developers), with `docs.json` and an allowlist `.mintignore` at the repository
root, `openspec/specs/` published in place and untouched, new pages under `docs/user/` and `docs/dev/`, and the
curated existing `docs/` pages published where they are. Chosen by the user over Alternative A; Alternatives B and C are ruled out above.

## Key Decisions

- **One site, two products** (user decision).
- **Content for the first version: configuration plus user-facing pages** (user decision), extended by the
  user's goal of a developer section, which takes the curated `docs/` pages and `openspec/specs/`.
- **`openspec/` structure is not touched** (user decision).
- **The Mintlify integration and the full `docs/` reorganisation are separate changes** (user decision). This
  change moves no existing file except `docs/sensitive-notifications.md` into the user section; the allowlist
  alone decides what is published. Moving files would be costly: 69 files outside archived changes and
  `docs/superpowers/` reference `docs/otp-*` (74 including `docs/superpowers/`), and the
  agent rules, CI checks and CODEOWNERS name `docs/` paths.
- **Layout** (agreed): `docs.json` and `.mintignore` at the root; new user pages in `docs/user/` (Users
  product); new developer pages in `docs/dev/` (Developers product), including one wrapper per capability in
  `docs/dev/specs/`; `ci.md`, `releasing.md`, `reproducible-builds.md`, `superspec-workflow.md`, `decisions/`
  and `rfcs/` published in place; `otp-*`, the proof-of-concept notes, `superpowers/`, `evidence/`, `gate-b/`,
  `checks/` and `docs/AGENTS.md` stay unpublished. URLs follow paths, e.g. `/docs/user/install`.
- **No new CI job; the Mintlify GitHub app's check validates the hosted build** (user decision). Local
  validation is `mint validate`. Because that check may stay red on the Free plan (previews are paid-only),
  the repository also gets its own guard, as a step in the existing `docs` node (see *Site guard*), so a page
  that would break the site is caught in CI either way.
- **Root `docs.json` plus an allowlist `.mintignore`** (agent call, from the observed facts above): the only
  way found to publish `openspec/specs/` in place.
- **Telemetry off** via `integrations.telemetry.enabled: false` (agent call, matching the project's
  no-tracking stance).
- **Branding from the landing page**: primary `#1e6b47` (light) and `#8fdbb0` (dark), the existing app icon
  (agent call).
- **The landing page in `site/` and its GitHub Pages deployment stay as they are** (agent call).
- **Out of scope**: pairing and external-device user guides (the feature is still in progress), a custom
  domain, and paid Mintlify features.
- **Spec titles through wrappers** (agreed): one `.mdx` per capability in `docs/dev/specs/` sets the title and
  imports `/openspec/specs/<capability>/spec.md` unchanged. Verified with `mint dev`: a spec published directly
  is titled "Spec", the wrapper shows the right title and the full text, and the imported file is not served
  as a page of its own. A capability without a wrapper is a **warning**, never an error, so archiving a change
  that adds a capability never fails CI and the superspec closeout procedure does not change; until a wrapper
  is added, the new spec is reachable only by its raw URL, titled "Spec". Adding the wrapper and its navigation
  entry is a follow-up the warning names. A change that removes or renames a capability must delete or rename
  its wrapper in the same change (a wrapper importing a missing file is an error, as the hosted build would
  break too); `docs/AGENTS.md` states this.
- **Developer pages** (agreed): Overview (from the root `AGENTS.md` node map, written for people), Architecture
  (from `CONTRIBUTING.md` "How Veles Works" and the node `AGENTS.md` files), Build and Contributing (wrappers
  importing `BUILD.md` and `CONTRIBUTING.md`, single source). The only edit to those two files is that their
  relative links (`BUILD.md` lines 63, 84, 112, 124; `CONTRIBUTING.md` lines 95-97) become absolute GitHub
  links, which work both on GitHub and on the site; a link that leaves the site for a page the site also
  publishes is accepted for this version. `.mintignore` re-includes `/BUILD.md` and `/CONTRIBUTING.md`
  (`CONTRIBUTING.md` is also on Mintlify's default ignore list). If an import from those root files does not
  resolve, the fallback is a short Build and Contributing page that links to the files on GitHub. The existing curated pages (`ci.md`,
  `releasing.md`, `reproducible-builds.md`, `superspec-workflow.md`, `decisions/`, `rfcs/`) form an
  "Engineering reference" group; the RFC gets the minimal fix that lets it parse.
- **User pages** (agreed): Welcome, Install, Grant notification access, Android 15+ sensitive notifications,
  Privacy and offline — written from the README, the landing page (problem, privacy, free, adb guide) and the
  existing guide. `docs/sensitive-notifications.md` moves to `docs/user/`; its links in `README.md` and
  `BUILD.md` are updated.
- **Dashboard** (agreed, owner action): the Mintlify project's docs directory stays the repository root and its
  branch `master`. The owner note is `docs/mintlify-owner-setup.md`, unpublished like the other owner-setup
  notes.
- **Plumbing** (agreed): `docs.json`, `.mintignore`, `docs/user/` and `docs/dev/` belong to the `docs` CI node in
  `.github/components.json`; `.mintignore` is added to `.github/CODEOWNERS` because it decides what is
  published, with the file's header comment widened to say so. `docs/reproducible-builds.md` is also a
  `ci-path` of `native-host`, so any fix there runs that node too.
- **Site guard** (agent call, from review): a standard-library Python check, `docs/checks/check_site.py` with
  its unit tests, run as a step in the existing `agent-files` job of `check-docs.yml` (the workflow contract's
  command allow-list in `verify/test/workflow-contracts.test.mjs` gains that command). It fails on: a `docs.json` navigation page whose file is missing; a
  wrapper importing a missing file; a wrapper missing from the navigation; and, in every published page and
  every file a wrapper imports, the MDX hazards seen to break the build (an HTML comment, a bare `<tag>` or a
  brace outside code spans and fences). A capability without a wrapper is a warning. This catches a newly
  archived spec that would break the whole site.
- **Agent and CI docs** (agent call, from review): `docs/AGENTS.md` gains a short section on the site layout,
  the allowlist, the wrapper rule and the guard; `docs/ci.md` lists the new step.
- **Privacy wording** (agent call, from review): the user Privacy page describes the app's offline guarantee and
  says separately that the documentation site is hosted by Mintlify with telemetry turned off; it makes no
  claim about the site beyond that setting.

## Open Questions

- **Whether the Mintlify PR check turns green once `docs.json` exists**, or still fails because preview
  deployments need a paid plan. Only the change's own pull request shows it; the result is reported, not
  assumed.
- **Whether the hosted build honours `.mintignore` exactly as the local CLI does**, including the
  `!/openspec/specs/` re-include and imports from re-included root files. Checked on the deployed site (the
  preview if available, otherwise production after merge): a sample of unpublished paths must return Not Found.
- **Relative links between published pages** (for example between the two decision records): whether Mintlify
  resolves `x.md` links; `mint broken-links` decides, and any that fail become site routes in this change.
- **Exact `.mintignore` patterns**: the verified pattern shape is in *Observed facts*; the final list follows the
  layout above and is checked by a build with every unpublished file present.
