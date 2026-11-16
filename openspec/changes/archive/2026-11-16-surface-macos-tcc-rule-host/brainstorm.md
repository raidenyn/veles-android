## Design Summary

Make the macOS test-app TCC rule load automatically for `native-bridge/host/` work without loading
for unrelated siblings such as the Windows installer. The rule is renamed
`native-bridge-test-app-tcc.md` (its narrowest common parent scope), its `globs`/`paths` list exactly
the two consuming folders (`native-bridge/installer/macos/**` and `native-bridge/host/**`), and the
agent-file check is extended to accept a rule's exact consuming patterns instead of only the single
scope pattern. The listing moves to `native-bridge/AGENTS.md`, the host node drops its by-hand read
instruction, and the `agent-guidance-layout` spec is amended for both the rule layout and the
multi-node case.

## Alternatives Considered

### Alternative A: Two exact patterns under the parent scope, with a small check extension
- **Approach**: Rename the rule `native-bridge-test-app-tcc.md`, list
  `native-bridge/installer/macos/**` and `native-bridge/host/**` in `globs`/`paths`, and extend
  `check_agent_files.py` to accept identical lists of patterns that each sit under the rule's scope
  folder. List the rule in `native-bridge/AGENTS.md`.
- **Pros**: One file, one source of truth, no by-hand read, no load for the Windows installer or the
  Tauri scaffold, and the filename still names the narrowest common parent for the listing.
- **Cons**: The check, its tests and the layout spec must be amended; the one-pattern invariant
  becomes "patterns inside the scope".
- **Why not chosen**: Chosen.

### Alternative B: Copy the rule into a second `host-*` file
- **Approach**: Keep the macOS rule and add a host-scoped copy with the same content.
- **Pros**: Both scopes load independently and each node lists its own file; no check change.
- **Cons**: Two copies of the identity, designated requirement and launch rules drift; the
  repository's layout rule is one concern in one file.
- **Why not chosen**: Duplicated normative content is the failure this layout rule exists to prevent.

### Alternative C: Re-scope the rule to the parent `native-bridge/**`
- **Approach**: Rename the rule `native-bridge-test-app-tcc.md` and set `globs`/`paths` to
  `native-bridge/**`.
- **Pros**: No check change; parent scopes are already cumulative.
- **Cons**: It loads the macOS-only rule for `native-bridge/installer/windows/**`, the Tauri scaffold
  and every other `native-bridge/` path. Review feedback rejected this over-triggering.
- **Why not chosen**: It is less precise than the rule's actual consumers.

### Alternative D: Make the rule always on (`repo-*`)
- **Approach**: Promote the content to an always-on repo rule.
- **Pros**: Loads in every node with no scoping concern.
- **Cons**: The rule is macOS-specific, and `repo-*` rules are capped at 4,096 bytes of always-on
  context; the current rule is about 7 KB.
- **Why not chosen**: It would consume every session's context and breach the always-on cap.

## Agreed Approach

Alternative A. The rule keeps its normative content verbatim and moves to the `native-bridge` parent
scope, but its `globs`/`paths` name only the two nodes that consume it. The agent-file check accepts a
non-empty list of patterns under the scope folder (identical `globs` and `paths`), so the filename
still selects the `AGENTS.md` that lists the rule and the list controls delivery. `docs/AGENTS.md`
and the `agent-guidance-layout` spec (the rule-layout requirement and the canonical-folder
requirement) describe the multi-node case.

## Key Decisions

- Rename to `.agents/rules/native-bridge-test-app-tcc.md`; the `native-bridge-` prefix is the longest
  scope prefix, so the scope is `native-bridge`.
- Frontmatter lists exactly `native-bridge/installer/macos/**` and `native-bridge/host/**` in both
  `globs` and `paths`.
- `check_agent_files.py` accepts one or more patterns per key, each under the scope folder, and
  requires `globs` and `paths` to match; brace expansion, non-lists and out-of-scope patterns still
  fail, with tests for the multi-pattern and out-of-scope cases.
- `native-bridge/AGENTS.md` lists the rule under `## Rules`; the macOS node keeps a prose pointer and
  removes the rule from its own `## Rules` list. The listing is required by the check and is the
  discoverability fallback when the scoped-rule plugin is unavailable; it is not the delivery
  mechanism.
- `native-bridge/host/AGENTS.md` keeps the pointer to `native-bridge/installer/macos/AGENTS.md` and
  names the rule without the by-hand instruction.
- No change to the rule's normative content, the scope table or the generated skills.

## Open Questions

None.
