# release-publishing Specification

## Purpose
TBD - created by archiving change component-releases-and-conventional-commits. Update Purpose after archive.

## Requirements
### Requirement: Only the owner can publish
The repository SHALL have exactly one release-producing workflow, `publish-release.yml`, triggered only by `workflow_dispatch`. Every job MUST be skipped or fail unless the ref is `refs/heads/master`, `github.actor` and `github.triggering_actor` are both `raidenyn`, and `github.sha` is a full 40-hex commit. No other workflow MUST push tags, create or edit GitHub Releases, or read signing credentials.

#### Scenario: Non-owner dispatch
- **WHEN** a collaborator other than `raidenyn` dispatches `publish-release`
- **THEN** the guard fails before any checkout, build, signing or tag push

#### Scenario: Owner run re-run by someone else
- **WHEN** a run started by `raidenyn` is re-run by another user
- **THEN** the guard fails because `github.triggering_actor` is not `raidenyn`

#### Scenario: Non-master dispatch
- **WHEN** `raidenyn` dispatches from a branch other than `master`
- **THEN** nothing is built or published

#### Scenario: No other workflow publishes
- **WHEN** the workflow contract suites run
- **THEN** they fail if any other workflow pushes tags, creates releases, or references signing secrets

### Requirement: Publish runs are serialized
`publish-release.yml` SHALL use one concurrency group without cancelling in-progress runs.

#### Scenario: Second dispatch while one runs
- **WHEN** the owner dispatches again while a publish run is in progress
- **THEN** the second run waits and the first is not cancelled

### Requirement: Release plan decides what is built
The publish workflow SHALL classify each release component at `github.sha` as RELEASE (its next version differs from its latest tag, or the previous combined release manifest lacks it) or CARRY, and MUST build and sign only RELEASE components. It MUST fail without building when nothing is RELEASE, when the repository checkout is shallow, or when a component's latest tag is newer than its version in the latest published combined release.

#### Scenario: One component changed
- **WHEN** only the web extension has a new version
- **THEN** only the web extension is built, and Android and installers are carried

#### Scenario: Nothing to release
- **WHEN** no component has a new version and the previous manifest lists every component
- **THEN** the run fails in `plan` with "nothing to release"

#### Scenario: First release
- **WHEN** no combined release exists
- **THEN** every release component is built at its initial version

#### Scenario: Orphaned tags
- **WHEN** `android-v0.2.0` exists but the latest published combined release lists Android 0.1.0
- **THEN** `plan` fails and points to the recovery runbook

### Requirement: Readiness covers partial releases
Drafting SHALL proceed only when the guard, plan, supply-chain policy and carry jobs succeeded and, for every component, each of its jobs succeeded if the component is RELEASE or was skipped if it is CARRY. Any failed or cancelled job MUST block drafting.

#### Scenario: Extension-only release
- **WHEN** the web extension is RELEASE and its build succeeded while Android and installer jobs were skipped as CARRY
- **THEN** drafting proceeds

#### Scenario: Selected job skipped
- **WHEN** Android is RELEASE but `sign-android` was skipped
- **THEN** drafting is blocked

### Requirement: Unchanged components are carried, not rebuilt
Each combined release SHALL include `release-manifest.json` listing every component's version and asset digests. For each CARRY component the publish workflow MUST download exactly the assets listed for it in the previous manifest and verify each digest before reusing it byte-for-byte; a missing, extra or mismatched asset MUST fail the run before drafting.

#### Scenario: Carried assets are identical
- **WHEN** Android is CARRY
- **THEN** the new release's Android APK and mapping have the same sha256 as in the previous release

#### Scenario: Tampered or incomplete previous asset
- **WHEN** a carried asset's digest differs from the previous manifest, or the installer's macOS asset is missing
- **THEN** the run fails and nothing is drafted or tagged

### Requirement: Signing only in protected environments
Android release signing SHALL run only in a job with environment `android-release`, which signs the unsigned APK produced by the build without rebuilding it and verifies that the signing certificate's SHA-256 equals the configured fingerprint. Installer signing and notarization MUST run only in jobs with environment `native-release`, keeping the existing `release_gate.py` stages. Repository-level keystore secrets MUST be removed and no reusable workflow MUST accept signing secrets.

