# verify/AGENTS.md

The supply-chain verifier and repository policy checks (OTP-01 1d). This folder is the CI
`supply-chain` node: `build-supply-chain.yml` runs `verify/verify-supply-chain.sh`, and the same
script is the local entry point. Reports land under `build/sbom/` and
`build/verification/supply-chain/`. Tool pins, prerequisites, the 0/1/2 contract and every policy
check: `docs/reproducible-builds.md`.

## Build & Test

Run from the repository root:

```bash
npm ci --ignore-scripts --prefix verify   # CI provisions the same tools
./gradlew installCargoDeny
node --test verify/test/*.test.mjs         # the verifier's own unit tests
```

Exit codes are the shared 0/1/2 contract: 0 all checks matched, 1 a policy/artifact mismatch, 2 a
usage, missing tool/input or environment/pin failure. Never report a failed check as a pass.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
