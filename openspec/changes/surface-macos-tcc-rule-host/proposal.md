## Why

`native-bridge/host/AGENTS.md` tells the agent to read `.agents/rules/macos-test-app-tcc.md` by hand before any macOS Bluetooth run, because the rule's `native-bridge/installer/macos/**` glob does not load under `native-bridge/host/`. The previous session went straight to the device run and skipped that read, so the build/launch/signing constraints never reached the agent. The rule must load by itself for host work.

## What Changes

**Rule scope**
- From: `.agents/rules/macos-test-app-tcc.md`, frontmatter `globs`/`paths` = `["native-bridge/installer/macos/**"]`, listed in `native-bridge/installer/macos/AGENTS.md`.
- To: `.agents/rules/native-bridge-test-app-tcc.md`, frontmatter `globs`/`paths` = `["native-bridge/installer/macos/**", "native-bridge/host/**"]`, listed in `native-bridge/AGENTS.md`; the macOS node keeps a prose pointer.
- Reason: those are the two nodes that consume the rule; the parent `native-bridge/**` scope alone would also load it for the Windows installer and the Tauri scaffold, which review feedback rejected. The agent-file check must accept the exact consuming list under the rule's scope.
- Impact: non-breaking for the two consuming nodes; no other `native-bridge/` path loads the rule.

**Host guidance**
- From: `native-bridge/host/AGENTS.md` says the macOS scope does not deliver the rule under the host node, so read it by hand.
- To: the host node points at the rule without the by-hand instruction, because its `native-bridge/host/**` pattern delivers it.
- Reason: the by-hand step was skipped in practice and is no longer needed.

**Layout check and spec**
- From: `check_agent_files.py` requires each node rule's `globs`/`paths` to be exactly one pattern equal to the scope's folder pattern, and `agent-guidance-layout` says the same.
- To: the check accepts identical, non-empty lists of patterns that each sit under the rule's scope folder; a rule consumed by several siblings is named for their narrowest common parent and lists only the consuming patterns. The check gains multi-pattern and out-of-scope tests, and `docs/AGENTS.md` and the delta spec describe the rule.
- Reason: the layout must permit a precise multi-node rule without weakening the scope check for ordinary rules.

**Unchanged**
- The rule's normative content (one identity, designated requirement, LaunchServices launch, grant reuse verification, no `tccutil reset`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `agent-guidance-layout`: the canonical-folder requirement and the rule-layout requirement now allow a macOS TCC rule consumed by the host node to be named for the parent `native-bridge` scope while listing only `native-bridge/installer/macos/**` and `native-bridge/host/**`, and to be listed by `native-bridge/AGENTS.md`; the layout scenarios cover multi-pattern and out-of-scope lists.

## Impact

- Renamed: `.agents/rules/macos-test-app-tcc.md` → `.agents/rules/native-bridge-test-app-tcc.md` (content unchanged apart from frontmatter).
- Edited: `native-bridge/AGENTS.md` (`## Rules`), `native-bridge/installer/macos/AGENTS.md` (pointer and `## Rules`), `native-bridge/host/AGENTS.md` (platform-frontends pointer).
- Check and documentation: `docs/checks/check_agent_files.py`, its tests, and `docs/AGENTS.md` accept and describe identical multi-pattern lists under a rule's scope.
- Synced at closeout: `openspec/specs/agent-guidance-layout/spec.md`.
- Checks: `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`, `openspec validate --all`, and a repository grep that no live file still names the old rule path.
- Unchanged: the agent-file check's scope table, the generated skills, and every macOS test-run instruction.
