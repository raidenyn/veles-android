# OTP-25 Signed Per-User Installers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the production plain-Rust Native Messaging host in signed, recoverable per-user Windows/macOS installers with strict origin isolation and auditable release evidence.

**Architecture:** A standalone Rust installer engine owns fixed-root transactions, OS registration, verification and recovery. Thin NSIS/SwiftUI frontends invoke it; separate unsigned-build and protected-signing workflows package only the production host. Physical installer acceptance remains a distinct release gate.

**Tech Stack:** Rust 1.98.0; locked serde/serde_json/sha2; Win32/WinTrust; macOS Security.framework/Foundation; SwiftUI/AppKit; NSIS 3.08; Python standard-library release tooling; existing Gradle and SHA-pinned GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-06-otp25-installers-design.md` (approach 1 approved for planning on 2026-11-06). RFC authority: `docs/rfcs/2026-08-21-otp-external-devices.md`, Native bridge installation and OTP-25.

## Global Constraints

- Production host name and macOS bundle ID are `me.nagaev.veles.bridge`.
- Development uses `me.nagaev.veles.bridge.dev`, a distinct bundle ID/root/manifest, and an explicit unpacked-extension ID.
- Windows root: `%LOCALAPPDATA%\Veles\NativeBridge`.
- macOS root: `~/Library/Application Support/Veles/NativeBridge`.
- Windows registers only HKCU `Software\Google\Chrome\NativeMessagingHosts\me.nagaev.veles.bridge`.
- macOS registration is `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/me.nagaev.veles.bridge.json`.
- Production packaging requires exactly one non-placeholder Chrome ID (32 `a`–`p` characters).
- No Tauri runtime, elevation, machine-wide install, autostart, updater, Linux package, trust/OTP access or local signing-key export.
- Sign only already-built verified inputs; signing jobs do not rebuild source.
- No signing of untrusted PR inputs; no unsigned fallback published as a production package.
- Atomic activation replaces one manifest selecting an immutable version; registry publication is separately journaled.
- Unknown ownership/process/trust state blocks mutation; do not kill Chrome or unrelated hosts.
- Preserve the existing local Apple Development fixture, its key and TCC state.
- Missing credentials, accounts or an attended grant are explicit BLOCKED results, never waivers by implication.
- This plan is not authorization to implement, provision accounts, sign, publish, push, merge or close #89. The owner asked to generate the plan and stop.

## Review Focus

1. Chrome launches between quiescence checks and cleanup: preserve live immutable payloads and do not report complete removal (Tasks 5, 7, 8).
2. Interrupted activation with stale/foreign registration or a forged receipt: recover only authenticated owned state, never overwrite/delete another product (Tasks 3, 6, 7).
3. Same-version repair or older rollback weakens a known denial/signing policy: fail closed even with an otherwise valid signature (Tasks 2, 4, 7).
4. A signed executable changes loadable content or an archive hides a duplicate/escaping path: reject it despite signature validity (Tasks 2, 9).
5. Non-admin, Unicode/space paths and macOS permission identity changes: no elevated fallback, shell interpolation or unobserved grant-retention claim (Tasks 3, 8, 11, 15).

---

## Execution boundaries and prerequisites

Execute in an isolated worktree at implementation time; keep this documentation
branch intact. Use a per-plan ledger and RED→GREEN for every task. Commands below
run from the repository root unless they explicitly change directory. Windows
uses `python` in place of `python3`, `pwsh`, and `gradlew.bat`; native tests must
run on their named OS, not be counted as passing because cfg-gated tests skip.

External inputs required before a **production** build/acceptance: approved Web
Store item ID, Windows publisher/certificate policy, Apple Developer ID/team,
notarization credentials, protected release environment approval, standard
non-admin accounts and attended Bluetooth consent. Retrieve credentials through
the harness secret mechanism/protected CI, never chat or a committed file. Unit
tests use synthetic descriptors and injected trust providers; they cannot enable
production installation. Continue independent implementation tasks if a release
input is absent, but mark dependent release/physical steps BLOCKED and stop short
of claiming the complete task/issue accepted.

### Fixed implementation choices

- Build targets initially match existing native outputs: `x86_64-pc-windows-msvc`
  and `aarch64-apple-darwin`. Acceptance baseline is Windows 10 x64 and macOS 26
  Apple Silicon, Chrome 132+. This does not promise older OSes, Intel Macs,
  Windows ARM64 or universal binaries. Use manifest OS guards and
  `MACOSX_DEPLOYMENT_TARGET=26.0`; record Windows builds' exact SDK/MSVC versions
  and macOS Xcode 26.6 build `17F113`, SDK `macosx26.5`. Existing `windows-2025`
  and `macos-26` runner labels alone are not reproducibility pins: lock and verify
  the complete compiler/SDK tool identity in Task 1.
- NSIS 3.08 archive is the already-reviewed
  `https://github.com/tauri-apps/binary-releases/releases/download/nsis-3/nsis-3.zip`,
  SHA-256 `1bb9fc85ee5b220d3869325dbb9d191dfe6537070f641c30fbb275c97051fd0c`.
  This uses an archive mirror, not Tauri libraries/plugins. Extract NSIS only;
  do not import WiX or `nsis_tauri_utils.dll`. Use Unicode MUI2 and zlib,
  `/NOCD`, an absolute script path and fixed input/output roots.
- Rust direct pins: serde `=1.0.228`, serde_json `=1.0.140`, sha2 `=0.10.9`;
  Windows bindings `=0.62.2` as in the host. Use target-specific system-framework
  FFI for macOS, not a Tauri dependency. Commit the standalone Cargo.lock;
  review transitive packages/build scripts/licenses before use.
- Release tooling is Python 3.12+ standard library; no new executable-parser
  package or download at installer runtime. Swift tools come from the pinned
  Xcode; `codesign`, `security`, `notarytool`, `stapler`, `hdiutil` and `spctl`
  come from that inspected system/toolchain, with their versions/hashes recorded.
