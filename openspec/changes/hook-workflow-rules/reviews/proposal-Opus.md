# Review record: proposal

- Artifact: proposal
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: [Opus] (`opus`)
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm sha256 5dcf925bdc99c3193f935ae86b0ccac32a151296b3fc42d6ef7d8600248e4e41; design sha256 c5f65ba8efdcb092d361780f7327cc93b5595ffa50be3c64ee5dea41f4ac31f0

## Pass 1

### Round 1 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | The pointer facts (closeout authorization) have no spec home. | Fixed: the pointer-block requirement is named in the `hooked-rule-delivery` entry. |
| medium | The single-source-of-models ban must extend to `.agents/hooked-rules/`. | Fixed. |
| low | Always-on wording in the review-gate audit scenario and the layout procedure-pointer requirements. | Fixed: blanket sentence plus named items. |
| low | Only OpenCode named under "owner decides"; design spikes both. | Fixed. |
| low | "Cap at a moment" wording missing from the delta list. | Fixed. |
| low | `docs/AGENTS.md` not named in Impact. | Deferred: it names no moved path; the plan checks it. |

Approved content: proposal sha256 0bc580be089cc517daf25d7bf2f4a3304c99a58393434be89dc284129d08ee43 — re-pointed after five non-triggering minor fixes (the first five findings above)

## Consistency check after triggering upstream edits

The brainstorm and design passes 2 (the `stop_hook_active` decision, the finalize-failure stop reason, two apply
entries). The reviewer judged the proposal consistent with no finding (in the tasks review, round 1).

Upstream hashes for this check: brainstorm sha256 723f9a98311b928eba1f7fc9df6279d19adbb8dcc7b8a5c6ab59589837df5276; design sha256 637c44d3a14899e9a840f92ecc6b244b9d63bc1e57c909d737566cd11dc67ec0

Approved content: proposal sha256 0bc580be089cc517daf25d7bf2f4a3304c99a58393434be89dc284129d08ee43

Upstream re-point: brainstorm sha256 b62296bb5de9aeb9c3029e7326b6550ade0baec69505af32d54d8fe63c31185d; design sha256 ca3761903ad863813d9ca9c239f314e4e070cd8414d5eee90dd963db850fbb9d — re-pointed after a non-triggering minor fix from the plan review (round 2, medium 1: the archive directory name is anchored to <YYYY-MM-DD>-<name>) (no proposal text changed)

Approved content: proposal sha256 0bc580be089cc517daf25d7bf2f4a3304c99a58393434be89dc284129d08ee43

## Pass — turn-end amendment (2026-11-20)

Owner-approved normative change: OpenCode 2.3.2 never emits `session.idle`; the stop gate uses
`session.execution.succeeded` (`spike.md` §2A). Amendment commits `654bf8f1` and `2cd8cfa4`.

Upstream hashes for this pass: brainstorm sha256 5039765682f6a99acc07db91825def99937d9ae65de38cb7846896775cdfdabd; design sha256 0ca6076e0be0a98b98fbef9a89bb5ebe39a157201a267302de9d71c1977704da

### Round 1 — approve

No finding: the two bullets that name the OpenCode event now say `session.execution.succeeded`, and no
proposal text was changed by the fix round.

Approved content: proposal sha256 7b49970f3a2828fea7be10679a7476652c2bbe5e6cac42b0a3a49f72440a3bb2
