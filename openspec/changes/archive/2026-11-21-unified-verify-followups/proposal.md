## Why

The verifier (`scripts/verify.py`) was meant to be the one deterministic gate, but its commands are still
copied into every node `AGENTS.md` and into `.agents/rules/android-lint-before-push.md`, and a drift check
forces that copy. This grows agent context for no gain. The push is guarded only by a rule asking the agent to
run the branch gate, and OpenCode, where most coding sessions run, has no Stop-time verification at all. This
change makes the verifier the single source, enforces the push with proof, and gives OpenCode the same hooks.

## What Changes

**Node guidance and the command table**
- From: every command and producer of `scripts/verify-commands.json` names a `doc`, every `excluded` line names
  one, and the drift check requires each documented command to be in the table (or `excluded`) and each entry
  to be documented.
- To: node `AGENTS.md` files keep only commands the verifier does not run (build, release, device, platform,
  producers and targeted lines such as `--tests "<class>"`). The `doc` field and the `excluded` list are removed;
  the drift check now fails when a node doc, or a rule it lists, documents a `check`/`test` entry.
- Reason: one source of truth; less context. Impact: tooling and docs only.

**The Android lint rule**
- From: `.agents/rules/android-lint-before-push.md` repeats the Android lint and source-guard commands, and
  `android/`, `rust/` and `protocol/` `AGENTS.md` point to it.
- To: the rule and every pointer to it are deleted; the verifier's `android` node runs those checks.

**The push gate**
- From: `.agents/hooked-rules/repo-pre-push-checks.md` gates `git push` only until the rule has been delivered,
  and tells the agent to run checks by hand where the verifier cannot run.
- To: a complete `verify.py --branch` run (exit 0, no `--deadline`, no `--base`, no step cut off, a clean tree
  with the same HEAD at start and end) writes a branch-pass record bound to HEAD and the merge base. The
  shared hooked-rules engine supports rules whose gates require a named proof; the pre-push rule requires
  `branch-pass`, so `git push` is denied until a pass for the current commit exists. Ref-deletion pushes pass.
  The by-hand fallback is removed.
- Impact: Claude Code and OpenCode alike, through the existing engine adapters.

**OpenCode Stop-time verification**
- From: only Claude Code's `Stop` hook runs `verify.py --hook claude`; `docs/opencode-rules.md` documents the gap.
- To: `verify.py --hook opencode` exists, and the OpenCode plugin runs it at a root session's turn end with a
  long budget, resuming the session with the block summary, even when the user has sent a new message since.
  A notice (contention, limits reached, an incomplete run) is shown as a message that does not resume the session. How OpenCode
  handles a resume while the session is busy is not yet observed; a spike records it first, and one real
  OpenCode turn joins Claude Code's in the real-session acceptance.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `affected-node-verification`: the table loses `doc` and `excluded`; the drift requirement is inverted; a
  complete branch run writes a branch-pass record.
- `agent-guidance-layout`: the Android lint rule and its pointers are removed; node docs must not list verifier
  commands; the pre-push rule's by-hand fallback is removed.
- `hooked-rule-delivery`: gates may require a named proof; the pre-push rule requires `branch-pass` and its
  delivery-once semantics change accordingly; the OpenCode adapter's stop handling also runs the verifier.
- `verification-stop-hook`: the hook has an OpenCode host (`--hook opencode`) with the same block contract,
  admission and bounds; notices become non-resuming messages; real-session acceptance covers an OpenCode turn.

## Impact

- Code: `scripts/verify.py`, `scripts/verify-commands.json`, `scripts/agent_rules.py`, a small shared
  branch-pass module under `scripts/`, `scripts/opencode-hooked-rules/index.js`, `docs/checks/verify_table.py`,
  `docs/checks/check_agent_files.py`, and their tests.
- Docs and guidance: `android/`, `rust/`, `protocol/` and other node `AGENTS.md` files, the root pointer block,
  `.agents/hooked-rules/repo-pre-push-checks.md`, `docs/verify.md`, `docs/opencode-rules.md`.
- Removed: `.agents/rules/android-lint-before-push.md`.
- Accepted limits: a push from a plain terminal is not gated; there is no override flag, so a machine where
  `--branch` reports an unexpected `NOT RUN` cannot push until provisioned; the per-line reasons of the removed
  `excluded` entries that matter move into `docs/verify.md`; a turn-end hook run during a background branch run
  reports contention and checks nothing.
- No product behaviour changes (no Android app, extension, Rust core, protocol or native-bridge code). CI jobs
  are unchanged.