- All generated files are under `build/native-bridge/installer/`, including
  `cargo-target/`, `unsigned/`, `signed/`, `tools/`, `reports/` and test sandboxes.
- Runtime limits: descriptor/journal/receipt each ≤256 KiB; inventory ≤4096
  entries and ≤256 MiB total payload; paths ≤1024 UTF-8 bytes, no case-folded
  duplicate normalized relative paths; bounded output ≤64 KiB, command wait
  ≤30 seconds except notarization (CI step timeout 20 minutes). Installer lock
  acquisition is nonblocking; host/cleanup wait is 10 seconds, then visible retry.
  These are installer limits, not host Native Messaging record limits.

## File map

New standalone crate `native-bridge/installer/`: `Cargo.toml`, `Cargo.lock`,
`rust-toolchain.toml`, `src/{lib,model,policy,paths,inventory,platform,journal,
transaction,operations,cli}.rs`, `src/platform/{windows,macos}.rs`,
`src/bin/{veles-bridge-maintenance,veles-bridge-cleanup}.rs` and `tests/*.rs`.
Each task below names its exact test files. `platform.rs` declares the injected
OS boundary; target files implement it; tests use a fault-injectable fake.

Packaging/config: `native-bridge/installer/config/{release.schema.json,
release.example.json,tools.lock.json}`, `windows/installer.nsi`,
`macos/{Package.swift,Sources/VelesBridgeInstaller/App.swift,Info.plist,
HostInfo.plist,Installer.entitlements,Host.entitlements}`,
`scripts/{release_config,package_inputs,correspondence,release_gate,evidence}.py`,
`scripts/{provision-nsis,sign-windows}.ps1`, `scripts/{package-macos,sign-macos}.sh`,
`scripts/{register-dev,remove-dev}.{ps1,sh}` and `scripts/test_*.py`.

Acceptance: `native-bridge/installer/acceptance/{cases,matrix}.json`,
`native-bridge/installer/README.md`, `docs/otp-25-installers.md`.
Integration modifies `build.gradle.kts`, host build-contract tests, supply-chain
and workflow contract tests, clean checks, the existing native producer workflows
and `release-build.yml`. Production credentialed processing lives in a new
`.github/workflows/release-native-installers.yml`, not in PR workflows.

## Shared interfaces and schemas

Tasks extend these types in `src/model.rs`; do not invent differently named
copies in frontends or platform modules:

```rust
enum Channel { Production, Development, Fixture }
enum Target { WindowsX64, MacosArm64 }
enum Operation { Inspect, Install, Repair, Rollback, Uninstall }
enum FailureCode { InvalidInput, OriginRejected, InvalidPackage, TrustDenied,
    TrustUnavailable, PolicyDenied, UnsupportedTarget, Busy, HostActive,
    InspectionDenied, ForeignState, UnsafePath, Io, RecoveryRequired,
    NoRollback, RepairPackageRequired, CleanupPending }
type InstallResult<T> = Result<T, InstallFailure>;
struct InstallFailure { code: FailureCode, prior_usable: bool }
struct Digest([u8; 32]);
struct InventoryEntry { path: String, size: u64, sha256: Digest, executable: bool }
struct ReleasePolicy { publishers: Vec<String>, denied_payloads: Vec<Digest>,
    rollback_payloads: Vec<Digest>, epoch: u64 }
struct ReleaseDescriptor { schema: u32, channel: Channel, version: String,
    source_commit: String, target: Target, minimum_os: String,
    extension_id: String, host_name: String, bundle_id: String,
    api_min: u32, api_max: u32, payload_id: Digest, policy: ReleasePolicy }
struct InstallReport { operation: Operation, state: String,
    prior_usable: bool, active_payload: Option<Digest>, failure: Option<FailureCode> }
```

Use strict serde parsing (unknown fields/duplicates rejected), integer bounds,
lowercase hex digests, and a deterministic compact UTF-8 JSON serialization with
sorted object keys and newline. Schemas are version 1. Package version is three
unsigned numeric components, compared numerically, not lexicographically.

Descriptor/config circularity is forbidden: the approved source release config
contains identity/policy/toolchain inputs, not its own source/output digest. Build
the host from that origin, derive its **unsigned canonical payload** digest, then
compile the maintenance engine with descriptor/policy and that digest embedded.
The complete unsigned input inventory is an output, not embedded in a binary it
hashes. Keep signed-file byte hashes in the externally sealed package inventory;
the engine verifies both actual signed files and their expected canonical content.

The maintenance binary is the persistent **policy carrier**: its platform signature
authenticates the embedded descriptor; on macOS the containing sealed signed app
also authenticates resources. Verify it as data before trusting embedded policy.
Never execute a cached carrier to ask whether it is authentic. Include descriptor
offset/length in a bounded versioned read-only section/resource and verify signed
coverage. No bespoke policy-signing key or online revocation service is introduced.

## Tasks

### Task 1: Reproducible installer inputs and toolchain contract

**Files:** Create `config/tools.lock.json`, `scripts/release_config.py`,
`scripts/provision-nsis.ps1`, `scripts/test_tools.py`,
`native-bridge/installer/rust-toolchain.toml`; modify
`verify/test/workflow-contracts.test.mjs` only to add independent tool-pin tests.

**Interfaces:** Produces `release_config.validate_tools(lock: dict, observed: dict)
-> list[str]`; `provision-nsis.ps1 -Destination <build-root>` verifies/extracts
only the pinned NSIS archive and fails on pre-existing/escaping destinations.
`tools.lock.json` records exact Rust/Xcode/SDK/NSIS identities above and the exact
existing Windows compiler/SDK inventory, not just runner labels.

- [ ] Write `test_tools.py` tests: `test_nsis_pin_matches_reviewed_archive`
  asserts the exact URL/hash above; `test_no_tauri_or_wix_payload` rejects their
  plugin/archive paths; `test_tool_mismatch_and_missing_identity_block` asserts
  nonempty errors for any missing compiler/linker/SDK fingerprint.
- [ ] Run `python3 -m unittest discover -s native-bridge/installer/scripts -p test_tools.py`.
  Expected: FAIL because new validators/lock do not exist.
