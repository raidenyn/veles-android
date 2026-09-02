# OTP-01 sub-project 1d: Verification and supply chain design

Status: Approved; implementation in progress
Issue: raidenyn/veles-android#80
Parent design: `docs/superpowers/specs/2026-08-26-otp-01-reproducible-toolchains-design.md`
Predecessors: 1a PR #107, 1b PR #109, 1c PR #110

## Purpose

Close OTP-01 by proving that the Android, web-extension, shared Rust, and
unsigned native-bridge artifacts are reproducible in their documented build
environments and by adding auditable SBOM, license, checksum, artifact-content,
and no-remote-code enforcement.

Sub-project 1d verifies the producer contracts delivered by 1a, 1b, and 1c. It
does not replace npm, Gradle, Cargo, Vite, wasm-pack, or Tauri as artifact
producers. It adds small component verifiers, reusable component build
workflows, and a tiny local aggregate entry point around those existing
producers.

## Scope and approved amendments

The parent OTP-01 design remains authoritative except for these user-approved
1d amendments:

1. The versioned Windows reference label is `windows-2025`, replacing
   `windows-2022`.
2. The versioned macOS reference label is `macos-26`, replacing `macos-15`.
3. The macOS reference selects Xcode 26.6 at
   `/Applications/Xcode_26.6.app`, asserts build `17F113`, and asserts SDK
   `macosx26.5`.
4. Exact reference and component-CI Node is 26.8.1 with bundled npm 11.19.0.
   The developer-facing package contract remains `engines.node = ">=22.0.0"`;
   only reproducibility and supply-chain environments require the exact pin.
5. CI does not invoke `verify/verify-all.sh`. CI builds and verifies each
   component in its own `build-<component>.yml` reusable workflow.

The runner labels are versioned labels, not immutable image identifiers. Every
native run still records and compares `ImageOS`, `ImageVersion`, and
`RUNNER_ARCH`. The project makes no claim that GitHub-hosted runner image SHA
pinning is available.

## Existing contracts preserved

- Lockfiles are the dependency reproducibility contract. CI uses `npm ci` and
  Cargo `--locked`; it never repairs drift.
- The APK remains the only Gradle subproject.
- Web-extension build and quality entry points remain npm-native.
- Rust and native-bridge entry points remain root Gradle tasks.
- Clean APK assembly continues to regenerate JNI automatically.
- No Gradle task installs Node, Rust, rustup, or an NDK.
- No `npx`, floating executable fallback, or unpinned executable download is
  allowed.
- Generated product outputs remain under `build/`, except the existing
  `web-extension/dist/` and `web-extension/rust-wasm/pkg/` exceptions.
- Root `clean` removes every declared product, verification, SBOM, checksum,
  and tool output under `build/` plus both named source-tree exceptions.
- Tauri signing and notarization credentials remain absent.
- The skeleton identity tuple remains exactly: npm package
  `@veles/native-bridge`, Tauri product name `Veles Native Bridge`, Tauri
  identifier `app.veles.native-bridge`, native host name
  `app.veles.native_bridge`, binary `veles-native-bridge` (plus `.exe` on
  Windows), macOS bundle `Veles Native Bridge.app`, extension ID
  `abcdefghijklmnopabcdefghijklmnop`, and allowed origin
  `chrome-extension://abcdefghijklmnopabcdefghijklmnop/`. Guard tests require
  every value independently. Production identities are later installer/release
  scope.

## Architecture

### Component ownership

Artifact producers emit canonical per-tree checksum manifests. Component
verifiers validate one producer and one environment boundary. Reusable
component workflows build an artifact and verify it in the same workflow.
Ordinary component outputs are uploaded only after verification. Native build
slots may upload explicitly named, short-lived unverified transport archives
because their independent jobs must transfer outputs to a comparison job; only
the comparison job's verified output is consumable by aggregation or downstream
workflows. A final workflow aggregates already-verified component manifests.

The layers are:

1. Existing npm/Gradle producers plus the new `rustPackage` producer.
2. Component verifier commands under `verify/`.
3. Reusable `build-<component>.yml` workflows.
4. `release-build.yml` as the thin CI caller and existing manual publisher.
5. `verify/verify-all.sh` as a local convenience orchestrator only.

No layer reimplements product compilation or packaging owned by a lower layer.

### Local aggregate entry point

The exact synopsis is:

```
verify/verify-all.sh <apk> <git-ref> <native-run-a-dir> <native-run-b-dir>
```

Before invoking a component, the script resolves `<git-ref>` to a commit,
requires it to equal `HEAD`, and requires an empty porcelain status including
untracked files. This binds all locally built web, Rust, and supply-chain
evidence to one clean source tree. It passes the resolved commit to native
verification and aggregation.

The native input layout is:

```
<native-run-a-dir>/windows/native-windows-run.tar
<native-run-a-dir>/windows/native-windows-run.tar.sha256
<native-run-a-dir>/macos/native-macos-run.tar
<native-run-a-dir>/macos/native-macos-run.tar.sha256
<native-run-b-dir>/windows/native-windows-run.tar
<native-run-b-dir>/windows/native-windows-run.tar.sha256
<native-run-b-dir>/macos/native-macos-run.tar
<native-run-b-dir>/macos/native-macos-run.tar.sha256
```

`verify/verify-all.sh` is intentionally small. From the repository root it
invokes, in order:

1. `verify/verify.sh <apk> <resolved-commit>`;
2. `verify/verify-web.sh`;
3. `verify/verify-rust.sh`;
4. `verify/verify-native.sh <resolved-commit> <native-run-a-dir>
   <native-run-b-dir>`;
5. `verify/verify-supply-chain.sh`; and
6. `verify/aggregate-checksums.sh <resolved-commit>`.

It:

- validates its command-line arguments;
- calls component verifier commands in a fixed documented order;
- stops on the first failure;
- normalizes and propagates the component exit status; and
- prints a component summary.

It contains no build implementation, checksum parser, runner-identity parser,
artifact allow-list, SBOM generator, license decision, or remote-code scanner.
The script fails with exit 2 rather than silently skipping an input or
component. Component outputs use their documented `build/` paths, and the final
command writes `build/verification/SHA256SUMS.toolchains`. Stable component
commands provide a future seam for changed-path selection, but 1d does not
implement selective verification.

CI never invokes `verify-all.sh`; it invokes component commands directly.

## Artifact and manifest contracts

### Canonical checksum format

Every standard checksum manifest uses:

- lowercase SHA-256;
- two ASCII spaces between digest and path;
- relative POSIX paths only;
- no absolute path, empty segment, `.` segment, or `..` segment;
- raw byte-order sorting under `LC_ALL=C`;
- one final newline; and
- no duplicate or self-referential record.

Manifest readers reject malformed, unsorted, duplicate, missing, unexpected,
absolute, and traversal records. They do not normalize invalid input into an
accepted form.

`SHA256SUMS.native-bridge` is the only extension to this grammar. It permits
exactly three ordered comment headers (`ImageOS`, `ImageVersion`, and
`RUNNER_ARCH`) before otherwise canonical checksum records. Standard manifests
reject comments.

### Android artifact

The Android build keeps its release APK, mapping, and existing `SHA256SUMS`
release contract. Verification delegates to `verify/verify.sh`, including its
signature-stripped comparison for signed APKs. The component verifier also
exports the canonical unsigned reference APK to
`build/verification/android/app-release-unsigned.apk`. PR CI passes the exact
commit SHA; tagged release verification passes the tag. APK content checks
continue to require exactly the approved ABI directories and exactly the three
Veles JNI entries. Mapping files and raw signed APK bytes remain release
evidence but are not claimed byte-reproducible and do not enter
`SHA256SUMS.toolchains`.

### Web-extension artifact

`npm run package` continues to produce:

```
build/web-extension/veles-extension-<version>.zip
build/web-extension/veles-extension-<version>.zip.sha256
```

It also emits `build/web-extension/SHA256SUMS`. The manifest covers the ZIP and
its sidecar and excludes itself. Existing extension tests remain authoritative
for the unpacked `dist/` file allow-list, manifest policy, and local-only code.
The producer clears stale package output before writing the tree.

### Rust JNI/WASM artifact

A new root `rustPackage` Gradle task depends on `rustJni` and `rustWasm`. It
replaces its output directory, rejects missing or unexpected inputs, and copies
the accepted outputs to:

```
build/rust-package/
  jni/arm64-v8a/libveles_crypto.so
  jni/armeabi-v7a/libveles_crypto.so
  jni/x86_64/libveles_crypto.so
  wasm/<complete generated wasm-pack package>
  SHA256SUMS
```

