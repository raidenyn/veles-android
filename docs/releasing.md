# Releasing

Veles ships three release components, each with its own version and tag:

| Component | Tag | Release assets |
|---|---|---|
| Android app | `android-v<semver>` | `veles-android-<v>.apk`, `veles-android-<v>-mapping.txt` |
| Web extension | `web-extension-v<semver>` | `veles-web-extension-<v>.zip` |
| Native installers | `installer-v<semver>` | `veles-installer-windows-<v>.exe`, `veles-installer-macos-<v>.dmg` |

Every publication is one **combined GitHub Release** tagged with the UTC date `YYYY.MM.DD`
(`YYYY.MM.DD.2`, `.3`, … for later releases the same day). It always carries exactly seven assets:
the five above plus `release-manifest.json` and `SHA256SUMS`. A component without changes since the
previous combined release is **carried**: its assets are copied byte-for-byte from that release after
their digests are checked against its `release-manifest.json`. Only changed components are built,
signed and tagged.

Only `.github/workflows/publish-release.yml` creates releases, pushes tags or reads signing
credentials. No other workflow may (the contract suites fail if one does). Pushing a plain semver
tag such as `1.0.0` no longer triggers anything.

## Versions come from commits

Versions are computed from Conventional Commits (see `scripts/commitlint.py`, the `pr-title` check
and `docs/ci.md`). For each component, only commits that touch that component's paths in
`.github/components.json` count, starting from its latest tag:

| Commit | Bump |
|---|---|
| `feat:` | minor |
| `fix:`, `perf:` | patch |
| `!` after the type, or a `BREAKING CHANGE:` / `BREAKING-CHANGE:` footer | major (also below 1.0) |
| `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `style`, `revert` | none |

PRs are squash-merged, so the PR title and body become the commit that counts. A component that was
never tagged starts at `0.1.0`.

**Preview.** Every push to `master` runs the `preview` job of `release-build.yml`. Its job summary
shows each component's current and next version, the bump reasons, and what would be carried. It
builds, signs and publishes nothing.

## Owner runbook

Only `raidenyn` can publish. The `guard` job (and every other job's `if:`) requires a
`workflow_dispatch` on `refs/heads/master`, with `github.actor` and `github.triggering_actor` both
`raidenyn`, and a full 40-hex `github.sha`. A dispatch or re-run by anyone else builds nothing.

1. **Check the preview** of the latest `master` push: the next versions are what you expect.
2. **Dispatch** *Actions → publish-release → Run workflow* on `master`. The workflow has no inputs.
   Runs are serialized (`concurrency: publish-release`, never cancelled): a second dispatch waits.
3. `plan` classifies each component as RELEASE or CARRY. It fails, before anything is built, when:
   - nothing is RELEASE ("nothing to release");
   - the checkout is shallow;
   - a component tag is newer than the version listed in the latest combined release (orphaned
     tags; see Recovery);
   - the latest combined release has no `release-manifest.json`.
4. **Approve the signing environments** when asked:
   - `android-release` for `sign-android` (only when Android is RELEASE);
   - `native-release` for `sign-windows` and `sign-macos` (only when the installer is RELEASE;
     both platforms are always built and signed together).
5. `gate-installer` runs `release_gate.py bundle` and `validate` for both platforms. `carry`
   verifies the carried assets. `ready` checks that every job of every RELEASE component succeeded
   and every CARRY component's jobs were skipped.
6. `draft` checks the embedded versions (installer descriptors, extension manifest) against the plan,
   stages the seven assets, attests the new ones and creates a **draft** release. No tag exists yet.
7. **Review the draft** under *Releases*: the version table, the changelog sections and the seven
   assets.
8. **Approve `release-publish`.** The `publish` job then:
   1. downloads the draft's assets and compares them with the digests `draft` recorded;
   2. pushes the tags of the RELEASE components at the release commit in one `git push --atomic`
      (carried components' tags are never touched);
   3. checks that the date tag is absent or already at the release commit;
   4. publishes the draft on its date tag and checks that the tag resolves to the release commit.

Rejecting an environment approval stops the run. Nothing has been tagged or published at that point
(a draft may exist; delete it or let the next run replace it).

## Bootstrap (first combined release)

With no combined release yet, `plan` finds no previous release and starts every component at `0.1.0`.
All three components are RELEASE: Android, the web extension and both installers are built and
signed, and `publish` pushes `android-v0.1.0`, `web-extension-v0.1.0` and `installer-v0.1.0`
together with the first date tag.

Before the first dispatch, complete the [owner settings](#one-time-owner-settings). Older plain
semver releases (`0.0.x`), drafts and prereleases are never taken as the previous combined release.

## Recovery

**`publish` failed after pushing tags** (for example, publishing the draft failed). Re-run **only the
failed `publish` job of the same run** (*Re-run failed jobs*). It is idempotent:

- a RELEASE tag already at the release commit is accepted;
- a RELEASE tag at any other commit fails the job;
- a release that is already published is left as it is;
- the draft digests are checked again before anything else.

Do not start a new dispatch instead: the new plan would see the pushed tags without a combined release
that lists them, and `plan` fails with "orphaned tag". A re-run must again be started by `raidenyn`.

**The run cannot be re-run** (artifacts expired, or it was abandoned). Remove what the interrupted
publication left behind, then dispatch again:

1. delete the orphaned component tags that `plan` names, for example
   `git push origin :refs/tags/android-v0.2.0` (only the tags of this release; never a tag listed in
   a published combined release);
2. delete the draft release for the date tag (*Releases → draft → Delete*), and the date tag if it
   was created;
3. dispatch `publish-release` again.

**A job before `publish` failed** (build, signing, gate, carry, ready or draft). Nothing was tagged or
published. Fix the cause on `master` and dispatch again, or re-run the failed jobs. A stale draft for
the same date tag is replaced by the next `draft` job.

## Rollback

Roll forward; do not rewrite a published release.

1. Land a fix on `master` whose commit bumps the affected component (`fix:` for a patch; a plain
   `revert:` does not bump, so follow it with, or squash it into, a `fix:`).
2. Dispatch `publish-release`. The affected component is RELEASE at its next patch version; the others
   are carried. The new release becomes the latest combined release.
3. Optionally edit the bad release's notes to point at the replacement.

Withdrawing a published release (deleting it, or marking it as a prerelease) is a last resort, for
example for a compromised asset. It makes the release before it the latest combined release again, so
the withdrawn release's new component tags become orphaned and `plan` fails until they are deleted
(see Recovery). Deleting them means the same version numbers are built again from different commits;
only do this when no one can have installed the withdrawn assets.

## One-time owner settings

Repository *Settings*:

1. **Environment `android-release`** with required reviewer `raidenyn`, limited to the `master` branch:
   - secrets `KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD` (move them here from
     the repository secrets, then **delete the repository-level copies**; no reusable workflow
     accepts signing secrets any more);
   - variable `VELES_ANDROID_CERT_SHA256`: the SHA-256 fingerprint of the existing release signing
     certificate (colons and case are ignored). Read it from real output only, for example
     `apksigner verify --print-certs <a release APK signed with this key>` or
     `keytool -list -v -keystore <keystore>`. `sign-android` fails when the signed APK's certificate
     differs.
2. **Environment `native-release`**: unchanged; see `docs/native-installer-owner-setup.md`.
3. **Environment `release-publish`** with required reviewer `raidenyn`, limited to `master`, no
   secrets. This approval is the point after which tags reach the remote.
4. **Workflow permissions**: the default `GITHUB_TOKEN` stays read-only; `publish-release.yml` grants
   `contents: write` (and, for `draft`, `id-token: write` and `attestations: write`) per job.
5. **Audit duplicates**: no repository or organization secret named `KEYSTORE_*`, `KEY_*`,
   `VELES_WIN_*`, `VELES_MAC_*` or `VELES_NOTARY_*` outside its environment.
6. **Squash merge** with the PR title and body as the commit message, and the `master` ruleset that
   requires `pr-title` and `ci-ok` (see `docs/ci.md`).

GitHub has no per-workflow dispatch ACL: another writer can request a run, but the guard rejects it
before any checkout, and the environment approvals are the boundary that gates key access and
publication. Signing keys never leave their environments; nothing in this repository holds a secret
value.