- [ ] Implement validator/provisioner and lock file. Read existing runner inventory
  artifacts or perform a read-only CI tool inventory; bind the concrete Windows
  compiler/SDK versions and hashes to the lock. Do not guess hashes or install
  tools in this planning session. If unavailable during execution, record that
  reproducible native builds are blocked, rather than insert a wildcard pin.
- [ ] Repeat the test command; expected: PASS. On Windows also run the provisioner
  into `build/native-bridge/installer/tools/nsis`; expected: verified NSIS 3.08,
  hash mismatch/traversal fixtures fail, no WiX/Tauri plugin extracted.
- [ ] Commit: `git add native-bridge/installer/config/tools.lock.json native-bridge/installer/scripts native-bridge/installer/rust-toolchain.toml verify/test/workflow-contracts.test.mjs && git commit -m "build: pin standalone installer toolchain inputs"`.

### Task 2: Strict package identity, inventory and policy carrier

**Files:** Create `Cargo.toml`, `Cargo.lock`, `src/{lib,model,policy,inventory}.rs`,
`tests/package_policy.rs`, `config/{release.schema.json,release.example.json}`.

**Interfaces:** Consumes Task 1 tools. Produces
`parse_descriptor(bytes: &[u8]) -> InstallResult<ReleaseDescriptor>`,
`verify_inventory(root: &Path, entries: &[InventoryEntry]) -> InstallResult<()>`,
`effective_policy(current: &ReleasePolicy, incoming: &ReleasePolicy,
op: Operation) -> InstallResult<ReleasePolicy>` and
`read_carrier(bytes: &[u8]) -> InstallResult<ReleaseDescriptor>`.
Carrier parsing locates bounded data; Task 4 supplies authenticity validation.

- [ ] Add table-driven `package_policy.rs` tests:
  `production_rejects_placeholder_multiple_and_dev_origins` expects OriginRejected
  for the existing placeholder, wildcard, comma list, wrong scheme and dev ID;
  `inventory_rejects_escape_duplicate_case_alias_and_partial_content` rejects
  `../x`, absolute/drive/ADS paths, NUL, `a`/`A`, duplicate archive members and a
  correct-size/wrong-hash file; `rollback_cannot_erase_known_denial` asserts union
  of denials and intersection of publisher/rollback sets; `carrier_bounds_and_strict_json`
  rejects duplicate/unknown keys, >256 KiB and out-of-range offsets.
- [ ] Run `cargo test --locked --manifest-path native-bridge/installer/Cargo.toml --test package_policy`.
  Expected: RED (first establish the locked crate/test harness, with missing
  implementations or failing assertions; dependency download errors are not RED).
- [ ] Implement the shared types/strict parsers and minimal policy/inventory code.
  Pin dependencies as above, channel-select host/bundle IDs, limit counts/sizes,
  and deny incoming older-policy relaxation during repair/rollback. Forward
  policy changes require a higher epoch and authenticated release authorization;
  this does not claim protection against a malicious same-user administrator.
- [ ] Repeat the test command; expected: PASS. Run
  `cargo fmt --manifest-path native-bridge/installer/Cargo.toml -- --check`;
  expected: exit 0.
- [ ] Commit: `git add native-bridge/installer && git commit -m "feat: validate installer identities inventories and policy"`.

### Task 3: Fixed paths, safe IO, locking and registration ownership

**Files:** Create `src/{paths,platform}.rs`, `tests/{safe_io,registration}.rs`.

**Interfaces:** Produces `InstallPaths::for_user(target: Target, channel: Channel,
user_home: &Path, local_app_data: Option<&Path>) -> InstallResult<InstallPaths>`;
`Platform` trait below. Define opaque `ValidatedPath`, `OwnedEntry`, `InstallLock`,
`PackageSource`, `TrustProof` and `Registration` here, and fault-injectable test
`FakePlatform`. Define `HostState::{Quiescent, Active, Unknown}` at this boundary;
Task 5 implements its real process-classification behavior. `TrustProof` contains the OS
verified signer/assessment and authenticated carrier bytes, **not** an unchecked
descriptor; Task 4 constructs `AuthenticatedPackage` after policy/content checks.
`Registration` holds exact manifest bytes and optional Windows HKCU preimage.

```rust
trait Platform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock>;
    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()>;
    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()>;
    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>>;
    fn publish_registration(&self, paths: &InstallPaths, value: &Registration) -> InstallResult<()>;
    fn remove_registration(&self, paths: &InstallPaths, expected: &Registration) -> InstallResult<()>;
    fn inspect_hosts(&self, paths: &InstallPaths) -> InstallResult<HostState>;
    fn verify_trust(&self, source: &PackageSource, expected: &ReleaseDescriptor) -> InstallResult<TrustProof>;
    fn remove_owned(&self, entry: &OwnedEntry) -> InstallResult<()>;
    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()>;
}
```

Mutating methods take validated paths/owned handles, never raw receipt-supplied
paths. Validated handles retain ancestry/file identity and must be revalidated
by each OS mutation; type construction alone does not eliminate TOCTOU.

- [ ] Write `safe_io.rs`: `partial_write_retries_and_readback_matches` injects
  short writes and asserts full binary bytes/newline/hash; `ancestor_swap_and_symlink_block`
  rejects a parent swapped for a symlink/reparse point; `unicode_paths_and_busy_lock`
  uses `Test User Å/用户` and expects exact absolute paths and Busy on second lock.
  Write `registration.rs`: `foreign_registration_is_not_claimed_from_receipt`
  asserts zero mutations with a forged owned receipt and foreign target.
- [ ] Run `cargo test --locked --manifest-path native-bridge/installer/Cargo.toml --test safe_io --test registration`.
  Expected: RED assertions or missing methods, not a skipped suite.
- [ ] Implement fixed layout, ownership records and the injected OS interface.
  Write-all/flush/read-back; use handle-relative no-follow traversal, not merely
  string-prefix canonicalization. Require absolute UTF-8-safe rendered JSON;
  Windows ownership checks use OS path semantics, not case-sensitive prefixes.
  Production obtains user roots from OS user/known-folder APIs, never a CLI root
  override or an unvalidated environment variable; injected roots are test-only.