The WASM subtree is the complete declared `web-extension/rust-wasm/pkg/` file
set, not only the `.wasm` binary. Its exact allow-list is derived from the
producer contract and rejects stale files.

The corresponding CI artifact has the stable name `rust-jni-wasm`. It is a
reusable build input, not verification-only evidence. In 1d the aggregate
workflow consumes it. Later workflows may consume its exact ABI and WASM paths
through an explicit dependency. This does not change the current Android
contract that clean APK assembly regenerates JNI, and it does not add a
production extension import of the WASM package.

### Native-bridge artifacts

`bridgePackage` continues to produce the accepted Windows ZIP and macOS tar.gz
plus sidecars. Each platform output tree also emits a standard `SHA256SUMS`.
The native build workflow creates an extracted verification view containing:

- the raw host binary;
- the native-messaging host manifest;
- NSIS and MSI installer outputs on Windows;
- the `.app` tree, including executable modes and symlink targets, on macOS;
- the DMG on macOS;
- the deterministic outer package and sidecar; and
- the standard checksum manifest.

The verification view is evidence for comparison and does not replace the
packaged release input. Archive bytes cover normalized archive metadata; the
extracted view identifies the first differing product file and independently
enforces the package allow-list. Tauri 2.6.0's Windows NSIS/MSI and macOS
DMG/.app producers embed platform timestamps or identifiers that cannot be
made byte-reproducible with supported tool flags. Each run first proves that
its outer package payload exactly matches the transported verification view,
then validates that package and sidecar against its native and component
checksum manifests. Installer payloads, their enclosing outer package, and its
sidecar are excluded from cross-run byte equality. The raw host binary,
including `Veles Native Bridge.app/Contents/MacOS/veles-native-bridge`, and the
host manifest remain cross-run byte compared.

Each run also emits canonical `METADATA.native-bridge.jsonl`, sorted by path.
Each JSON line records path, entry type, and four-digit octal mode; regular-file
records include SHA-256, symlink records include the literal target and its
SHA-256, and directory records include no content digest. JSON string escaping
is authoritative for paths and targets. A deterministic tar transport contains
the verification view, metadata JSONL, checksum files, and identity. The run
slot uploads only that tar plus its SHA-256 sidecar, preserving all evidence as
bytes even if GitHub artifact extraction would otherwise alter modes or
symlinks. The comparison job validates the tar allow-list before extraction,
binds every package payload to its view, validates metadata for each run, and
then compares only the stable raw evidence bytes.

The transport also contains `SOURCE-COMMIT`, exactly one lowercase 40-character
Git commit followed by a newline. It is covered by the transport sidecar but is
not a product checksum record. Native verification requires both run slots and
both platforms to equal the resolved commit passed by `verify-all.sh` or the
caller workflow. Source mismatch is exit 2 and comparison does not proceed.

Each native run emits `SHA256SUMS.native-bridge` with deterministic comment
headers followed by ordinary checksum records:

```
# ImageOS=<non-empty value>
# ImageVersion=<non-empty value>
# RUNNER_ARCH=<non-empty value>
<sha256>  <relative path>
```

The parser requires each identity key exactly once and rejects unknown or empty
identity fields. Standard checksum records remain compatible with tools that
ignore comment lines.

### Aggregate manifest

Verified component records are namespaced and sorted into:

```
build/verification/SHA256SUMS.toolchains
```

Namespaces are `android/`, `web-extension/`, `rust/`,
`native-bridge/windows/`, and `native-bridge/macos/`. The aggregate contains
only these records:

- `android/`: the canonical unsigned reference APK;
- `web-extension/`: the compared extension ZIP and its sidecar;
- `rust/`: every compared regular file under `jni/` and `wasm/`; and
- each native namespace: the stable host binary and host manifest, which were
  cross-run compared; and the outer package, sidecar, installer/app payload
  records, and `METADATA.native-bridge.jsonl`, which are package-bound and
  self-validated in their emitting run but intentionally not cross-run byte
  compared.

Signed APK bytes, mapping files, run-slot transport archives, runner identity,
component checksum manifests, SBOMs, license reports, and audit reports are
excluded. They remain separately uploaded evidence where applicable.

Aggregation is all-or-nothing. Every required namespace and component manifest
must be present and valid. The aggregator never invents a digest for a missing
component.

## Reference environments and data flow

### Web reference image

