# Surface the macOS TCC rule under native-bridge/host Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this plan
> task-by-task where a subagent is warranted; this change is small enough for one agent.

**Goal:** Re-scope the macOS test-app TCC rule to `native-bridge/**` so it loads automatically for
`native-bridge/host/` work, and update the node guidance and layout spec around the new scope.

**Architecture:** One file rename plus frontmatter change, three `AGENTS.md` pointers and a delta spec.
No rule content, check code or generated skill changes.

**Tech Stack:** Markdown, YAML frontmatter, the repository's stdlib Python agent-file check and OpenSpec.

**Spec:** `openspec/changes/surface-macos-tcc-rule-host/specs/agent-guidance-layout/spec.md`.

## Global Constraints

- Frontmatter `globs` and `paths` MUST each be exactly `["native-bridge/**"]`, matching the
  `native-bridge` scope in `docs/checks/check_agent_files.py`; no brace expansion, no second pattern.
- The rule's normative text (identity, designated requirement, launch path, grant reuse) is unchanged.
- `native-bridge/installer/macos/AGENTS.md` stays the canonical home of the macOS signing identity.
- The root `AGENTS.md` gains no macOS content.
- No file under `.agents/skills/` or `.claude/skills/` changes.
- One commit: `docs(agents): deliver the macOS TCC rule under host`.

---

### Task 1: Rename and re-scope the rule

**Files:**
- Rename: `.agents/rules/macos-test-app-tcc.md` → `.agents/rules/native-bridge-test-app-tcc.md`
- Test: `python3 docs/checks/check_agent_files.py --root .`

- [x] **Step 1: Confirm the old scope is present (red check)**

```bash
grep -n 'native-bridge/installer/macos/\*\*' .agents/rules/macos-test-app-tcc.md
```

Expected: two hits, the `globs` and `paths` entries.

- [x] **Step 2: Rename the file**

```bash
git mv .agents/rules/macos-test-app-tcc.md .agents/rules/native-bridge-test-app-tcc.md
```

- [x] **Step 3: Replace the frontmatter patterns**

Set both lists to the single element `"native-bridge/**"`, keeping the `trigger: glob` line and the
body untouched.

- [x] **Step 4: Check the file (green)**

```bash
sed -n '1,8p' .agents/rules/native-bridge-test-app-tcc.md
```

Expected: `trigger: glob`, then `globs:` and `paths:` each with only `native-bridge/**`.

---

### Task 2: Update the node guidance

**Files:**
- Modify: `native-bridge/AGENTS.md` (`## Rules`)
- Modify: `native-bridge/installer/macos/AGENTS.md` (prose pointer and `## Rules`)
- Modify: `native-bridge/host/AGENTS.md` (platform frontends)

- [x] **Step 1: List the rule in the parent node**

In `native-bridge/AGENTS.md`, replace `None.` under `## Rules` with a bullet naming
`.agents/rules/native-bridge-test-app-tcc.md` and its subject (one fixed macOS test-app identity and
the reusable Bluetooth grant).

- [x] **Step 2: Point the macOS node at the renamed rule**

Update the prose sentence in `native-bridge/installer/macos/AGENTS.md` to
`.agents/rules/native-bridge-test-app-tcc.md`, and remove the now parent-scoped bullet from its
`## Rules` section so that section holds no entry (`None.`).

- [x] **Step 3: Drop the by-hand claim in the host node**

In `native-bridge/host/AGENTS.md`, keep the pointer to `native-bridge/installer/macos/AGENTS.md`, name
the new rule path, and state that it applies to the host node and loads from the repository root; remove
the sentence that the macOS scope does not deliver it under `native-bridge/host/`.

- [x] **Step 4: Check for stale references (green)**

```bash
grep -rn "macos-test-app-tcc" --exclude-dir=.git . | grep -v '^./openspec/changes/archive/'
grep -rn "does not deliver it" native-bridge
```

Expected: the first prints only the not-yet-synced main spec
`openspec/specs/agent-guidance-layout/spec.md` (the closeout sync updates it); the second prints nothing.

---

### Task 3: Verify and commit

**Files:** none expected.

- [x] **Step 1: Run the agent-file check**

```bash
python3 docs/checks/check_agent_files.py --root .
```

Expected: exit 0, warnings allowed.

- [x] **Step 2: Run the docs unit tests**

```bash
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

Expected: all tests pass.

- [x] **Step 3: Validate OpenSpec**

```bash
openspec validate --all
```

Expected: every item valid, including the new change and its delta spec.

- [x] **Step 4: Inspect the change**

```bash
git status --short
git diff HEAD --stat
```

Expected: the rename, the three `AGENTS.md` files, and the change directory only.

- [x] **Step 5: Make the single commit**

```bash
git add -A .agents/rules native-bridge/AGENTS.md native-bridge/installer/macos/AGENTS.md \
  native-bridge/host/AGENTS.md openspec/changes/surface-macos-tcc-rule-host
git commit -m "docs(agents): deliver the macOS TCC rule under host"
git status --short
```

Expected: one commit and a clean tree.