- [ ] Repeat the command; expected: PASS, foreign state remains byte-for-byte intact.
- [ ] Commit: `git add native-bridge/installer && git commit -m "feat: add safe per-user installer IO and ownership boundary"`.

### Task 4: Package authentication and offline trust decisions

**Files:** Create `src/platform/{windows,macos}.rs`, `tests/trust.rs`;
extend `src/platform.rs` and `src/policy.rs`.

**Interfaces:** Implements `Platform::verify_trust` and produces
`authenticate_package(platform: &impl Platform, package: &Path,
expected: &ReleaseDescriptor) -> InstallResult<AuthenticatedPackage>`.
`AuthenticatedPackage` owns a verified descriptor, inventory, policy carrier
proof and immutable source handles; no public unchecked constructor.

- [ ] Write `trust.rs` tests: `valid_signature_wrong_publisher_denied`,
  `offline_required_assessment_unavailable_preserves_prior`,
  `explicit_revocation_and_unsigned_carrier_rejected`,
  `stale_signed_policy_cannot_relax_repair`. Assert TrustDenied/TrustUnavailable/
  PolicyDenied and zero registration writes, not a generic success with warning.
- [ ] Run `cargo test --locked --manifest-path native-bridge/installer/Cargo.toml --test trust`.
  Expected: RED.
- [ ] Implement WinVerifyTrust + authenticated signer-chain policy (no revocation
  disabling); macOS Security.framework code/resource verification and native
  execution assessment with Team ID/designated-requirement policy. Verify
  Authenticode coverage of the descriptor resource on Windows and app resource
  seal on Mac. Missing/invalid cached carrier fails closed. Do not treat exit 0
  from `codesign` alone as sufficient publisher/notarization evidence.
- [ ] Repeat tests; expected: PASS. On native runners verify signed OS test
  samples and unsigned/altered negatives through the actual adapters; expected:
  expected classifications or explicitly BLOCKED credential-dependent samples,
  never portable tests represented as native signing proof.
- [ ] Commit: `git add native-bridge/installer && git commit -m "feat: enforce platform publisher and offline trust policy"`.

### Task 5: Host process inspection and bounded quiescence

**Files:** Extend `src/platform/{windows,macos}.rs`, `src/platform.rs`;
create `tests/quiescence.rs`.

**Interfaces:** Consumes Task 3's `HostState` and implements
`Platform::inspect_hosts(paths: &InstallPaths) -> InstallResult<HostState>`;
`require_quiescent(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<()>`.

- [ ] Write `quiescence.rs`: `two_profiles_and_prior_version_block`,
  `inaccessible_candidate_is_unknown_not_quiescent`,
  `pid_reuse_not_bound_to_name`, `late_launch_preserves_live_payload`.
  Active/unknown states must return HostActive/InspectionDenied, wait ≤10 seconds,
  never call a termination method and retain every possibly live version.
- [ ] Run `cargo test --locked --manifest-path native-bridge/installer/Cargo.toml --test quiescence`.
  Expected: RED.
- [ ] Implement process image/file-identity and user ownership inspection using
  native APIs. Unknown *potential owned* candidates block; do not require reading
  all unrelated protected system processes. Recheck identity against PID reuse.
  Do not add a service, endpoint, Chrome debugger or port-control mechanism.
- [ ] Repeat tests; expected: PASS. Native integration starts a fixture child at
  an owned test path and one unrelated child; only the owned child blocks.
- [ ] Commit: `git add native-bridge/installer && git commit -m "feat: block installer mutations while owned hosts remain active"`.

### Task 6: Journaled staging, activation and crash recovery

**Files:** Create `src/{journal,transaction}.rs`, `tests/transaction.rs`.

**Interfaces:** Produces `install(platform: &impl Platform, paths: &InstallPaths,
package: AuthenticatedPackage) -> InstallResult<InstallReport>` and
`recover(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<InstallReport>`.
Journal v1: transaction ID, operation, phase, old/new payload digests, old/new
manifest bytes/hashes, owned registration preimage, policy-carrier references,
created-path inventory. Phases: Prepared, Staged, ManifestActivated,
RegistrationPublished, ReceiptCommitted, CleanupPending. Reject unknown phases.

- [ ] Write `transaction.rs`: `crash_at_every_durable_boundary` injects one
  failure before/after every write/flush/rename/registry/receipt step for fresh
  install and upgrade; recovery leaves old/new complete registration, not partial
  JSON or an absent previous version. `journal_claim_cannot_override_foreign_live_manifest`
  expects ForeignState and no writes. `registry_publish_failure_restores_preimage`
  asserts a first-install failure removes only newly owned assets.
- [ ] Run `cargo test --locked --manifest-path native-bridge/installer/Cargo.toml --test transaction`.
  Expected: RED.
- [ ] Implement the explicit phase machine. Authenticate staged and retained
  inputs, stage on destination volume, recheck quiescence, atomically replace
  manifest, then publish fixed Windows HKCU registration if missing. Read back
  exact bytes and flush before committing receipt; recover by inspecting verified
  live state, not phase alone. Interrupted maintenance publication must retain
  a verified recovery-capable old carrier; do not overwrite it before commit.
- [ ] Repeat tests; expected: PASS including all injected crash points.
- [ ] Commit: `git add native-bridge/installer && git commit -m "feat: activate installer payloads with journaled recovery"`.

### Task 7: Repair, upgrade, rollback and uninstall semantics

**Files:** Create `src/operations.rs`, `tests/operations.rs`; extend transaction
and policy modules as needed.

**Interfaces:** Produces `run_operation(platform: &impl Platform,
paths: &InstallPaths, operation: Operation, package: Option<AuthenticatedPackage>)
-> InstallResult<InstallReport>`. Inspect and uninstall need no new package;
repair needs an authenticated same-version source; rollback needs a retained
verified carrier/payload plus current policy. Inspect returns only safe metadata.