`verify/Dockerfile.web` uses `node:26.8.1-bookworm-slim` pinned by immutable
digest and asserts Node 26.8.1 and npm 11.19.0. It receives the repository
read-only, copies required source into a temporary clean worktree, installs with
`npm ci --ignore-scripts`, runs existing quality and bundle checks, runs
`npm run package`, and exports `build/web-extension/`.

The web verifier first validates the candidate tree and manifest, then builds
the reference tree and requires identical paths and bytes. A Docker base digest
or repository pin mismatch is an environment/pin failure, not artifact drift.

### Rust reference image

`verify/Dockerfile.rust` pins:

- the same Temurin/JDK baseline and Android SDK/NDK versions as the retained
  APK verifier;
- Rust 1.98.0 from `rust/rust-toolchain.toml`;
- cargo-ndk 4.1.2, wasm-pack 0.15.0, and wasm-bindgen-cli 0.2.127 from
  `rust/toolchain-tools.toml`; and
- Node 26.8.1 with npm 11.19.0 for the generated WASM smoke test.

Its base image is digest-pinned and downloaded archives have committed
cryptographic hashes. It asserts duplicated repository pins before building,
runs `rustPackage` in a clean copied worktree, and exports
`build/rust-package/`. Candidate and reference path sets must match before file
bytes are compared.

The existing `verify/Dockerfile` remains the Android verifier. Common pins may
be kept textually aligned, but 1d does not replace it with a new generic image.

### Native Windows environment

Windows builds use `windows-2025`, the label's default x64 architecture, exact
Node 26.8.1, the bridge Rust 1.98.0 pin, and the locked Tauri CLI 2.6.0. Each run
asserts non-empty `ImageOS`, `ImageVersion`, and `RUNNER_ARCH` before building.

Tauri 2.6.0 normally populates private WiX/NSIS tool caches on demand. The
workflow instead provisions the exact Tauri-declared WiX/NSIS archives and
plugin into an isolated cache using committed URLs and SHA-256 hashes, validates
their complete expected file sets, then disables network access for
`bridgePackage`. This prevents a hidden Tauri download from occurring inside a
Gradle task. The available runner WiX installation is diagnostic only; the
build uses the verified isolated cache so weekly runner changes cannot silently
select another toolset.

### Native macOS environment

macOS builds use the versioned `macos-26` ARM64 label, exact Node 26.8.1, the
bridge Rust 1.98.0 pin, and locked Tauri CLI 2.6.0. The workflow sets
`DEVELOPER_DIR=/Applications/Xcode_26.6.app`, asserts Xcode build `17F113`, and
asserts SDK `macosx26.5`. Each run requires the same non-empty identity triple
as Windows.

### Native comparison rule

Windows and macOS each build in two independent jobs with fresh workspaces and
tool caches. Comparison validates each tree and then compares the identity
triple independently per platform. It compares bytes only when all three fields
match.

If identities differ, verification exits 2 and prints both triples plus the
instruction `re-run on matched image`. It does not classify an environment
mismatch as artifact drift. If identities match but stable raw evidence paths
or bytes differ, verification exits 1 and prints the first differing relative
path and both hashes. Tauri installer payloads and the outer package transport
are the documented exception: both slots must independently validate their
native and component manifests, but their bytes are not compared across slots.

No native verifier claims that `windows-2025` or `macos-26` pins an underlying
image SHA. Self-hosted immutable images remain a future extension point.

## SBOM design

### Tool pins

A dedicated `verify/package.json` and committed `verify/package-lock.json` pin:

- `@cyclonedx/cyclonedx-npm` 6.0.1; and
- `license-checker-rseidelsohn` 5.0.1.

They install with `npm ci --ignore-scripts` and execute only from
`verify/node_modules/.bin/`. There is no `npx` or global fallback.

A committed verification tool manifest pins:

- `cargo-cyclonedx` 0.5.9; and
- `cargo-deny` 0.20.2.

They install with exact `cargo install --locked --version` commands into
separate roots below `build/verify-tools/`. Restored binaries are version-checked
before use. The project Cargo lockfiles remain committed and every analyzed
workspace command uses `--locked`.

### Outputs

CycloneDX JSON is generated at exactly:

```
build/sbom/web-extension.cdx.json
build/sbom/rust.cdx.json
build/sbom/native-bridge.cdx.json
```

