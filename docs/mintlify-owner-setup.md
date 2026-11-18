# Mintlify owner setup

Owner-side settings for the documentation site (the Mintlify project). This note is not published: the
root `.mintignore` allowlist does not re-include it. What the site publishes is decided in the repository
by `docs.json` and `.mintignore`; see the *Documentation site* section of `docs/AGENTS.md`.

## Dashboard settings

- **Repository:** the Veles repository, connected through the Mintlify GitHub app.
- **Docs directory:** the repository root (leave the path empty), because `docs.json` and `.mintignore`
  live at the root and pages come from `docs/`, `BUILD.md` and `openspec/specs/`.
- **Branch:** `master`. A merge to `master` triggers a deployment.

## Reading the deployment check

Mintlify reports a "Mintlify Deployment" check on commits. It is informational and is not a required
status check: the required checks are the repository's own (`ci-ok`). A red Mintlify check means the hosted
build rejected something (a navigation page that is not published, an MDX parse error, a broken
`docs.json`); open its details link, fix the named file and push. The `docs / agent-files` site guard
catches most of these earlier, but it is stricter in some places and looser in others than the hosted
build, so a green guard does not guarantee a green deployment.

Pull request preview deployments need a paid Mintlify plan; on the free plan only `master` is deployed,
so a docs change is first seen live after it merges. Validate locally with the pinned Mintlify CLI
(`mint@4.2.1199`: `validate`, then `broken-links`) when a change touches many pages.

## Privacy

The site is hosted by Mintlify. `docs.json` sets `integrations.telemetry.enabled` to `false`, which
disables Mintlify's visitor telemetry. The site makes no other privacy claim; the app's own guarantees
(no internet permission, no telemetry) are separate from the site.
