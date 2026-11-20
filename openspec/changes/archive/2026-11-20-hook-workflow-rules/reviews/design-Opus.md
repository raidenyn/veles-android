# Review record: design

- Artifact: design
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: [Opus] (`opus`)
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm sha256 5dcf925bdc99c3193f935ae86b0ccac32a151296b3fc42d6ef7d8600248e4e41 (re-pointed from 811587c8db4ea87eb06fb71c543e368b9e44df3f2ee9b1c82182519c896315ec: five non-triggering fixes requested by this review's M1)

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | F1: a FAIL from an earlier iteration hides a re-apply. | Fixed: FAIL counts only at the same iteration; test added. |
| medium | F2: the reminder is spent before the post-verify stall. | Fixed: stop record keyed by phase. |
| medium | F3: `opsx:` normalisation does not make one trigger. | Fixed: fixed mapping table. |
| medium | F4: OpenCode idle after an abort or error. | Fixed: excluded; spike checks it. |
| medium | F5: main working tree not searched. | Fixed. |
| low | F6: change names from any command. | Fixed: only `openspec` commands. |
| low | F7: stop records and `void`. | Fixed: the block carries the stop entry. |
| low | F8: fail-open silent. | Fixed: `systemMessage` and stderr. |
| low | F9: bounds ambiguous. | Fixed. |
| low | F10: spec deltas not listed. | Fixed: §9. |
| low | F11: escape hatch leaves a change pending. | Fixed: stated residual. |

### Round 2 — approve

Reviewed content: the design after the round 1 fixes.

| Severity | Finding | Disposition |
|---|---|---|
| medium | M1: brainstorm and design disagree after F2; stale "once per iteration" text; stop record fields. | Fixed: phase keyed on iteration and verify state; Risks and §4 aligned; brainstorm amended (see its record). |
| low | L1: `void` of stop records unspecified. | Fixed. |
| low | L2: plugin must ignore a `context` failure response at idle; `block` only on `stop`. | Fixed. |
| low | L3: the table applies to every event form. | Fixed. |
| low | L4: change-name length unbounded. | Fixed: at most 64 characters. |
| nit | `opsx:sync` would map wrongly. | Deferred: no rule names that skill; the plan states the table covers only the skills the rules list. |
| nit | `--separate-git-dir` checkouts break the main-tree rule. | Deferred: the plan records it as an unhandled form. |

Approved content: design sha256 c5f65ba8efdcb092d361780f7327cc93b5595ffa50be3c64ee5dea41f4ac31f0 — re-pointed after five non-triggering minor fixes (M1, L1-L4)

## Pass 2

A triggering edit after the cycle, from the specs review: `stop_hook_active` no longer suppresses a block in a new
phase; the closeout lead paragraph adds "finalize cannot create or confirm the pull request".

Upstream hashes for this pass: brainstorm sha256 723f9a98311b928eba1f7fc9df6279d19adbb8dcc7b8a5c6ab59589837df5276

### Round 1 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | No live check shows Claude Code obeying a second block while `stop_hook_active` is true. | Fixed: §7 spike and spec outcome 7. |
| low | An undecided `verify.md` counted as `pass`. | Fixed: `pass` needs PASS or PASS WITH WARNINGS ticked. |
| low | §5 "gains" wording vs two separate entries. | Fixed. |
| low | Stop records unbounded. | Fixed: 40 newest, removed with the session file. |

Approved content: design sha256 637c44d3a14899e9a840f92ecc6b244b9d63bc1e57c909d737566cd11dc67ec0 — re-pointed after four non-triggering minor fixes (the round 1 findings)

Upstream re-point: brainstorm sha256 b62296bb5de9aeb9c3029e7326b6550ade0baec69505af32d54d8fe63c31185d — re-pointed after a non-triggering minor fix from the plan review (round 2, medium 1: the archive directory name is anchored to <YYYY-MM-DD>-<name>)

Approved content: design sha256 ca3761903ad863813d9ca9c239f314e4e070cd8414d5eee90dd963db850fbb9d — re-pointed after a non-triggering minor fix from the plan review (round 2, medium 1: the archive directory name is anchored to <YYYY-MM-DD>-<name>)

## Pass — turn-end amendment (2026-11-20)

Owner-approved normative change: OpenCode 2.3.2 never emits `session.idle`; the stop gate uses
`session.execution.succeeded` (`spike.md` §2A). Amendment commits `654bf8f1` and `2cd8cfa4`.

Upstream hashes for this pass: brainstorm sha256 5039765682f6a99acc07db91825def99937d9ae65de38cb7846896775cdfdabd

### Round 1 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | §3 names `parentID` but not its source (`session.get`) or the fail-open when that call fails. | Deferred: the plugin's general fail-open rule covers it; `docs/hook-delivered-rules.md` (Task 11) records the detail. |

Approved content: design sha256 0ca6076e0be0a98b98fbef9a89bb5ebe39a157201a267302de9d71c1977704da

## Pass — stop-gate arming amendment (2026-11-20)

Owner ruling: the reminder reaches only the session that will finalize. Commits 90340ef5, 2debe9d2, 982dc4ef.

Upstream hashes for this pass: brainstorm sha256 fc2565ee022c5c34ea26082455ce71569d3f6b67c735a8901ba49742b6908a79

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| medium | §10 live test 2 named the change without applying, contradicting the rule. | Fixed: the session invokes apply first. |
| medium | The risks line said the gate is armed by `--change` calls. | Fixed: armed by applying only. |
| low | The risks did not list apply-by-read-only being unarmed. | Fixed. |
| low | "Bound the reminder to two" needed "per apply iteration". | Fixed. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | The `decide` docstring called a `SKILL.md` read an apply-skill moment. | Fixed in d2b89339. |

Approved content: design sha256 8e6c6b32aeef0a8d8c1dc6daaf4d346308e2760d1d98f7d11450a5a7b4963ab2