The web SBOM includes development/build dependencies because they are the
extension's effective locked dependency tree. The Rust SBOM uses `rust/Cargo.lock`.
The native-bridge SBOM uses `native-bridge/src-tauri/Cargo.lock`, as required by
the parent design. The bridge npm/Tauri CLI tree is license- and script-scanned
but does not create an unrequested fourth SBOM.

Each output must parse as CycloneDX JSON, identify the intended root component,
contain a non-empty dependency graph consistent with its lockfile, and contain
no unresolved component reference. SBOMs are evidence artifacts and are not
included in `SHA256SUMS.toolchains`.

## License policy

Root `licenses.toml` is the single cargo-deny policy used against both Rust
lockfiles. Root `.license-policy.json` is the single npm policy used by a thin
wrapper around license-checker JSON for both npm lockfiles.

Default allowed SPDX licenses are:

- MIT and MIT-0;
- Apache-2.0 and Apache-2.0 WITH LLVM-exception;
- 0BSD, BSD-1-Clause, BSD-2-Clause, BSD-2-Clause-Patent, BSD-3-Clause,
  BSD-3-Clause-Clear, BSD-4-Clause, BSD-4-Clause-Shortened, and
  BSD-4-Clause-UC;
- ISC;
- Zlib;
- BlueOak-1.0.0;
- Python-2.0;
- Unicode-3.0;
- MPL-2.0; and
- BSL-1.0, the Boost Software License rather than BUSL.

Default denied licenses and families are:

- GPL-* and AGPL-*;
- LGPL-* without a named per-package static-linking exception;
- SSPL-*;
- BUSL-* and other Business Source License identifiers;
- Elastic-*;
- CC0-*; and
- Unlicense.

An OR expression passes only through an allowed branch. Current dependencies
that offer MIT or Apache alternatives to CC0 or Unlicense therefore pass
without globally allowing CC0 or Unlicense. LGPL exceptions, if ever needed,
must name the exact package/version and rationale; the initial policy contains
none.

Unknown, missing, malformed, or ambiguous licenses fail. Diagnostics include
package and version, detected expression, available license text or local path,
and the path to the repository policy requiring conscious review.

## Install-script and remote-code policy

Both npm projects set `ignore-scripts=true`, making ordinary `npm ci` incapable
of executing dependency lifecycle scripts. Verification also passes
`--ignore-scripts` explicitly.

The scanner requires every lockfile `hasInstallScript` package to match an
exact reviewed package/version and SHA-256 hashes of both its lifecycle command
and referenced script content. The initial exceptions are only the locked
esbuild packages and platform-optional fsevents package. These scripts remain
disabled; the allow-list records dormant package content rather than permission
to execute it. A new package, version, command, referenced file, or hash fails
with the policy path.

Cargo dependencies are acquired from committed lockfiles, then reference builds
run with network disabled. Cargo build scripts are inventoried from locked
registry sources and scanned for network clients, shell download commands, and
remote executable launch patterns. An exact reviewed package/checksum exception
is required for any ambiguous script; no floating name-only exception is
accepted.

Every product packaging flow separates acquisition from execution. The web
candidate package is built on the host; the web reference package is built in
a Docker container with `--network=none` (the verifier compares candidate and
reference bytes afterward). Native jobs complete `npm ci`, `cargo fetch --locked`,
Rust toolchain setup, and the pinned Windows Tauri-cache provisioning before
activating an outbound-network deny for the packaging step only. The job proves
connectivity by successfully reaching one fixed HTTPS probe endpoint immediately
before denial, then requires the same endpoint to fail during denial. Windows
activates and inspects a platform-wide outbound NetFirewall rule; macOS denies
outbound network only for the `bridgePackage` process tree via `sandbox-exec`
(never mutating host PF configuration). Both set Cargo offline mode, run
`bridgePackage` under the deny, and Windows restores host networking in an
unconditional cleanup step. Failure of the pre-denial probe is an environment
error, not proof of isolation. A platform that cannot prove the rule and
before/after behavior fails with exit 2; static scanning alone is not accepted
as evidence that packaging was offline.

Repository source and configuration scans reject:

- extension remote script origins or weakened CSP;
- Tauri updater wiring;
- `npx` or runtime download fallback;
- `curl | sh`, equivalent shell-pipe execution, and floating executable URLs;
- undeclared npm lifecycle scripts; and
- generated installers configured to fetch bootstrapper executables.