#### Scenario: Wrong signing key
- **WHEN** the `android-release` keystore's certificate differs from the configured fingerprint
- **THEN** `sign-android` fails and nothing is drafted

#### Scenario: Secret outside the environment
- **WHEN** the contract suites scan all workflows
- **THEN** they fail if keystore or native signing secrets are referenced outside `android-release` or `native-release` jobs

### Requirement: Installer release gate
When the installer is RELEASE, the publish workflow SHALL build, verify and sign both the Windows and macOS installers and run the installer release gate (`release_gate.py bundle` and `validate`) for both platforms, and drafting MUST require its success.

#### Scenario: Both platforms always
- **WHEN** the installer is RELEASE
- **THEN** both platforms are built and signed and the gate validates both

#### Scenario: Gate rejects evidence
- **WHEN** `release_gate.py validate` reports an incomplete bundle
- **THEN** the gate job fails and nothing is drafted

### Requirement: All-or-nothing publication
No tag SHALL reach the remote before the owner approves publication in the `release-publish` environment. The publish step MUST re-verify the draft's assets against the digests recorded when drafting, push the tags of all RELEASE components at `github.sha` atomically, leave CARRY components' tags untouched, then publish the draft. Re-running the publish step MUST be safe.

#### Scenario: Failure before approval
- **WHEN** macOS installer signing fails after the Android build succeeded
- **THEN** no component tag exists on the remote and no release is published

#### Scenario: Draft digest mismatch
- **WHEN** a draft asset's sha256 differs from the recorded digest
- **THEN** publishing aborts before any tag is pushed

#### Scenario: Re-run after a partial publish
- **WHEN** the tags were pushed but publishing the draft failed, and the owner re-runs the publish job
- **THEN** existing tags at `github.sha` are accepted and the draft is published

#### Scenario: Tag already elsewhere
- **WHEN** a RELEASE component's new tag already exists at a different commit
- **THEN** the publish job fails without publishing

#### Scenario: Carried tags stay put
- **WHEN** only the web extension is RELEASE and `android-v0.1.0` and `installer-v0.1.0` point at older commits
- **THEN** only `web-extension-v<next>` is pushed and the release is published

### Requirement: Combined date-tagged release
Each publish SHALL produce one GitHub Release tagged with the UTC date `YYYY.MM.DD`, or `YYYY.MM.DD.N` (N >= 2) for later releases the same day.

#### Scenario: Second release on the same day
- **WHEN** `2026.11.20` exists and the owner publishes again that day
- **THEN** the new release is tagged `2026.11.20.2`

### Requirement: Combined release assets
The combined release SHALL contain exactly: `veles-android-<v>.apk`, `veles-android-<v>-mapping.txt`, `veles-web-extension-<v>.zip`, `veles-installer-windows-<v>.exe`, `veles-installer-macos-<v>.dmg`, `release-manifest.json` and `SHA256SUMS`. It MUST NOT contain the standalone native host. New assets MUST have build provenance attestations.

#### Scenario: Complete asset set
- **WHEN** a combined release is published
- **THEN** it has exactly those seven assets and no native host executable

#### Scenario: New assets are attested
- **WHEN** a component is RELEASE
- **THEN** its assets verify with `gh attestation verify`

### Requirement: Combined release notes
Release notes SHALL start with a table listing every release component, its version, and whether it is new or unchanged since a named release, followed by each new component's changelog section and download verification instructions.

#### Scenario: Notes list carried versions
- **WHEN** release `2026.11.20` builds only Android 0.2.0 while the extension and installers stay at 0.1.0 since `2026.11.09`
- **THEN** the table shows Android 0.2.0 as new and the others as unchanged since `2026.11.09`

### Requirement: Release preview on master
Every push to `master` SHALL write the release plan (current and next version per component, bump reasons, what would be carried) to the job summary without building, signing or publishing.

#### Scenario: Preview after a feature merge
- **WHEN** a `feat:` PR touching `web-extension/` is merged
- **THEN** the summary shows the web extension's next minor version and that Android and installers would be carried

### Requirement: Legacy release paths retired
`release.yml`, `release-native-installers.yml`, and the manual prerelease publication in `release-build.yml` SHALL be removed in the change that adds `publish-release.yml`.

#### Scenario: Plain semver tag push
- **WHEN** someone pushes a tag `1.0.0`
- **THEN** no workflow builds or publishes a release
