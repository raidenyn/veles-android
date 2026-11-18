# AGENTS.md

Veles turns noisy bank OTP notifications into a clean, copy-ready one-time code, on device and
strictly offline — no internet permission, no telemetry. Setup: `CONTRIBUTING.md`; build: `BUILD.md`.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there.

## Node map

Read `<dir>/AGENTS.md` before changing files there; node detail lives in the docs it links.

- `android/` — the Android app: OTP handling, permissions, pairing and the app build/test.
- `web-extension/` — the MV3 browser extension: connector, pairing UI and its npm build/test.
- `rust/` — the shared Rust crypto core: JNI, WASM and host-JVM targets.
- `protocol/` — the versioned external-device protocol contract and its conformance fixtures.
- `native-bridge/` — the Tauri packaging scaffold for the Native Messaging host.
- `native-bridge/host/` — the production Native Messaging host: plain Rust, no Tauri.
- `native-bridge/installer/` — the standalone installer engine and its platform frontends.
- `native-bridge/installer/macos/` — the macOS installer frontend and its signing identity.
- `native-bridge/installer/windows/` — the Windows installer frontend.
- `verify/` — the supply-chain verifier and the repository policy checks.
- `scripts/` — release tooling, Conventional Commits checks and the component map.
- `docs/` — documentation, decision records and the agent-file check.

## Rules

Repo-wide rules in `.agents/rules/` are always on:
`.agents/rules/repo-real-device-testing.md`, `.agents/rules/repo-no-time-based-tests.md`,
`.agents/rules/repo-pre-push-checks.md`, `.agents/rules/repo-review-models.md`,
`.agents/rules/repo-superspec-review-gate.md`, `.agents/rules/repo-tracker-intake.md`,
`.agents/rules/repo-superspec-closeout.md`, `.agents/rules/repo-signing-keys.md`.
Node rules are path-scoped and listed in each node's `AGENTS.md`.

OpenCode needs the `opencode-rules` plugin pinned in `opencode.json` (`docs/opencode-rules.md`); do not run
`/init`; a parent `CLAUDE.md` hides `AGENTS.md`. Tool and checkout setup: `CONTRIBUTING.md`.

## Hook-delivered rules
<!-- hooked-rules:begin -->
- Every commit subject and PR title is a Conventional Commit `type(scope): description`; the type guide is delivered when you apply or finalize a change and before `git commit`, `gh pr create` or `gh pr edit`; read this file at those moments if it was not. — .agents/hooked-rules/repo-commit-types.md
<!-- hooked-rules:end -->
