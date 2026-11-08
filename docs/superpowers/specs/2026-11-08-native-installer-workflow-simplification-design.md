# Simplified native installer workflows — proposed design

Status: proposed for owner review; planning only. Supersedes the OTP-25 release
orchestration requirements only after approval. Does not authorize implementation,
merge, publication, environment changes, or a release run.

## Intent

For a repository operated by raidenyn and the assistant, replace manual digest
approval, release-tag dispatch and cross-run selection with two understandable
paths: labeled PR debug installers and owner-triggered signed master installers.
Keep installation safety and signature verification, not the multi-party ceremony.

## Current implementation review

- `.github/workflows/release-build.yml` couples native producers to Android/web/
  Rust/supply-chain jobs through the broad `release-build` label. Ordinary master
  pushes build Android, **not native installers**. Manual dispatch also publishes
  an Android prerelease; it is not a suitable native installer entry point.
- `build-native-{windows,macos}.yml` produce unsigned input trees, not downloadable
  finished setup/DMG installers. Presence of release config implicitly selects
  production; PRs therefore currently compile the production channel.
- `build.gradle.kts` hardcodes Rust/Swift release profiles and A/B slots. A true
  debug installer needs explicit profile and channel selection, not artifact renaming.
- `release-native-installers.yml` requires a version tag, commit, two producer run
  IDs and two approved descriptor hashes. Four signing jobs use `native-release`.
  `release_gate.py:authorize` and bundle context validation enforce that old model.
- The current two-target bundle cannot pass for a Windows-only build. Existing
  macOS reproducibility and DER-entitlements blockers are independent defects;
  workflow simplification must not disguise them as successful signing.
- Read-only environment inspection on 2026-11-08: `native-release` already has
  raidenyn as its sole reviewer, self-approval allowed, admin bypass disabled,
  but **no branch/tag restriction**. No settings were changed during planning.

## Approaches

1. **Recommended:** dedicated PR caller and dedicated master-only release caller,
   sharing platform build/packaging code. One owner approval boundary replaces
   manually supplied artifact authorization. Clear secret isolation and few inputs.
2. Extend `release-build` with more modes and labels. Fewer caller files, but
   native labels risk triggering unrelated Android signing/publication.
3. Keep the existing release graph with defaults for all inputs. Smallest diff,
   but retains tags, cross-run coupling and the two-platform eligibility problem.

Recommendation is an assistant proposal, not an already-approved owner choice.

## PR path

```text
PR + windows installer build label -> raidenyn approval -> Windows debug setup
PR + mac installer build label     -> raidenyn approval -> macOS debug DMG
both labels                       -> approval -> both selected builds
no labels                         -> no installer builds
```

- Labels are exactly `windows installer build` and `mac installer build`.
- Use `pull_request` events `opened`, `reopened`, `labeled`, `unlabeled`,
  `synchronize`, `ready_for_review`; drafts do not build. Re-evaluate current labels
  on every event. Label removal/new commits cancel obsolete debug runs. Checkout
  the event's exact PR head SHA after approval; use no `pull_request_target`.
- A secret-free `native-installer-debug` environment approval job precedes any
  PR-controlled code. Reviewer is raidenyn, self-approval permitted. Forks receive
  no secrets or write token; GitHub's fork-workflow approval may additionally apply.
- Debug defaults to unsigned Windows and ad-hoc-signed macOS (arm64 loader
  requirement), not a reusable private debug key. No release environment or secrets.
- Compile Rust/Swift in debug configuration and retain PDB/dSYM where available.
  Build once; do not require release A/B reproducibility for debug artifacts.
- Reuse the isolated fixture identity/paths as the test channel; prominently mark
  output as DEBUG / NOT FOR RELEASE. Keep strict `X.Y.Z` descriptor version;
  encode PR number/SHA/run ID in artifact names and metadata, not version syntax.
