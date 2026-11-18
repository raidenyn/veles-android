# Traceability: AGENTS.md 40-line cap

Final line counts from `git ls-files '*AGENTS.md' | xargs wc -l`:

```
   40 AGENTS.md
   34 android/AGENTS.md
   28 docs/AGENTS.md
   30 native-bridge/AGENTS.md
   33 native-bridge/host/AGENTS.md
   38 native-bridge/installer/AGENTS.md
   39 native-bridge/installer/macos/AGENTS.md
   32 native-bridge/installer/windows/AGENTS.md
   37 protocol/AGENTS.md
   30 rust/AGENTS.md
   34 scripts/AGENTS.md
   28 verify/AGENTS.md
   25 web-extension/AGENTS.md
```

Every file is at or under 40 lines.

## Section destinations

### Root `AGENTS.md` (56 → 40)

| Section | Destination |
|---|---|
| Purpose, run-from-root notice | stays, compressed to 2 lines |
| Node map (12 entries) | stays (the checker requires it) |
| Repo-wide rule list (8 paths) | stays, two paths per line |
| Tools: OpenCode requirement | stays as one line, now links `docs/opencode-rules.md` |
| Tools: Claude Code versions, `core.symlinks`, OpenSpec CLI range | `CONTRIBUTING.md` (already held there) |
| `/init` and parent-`CLAUDE.md` warning | stays as one line |
| Hook-delivered rules pointer block | stays, regenerated |

### `android/AGENTS.md` (137 → 34)

| Section | Destination |
|---|---|
| Intro and independent-build note | stays, links `docs/android.md` |
| Architecture Overview, Core Flow | `docs/android.md` |
| Bank Handler Configuration | `docs/android.md` |
| Test Screen | `docs/android.md` |
| Sensitive notifications (Android 15+) | `docs/android.md` |
| Package Structure | `docs/android.md` |
| Navigation | `docs/android.md` |
| Testing Approach | `docs/android.md` |
| Adding a New Bank Handler | `docs/android.md` |
| Signing identities (Android) | stays, compressed; the keystore/init-script names and every signing rule (fingerprint comparison before `adb install -r`, no borrowed credentials, no unread fingerprints, one key per app identity) stay; only the fixture build line and backup detail move to `docs/android.md`; the heading is kept for `docs/otp-11-debug-controls.md` |
| Build & Test commands, NDK/rustup, `protocolJvmTest` | stay |
| `androidGitVersion` version format | `docs/releasing.md` (single home); linked from the file |

### `docs/AGENTS.md` (177 → 28)