- [ ] Write `operations.rs`: `same_version_repairs_missing_files_not_trust`,
  `older_install_does_not_silent_downgrade`, `rollback_rejects_known_denial`,
  `missing_repair_source_requests_signed_download`,
  `uninstall_twice_preserves_foreign_files_and_registration`,
  `chrome_relaunch_during_remove_reports_cleanup_pending`. Assert receipts never
  authorize arbitrary paths and no operation touches Chrome profile/trust paths.
- [ ] Run `cargo test --locked --manifest-path native-bridge/installer/Cargo.toml --test operations`.
  Expected: RED.
- [ ] Implement operations using Tasks 2–6, retain one verified prior host and
  its authenticated policy carrier, intersect rollback policy and preserve denied
  digests. Remove registration first only after ownership/quiescence validation;
  journal partial uninstall, retry idempotently, and preserve foreign leftovers.
  Never report success while an owned host/helper remains or deletion is deferred.
- [ ] Repeat tests; expected: PASS. Run the whole installer crate test suite;
  expected: exit 0 with no portable regression.
- [ ] Commit: `git add native-bridge/installer && git commit -m "feat: implement installer maintenance and removal operations"`.

### Task 8: Real native filesystem/registry adapters and cleanup helper

**Files:** Complete `src/platform/{windows,macos}.rs`; create
`src/bin/veles-bridge-cleanup.rs`, `tests/{native_windows,native_macos,cleanup}.rs`.

**Interfaces:** Implements remaining Task 3 `Platform` methods. Cleanup request is
a bounded single-use capability over an inherited pipe containing exact owned
file identities and parent process handle, never a recursive delete path or a
command line. Helper verifies parent exit and own signature/path before deletion;
returns an authenticated completion status over the pipe. Keep helper outside
the removable root in a private temporary directory and delete on exit using
native OS deletion semantics; assert actual removal or CleanupPending.

- [ ] Write native tests `windows_hkcu_manifest_swap_and_partial_registry_failure`,
  `macos_atomic_replace_parent_flush_and_no_symlink_follow`,
  `non_admin_unicode_paths`, `cleanup_rejects_forged_pipe_and_pid_reuse`,
  `cleanup_timeout_never_reports_uninstall_complete`. Use test-only redirected
  private roots/HKCU test subkeys through injected adapters, unavailable in the
  production CLI; never mutate a person's production registration in tests.
- [ ] Run `cargo test --locked --manifest-path native-bridge/installer/Cargo.toml --test native_windows --test native_macos --test cleanup` on each native OS.
  Expected: RED in that OS's real tests; other-OS skips are explicitly listed.
- [ ] Implement native lock/security/rename/flush/registry APIs. Use Windows
  Unicode APIs and correct registry view with an absolute manifest path; prove
  Chrome-visible HKCU lookup, not a guessed WOW64 path. Use macOS no-follow file
  descriptors, destination-volume rename and appropriate durability primitives.
  Reject test-root options in production binaries. Bound cleanup to 10 seconds,
  no process kill, shell, startup task or reboot deletion fallback.
- [ ] Repeat native tests; expected: PASS on both native runners. A helper that
  cannot remove itself safely is an unresolved blocker, not a claimed clean exit.
- [ ] Commit: `git add native-bridge/installer && git commit -m "feat: add native per-user registration and bounded cleanup"`.

### Task 9: Deterministic inputs and signed-content correspondence

**Files:** Create `scripts/{package_inputs,correspondence}.py`,
`scripts/{test_package_inputs,test_correspondence}.py`.

**Interfaces:** `package_inputs.build_inventory(root: Path) -> dict`;
`correspondence.compare(unsigned: Path, signed: Path, kind: str) -> dict`
returns `{schema:1, verdict:"PASS"|"FAIL", differences:[...], input_hashes:{...}}`.
CLI: `python3 .../correspondence.py --unsigned U --signed S --kind pe|macho|tree --report R`;
exit 0 only for valid allowed mutations, 1 mismatch, 2 invalid input/environment.

- [ ] Write tests `pe_only_certificate_table_checksum_and_signature_overlay_change`,
  `pe_changed_code_or_extra_overlay_rejected`,
  `macho_only_code_signature_command_blob_and_validated_linkedit_extents_change`,
  `macho_changed_text_uuid_or_extra_slice_rejected`,
  `tree_rejects_unexpected_resource_and_duplicate_archive_member`,
  `two_clean_input_trees_equal_despite_checkout_path`. Include real format fixture
  bytes built on native runners and retained sanitized fixture provenance, not
  just mocked parser fields.
- [ ] Run `python3 -m unittest discover -s native-bridge/installer/scripts -p 'test_*inputs.py'` and
  `python3 -m unittest discover -s native-bridge/installer/scripts -p test_correspondence.py`.
  Expected: RED for missing comparators.
- [ ] Implement bounded strict PE/Mach-O readers. PE: compare headers/sections/
  resources/overlay, excluding only checksum, security-directory fields, the
  validated terminal WIN_CERTIFICATE region and signature alignment padding.
  Reject overlapping/out-of-range sections and unauthenticated trailing data.
  Mach-O: compare architecture/slice set, every non-signature load command and
  section; account only for LC_CODE_SIGNATURE and exact validated signature-
  related `__LINKEDIT` extent/size changes; keep UUID, entitlements semantics,
  executable code and other linkedit data invariant. Reject unknown transforms.
  Tree: allow only verified `_CodeSignature/CodeResources` and documented stapled
  ticket additions, still verify their signature/seal separately. Do not ignore
  broad directories/extensions or compare only loaded code. DMG/NSIS container
  variation is verified by signed container plus extracted payload inventory.
- [ ] Repeat both commands; expected: PASS. Run comparators on native unsigned/
  signed fixtures; expected: exact allowed-difference reports. If actual tool
  mutations differ, investigate before changing the allowlist and ledger it.
- [ ] Commit: `git add native-bridge/installer/scripts && git commit -m "build: verify reproducible installer inputs and signing correspondence"`.

