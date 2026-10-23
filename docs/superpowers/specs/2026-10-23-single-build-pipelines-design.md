# Single-build pipelines with optional rebuild support

## Decision and intent

PR #111 should support rebuilding from source without making reproducibility a
CI or publication gate while the project stabilizes. The user approved building
native executables in the same target-platform jobs that create their installers.

## Approaches

1. Retain A/B verification but deduplicate its YAML: smaller definitions, same
   runtime cost and evidence machinery.
2. Verify only releases: cheaper everyday CI, but still a complex release gate.
3. Build once and keep optional rebuild commands: selected by the user; ordinary
   tests and policy checks remain, and reproducibility can be enforced later.

Linux cross-compilation was considered. Keep native compilation and packaging
together on Windows/macOS to avoid introducing another toolchain.

## Required behavior

- Component producer workflows do not invoke reproducibility verifiers, create
  A/B run transports, compare runner identities, or require Docker reference builds.
- Android builds one release variant per invocation, signed when configured and
  unsigned otherwise. Validate JNI contents against that actual APK; upload its
  mapping and checksums together. Do not build an extra unsigned reference APK.
- Windows produces its executable, NSIS, and MSI in one Windows installer job.
  macOS produces its executable, `.app`, and DMG in one macOS installer job.
- Retain pinned compiler/tool versions, dependency lockfiles, producer package
  validation, deterministic outer packaging, and component checksum manifests.
- Preserve normal tests, lint, SBOM/license/script/remote-code policy checks.
- Tag releases publish without an independent rebuild. Preserve provenance,
  checksums, version/tag validation, collision protection, and the draft asset
  digest check (the latter is artifact integrity, not reproducibility).
- Manual master prereleases still reject other refs and protect real releases.
  They depend on successful single-build producers and supply-chain policies.
- Use neutral artifact names (`android-release`, `web-extension`,
  `native-windows`, `native-macos`, `supply-chain-reports`); retain `rust-jni-wasm`.

## Simplification boundaries

Remove the reusable toolchain-aggregate workflow from active CI and its caller
dependencies. Component checksums identify downloadable products without native
transport restoration or a second checksum hierarchy. Keep the existing optional
local rebuild/aggregate tools and their focused tests for future use; describe
native comparison accurately as stable-payload comparison, not installer-byte
reproducibility. No new scheduled or manual verification workflow is required.

Installer workflows call normal Gradle packaging rather than network-denial
wrappers. Preserve required Windows pinned installer-tool provisioning and its
absolute `NSIS_PATH`; set the pinned macOS Xcode via job environment. No runner
image-identity equality requirement remains in production jobs.

## Documentation and verification

Update README, CLAUDE.md, and reproducible-build instructions. Mark previous 1d
design/plan gate requirements superseded by this decision rather than rewriting
historical progress reports. Optional tools remain explicit opt-in diagnostics;
successful publication makes no reproducibility claim.

Workflow contract tests must cover absence of automatic comparisons, signed and
unsigned artifact routing, single native jobs, retained integrity/policy checks,
and publication dependencies. Validate YAML with actionlint when available and
run the verification tooling tests. Actual Windows/macOS installer execution
requires GitHub target runners and must be reported separately from local checks.
