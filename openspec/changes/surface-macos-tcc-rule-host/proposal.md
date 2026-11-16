## Why

`native-bridge/host/AGENTS.md` tells the agent to read `.agents/rules/macos-test-app-tcc.md` by hand before any macOS Bluetooth run, because the rule's `native-bridge/installer/macos/**` glob does not load under `native-bridge/host/`. The previous session went straight to the device run and skipped that read, so the build/launch/signing constraints never reached the agent. The rule must load by itself for host work.

## What Changes

**Rule scope**
- From: `.agents/rules/macos-test-app-tcc.md`, frontmatter `globs`/`paths` = `["native-bridge/installer/macos/**"]`, listed in `native-bridge/installer/macos/AGENTS.md`.
- To: `.agents/rules/native-bridge-test-app-tcc.md`, frontmatter `globs`/`paths` = `["native-bridge/**"]`, listed in `native-bridge/AGENTS.md`; the macOS node keeps a prose pointer.
- Reason: `native-bridge/**` is the narrowest scope that is cumulative over both `native-bridge/installer/macos/` and `native-bridge/host/`, and the agent-file check requires a rule's frontmatter to be exactly its filename's scope pattern.
- Impact: non-breaking; the rule now also loads, inertly, for other `native-bridge/` paths.

**Host guidance**
- From: `native-bridge/host/AGENTS.md` says the macOS scope does not deliver the rule under the host node, so read it by hand.
- To: the host node points at the rule without the by-hand instruction, because the parent scope delivers it.
- Reason: the by-hand step was skipped in practice and is no longer needed.

**Layout spec**
- From: `agent-guidance-layout` requires a macOS TCC rule to be named `macos-<topic>.md`, scoped to `native-bridge/installer/macos/**`, and listed by that node.
- To: a macOS TCC rule consumed by more than one `native-bridge/` node is named `native-bridge-<topic>.md`, scoped to `native-bridge/**`, and listed by `native-bridge/AGENTS.md`; the macOS node still owns the identity facts and points at the rule.
- Reason: the requirement must describe and permit the parent-scope delivery the host node needs.

**Unchanged**
- The rule's normative content (one identity, designated requirement, LaunchServices launch, grant reuse verification, no `tccutil reset`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `agent-guidance-layout`: the canonical-folder requirement and its host-pointer and macOS-rule scenarios now allow a macOS TCC rule consumed by the host node to be scoped to the parent `native-bridge/**` and listed by `native-bridge/AGENTS.md`.

## Impact

- Renamed: `.agents/rules/macos-test-app-tcc.md` → `.agents/rules/native-bridge-test-app-tcc.md` (content unchanged apart from frontmatter).
- Edited: `native-bridge/AGENTS.md` (`## Rules`), `native-bridge/installer/macos/AGENTS.md` (pointer and `## Rules`), `native-bridge/host/AGENTS.md` (platform-frontends pointer).
- Synced at closeout: `openspec/specs/agent-guidance-layout/spec.md`.
- Checks: `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`, `openspec validate --all`, and a repository grep that no live file still names the old rule path.
- Unchanged: the agent-file check's scope table, `docs/AGENTS.md`, the generated skills, and every macOS test-run instruction.
