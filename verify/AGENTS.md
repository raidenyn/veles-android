# verify/AGENTS.md

The supply-chain verifier and the repository policy checks (OTP-01 sub-project 1d). This folder is
the CI `supply-chain` node: `build-supply-chain.yml` runs `verify/verify-supply-chain.sh`, and the
same script is the local entry point. Reports land under `build/sbom/` and
`build/verification/supply-chain/`.

## Build & Test

Run from the repository root:

```bash
# Local prerequisites (CI provisions them in build-supply-chain.yml)
npm ci --ignore-scripts --prefix verify   # cyclonedx-npm + license-checker -> verify/node_modules/.bin/
./gradlew installCargoDeny                # cargo-deny -> build/verify-tools/

# The full policy verifier: SBOM generation + validation, licenses, install scripts,
# cargo build scripts, vendored patches and remote-code scan
verify/verify-supply-chain.sh

# Optional local checksum aggregation for a commit
verify/aggregate-checksums.sh <commit>

# The verifier's own unit tests
node --test verify/test/*.test.mjs
```

`verify/verify-supply-chain.sh` asserts the **exact** reference pin — **Node 26.8.1 with bundled npm
11.19.0** — and the active Rust toolchain; a mismatch exits 2. The developer-facing package contract
stays a floor (`engines.node = ">=22.0.0"` in `web-extension/package.json` and
`native-bridge/package.json`); only the supply-chain and reproducible-build environments require the
exact pin. `verify/package.json` pins `@cyclonedx/cyclonedx-npm` 6.0.1 and
`license-checker-rseidelsohn` 5.0.1 (its CLI banner prints a stale `4.4.2`; the committed lockfile is
authoritative). `verify/cargo-tools.toml` pins `cargo-cyclonedx` 0.5.9 and `cargo-deny` 0.20.2.

Exit codes are the shared 0/1/2 contract: 0 all checks matched, 1 a policy/artifact mismatch, 2 a
usage, missing tool/input or environment/pin failure. Never report a failed check as a pass, and
never emit a success manifest after a failure.

## Policy checks

- **Install scripts** — `verify/install-script-policy.json` holds the exact reviewed npm lifecycle
  command/content hashes; dependency installs use `npm ci --ignore-scripts` and
  `check-npm-install-scripts.mjs` fails on any unreviewed lifecycle script. Cargo build scripts are
  governed the same way by `verify/cargo-build-script-policy.json` and
  `check-cargo-build-scripts.mjs`.
- **Remote code** — `verify/check-remote-code.mjs` scans its declared scopes as **code with no
  extension filter** (so a new file is scanned by default) and rejects `npx`, `curl`/`wget` piped to
  a shell, remote CSP script sources, Tauri updater wiring and floating executable downloads. Files
  whose basename is `AGENTS.md` are guidance prose and are skipped; a script file with the same text
  is still rejected (`verify/test/remote-code.test.mjs`).
- **SBOMs and licenses** — six SBOMs (`web-extension`, `protocol`, `rust`, `native-bridge`,
  `native-host`, `native-installer`) are generated and validated; `licenses.toml` and
  `.license-policy.json` are the single cargo-deny / npm licence policies for both lockfiles.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