- Unsigned production verification will not install such payloads. Add a strictly
  compile-time debug trust adapter for fixture builds, accepting only the embedded
  fixture carrier and matching payload inventory, never production identity/paths.
  It is not an authentication boundary: debug packages are untrusted code. No CLI,
  environment, descriptor field or runtime flag may enable it in release binaries.
  This includes the cleanup-helper path. Production verification remains unchanged.
- Upload finished installers, checksum/metadata and symbols separately. Test real
  non-admin install/repair/uninstall and production co-existence before calling
  these usable installers; upload of an unsigned input tree is insufficient.

## Release path

```text
raidenyn -> Run workflow on master -> select windows / macos / both
        -> release-profile unsigned A/B build(s) in this run
        -> raidenyn native-release approval -> sign/package/verify -> artifacts
```

- Proposed trigger decision: **manual release dispatch on master**, not automatic
  signing on each push. Normal master CI remains automatic. This implements
  owner-only release initiation without relying on who happened to merge a PR.
- Only input is `platform` (`windows`, `macos`, `both`; default `windows`). Resolve
  the immutable source SHA from this run's master dispatch, not a caller-supplied
  ref/commit. Reject all other events/refs before checkout/build; require both
  `github.actor` and `github.triggering_actor` to be `raidenyn`, including reruns.
- Build and sign within one workflow run; no external run IDs, manual descriptor
  digest inputs/variables, or tag prerequisite. Retain internal descriptor hashes,
  provenance, inventory, tool pins and release A/B comparison. Capture and validate
  same-run artifact IDs/digests and the exact source SHA before importing keys.
- Keep `native-release` for release secrets only, selected deployment branch
  **master only**, raidenyn sole reviewer, self-approval allowed, admin bypass off.
  No PR branch or tag allowed. One protected signing/packaging job per selected OS
  performs nested signing, assembly, outer signing and verification; scope secret
  mappings to signing steps and always remove temporary key material/trust anchors.
- Windows uses the existing pinned self-signed release identity and timestamp.
  Keep certificate pins, recorded anchor and signature/payload correspondence.
  Never fall back to unsigned if any release secret or verification fails.
- macOS release needs an explicitly supported Apple signing identity and production
  config. Missing credentials or unresolved correspondence/reproducibility fails the
  selected macOS target with an actionable diagnostic; no unsigned fallback or
  fake successful notarization. Notarization/public trust remains separate scope.
- Each selected platform validates independently. Windows-only can succeed as a
  signed Windows artifact, without claiming macOS or the 32-case acceptance matrix
  passed. Both selected means both must succeed for overall success.
- Artifacts only; no GitHub Release publication or automatic tag creation.

## Permissions and limits

Workflow actor checks are defense in depth, not a GitHub permission boundary.
The debug approval job is a workflow-controlled convenience gate, not a
tamper-resistant boundary against a malicious repository writer: a PR can modify
its own `pull_request` workflow and remove that job. This design trusts the two
collaborators for secret-free debug work. Protect workflow changes with owner
review and never put release secrets in the debug environment. If approval must
also withstand a malicious workflow-changing PR, stop and design a separate
trusted dispatcher before implementing Task 2; do not claim this gate provides it.
GitHub does not offer a per-workflow dispatch ACL: another writer may request a
run, but it must fail before builds/secrets. Only raidenyn should have repository
administration/environment/secret-management permission. Release secrets must not
also exist as broadly available repo/org secrets. Protect master and release
workflow/build script changes with raidenyn review. An administrator can always
change these policies; this is not protection against the repository owner.

Scope is **native installer credentials and release workflows**. The pre-existing
Android signing workflow is not redesigned here; its PR secret exposure is a
separate audit item, not silently claimed fixed by the native workflow guards.

## Non-goals and constraints

- Windows 10+, static CRT; existing macOS arm64 / macOS 26.0 floor remains.
- Keep the central extension-ID allowlist and deferred Store-ID migration.
- Do not weaken safe paths, ownership, policy, rollback, inventory or cleanup rules.
- No public certificate procurement, new release publication scheme, or acceptance
  ledger rewriting. No requirement for a second independent human approver.
- This proposal withdraws the earlier merge-ready conclusion pending these changes.