### Task 10: Engine CLI and Windows NSIS frontend

**Files:** Create `src/cli.rs`, `src/bin/veles-bridge-maintenance.rs`,
`windows/installer.nsi`, `tests/cli.rs`, `scripts/test_windows_package.py`.

**Interfaces:** CLI accepts only `inspect|install|repair|rollback|uninstall` and
an internal authenticated package channel, emits one ≤64 KiB JSON InstallReport
on stdout, safe status-only diagnostics on stderr. Exit: 0 complete, 1 operation
failure, 2 invalid invocation. NSIS consumes exit/report and never mutates Chrome
registration itself. No caller-selected root, origin, arbitrary command or URL.

- [ ] Write `cli.rs` tests `reject_root_origin_shell_and_unknown_flags`,
  `report_no_payload_or_environment_leak`, `pending_cleanup_is_nonzero`.
  Write `test_windows_package.py` assertions for RequestExecutionLevel user,
  quoted Unicode engine invocation, embedded descriptor/inventory, HKCU-only
  uninstall metadata, repair/update/rollback/uninstall and no auto-launch host.
- [ ] Run `cargo test --locked --manifest-path native-bridge/installer/Cargo.toml --test cli` and
  `python3 -m unittest discover -s native-bridge/installer/scripts -p test_windows_package.py`.
  Expected: RED.
- [ ] Implement the CLI and thin MUI2 flow. NSIS extracts only authenticated
  product assets to a private staging location and calls the engine; include a
  signed maintenance/uninstall entry under the fixed root. Generate/sign the
  NSIS uninstaller during packaging (not at end-user runtime) and embed that
  verified binary; the NSIS compiler's normal extraction/preparation step belongs
  to unsigned packaging, not the signing job. Keep Windows cleanup in Task 8.
- [ ] Repeat commands; expected: PASS. Compile with pinned `makensis` and inspect
  extracted unsigned inputs; expected: test-only artifact cannot register production,
  all requested operations map to the engine, no WebView/Tauri dependency.
- [ ] Commit: `git add native-bridge/installer && git commit -m "feat: add per-user Windows installer and maintenance frontend"`.

### Task 11: Native macOS installer frontend and bundle inputs

**Files:** Create `macos/Package.swift`, `macos/Sources/VelesBridgeInstaller/App.swift`,
`macos/{Info.plist,HostInfo.plist,Installer.entitlements,Host.entitlements}`,
`macos/Tests/VelesBridgeInstallerTests/FrontendTests.swift`,
`scripts/package-macos.sh`, `scripts/test_macos_package.py`.

**Interfaces:** Swift `MaintenanceClient.run(_ operation: String) async throws
-> MaintenanceReport` decodes Task 10's strict report; `MaintenanceReport` mirrors
InstallReport fields, not arbitrary host data. `package-macos.sh --inputs I
--output O` creates an unsigned deterministic `.app` input tree and DMG layout.
Production host bundle ID remains exact; installer/maintenance UI bundle ID is
`me.nagaev.veles.bridge.installer`, distinct from the Bluetooth host.

- [ ] Write Swift tests `testUnicodePathsUseProcessArgumentsNotShell`,
  `testActiveHostShowsCloseTabsAndRetry`, `testTrustUnavailableNeverShowsDone`,
  `testMaintenanceDoesNotLaunchHost`; Python tests assert host bundle ID,
  NSBluetoothAlwaysUsageDescription, no launch agents/system paths, and only
  required hardened-runtime entitlements (no blanket disable-library-validation).
- [ ] Run `swift test --package-path native-bridge/installer/macos` on macOS and
  `python3 -m unittest discover -s native-bridge/installer/scripts -p test_macos_package.py`.
  Expected: RED.
- [ ] Implement SwiftUI/AppKit operation/status UI and maintenance-copy handling;
  invoke engine by absolute signed bundle path with structured argv, never shell
  strings. Build arm64 with pinned SDK/deployment target; put Bluetooth usage
  text on host, not an installer pretending to own BLE. Do not reset TCC or reuse
  the approved fixture. Package self-contained inputs with deterministic modes.
- [ ] Repeat tests; expected: PASS. Inspect unsigned bundle/DMG inputs; expected:
  no privileged helper, App Translocation never becomes the installed host path,
  and production signing remains blocked until Task 13 credentials exist.
- [ ] Commit: `git add native-bridge/installer && git commit -m "feat: add native per-user macOS installer frontend"`.

### Task 12: Separate development registration and connector compatibility

**Files:** Create `scripts/{register-dev,remove-dev}.{ps1,sh}`,
`scripts/test_development.py`; modify `web-extension/src/manifest.ts`,
`web-extension/test/manifest-guard.test.ts`,
`web-extension/src/connector/view-model.ts`,
`web-extension/test/connector/{view-model,integration}.test.ts` only where production release
identity/guidance needs integration; extend `tests/package_policy.rs`.

**Interfaces:** Dev scripts require `-ExtensionId/-HostBinary` or
`--extension-id/--host-binary`, select `.dev`/`NativeBridgeDev` internally and
validate a separately compiled dev host's authenticated build descriptor.
Production extension ID is injected from the same approved release config used
by host packaging; default remains explicitly development-only outside release.
Never change the production connector to silently connect to `.dev`.
The dev workflow builds a separate maintenance binary with its explicit dev ID
compiled in. Locally built/ad-hoc dev payloads may use a development verification
policy only in this dev-compiled binary; production binaries cannot select it at
runtime. This is developer registration, not distribution signature acceptance.

- [ ] Write `test_development.py`: `test_prod_roots_names_and_origins_rejected`,
  `test_remove_dev_leaves_production_bytes_unchanged`,
  `test_host_allowlist_mismatch_rejected`. Add extension tests
  `releaseIdentityRejectsPlaceholder` and `installGuidancePreservesTrustAndRetry`;
  compatibility test pairs API overlap 1↔1 (ready) and 1↔2-only
  (incompatible_api, no BLE enabled), without changing protocol semantics.
