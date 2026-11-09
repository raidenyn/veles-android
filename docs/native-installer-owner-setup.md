# Native installer owner setup (OTP-25)

This page lists the GitHub settings the owner must create or verify for the two installer
paths. Implementation is complete without these settings, but no labeled debug build or
owner release can run until they exist. This change did not modify any label, environment,
secret, variable or repository setting; the steps below are owner actions.

Authority: `docs/superpowers/specs/2026-11-08-native-installer-workflow-simplification-design.md`.
The signing and trust model itself is unchanged; see
`docs/decisions/2026-11-08-self-signed-completion-identity.md`.

## 1. Labels

Create these labels with exactly these names (case and spaces included):

| Label | Meaning |
|---|---|
| `build-windows-installer` | Build the Windows **DEBUG / NOT FOR RELEASE** installer for the pull request. |
| `build-macos-installer` | Build the macOS **DEBUG / NOT FOR RELEASE** DMG for the pull request. |

`build-pr-installers.yml` re-evaluates the current label set on every reviewed
`pull_request` event, so adding or removing a label selects or deselects that platform.
Both labels select both builds; neither label selects nothing.

The `build-android` and `build-web-extension` labels are **unrelated** to native installers:
they select the unsigned component builds in `release-build.yml`, which never builds native
installers (`build-watch` is reserved and builds nothing yet). The earlier installer labels
`windows-installer-build` / `mac-installer-build` are retired and trigger nothing.

Signed release installers are not selected by a label. They are built and signed by the
owner-only `publish-release.yml` (dispatched by `raidenyn` on `master`, no inputs) whenever
the installer component has releasable changes; both platforms are always built and signed
together. See `docs/releasing.md`.

## 2. Debug approval environment: `native-installer-debug`

Create (or verify) a repository environment named `native-installer-debug`:

- **No secrets** and no variables that carry credentials. The environment is a
  secret-free approval gate only.
- **Required reviewers:** `raidenyn` only.
- **Allow self-review:** on (the owner may approve their own debug build).
- **Prevent self-review:** off.
- **Administrators can bypass:** off.
- Deployment branch/tag policy: no restriction needed for a `pull_request` gate; the
  workflow checks out and builds the event's exact PR head SHA only after this approval.
- The approval job runs no PR-controlled code, checks out nothing and runs no build.

This gate is **workflow-local**. It is not resistant to a pull request that edits its own
`pull_request` workflow and removes the approval job. That risk is accepted only because
the debug environment and every debug job hold no release secret and grant only
`contents: read`. Debug artifacts are untrusted, unsigned (Windows) or ad-hoc-signed
(macOS) DEBUG output.

## 3. Release environment: `native-release`

Configure the repository environment named `native-release`:

- **Deployment branches and tags:** `master` **only**. Do not allow any pull-request
  branch, feature branch or tag. This is the missing restriction recorded during the
  2026-11-08 planning inspection.
- **Required reviewers:** `raidenyn` only.
- **Allow self-review:** on.
- **Prevent self-review:** off.
- **Administrators can bypass:** off.
- **Secrets and variables:**
  - `VELES_WIN_PFX_BASE64`, `VELES_WIN_PFX_PASSWORD`, `VELES_WIN_ROOT_CER_BASE64`
    (secrets);
  - `VELES_WIN_CERT_SHA1`, `VELES_WIN_PUBLISHER`, `VELES_WIN_TIMESTAMP_URL`,
    `VELES_WIN_TRUST_SELF_SIGNED` (variables);
  - the macOS signing/notarization secrets and variables named in the `sign-macos` job,
    when that platform is in scope. Without an Apple Developer ID, use the self-signed
    acceptance identity instead (next section).
- The release workflow reads these only in the step-level `env:` of the signing steps.
  Build, verification, assembly, packaging without signing, cleanup and gate steps hold
  no credential.
- Every `*_BASE64` credential secret may be single-line or line-wrapped base64, with or
  without a trailing newline: the 76-column output of `base64` and the 64-column output
  of `openssl base64` are accepted, as are the unframed base64 bodies those tools emit
  for `certutil -encode`. ASCII whitespace inside the value is ignored; everything else
  outside the base64 alphabet is refused.
- `VELES_WIN_ROOT_CER_BASE64` may instead be one complete PEM certificate
  (`-----BEGIN CERTIFICATE-----` … `-----END CERTIFICATE-----`, any line wrapping, which
  is what `certutil -encode` writes). The gate stores it as the certificate DER and
  records `trust_anchor_sha256` over those DER bytes, so the recorded anchor is the same
  whether the value is base64 or PEM. PEM framing is accepted for this secret only; the
  PFX, P12 and notary-key secrets are raw file bytes encoded as base64.

Only `raidenyn` should be able to manage repository administration, environments and
secrets. A collaborator who can request a workflow run is not thereby allowed to approve
the environment or read its secrets.

