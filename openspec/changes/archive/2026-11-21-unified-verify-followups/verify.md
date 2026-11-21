# Verification Report

**Change**: `unified-verify-followups`
**Verified at**: `2026-11-21 09:45`
**Iteration**: `1`
**Verifier**: `Opus whole-branch review + scoped fix re-review + controller audit`

---

## 1. Structural Validation (`openspec validate --all --json`)

- [x] All items `"valid": true`

**Result**:

```text
items 34, passed 34, failed 0
```

The change keeps its four pre-existing "Requirement text is very long" warnings, which are not
errors.

---

## 2. Task Completion (`tasks.md`)

- [x] All `- [ ]` have been changed to `- [x]` (20 of 20)

**Incomplete tasks** (if any):

| Task | Reason incomplete | Blocks archive? |
|---|---|---|
| — | — | — |

---

## 3. Delta Spec Sync State

| Capability | Sync status | Notes |
|---|---|---|
| `affected-node-verification` | ✗ pending sync | The delta adds the branch pass and replaces the two drift directions; the closeout syncs once, when checks are green. |
| `agent-guidance-layout` | ✗ pending sync | The delta removes the lint rule and narrows node guidance; sync at closeout. |
| `hooked-rule-delivery` | ✗ pending sync | The delta adds proof-requiring gates and modifies the frontmatter/gates/pre-push requirements; sync at closeout. |
| `verification-stop-hook` | ✗ pending sync | The delta adds the OpenCode turn-end verification and modifies the hook/limits requirements; sync at closeout. |

---

## 4. Design / Specs Coherence Spot Check

| Sample item | design description | specs counterpart | Gap |
|---|---|---|---|
| D1 one-way guidance | docs hold only commands the verifier does not run; one duplicate check | `affected-node-verification` "One command table" and "The table cannot drift from the docs"; `agent-guidance-layout` "One AGENTS.md per node and platform folder" | none |
| D2 branch pass | one module owns the version-1 record; a complete run writes it; every `--branch` run deletes it after the lock | `affected-node-verification` "A complete branch run writes a branch pass" | none |
| D3 proof gates | `requires`/`exempt`, per-segment gates, proof registry, 3.5 s deadline, repeat deny | `hooked-rule-delivery` "Proof-requiring gates", "Pre-push rule triggers", frontmatter and gates requirements | none |
| D4 OpenCode host | `--hook opencode`, host in the child, `<host>:<session id>`, version 2, SIGTERM stops the child | `verification-stop-hook` "The hook runs the active checkout's verifier" and "Hook output contract" | none |
| D5 plugin slot | separate per-session verifier slot, 30-minute budget, SIGTERM then SIGKILL, dispose | `verification-stop-hook` "OpenCode turn-end verification" | none |
| D6 docs | tables/docs describe the new behaviour and limits | `verification-stop-hook` "Documented known limits"; `docs/verify.md`, `docs/opencode-rules.md`, `docs/hook-delivered-rules.md` | none |

**Drift warnings** (non-blocking):

- Design D3's parenthetical "so its moments do not re-deliver" is not met by the state model (the
  delivery entry carries no triggers). The normative spec requires only "recorded as delivered",
  which holds; `docs/hook-delivered-rules.md` now says the moments may deliver it again.
- The Claude real-session linked-worktree move is not exercised: C3 records "hook dispatch PASS;
  real-session move UNVERIFIED" (the OpenCode real-session move, O3, passed).
- The option-form push exemption's value-option lookbehind is whitespace-sensitive (two spaces or a
  tab) and does not name `--exec`. The gate is documented as a cooperative guardrail; the normal
  forms are gated.

---

## 5. Implementation Signal

- [x] No unstaged files in the worktree (after committing this report and `apply.md`)
- [ ] All related commits have been pushed — **not yet**: finalize pushes this branch and creates the
  PR as the canonical next step; the branch was pushed through the new gate at `13ab5614` and needs
  one fresh pass for the final HEAD.

**Commit range** (if known): `043e9aa6..6f00ae7a` before this receipt; the final push includes the
receipt commit.

---

## 6. Review Records (review gate)

| Artifact | Reviewer | Hash state | Open critical or high findings |
|---|---|---|---|
| brainstorm | Opus | current | none |
| proposal | Opus | current | none |
| design | Opus | current | none |
| specs (4 files) | Opus | current | none |
| tasks | Opus | current | none |
| plan | Opus | current | none |

- [x] Every planning file has an approved record from every reviewer the record header lists
- [x] Every recorded hash — the file's own and each upstream file's — matches the file on disk
  (controller audit recomputed all ten; tasks.md checkboxes normalize under the documented hash)
- [x] Every changed hash carries the note for its non-triggering edit
- [x] The recorded upstream set is the full upstream set
- [x] No critical or high finding is open

**Failed items** (FAIL if any):

| Artifact | Failure |
|---|---|
| — | — |

---

## Overall Decision

- [x] ⚠️ PASS WITH WARNINGS — can proceed but note: the Claude real-session worktree move is
  UNVERIFIED (hook dispatch and the OpenCode real-session move passed), design D3's parenthetical is
  corrected in the docs, and the exemption pattern has a whitespace/`--exec` residual documented as
  a cooperative-guardrail limit.

**Next step**:

Run the finalize artifact: commit this report and `apply.md`, run one fresh `python3 -I
scripts/verify.py --branch` on the final committed HEAD, push the branch through the gate, create the
PR (none exists), state the verification evidence and the UNVERIFIED sub-case, then hand off to the
closeout for one sync and archive when checks are green.
