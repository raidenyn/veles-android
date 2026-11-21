# protocol/AGENTS.md

The external-device protocol contract (OTP-02). The normative authority is
`protocol/docs/veles-v1.md`; everything else here is derived from it or from the approved RFC and can
never override either. The package is test-only: product code never imports it.

## Layout

- `protocol/v1/` — machine-readable contract: requirement inventory (`manifest.json`), limits, binary
  layouts, envelope and fixture schemas, state descriptors, and the shared fixtures.
- `protocol/checks/` — `node:test` suites and the fixture/binary/envelope/state interpreters.
- `protocol/tools/` — vector generators and the contract-text check.
- `protocol/conformance/` — cross-endpoint conformance scenarios.
- `protocol/docs/` — the spec itself plus review and disposition records.

`protocolCheck` covers the Node contract checks (`protocolContractCheck`), the host-only Rust consumer
(`protocolRustTest`), both TypeScript consumers (`protocolWebTest` in `web-extension/` and the
native-bridge consumer), the Android consumer `:app:protocolJvmTest`, and the session loopback. Node
>= 22 and Python >= 3.8 are required. CI builds `./gradlew rustHostJni` first because the loopback
child JVM loads that test-only cdylib.

## Rules

Android CI also runs for these changes.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
