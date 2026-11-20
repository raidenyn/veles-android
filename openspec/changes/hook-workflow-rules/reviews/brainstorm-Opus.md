# Review record: brainstorm

- Artifact: brainstorm
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: [Opus] (`opus`)
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none (brainstorm is the first planning file)

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | F1: the moves conflict with main specs (`real-device-testing`, `agent-guidance-layout`, `superspec-closeout`, `superspec-review-gate`) and the stop gate changes `hooked-rule-delivery`; none named. | Fixed: section "Specs and files the move touches". |
| high | F2: live references to the moved paths (config, schema, verify template, docs) not listed. | Fixed: listed in the same section. |
| high | F3: the stop predicate does not say which checkout it reads or how a session is tied to a change. | Fixed: session change set from `--change <name>`; event checkout plus linked worktrees. |
| high | F4: real-device testing covers all work but its moments are workflow-only. | Fixed: `gh pr create` gate; pointers state the rule; paragraph "Rules that apply outside the workflow". |
| medium | F5: "one reminder" contradicts once per generation. | Fixed: once per session and apply iteration, surviving compaction. |
| medium | F6: no exception for stops other rules require. | Fixed: the reminder text allows the stop and says the next stop passes. |
| medium | F7: root cause asserted. | Fixed: reworded; always-on text loses at the stop; the stop gate and pointer carry the load. |
| medium | F8: no byte arithmetic. | Fixed: "Delivery budget" section. |
| medium | F9: review gate missing at archive; brainstorm moment unclear. | Fixed: archive skill moment; `openspec instructions brainstorm`. |
| medium | F10: 40-line cap left open. | Fixed: "Root AGENTS.md line budget" (net 40). |
| low | F11: stop fail-open unstated. | Fixed: Key Decision. |
| low | F12: OpenCode child sessions; guardrail reaction. | Fixed: Open Questions for the spike and verification. |

### Round 2 — approve

Reviewed content sha256 3a72897395ee36fe70aa8b226b2dedd0b42cda286e86c3371b9934d8eccbdf0d

| Severity | Finding | Disposition |
|---|---|---|
| medium | `agent-guidance-layout` delta list incomplete. | Fixed. |
| medium | Archived change and stale worktree copies not covered by the predicate. | Fixed: an archived copy counts as finalized. |
| low | "Newest apply.md" undefined. | Fixed: highest `Iteration:`, checkout order on a tie. |
| low | Session change-set key unstated. | Fixed: session-wide. |
| low | Finalize's own push denied once by the pre-push gate. | Fixed: `openspec instructions finalize` moment for pre-push. |
| low | Stop fail-open channel left open. | Deferred to design: the design picks the `Stop` output field. |
| low | Missing live references (`docs/dev/specs/real-device-testing.mdx`, `docs/superspec-workflow.md:194`, `docs/hook-delivered-rules.md`, `.claude/settings.json`, the hook tuple in the check's tests). | Deferred to design: the design's file list carries them. |
| low | Three kept-rule sizes are file bytes, not body bytes. | Deferred: informational only for kept rules; the design states body bytes. |

### Round 3 — changes-required

A normative edit after round 2 (Design Summary item 6, moment de-duplication, prompted by the re-delivery
observed in this session) reopened the pass.

| Severity | Finding | Disposition |
|---|---|---|
| high | Blanket de-duplication makes moments one-shot and recreates the stale-text stall. | Fixed: de-duplicate per trigger. |
| medium | Pending entries would suppress moments. | Fixed: only `delivered` entries suppress. |
| medium | Stale delivery residual widens. | Fixed: same-trigger only; spec and doc text amended; E2E adds the case. |
| medium | Delta list and decision record incomplete. | Fixed: Key Decision lists the Moments requirement, "Cap at a moment" and Stale credit. |
| low | Stop block vs delivery state undefined. | Fixed: a stop block is its own record. |

### Round 4 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| medium | The worked example (`openspec instructions apply`) triggers nothing. | Fixed. |
| medium | No state migration plan. | Fixed: state version 2. |
| low | Skill trigger identity undefined. | Fixed: normalised `skills` entry. |
| low | Re-delivery update semantics. | Fixed: separate `triggers` field; additive. |
| low | Budget check cannot enumerate regex commands. | Fixed: skill names plus a fixed command list plus the stop text; runtime cap is the backstop. |

### Round 5 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | Item 6 names `openspec instructions finalize` both as a skipped repeat and as a different moment. | Fixed (cosmetic): "after the apply skill, `openspec instructions finalize`". |

Approved content: brainstorm sha256 811587c8db4ea87eb06fb71c543e368b9e44df3f2ee9b1c82182519c896315ec — re-pointed after one cosmetic fix (the round 5 low finding)

### Non-triggering upstream fixes from the design review

The design review (round 2, finding M1, medium) asked that the brainstorm match the design's fixes. Five minor
fixes: Design Summary item 1 ("once per phase"); the once-per-iteration decision becomes once per phase; the stop
block records the once-per-phase entry; the skill trigger maps `opsx:<x>` through a fixed table; the worktree
search includes the main working tree.

Approved content: brainstorm sha256 5dcf925bdc99c3193f935ae86b0ccac32a151296b3fc42d6ef7d8600248e4e41 — re-pointed from 811587c8db4ea87eb06fb71c543e368b9e44df3f2ee9b1c82182519c896315ec after five non-triggering minor fixes (design review M1)

## Pass 2

A triggering edit after the cycle: the specs review (round 2, medium) showed that honouring Claude Code's
`stop_hook_active` would let the post-verify stall pass in a continued turn; the once-per-phase decision now says
the per-phase record, not the flag, bounds the gate. This was a sixth minor fix since the last completed review,
so it reopened review.

### Round 1 — approve

Combined consistency review of brainstorm, design and specs.

| Severity | Finding | Disposition |
|---|---|---|
| low | "The once-per-iteration record" left stale in the fail-open decision. | Fixed (cosmetic). |

Approved content: brainstorm sha256 723f9a98311b928eba1f7fc9df6279d19adbb8dcc7b8a5c6ab59589837df5276 — re-pointed after one cosmetic fix (the round 1 low finding)

Approved content: brainstorm sha256 b62296bb5de9aeb9c3029e7326b6550ade0baec69505af32d54d8fe63c31185d — re-pointed after a non-triggering minor fix from the plan review (round 2, medium 1: the archive directory name is anchored to <YYYY-MM-DD>-<name>)