| Section | Destination |
|---|---|
| Intro | stays, links the new pages |
| The agent-file check: explanation | `docs/agent-guidance.md` |
| The agent-file check: commands and exit semantics | stay |
| Gate B evidence matrix validator | command stays; explanation in `docs/agent-guidance.md` |
| Adding a rule or a node | `docs/agent-guidance.md` |
| OpenSpec version and update procedure | `docs/openspec-workflow.md` |
| OpenSpec workflow list and skill names | `docs/openspec-skills.md` (the checker's `DOCUMENTED_SKILLS`) |
| The superspec schema fork | `docs/openspec-workflow.md`; the `(schema version v5)` mention stays |
| OpenCode and the plugin | `docs/opencode-rules.md`; one-sentence requirement with a link stays |
| Hook-delivered rules | stays short, with the root-cap statement; detail remains in `docs/hook-delivered-rules.md` |
| Rules | stays |

### `scripts/AGENTS.md` (102 → 34)

| Section | Destination |
|---|---|
| Intro | stays, links `docs/ci.md` and `docs/releasing.md` |
| Build & Test commands | stay, compacted; the project-flow commands point at `--help` |
| `project_flow.py` exit codes | stay |
| Conventional Commits explanation | `docs/releasing.md`; the required guidance-only-`docs` sentence stays |
| Versions and tags, build-time injection | `docs/releasing.md` |
| Git hooks (`core.hooksPath`) | `docs/ci.md` |
| Workflow ownership table | `docs/ci.md` (already held the tree/table; no duplicate added) |
| Rules | stays |

### `verify/AGENTS.md` (60 → 28)

| Section | Destination |
|---|---|
| Intro | stays, links `docs/reproducible-builds.md` |
| Build & Test commands | stay |
| Tool pins and prerequisites | `docs/reproducible-builds.md` (already held them; nothing duplicated) |
| Exit codes 0/1/2 | stay; `docs/reproducible-builds.md` also states them |
| Policy checks (install scripts, remote code, SBOMs) | `docs/reproducible-builds.md` |
| Rules | stays |

### `native-bridge/AGENTS.md` (43 → 30)

| Section | Destination |
|---|---|
| Intro, Tauri warning | stays, links `docs/otp-23-requirements-matrix.md` D-01 |
| Tauri-free crate pointers | stay |
| Build & Test commands | stay |
| Toolchain and dependency pins | stay |
| Rules | stays |

No prose was dropped; it was compressed.

### `native-bridge/host/AGENTS.md` (48 → 33)

| Section | Destination |
|---|---|
| Intro and wire-protocol pointer | stays |
| Build & Test commands | stay |
| Toolchain pin and target directory | stay |
| CI artifact and SBOM names | stay (`test_build_contract.py` requires them) |
| Platform frontends and TCC rule pointer | stay |
| Rules | stays |

No prose was dropped; it was compressed.

### `native-bridge/installer/macos/AGENTS.md` (49 → 39)

| Section | Destination |
|---|---|
| Intro and instructions | stays, links `docs/otp-25-installers.md` and `docs/desktop-ble-poc.md` |
| Canonical identity statement | stays |
| Signing identity and TCC (macOS) | stays, compressed; the identity rules, bundle id, no-fallback rule and publish-only signing stay; history is already in the two docs |
| Build & Test commands | stay |
| Rules | stays |

No canonical rule was moved out of the node.

### `rust/AGENTS.md` (45 → 30)

| Section | Destination |
|---|---|
| Intro and output paths | stays, compressed |
| Build & Test commands | stay |
| Output notes | stay |
| Rules (Android CI pointer) | stays |

No prose was dropped; it was compressed.

## Deliberate drops

- **Root tool versions, checkout setup and the OpenSpec CLI range.** Dropped from the root because
  `CONTRIBUTING.md` already states each one; the root now links it. Verified by reading the linked
  section.
- **`docs/AGENTS.md` Gate B explanation.** The command stays; the explanation moves to
  `docs/agent-guidance.md`.
- **`scripts/AGENTS.md` version/tag narrative.** Moved to `docs/releasing.md`, which already had the tag
  table and the bump rules; the moved text adds the build-time injection facts.
- No other fact was dropped. The architecture, workflows, pins and policy text all have a destination
  above.

## Tauri decision

`docs/otp-23-requirements-matrix.md` D-01 (line 52) records the current Tauri-vs-plain evaluation and the
negative decision on retaining Tauri solely for packaging. `native-bridge/AGENTS.md` keeps the one-line
warning and links D-01; no evaluation text was lost.

## Verification

- `python3 docs/checks/check_agent_files.py --root .` — exit 0, no findings.
- `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` — 190 tests, OK.
- `python3 -I scripts/components.py check` — exit 0.
- `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py'` — 25 tests,
  OK.
- `openspec validate agent-file-line-cap` — valid.
- `git grep` for the roster model names — only the roster.
- `git grep '/opsx:' openspec/schemas` — no hits.
- `git diff --name-only "$(git merge-base origin/master HEAD)" -- .agents/skills .claude/skills` —
  empty.
- `node --test verify/test/*.test.mjs` — 116 tests, 115 pass, 1 skipped, 0 fail. The earlier
  `license-policy.test.mjs` failure was the missing `verify/node_modules` toolchain; after
  `npm ci --ignore-scripts --prefix verify` it passes. The change touches no file under `verify/`.