### macOS self-signed acceptance identity

When no Apple Developer ID is available, the macOS candidate uses the project
self-signed leaf from `docs/decisions/2026-11-09-self-signed-macos-acceptance.md`.
In `native-release` set:

| Kind | Name | Value |
|---|---|---|
| secret | `VELES_MAC_P12_BASE64` | base64 of the single self-signed PKCS#12 (the leaf is its own anchor) |
| secret | `VELES_MAC_P12_PASSWORD` | that PKCS#12's password |
| variable | `VELES_MAC_IDENTITY` | `Veles Self-Signed Acceptance (not a release signer)` |
| variable | `VELES_MAC_PUBLISHER` | `sha256:9d0d1948de28df32ef23091fab0805894d531863ca0b42c44f9ed1b4eb042cc6` |
| variable | `VELES_MAC_TRUST_SELF_SIGNED` | `1` |

`VELES_MAC_TEAM_ID` and `VELES_NOTARY_*` stay unset in this mode. The identity is a
**single** self-signed code-signing certificate, not a separate root plus leaf: the
hosted `macos-26` runners (macOS 26.7) refuse to sign with a certificate whose chain
does not validate (`errSecInternalComponent` after `unable to build chain to
self-signed root`), and a headless `security add-trusted-cert` needs interactive
authorization. A single self-signed certificate signs without any trust change. The
acceptance Mac may trust that certificate once before opening the disk image:

```sh
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain leaf.pem
```

Clearing `VELES_MAC_TRUST_SELF_SIGNED` and setting a real team, notary key and
Developer ID identity returns the gate to the notarized path with no code change,
but the descriptor must also be updated first: `config/release-macos.json` currently
authorizes only the self-signed `sha256:` publisher. Replace that entry with
`team:<TEAMID>` (or add it) before rebuilding, or `signer_policy` rejects the team.

## 4. Audit native signing secrets for duplicates

Before relying on the environment scope:

1. List repository secrets and variables, then organization secrets and variables.
2. Search for duplicate native-signing names such as `VELES_WIN_*`, `VELES_MAC_*` and
   `VELES_NOTARY_*`.
3. Delete or rotate any native-signing duplicate outside `native-release`. A broadly
   available repository or organization secret would bypass the environment boundary for
   anyone who can run a workflow that references it.
4. Confirm that only `raidenyn` holds repository admin, environment approval and
   secret-management rights. An administrator can always change these policies; this
   setup is not protection against the repository owner.

No secret values belong in this repository, in workflow files or in documentation.

## 5. Dispatch authorization is not an ACL

GitHub has no per-workflow dispatch ACL. The repository cannot prevent another writer
from **requesting** a `publish-release` run. The `guard` job rejects any request
that is not all of:

- event `workflow_dispatch`;
- ref `refs/heads/master`;
- `github.actor == raidenyn`;
- `github.triggering_actor == raidenyn` (reruns re-evaluate the rerunner);
- a full 40-hex `github.sha`.

That guard runs before any checkout, build or key access. A rejected request therefore
builds nothing and imports no key. The actor checks in every build/verify/sign job and in
the final gate are **defense in depth, not an access-control list**. Only the
`native-release` environment approval is the boundary that gates key access.

The debug approval gate has the same limit: a PR can edit its own workflow and remove the
approval job. Debug jobs hold no release secrets, and the debug environment holds none.

## 6. Obsolete approved-descriptor variables

The old tag/approved-digest flow used variables named `VELES_APPROVED_DESCRIPTOR_SHA256_*`.
The new gate computes the descriptor digest from the checked-out descriptor and does not
read those variables.

**Do not delete them as part of this change.** List them as removable only after the owner
has:

1. dispatched a release from `master` against the new workflow;
2. verified the same-run A/B, descriptor, payload and per-platform signing reports; and
3. explicitly approved removal.

Until then they are inert configuration, not a second authorization path: no workflow
reads them.

## 7. Verification checklist

After the owner applies the settings, verify without publishing:

- `release-build.yml` builds only the changed release components on an ordinary `master` push
  and the labeled components (`build-android`, `build-web-extension`) on a pull request; it runs
  no native job.
- A pull request with no installer label runs no installer builder.
- A pull request with `build-windows-installer` waits for `native-installer-debug`
  approval and builds only the Windows debug installer; `build-macos-installer` does the
  same for macOS; both labels build both.
- A non-master or non-owner release dispatch fails in `guard` before checkout or build.
- The release gate reports `windows: PASS|BLOCKED` and/or `macos: PASS|BLOCKED` for the
  selected platforms. That status is build/sign status, not acceptance-matrix
  completion.

Hosted execution of the release path requires real credentials and `native-release`
approval. It is not executed by the local test suite and is not claimed by it.
