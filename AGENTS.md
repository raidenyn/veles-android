# AGENTS.md

Veles turns noisy bank OTP notifications into a clean, copy-ready one-time code: it extracts the code,
amount and merchant on device and re-presents them with a one-tap "Copy" action. It is free, open source
and strictly offline — no internet permission, no telemetry, no tracking. Contributor setup lives in
`CONTRIBUTING.md`; build and install prerequisites in `BUILD.md`.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there.

## Node map

Before changing files under `<dir>/`, read `<dir>/AGENTS.md`. Each node owns its own architecture,
commands and rules; this file intentionally holds no node detail.

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

Repo-wide rules in `.agents/rules/` are always on and each holds one concern:
`.agents/rules/repo-conventional-commits.md`, `.agents/rules/repo-no-time-based-tests.md`,
`.agents/rules/repo-pre-push-checks.md`, `.agents/rules/repo-review-models.md`,
`.agents/rules/repo-superspec-review-gate.md`, `.agents/rules/repo-tracker-intake.md`, `.agents/rules/repo-signing-keys.md`. Node rules are
scoped by path to their folder and listed in that node's `AGENTS.md`; if your tool cannot load a
scoped rule, read the file the node lists.

## Tools

- **OpenCode** needs the `opencode-rules` plugin at the range pinned in `opencode.json`; without it no
  scoped rule is delivered. Setup and troubleshooting: `docs/AGENTS.md`.
- **Claude Code** ≥ 2.1.277 (≥ 2.1.281 for Bedrock and telemetry-off sessions); a `CLAUDE.md` or
  `CLAUDE.local.md` in any parent folder hides `AGENTS.md`.
- **Checkout**: `core.symlinks=true` is required so the `.claude/` links resolve; platform setup notes
  are in `CONTRIBUTING.md`.
- **OpenSpec** CLI `>= 2.0.0, < 3` (2.0.0 last verified; 1.x is unsupported).
- Do not run `/init` in any agent tool: it rewrites guidance and can create a nested `CLAUDE.md`.