The headless bridge sets Tauri's Windows WebView install mode to `skip`. This is
safe for the approved headless `windows = []` skeleton and prevents generated
installers from downloading a WebView bootstrapper at install time. Existing
tests gain an exact assertion for this setting.

## CI workflows

### Workflow structure

Each reusable workflow is named for the artifact it builds and verifies:

| Workflow | Build artifact | Verification owned by workflow |
|---|---|---|
| `build-android.yml` | APK, mapping, checksums | Docker APK rebuild and APK ABI/JNI allow-list |
| `build-web-extension.yml` | extension ZIP tree | bundle allow-list and web reference byte comparison |
| `build-rust.yml` | `rust-jni-wasm` | Rust reference byte comparison and JNI/WASM allow-list |
| `build-native-windows.yml` | verified Windows bridge tree | two independent builds, identity gate, byte comparison |
| `build-native-macos.yml` | verified macOS bridge tree | two independent builds, identity gate, byte comparison |
| `build-supply-chain.yml` | SBOM/license/audit reports | report validation and policy enforcement |
| `build-toolchain-manifest.yml` | `SHA256SUMS.toolchains` | component-manifest validation and aggregation |

Verification is a step or job inside the workflow that owns the artifact. No
generic verification workflow accepts an unverified product and no component
defers its own byte/content decision to `verify-all.sh`.

The native workflows each contain two independent target build jobs and one
comparison job. Each build job uploads an `unverified-<platform>-run-<slot>`
transport tar and sidecar with minimum retention solely for its sibling
comparison job. The names and manifests mark these as unverified, and the
caller never exposes them as downstream build outputs. Only the comparison job
uploads `verified-native-<platform>`, which is the sole native output consumed
by the toolchain-manifest workflow or later workflows.

### Caller and triggers

`.github/workflows/release-build.yml` becomes a thin reusable-workflow caller
and retains its existing manual prerelease publication behavior.

- An ordinary push to `master` runs only `build-android.yml`, preserving the
  existing release-build cost profile.
- A pull request carrying the existing `release-build` label runs the complete
  component graph.
- A manual `workflow_dispatch` has an initial guard requiring
  `github.ref == 'refs/heads/master'`. A dispatch from any selectable non-master
  ref fails before build or publication. A valid master dispatch runs the
  complete component graph.
- Manual prerelease publication depends on successful Android verification,
  successful `build-supply-chain.yml`, and successful
  `build-toolchain-manifest.yml`.

Toolchain packages, SBOMs, licenses, audits, and aggregate checksums remain CI
artifacts. They are not added to public GitHub Releases in 1d.

Reusable workflow inputs are limited to commit SHA and explicit artifact names.
Permissions are minimal. Only `build-android.yml` accepts existing APK signing
secrets; native workflows receive no signing or notarization credentials.

Future path-based component selection belongs in `release-build.yml`. The
component workflows and artifact names are designed for it, but changed-path
selection is deferred.

### Actions and artifact upload

All repository workflow `uses:` references are pinned to immutable commit SHAs
with human-readable version comments. An upgrade changes the SHA and comment
together after release-note review.

Every artifact upload uses an exact source allow-list, fails when any file is
missing, has fixed retention, and avoids broad workspace globs. Unverified
native transport artifacts use the shortest retention and cannot satisfy any
caller output. Native artifact names include platform and independent run slot;
environment identity comes only from validated checksum metadata, not the
artifact name.

## Error handling

All component verifiers and `verify-all.sh` use:

- exit 0: every requested check matched;
- exit 1: artifact bytes, artifact allow-list, license policy, lifecycle policy,
  remote-code policy, or generated evidence did not match its accepted
  contract; and
- exit 2: usage, missing input/tool, unsupported environment, pin mismatch,
  build failure preventing comparison, missing identity, or unmatched native
  environment identity.

A component never emits a success manifest after failure. Build failure is not
misreported as artifact mismatch. Missing tools identify the tool and pinning
file. Pin drift identifies expected and actual values. Native identity mismatch
prints both complete triples and `re-run on matched image`. Byte mismatch prints
component, relative path, and both hashes. Diagnostics never include secrets,
input content, or absolute runner paths in published manifests.

## Testing

### Script-level tests

Node's built-in test runner covers:

- checksum generation and parsing;
- deterministic ordering;
- absolute/traversal/duplicate/self-reference rejection;
- native identity header validation and mismatch handling;
- aggregate namespace validation;
- npm lifecycle command/content hash decisions;
- license expression allow, deny, OR-alternative, and unknown behavior; and
- exit-code classification.