- [ ] Run `python3 -m unittest discover -s native-bridge/installer/scripts -p test_development.py` and
  `npm --prefix web-extension test`.
  Expected: new assertions RED; pre-existing tests are not changed to hide a mismatch.
- [ ] Implement wrappers through the shared engine with no root overrides; dev
  removal uses the same ownership safeguards. Make extension production builds
  consume validated release identity; retain separate explicit development
  configuration. Keep existing missing-host vs incompatible-API guidance and
  user-invoked installer/retry links, no trust reset/automatic replay.
- [ ] Repeat commands and `./gradlew bridgeHostTest`; expected: PASS. Native dev
  walkthrough confirms production registration bytes remain unchanged.
- [ ] Commit: `git add native-bridge/installer web-extension && git commit -m "feat: isolate development registration and release identity"`.

### Task 13: Protected signing, notarization and authenticated release gate

**Files:** Create `scripts/{sign-windows.ps1,sign-macos.sh,release_gate.py,
test_release_gate.py}`, `.github/workflows/release-native-installers.yml`.

**Interfaces:** Signing scripts accept verified unsigned artifact paths plus
approved descriptor digest and **credential references** from the environment;
emit signed tree, SHA256SUMS, signature/notary/staple and Task 9 correspondence
reports. `release_gate.validate(bundle: Path, expected_commit: str,
expected_descriptor: str) -> list[str]` accepts only complete independently
verified A/B equality, artifact provenance and platform signing reports.

- [ ] Write `test_release_gate.py`: `test_missing_credential_has_no_unsigned_fallback`,
  `test_pr_and_unapproved_descriptor_cannot_sign`,
  `test_mismatched_commit_target_policy_or_payload_fails`,
  `test_unnotarized_unstapled_or_correspondence_failure_blocks`,
  `test_cleanup_runs_on_each_signing_failure`. Use fake subprocess runners and
  verify commands/exit handling; do not assert physical signature validity from mocks.
- [ ] Run `python3 -m unittest discover -s native-bridge/installer/scripts -p test_release_gate.py`.
  Expected: RED.
- [ ] Implement protected workflow with trusted immutable source ref and approved
  descriptor digest, protected environment `native-release`, least privilege,
  no pull_request signing trigger and no secret expression interpolation into
  shell scripts. Validate provenance and payload hashes before private-key use.
  Require A/B unsigned equality first; sign the chosen already-built A executables
  without rebuilding them. Use distinct jobs: verify unsigned inputs → sign nested
  components → assemble containers **without credentials** → sign/assess outer
  containers. Windows signs/timestamps host, engine, helper and prebuilt uninstaller;
  the assembly job runs pinned makensis on the unchanged verified NSIS recipe to
  embed those signed files (container assembly, not a Rust/frontend rebuild), then
  the outer-signing job signs the resulting setup. Compare that assembled setup
  immediately before/after its Authenticode signature as PE, and verify extracted
  nested hashes against the signed-input inventory. Do not compare compressed
  signed-payload container bytes to the earlier unsigned-payload container.
  macOS signs nested code/apps inside-out, assembles DMG without credentials,
  then signs/submits/waits/staples supported apps/DMG and assesses/validates.
  Report each pre/post-sign boundary independently and check immutable recipe/
  input digests across job transfers; no signing job compiles source.
- [ ] Run tests; expected: PASS. With authorized CI credentials run a private
  candidate signing job and verify actual signatures/notary tickets/correspondence;
  absent credentials: explicitly BLOCKED, no artifact eligible for production.
  No release publication or issue closure in this task; upload private candidate
  evidence through the authorized artifact path only.
- [ ] Commit: `git add native-bridge/installer/scripts .github/workflows/release-native-installers.yml && git commit -m "build: gate native installer signing and notarization"`.

### Task 14: Gradle, native producers, reproducibility and supply-chain integration

**Files:** Modify `build.gradle.kts`, `.github/workflows/build-native-{windows,macos,host}.yml`,
`.github/workflows/{ci,release-build}.yml`, `verify/{generate-sboms.sh,
verify-sboms.mjs,check-cargo-build-scripts.mjs,check-remote-code.mjs}`,
`rust/scripts/assert-clean.sh`,
`verify/test/workflow-contracts.test.mjs`,
`native-bridge/host/scripts/test_build_contract.py`;
create `native-bridge/installer/scripts/test_build_contract.py`.

**Interfaces:** Produce Gradle tasks `bridgeInstallerFormat`,
`bridgeInstallerLint`, `bridgeInstallerTest`, `bridgeInstallerUnsigned`,
`bridgeInstallerPackageFixture`, `bridgeInstallerSupplyChain`. Unsigned production
inputs require approved config; fixture packaging hard-selects Fixture channel.
Use separate explicit A/B target dirs and remap source paths/deterministic link
settings; no stale cache reuse is evidence of independent builds. Unsigned A/B
payloads feed Task 13 without recompilation; reports retain both identities.

- [ ] Add Python/Node contract tests asserting no Tauri scaffold production path,
  only explicit production host/maintenance bins, no simulator/all-features,
  locked cargo, all output under build/, exact origin as a tracked build input,
  signed candidates never sourced from PR artifact substitutions, artifact hashes
  and modes preserved, native producer tasks called once per selected input tree,
  old Android-only publication conditions unchanged. Add negative mutation tests
  for each of these invariants, not just snapshots of changed workflow text.
- [ ] Run `python3 -m unittest discover -s native-bridge/installer/scripts -p test_build_contract.py` and
  `node --test verify/test/workflow-contracts.test.mjs`.
  Expected: new contracts RED against scaffold producers.
- [ ] Implement task graph/CI changes, update old workflow contract assertions
  deliberately to the new graph and keep their anti-secret/immutable-ref checks.
  Ordinary producers emit fixture/unsigned artifacts, never mislabeled signed
  installers; production signing stays protected/separate. Include installer Rust,
  Swift, NSIS and helpers in inventory/licenses/SBOM/build-script policy. Extend
  clean checks for all new target/output roots. Do not change `release.yml`'s
  Android publishing policy or auto-attach native candidates to public releases.
