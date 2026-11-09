# OTP-25: Signed per-user native-bridge installers

Status: **Approach 1 approved for planning, 2026-11-06.** The owner requested
“Approve approach 1. Generate the plan and stop.” No implementation or release
acceptance is authorized by this document.

## Intent and authority

Give ordinary Chrome users a signed Windows/macOS installation of the plain-Rust
Veles native host that they can install, repair, update, roll back and remove
without administrator access, losing extension trust, or leaving stale native
registration. This unblocks the packaging prerequisite for protected transport;
it does not itself authorize real OTP delivery.

Authority: [issue #89](https://github.com/raidenyn/veles-android/issues/89), the
[RFC installation contract](../../rfcs/2026-08-21-otp-external-devices.md#native-bridge-installation)
and its OTP-25 acceptance criteria. Preserve the separate acceptance obligations
in #94, #102, #103, #125 and #127. Earlier feasibility results are not installer
acceptance.

The owner requested a design first, then approved the standalone approach for
planning. Detailed tool and transaction choices below are **designer choices**,
not individually specified owner requirements.

## Existing system

- `native-bridge/host` is the production plain-Rust host. `bridgeHostBuild`
  builds only `veles-native-host`, with no simulator feature or Tauri runtime.
- `.github/workflows/build-native-host.yml` builds/tests the real adapters but
  emits unsigned executable artifacts, not user installers.
- `bridgePackage` and `build-native-{windows,macos}.yml` still package the older
  Tauri scaffold. They must not be relabeled as production-host packages.
- The host embeds its origin allowlist at compile time using
  `VELES_ALLOWED_ORIGINS`; its default and the extension's current ID are a
  development placeholder, not an established Web Store item ID.
- Existing feasibility registration scripts and the approved Mac signing fixture
  are observation/development assets, not production installation machinery.
- The connector already separates pre-launch native-port failures from host API
  negotiation failures and has retry/install guidance. Updates must retain that
  distinction and never reset trust as a recovery shortcut.

## Goals, exclusions and failure conditions

Required: exact-origin registration; per-user locations; signed self-contained installers;
repair; staged upgrade and crash recovery; explicit rollback; complete uninstall;
separate development registration; reproducible unsigned inputs; verified signing
correspondence; release evidence; standard non-admin walkthroughs on both OSes.

Excluded: Linux packaging, Tauri runtime, machine-wide installs, privileged helper,
service/daemon/launch agent/scheduled task, automatic updater, cloud/telemetry,
extension or Android trust changes, OTP storage, and native-host protocol redesign.

Unacceptable: defaulting to the placeholder production ID; treating unsigned test
packages as signed releases; modifying an unrelated Chrome registration; killing
unrelated processes; trusting arbitrary signed publishers; deleting the last
verified working payload before activation; exporting the approved local Mac key;
silently treating implementation/CI success as physical or release acceptance.

## Approaches

1. **Standalone platform installers — selected by the owner.** Pinned NSIS on Windows and
   a small signed/notarized macOS installer application distributed in a stapled
   DMG, sharing a Rust transaction engine. Explicit per-user behavior, modest
   runtime surface, no Tauri. Costs: native UI/build glue and transaction tests.
2. **Tauri bundler wrapping the plain host.** Would reuse scaffold tooling but
   retains a larger tool dependency surface and requires a separately reviewed
   bundler justification. Existing D-01 decision does not approve this approach.
3. **Registration scripts / ordinary archive only.** Useful for developers, not
   an adequate signed end-user install/repair/uninstall experience or acceptance
   substitute. Retain only as a separate development workflow.

Approach 1 is selected. A system-wide macOS PKG is deliberately not
selected: a root installer script must not guess which user's home to mutate.

## Components and ownership

Use `native-bridge/installer/` for a standalone Rust installer engine, native
frontends, package metadata, development scripts and contract tests. Keep the
production host crate independent: it never links or runs installer code.

- **Shared transaction engine:** fixed-root path policy, inventory and hash
  checks, staging, activation, recovery, repair, rollback and removal. A narrow
  command interface exposes `inspect`, `install`, `repair`, `rollback`,
  `uninstall`. It consumes only the package's embedded, authenticated inventory,
  not arbitrary user-supplied install paths or remote URLs.
- **Windows:** NSIS `RequestExecutionLevel user` installer/uninstaller delegates
  mutations to the engine. HKCU-only uninstall metadata makes removal discoverable
  in Installed Apps. No per-machine fallback; no administrator relaunch.
- **macOS:** a small native SwiftUI/AppKit installer frontend runs as the logged-in
  user and invokes its embedded signed engine. It exposes repair/update/rollback
  and uninstall when launched again. Install a signed maintenance copy under the
  documented Veles root so uninstall does not require retaining the download.
  No authorization prompt or privileged PKG script.
- **Gradle/CI:** explicit production-host packaging tasks, separate from scaffold
  tasks. Native tools are pinned with verified downloads; the plan settles exact
  versions and dependency checksums before implementation.

Frontends are thin: one transaction implementation owns rollback and filesystem
policy, not parallel NSIS/Swift/Rust algorithms. Both frontends render bounded
status codes as user-facing guidance; logs contain no keys, OTPs or record bytes.

## End-user flow

Functional sketch, not a visual-layout prescription:

```text
Open signed installer → inspect this user's installation
  Missing: Install
  Healthy: Repair | Update (when newer) | Roll back (when available) | Uninstall
  Damaged/conflicting: explain the exact problem; Repair only if ownership is safe
→ Close Veles connector tabs if a host is active → Retry
→ Verify package → Stage → Activate → Verify registration → Done
→ Open/retry Veles connector; Bluetooth consent may require owner action
```

Never open a native-host session as an installer side effect. Chrome owns host
launch and lifetime. Do not promise that installation proves Bluetooth consent,
API negotiation or link functionality; the Chrome launch acceptance case proves
those separately. Errors name a safe action and whether the prior install remains
usable. There is no automatic download, permission reset or trust deletion.

## Release configuration and identity

Use a versioned release descriptor binding source commit, release version,
target architecture/minimum OS, supported host API range, production Web Store
item ID, bundle/host IDs, publisher identities and unsigned inventory hashes.
The descriptor and inputs belong to the release provenance and authenticated
signed installer; a mutable CI variable alone is not authoritative release
configuration. Release authorization attests the descriptor's exact digest.

Production packaging requires exactly one non-placeholder Chrome ID (32 `a`–`p`
characters), writes its one literal `chrome-extension://<id>/` origin to the
manifest template, and compiles that identical origin into the host. Reject
missing/multiple/wildcard/development/unrelated origins and mismatches. The real
Web Store item ID is an external release prerequisite; do not invent one.

Production host name and macOS bundle ID are `me.nagaev.veles.bridge`. Development
uses `me.nagaev.veles.bridge.dev`, a distinct bundle ID/root/manifest, and an
explicit unpacked-extension ID. A dev host is separately built with its dev
allowlist; scripts may not register a production binary as a dev host and imply
that it accepts a different origin. Reject production IDs/roots in dev commands.
Do not broaden the connector's production host selection to use dev implicitly.

## Installed layout

Windows root: `%LOCALAPPDATA%\Veles\NativeBridge`.
macOS root: `~/Library/Application Support/Veles/NativeBridge`.

Under either root:

```text
versions/<release-and-payload-id>/  immutable verified host payload and inventory
maintenance/                      signed maintenance tool / uninstall assets
state/                            bounded transaction journal and receipts
```

Windows uses a stable `me.nagaev.veles.bridge.json` under its root and HKCU
`Software\Google\Chrome\NativeMessagingHosts\me.nagaev.veles.bridge` pointing
to that absolute manifest path. macOS uses
`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/me.nagaev.veles.bridge.json`.
Each manifest names the absolute executable in the selected immutable version;
on macOS it is inside the signed `.app` with the final bundle ID and Bluetooth
usage declaration. Neither platform requires a runtime launcher or `current`
symlink. Development roots use `NativeBridgeDev` and the `.dev` host name.

Installer state is not trust: receipts record owned paths, hashes, active/prior
version and operation state only. There is no Chrome profile/IndexedDB access.
Paths with spaces and non-ASCII user names are supported.

## Transaction and recovery model

1. Acquire an exclusive per-user installer lock. Validate the authenticated
   package, target, origin, inventory and signer policy before modifying active
   registration. Refuse foreign ownership, unsupported future state formats,
   symlink/reparse-point escapes and path traversal. Do not rely on user-writable
   receipts alone as authority to execute or recursively delete paths.
2. Quiesce this installation: ask the user to close connector tabs, then verify
   that no process executing any owned host version remains. Use OS process
   identity/path checks, bounded waits and retry; inaccessible/uncertain process
   state is a blocker. Do not terminate Chrome, guess PIDs, or claim the installer
   can close another process's Native Messaging port. Require all owning sessions
   to close, including other Chrome profiles.
3. Stage on the destination volume; create with user-only permissions. Validate
   every file hash, executable/bundle signature and expected publisher identity.
   Flush staged files and journal intent before an activation mutation. Never
   execute staged code merely to check its claimed version.
4. Commit activation by an atomic replacement of the **one manifest that selects
   the executable**. Both old/new manifests point to complete immutable versions;
   there is no two-file manifest/`current` activation. On Windows an existing
   managed HKCU value stays fixed. First install creates the manifest before
   writing HKCU. Registry publication is a separate journaled step, not falsely
   described as atomic with a filesystem rename.
5. Verify read-back registration bytes, absolute target, inventory and signatures;
   write the committed receipt. Retain the last verified prior version. A failure
   restores the prior manifest/managed HKCU value and records the outcome. Fresh
   install failure removes only the newly owned registration/assets.
6. On restart, reconcile journal, live manifest and verified inventories before
   offering further changes. An interrupted transaction either completes the
   authenticated staged activation or restores the last verified version; an
   ambiguous or foreign state fails closed with recovery guidance. Never infer
   success from a journal marker alone. Include first-install, existing-update
   and post-activation/pre-receipt crash points in tests.

Atomic manifest replacement protects new readers from partial JSON; it does not
claim to close a Chrome launch race across processes. Recheck host activity before
commit and cleanup; retain immutable versions when Chrome races the quiescence
check. If the platform cannot safely remove files while a host remains active,
abort/defer removal visibly rather than claim a complete uninstall.

Maintenance assets are staged/verified before publication too. Keep a recovery
capable prior maintenance tool until transaction completion; replacement/removal
of an executing Windows maintenance binary uses a narrowly bounded owned-file
helper, never an unbounded shell command or a reboot-required machine operation.
Its exit and file cleanup are part of acceptance.

## Operation semantics

- **Repair:** revalidate and restore missing/damaged managed files from the
  authenticated same-version package, then restore exact registration. Conflicting
  registrations are reported, not overwritten. If local recovery content is
  damaged, request a fresh signed download rather than trusting it or going online.
- **Upgrade:** stage the new signed payload and retain the previous verified one.
  Same-version reruns repair; an older installer does not silently downgrade.
- **Rollback:** explicit user operation to a retained, signature-verified prior
  payload. Preserve its origin and API metadata, warn when outside the tested
  extension compatibility window, and do not bypass current publisher/release
  revocation policy as defined below. If no valid retained version exists, give
  repair guidance.
- **Uninstall:** after quiescence, remove only this product's registration,
  manifest, versions, receipts and maintenance assets (plus Windows HKCU uninstall
  metadata). Recheck registration ownership immediately before removal. Missing
  owned files are idempotent; a foreign replacement is left alone and reported.
  Complete only after owned host processes and bounded cleanup helper exit.
  Unrelated files prevent root-directory removal and are preserved/reported.
- **No auto-update:** an installer may report the embedded package's version but
  never polls a server. Update links remain ordinary user-invoked signed-download
  guidance in the extension/docs.

### Offline trust and revocation policy

Designer decision: self-contained means the installer includes its payload and
does not download product content. It is **not** a promise that every OS trust
assessment succeeds without a network connection. The engine does not contact a
Veles service, but normal OS signature/Gatekeeper validation may use the OS's
network-backed certificate/notarization services. Do not suppress those checks,
disable revocation, change system trust, or bypass Gatekeeper to make offline
installation pass. An unavailable required OS assessment blocks activation with
retry guidance; an explicit OS revocation/denial blocks it as well. Preserve the
prior installation without claiming it has been newly security-certified.

The release descriptor's authenticated policy includes allowed publisher
identities, denied release/payload digests and the permitted rollback inventory.
Persist the last committed policy with its authentication proof; repair and
rollback enforce it together with the authenticated incoming package policy and
OS trust results. For those operations, denials accumulate and publisher/rollback
eligibility must satisfy both policies; an older installer cannot remove a known
denial. A future policy relaxation or signer rotation requires an explicitly
authorized forward maintenance release, not a rollback. Missing/unverifiable
policy proof fails closed. These are application policy checks, not a custom
replacement for the OS certificate trust engine.

An offline machine cannot discover a newly revoked Veles release absent an OS
update or a newer authenticated maintenance package. State this limitation in
support/release documentation and evidence; never describe cached policy as
globally fresh. There is no new online freshness service or automatic updater in
scope. Test cached trust success, required trust information unavailable, explicit
OS denial, a known denied payload, and an attempted stale-policy rollback.

## Signing and reproducibility

Produce two independent clean unsigned builds per release target from the exact
source/toolchain/SDK/dependency/descriptor inputs. Compare the host, unsigned
bundle payloads, inventory, manifest **templates** and installer input trees
byte-for-byte with normalized archive ordering/modes/timestamps. Installed
absolute paths are intentionally user-dependent, so compare their deterministic
rendering separately; do not claim installed manifests match across user homes.

Windows: Authenticode-sign the host, engine, cleanup helper and installer/
uninstaller as applicable; timestamp and verify signatures/chains against the
configured publisher policy. macOS: Developer ID-sign nested executables and
bundles inside-out with the specified hardened-runtime/entitlement policy,
notarize the distribution, staple supported distributed containers/apps, and
verify signatures, Gatekeeper assessment, notary outcome and staple validation.
No ad-hoc signature substitutes in a production channel.

The plan must specify executable-format-aware correspondence checks: compare PE
loadable content/metadata with only documented Authenticode mutations excluded;
compare Mach-O slices/content with only documented code-signing/linkedit changes
excluded; compare resource inventories with only explicit signature/ticket
artifacts excluded. Unexpected mutations fail. Signature removal followed by a
blind full-file comparison is not an adequate universal algorithm. Retain both
unsigned and signed digests, correspondence reports and tool identities.

Sign only already-built verified inputs; signing jobs do not rebuild source.
Use protected trusted-release jobs/environments and least-privilege CI credential
delivery, ephemeral keychains/certificate stores and guaranteed cleanup. No secrets
in artifacts/logs, no signing of untrusted PR inputs, and no unsigned fallback
published as a production package. Unsigned PR/fixture artifacts are unmistakably
test-only and cannot register the production host.

Preserve the existing local Apple Development fixture, its key and TCC state.
Distribution identity and versioned bundle paths can affect Bluetooth consent:
test install/repair/update/rollback grant behavior with the actual final identity.
A new prompt is not silently accepted or hidden by a permission reset.

## Build and release integration

Keep production-host and scaffold identities explicit. Rewire native release
producers to consume verified production-host inputs and standalone packaging;
retire scaffold outputs from the release graph, without deleting historical
feasibility material. Retain the existing single-build principle and pinned
native runners/SDKs. Extend supply-chain reports to cover the installer engine,
frontend/tool dependencies and shipped payload, not just host crates.

Separate unsigned validation from credentialed release signing. PR jobs test
contracts and produce test-only packages; protected release jobs bind approved
source/config digests, reproducibility results, SBOM/licenses, signatures,
correspondence and final package hashes/provenance before publication is eligible.
Do not change the current Android publication policy merely to hide a missing
native signing prerequisite. Exact workflow/task names are builder decisions.

The compatibility manifest records the tested extension/host API overlap, not a
promise of indefinite compatibility. Verify extension-only and host-only updates
with overlap; no overlap must fail closed with visible update/install guidance.
Packaging does not replace `hello`/`ready` negotiation with a receipt-based check.

## Verification and acceptance

Automated tests must cover strict production/dev origins and identity separation;
foreign registrations; path escapes and reparse points; partial writes/read-back;
exclusive locking; corrupt signatures/inventories; missing credentials; failed
staging; every journal/activation/registry crash boundary; retained-version
rollback; repeated repair/removal; active-host races and process-inspection denial;
bounded cleanup; and executable/resource correspondence negative cases.

Native integration jobs verify actual HKCU/filesystem locations, signatures and
package structure. A portable fake-filesystem test is not proof of Windows
registry behavior, macOS rename/durability, standard-user access or Gatekeeper.

Record separate Windows/macOS **standard non-admin** clean install, Chrome launch,
repair, upgrade, failed upgrade/rollback, explicit rollback and uninstall cases,
plus fresh-user walkthroughs. Include exact-origin rejection, dev coexistence,
multiple Chrome profiles, API-compatible/incompatible independent updates,
grant retention/recovery, user paths with spaces/non-ASCII, no extra endpoints,
no elevation/system files/autostart, host exit and post-uninstall cleanup.

Each physical verdict binds source/tool/config hashes, signed/unsigned/package
digests, signer/OS/Chrome/account context, controlled actions and timestamps,
observed returns, raw capture checksums and independent adjudication. Preserve
failed/blocked attempts. Privacy-review captures before publication. No binaries,
credentials, signing keys or whole-machine private traces in ordinary source
commits. Publish packages only through the separately authorized release path.

Implementation may be reviewed before physical matrices finish, but #89 stays
open and release/protected-delivery gates remain blocked until its complete
acceptance inventory and owner decision exist. Missing credentials, accounts or
an attended grant are explicit BLOCKED results, never waivers by implication.

## External prerequisites and builder discretion

Before production release, the owner/release operator must supply or approve the
actual Web Store item ID and authenticated descriptor, Windows publisher signing
policy/credentials, Apple Developer ID/team/notarization setup, protected CI
release authorization and non-admin test accounts/attended consent. Do not request
secret values in chat or reuse/export the feasibility signing key.

The implementation plan must record the supported Windows/macOS versions and
architectures from the project's pinned build outputs, exact installer-tool pins,
transaction state schema, bounded cleanup mechanism and executable correspondence
algorithm. Builders may settle module names, UI layout, test fixture names and
bounded timeouts without another design decision, provided the guarantees above
hold. Expanding platform support, changing signing identity policy, adding elevated
installation or weakening acceptance requires a new explicit owner decision.

## Design checklist

- [x] Read the authoritative RFC, issue and existing production/scaffold pipelines.
- [x] Document approach, constraints, lifecycle, security and acceptance boundaries.
- [x] Fresh-context builder check and resolve material planning questions.
  Reviewer asked for offline/revocation semantics; the policy above records the
  designer's resolution and its offline-freshness limitation.
- [x] Owner approval of approach 1 for planning; no implementation authorization.
- [x] Implementation plan: `../plans/2026-11-06-otp25-installers.md`.
  No implementation in this change.
