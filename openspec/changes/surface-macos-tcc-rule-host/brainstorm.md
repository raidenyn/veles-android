## Design Summary

Make the macOS test-app TCC rule load automatically for `native-bridge/host/` work. Today the rule is
scoped to `native-bridge/installer/macos/**`, so `native-bridge/host/AGENTS.md` has to tell the agent to
read it by hand before a macOS Bluetooth run. The chosen fix re-scopes the rule to the parent
`native-bridge/**` scope, renames it `native-bridge-test-app-tcc.md`, lists it in
`native-bridge/AGENTS.md`, and drops the by-hand instruction from the host node. The rule text itself is
unchanged; the `agent-guidance-layout` spec is amended to allow the parent scope for a rule two nodes
consume.

## Alternatives Considered

### Alternative A: Add `native-bridge/host/**` to the existing rule's globs
- **Approach**: Keep `macos-test-app-tcc.md` and list both `native-bridge/installer/macos/**` and
  `native-bridge/host/**` under `globs` and `paths`.
- **Pros**: Smallest textual diff in the rule file.
- **Cons**: `docs/checks/check_agent_files.py` derives a rule's scope from its filename and requires
  `globs` and `paths` to be exactly `[<scope pattern>]`; a second pattern fails the check. A `macos-*`
  file cannot legally carry the host pattern.
- **Why not chosen**: It cannot pass the repository's agent-file check.

### Alternative B: Copy the rule into a second `host-*` file
- **Approach**: Keep the macOS rule and add a host-scoped copy with the same content.
- **Pros**: Both scopes load independently and each node lists its own file.
- **Cons**: Two copies of the identity, designated requirement and launch rules drift; the repository's
  layout rule is one concern in one file.
- **Why not chosen**: Duplicated normative content is the failure this layout rule exists to prevent.

### Alternative C: Re-scope the rule to the parent `native-bridge/**`
- **Approach**: Rename the rule `native-bridge-test-app-tcc.md`, set `globs` and `paths` to
  `native-bridge/**`, list it in `native-bridge/AGENTS.md`, and keep the macOS node's prose pointer to
  the canonical identity. Parent scopes are cumulative, so both `native-bridge/installer/macos/` and
  `native-bridge/host/` receive the rule.
- **Pros**: One file, one source of truth, no by-hand read, and the agent-file check accepts the exact
  parent pattern. The macOS-only content simply does not apply under `installer/windows/` or the Tauri
  scaffold.
- **Cons**: The rule now also loads for other `native-bridge/` paths, where it is inert; the
  `agent-guidance-layout` spec's "macOS rule lives in the node" scenario must be amended.
- **Why not chosen**: Chosen.

### Alternative D: Make the rule always on (`repo-*`)
- **Approach**: Promote the content to an always-on repo rule.
- **Pros**: Loads in every node with no scoping concern.
- **Cons**: The rule is macOS-specific, and `repo-*` rules are capped at 4,096 bytes of always-on
  context; the current rule is about 7 KB.
- **Why not chosen**: It would consume every session's context and breach the always-on cap.

## Agreed Approach

Alternative C. The fix is a scope change, not a content change: the rule keeps its identity, designated
requirement, launch and grant-reuse instructions verbatim, moves to the parent scope, and is listed by
the parent node. `native-bridge/installer/macos/AGENTS.md` stays the canonical home of the signing
identity and points at the rule; `native-bridge/host/AGENTS.md` points at both and no longer claims the
rule is undelivered.

## Key Decisions

- Rename `.agents/rules/macos-test-app-tcc.md` to `.agents/rules/native-bridge-test-app-tcc.md`; the
  `native-bridge-` prefix is the longest scope prefix, so the scope is `native-bridge`.
- Frontmatter `globs` and `paths` are each exactly `["native-bridge/**"]`.
- `native-bridge/AGENTS.md` lists the rule under `## Rules`; the macOS node keeps a prose pointer and
  removes the rule from its own `## Rules` list.
- `native-bridge/host/AGENTS.md` keeps the pointer to `native-bridge/installer/macos/AGENTS.md` and names
  the rule without the "read it by hand" instruction.
- The `agent-guidance-layout` delta modifies the canonical-folder requirement: a macOS TCC rule consumed
  by more than one `native-bridge/` node is scoped to their narrowest common parent and listed by that
  scope's `AGENTS.md`; the macOS node still owns the identity facts.
- No change to the rule's normative content, the agent-file check, the scope table or the generated
  skills.

## Open Questions

None.
