# web-extension/AGENTS.md

The Veles OTP browser extension (MV3; OTP-01 sub-project 1a, with OTP-10 bundling the generated WASM
core). Requires node >= 22 and npm on PATH. The source imports the generated WASM package, so from the
repository root run `./gradlew rustWasm` first: `npm run build`/`package`/`typecheck`, `npm test` and the
browser check all need it. The packaged zip and its sha256 sidecar land in `../build/web-extension/`.

## Build & Test

```bash
cd web-extension
npm ci                          # install deps from lockfile
npm run build                   # vite build -> dist/ (test:bundle and test:browser read it)
npm run package                 # vite build + deterministic zip + sha256 sidecar -> ../build/web-extension/
```

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