- [ ] Repeat contract tests; run `./gradlew bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest bridgeHostTest` and
  `python3 -m unittest discover -s native-bridge/installer/scripts -p 'test_*.py'`.
  Expected: PASS. On each native target perform two clean independent unsigned
  builds and Task 9 inventory comparison; expected: byte-identical host/engine/
  frontend/input trees, or explicit release blocker requiring root-cause work.
  Run `./gradlew bridgeInstallerSupplyChain`; expected: all shipped components
  covered and policy exit 0, not only an SBOM file exists.
- [ ] Commit: `git add build.gradle.kts .github/workflows verify native-bridge/host/scripts/test_build_contract.py native-bridge/installer && git commit -m "build: replace scaffold release packaging with production installer graph"`.

### Task 15: Installer acceptance matrix, evidence validator and non-admin walkthroughs

**Files:** Create `acceptance/{cases,matrix}.json`, `scripts/{evidence,
test_evidence}.py`, `README.md`, `docs/otp-25-installers.md`.

**Interfaces:** `evidence.validate(run: Path, cases: Path,
anchor: Path) -> list[str]` checks exact externally supplied source/package/
signer/config bindings, raw capture checksums, actions and separate adjudication.
CLI exits 0 structurally valid, 1 invalid evidence, 2 missing environment/input.
Cases have stable `<windows|macos>.<case>` IDs and PASS/FAIL/BLOCKED/NOT_RUN;
initial matrix has no physical PASS. Validator success is necessary, not acceptance.

- [ ] Write `test_evidence.py`: `test_admin_account_cannot_prove_standard_user`,
  `test_wrong_signed_candidate_or_self_attested_digest_rejected`,
  `test_missing_grant_observation_is_not_retention_pass`,
  `test_prior_failures_are_append_only`, `test_matrix_has_all_required_cases`.
  Assert each OS has clean-install, launch, repair, upgrade, failed-upgrade,
  rollback, uninstall, fresh-user, origin-negative, dev-coexistence, multi-profile,
  compatibility, permission-recovery, Unicode-path, hygiene and offline-trust cases.
- [ ] Run `python3 -m unittest discover -s native-bridge/installer/scripts -p test_evidence.py`.
  Expected: RED.
- [ ] Implement cases, validator and exact user/developer instructions (including
  maintenance app location, close-connector retry, no trust reset, on-demand
  host, offline freshness limitation, signed download/repair and dev separation).
  Record output binary/signing/source provenance and clocks as required by spec.
  Keep publication of raw captures subject to privacy review and independent
  adjudication. Link physical obligations to #89/#125/#127 without duplicating
  BLE/protected-delivery gate claims.
- [ ] Repeat tests; expected: PASS. With authorized devices/accounts/candidate,
  run each native walkthrough under an actual standard non-admin user and fresh
  user. Record before/after registry/filesystem/process/window/endpoint state,
  Chrome exact-origin launch/rejection, API 1↔1 and no-overlap failures, retained
  trust and attended permission outcomes. If unavailable, leave affected cases
  BLOCKED/NOT_RUN and #89 open; never manufacture fixtures as physical acceptance.
- [ ] Commit only privacy-reviewed source/docs/evidence summaries:
  `git add native-bridge/installer/acceptance native-bridge/installer/scripts native-bridge/installer/README.md docs/otp-25-installers.md && git commit -m "test: define and record gated per-user installer acceptance"`.

## Final verification and handoff (execution only)

- Run complete installer Rust/Swift/Python/native contract suites, host suites,
  extension tests and existing supply-chain/workflow checks for affected consumers.
  Preserve full outputs in the plan ledger; do not report skipped native tests as
  native PASS. Inspect `git diff --check` and the complete branch diff.
- Obtain one fresh whole-branch implementation/security review according to the
  chosen execution skill. Review all five Review Focus cases and any ledgered
  rulings; fixes need failing regression tests followed by green relevant suites.
- Produce a requirement-to-evidence ledger. Keep unresolved production identity,
  credential, account, physical, reproducibility or publisher-policy obligations
  visible; implementation review and CI cannot close #89 or gates B/C/D.
- Report exact commits, tests, blocked cases and deferred findings. Owner separately
  authorizes any push, candidate publication, identity change or acceptance/closure.

## Plan self-review record

- Spec coverage: identities/layout/policy (Tasks 1–4, 12); quiescence, transactions,
  maintenance cleanup (5–8); correspondence/reproducibility (9, 14); native
  frontends (10–11); signing/release isolation (13–14); acceptance/docs (15).
- Interfaces: all shared Rust types live in Task 2/3; all mutations use Platform;
  frontends consume Task 10 reports; Task 13 consumes Task 9 reports and Task 14
  unsigned artifacts. Task 13 can implement/test its gate with fixtures before
  Task 14 wires actual artifacts; it does not count a production run before then.
- Review Focus: every named failure mode has a named owning regression test.
- No product code, dependencies, accounts, signatures, release artifacts or physical
  acceptance is created by writing this plan. Stop after saving/reviewing it.

### Task 16 (final stage): replace the development extension ID with the real one

**Trigger:** the project reaches its final stage and the Chrome Web Store item exists.
**Why deferred:** the owner decision of 2026-11-08 completes the project with the
development extension ID, so packaging and the signing gates are not blocked on an
external Store listing.

- [ ] Edit `native-bridge/installer/config/extension-ids.json` only: set `production`
      to the real 32-character Store item ID. Release configs name the alias, and the
      descriptor carries the resolved raw id, so no other installer config changes.
- [ ] Update `web-extension/src/manifest.ts` (development/production identity) and the
      extension manifest key so the shipped extension uses the same id.
- [ ] Revisit `scripts/development.py`'s production-id refusal if the new production
      id was previously accepted as a development id.
- [ ] Re-run `assemble_unsigned.py check-config`, the release gate and the acceptance
      rows with the real id. Optionally replace the self-signed signing identity with
      the CA certificate at the same time (clear `VELES_WIN_TRUST_SELF_SIGNED`).