Shell fixtures prove that `verify-all.sh` calls component commands in order,
stops at the first failure, propagates normalized status, and contains no
component implementation.

### Producer and integration tests

- Extension bundle tests assert the new package manifest and stale-output
  replacement.
- Native package extraction tests assert verification-view contents,
  executable modes, symlinks, standard/native manifests, unexpected-file
  rejection, and WebView download disablement.
- Gradle integration exercises `rustPackage`, the exact three JNI ABIs, the
  complete WASM package, stale-output replacement, and root `clean`.
- Docker pin checks run before expensive builds and test deliberate pin drift.
- Candidate/reference integration tests compare actual extension and Rust
  outputs.
- Each target-native workflow is the acceptance test for that platform's
  reproducibility and runner-identity behavior.

Any nondeterministic raw binary, mode, symlink, or manifest is a hard 1d
blocker. A fix must make the production artifact deterministic. The sole
exception is the documented Tauri NSIS/MSI/DMG/.app producer payloads and their
enclosing package/sidecar: Tauri 2.6.0 cannot produce those bytes
reproducibly with supported flags, so each slot's manifest validates them
locally while raw host evidence remains cross-run byte-comparable. The verifier
may not otherwise hide product differences through post-production
normalization or compare only extracted semantic content when the accepted
artifact is byte-comparable.

## Documentation and upgrades

Implementation updates:

- `docs/reproducible-builds.md` with exact component and aggregate commands,
  environments, pins, output paths, trust boundaries, and failure recovery;
- `README.md` with the verification/supply-chain entry points and artifact
  locations;
- `CLAUDE.md` with developer and CI commands, exact verification Node, and
  reusable workflow ownership; and
- verifier usage text with required local native inputs and exit codes.

Upgrade procedure requires:

1. change the human-readable version and immutable digest/hash together;
2. regenerate and review the affected committed lockfile;
3. review tool and runner-image release notes;
4. update lifecycle/license exceptions only with package text and rationale;
5. run the affected component workflow; and
6. run the complete labeled or manually dispatched graph before accepting the
   new reference environment.

Runner documentation states that image labels are versioned but images update
weekly. Recorded identity permits post-hoc equality only. If stable labels or
installed Xcode/tool versions disappear, the pin update follows this procedure
rather than silently selecting a default.

## Acceptance criteria

Sub-project 1d is accepted when:

1. The existing Android verifier still verifies the candidate/released APK.
2. The web reference image rebuilds the extension package and candidate and
   reference paths/bytes match.
3. The Rust reference image rebuilds exact JNI/WASM package inputs and candidate
   and reference paths/bytes match.
4. Two Windows and two macOS builds compare bytes only after complete identity
   equality and exact source-commit equality, and unmatched identities produce
   the required rerun instruction.
5. Every package tree emits a deterministic checksum manifest and the aggregate
   manifest covers every reproducible component namespace.
6. All three required CycloneDX JSON files are generated and validated.
7. Both npm trees and both Cargo lockfiles pass the reviewed license policy;
   unknown licenses fail with reviewable text/path.
8. npm lifecycle scripts remain disabled and exact-content audited; Cargo and
   packaging phases prove no network fetch or remote executable fallback.
9. Artifact allow-lists cover APK ABIs/JNI, extension files, Rust JNI/WASM, and
   native package contents.
10. The reusable component workflows expose only verified component outputs;
    native run-slot uploads are explicitly unverified internal transport with
    minimum retention. The documented release-build triggers and non-master
    dispatch guard run the intended graph.
11. `verify-all.sh` provides the complete local orchestration contract and
    preserves exit codes 0, 1, and 2 without being used by CI.
12. Root `clean` removes every new declared generated output.
13. Documentation names exact commands, pins, outputs, trust limitations, and
    upgrade steps.

## Out of scope

- BLE transport or device discovery
- cryptographic protocol behavior or real SPAKE2
- Android, extension, or bridge OTP integration
- UI
- production bridge/bundle/origin identities
- signing, notarization, or signing credentials
- signed-to-unsigned artifact correspondence (OTP-25)
- public release publication of extension/native/supply-chain artifacts
- self-hosted runner infrastructure
- PR changed-path selection
- replacement of npm/Gradle/Cargo/Tauri producer entry points
