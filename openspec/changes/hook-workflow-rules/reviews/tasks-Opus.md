# Review record: tasks

- Artifact: tasks
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: [Opus] (`opus`)
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm sha256 723f9a98311b928eba1f7fc9df6279d19adbb8dcc7b8a5c6ab59589837df5276; design sha256 637c44d3a14899e9a840f92ecc6b244b9d63bc1e57c909d737566cd11dc67ec0; proposal sha256 0bc580be089cc517daf25d7bf2f4a3304c99a58393434be89dc284129d08ee43; specs/agent-guidance-layout/spec.md sha256 3f7a81bb1aea06018990e639633a701fe4c8a44bc2f468769ff4cdd2ded772df; specs/hooked-rule-delivery/spec.md sha256 7e4970a013b9b42ddc4bcf6ec83abd2278141b830a23bd7cc7035256b7f9a43f; specs/real-device-testing/spec.md sha256 b8716a68bb192a8e770570ee1931679798a6db6cdb45bf3fa389b66e828f721a; specs/superspec-closeout/spec.md sha256 ee5e4641db075e9ee0515f294ed2d100e874e2e694f477f63ab8370772b9a993; specs/superspec-review-gate/spec.md sha256 881683095bb0707a09580191479b7913c2b3fcb4a2cc5277a79d56634d01bd33

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | H1: the old-path grep cannot pass while main specs keep old paths until sync. | Fixed: 7.5 excludes `openspec/changes/` and `openspec/specs/`. |
| medium | M1: no-wall-clock rule not restated. | Fixed: header and 3.6, 4.2. |
| medium | M2: checks before the move. | Fixed: move is section 5, checks section 6. |
| medium | M3: 3.3 too big. | Fixed: split into 3.3–3.6. |
| medium | M4: 8.2 too big. | Fixed: 8.2–8.5 with stop-and-ask. |
| medium | M5: tool versions documented before the live run. | Fixed: 8.6. |
| low | Gaps: re-arm, plain block entry, block not delivering, stale-credit test, greps, `spike.md` location, site pages, hook list. | Fixed. |
| low | Quoted forms and long names; bare repo; `wc -c`. | Fixed. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | L1: the real-device site page only imports the spec. | Fixed: "no edit expected". |
| low | L2: name the `MOVED_RULES` map. | Fixed. |
| low | L3: predicate edge cases. | Fixed. |
| low | L4: do not push between sections 5 and 6. | Fixed. |
| low | L5: plugin does nothing at idle on engine failure. | Fixed. |

Approved content: tasks sha256 7577f63696e85a3079f383404ff49e93a096f250bcbbc88644a37c53003bc275 — re-pointed after five non-triggering minor fixes (L1-L5)

Upstream re-point: brainstorm sha256 b62296bb5de9aeb9c3029e7326b6550ade0baec69505af32d54d8fe63c31185d; design sha256 ca3761903ad863813d9ca9c239f314e4e070cd8414d5eee90dd963db850fbb9d; specs/hooked-rule-delivery/spec.md sha256 bf61182c3825dff69875c03e40bae04986c91f005f8efd0e80b61cd386877f17 — re-pointed after a non-triggering minor fix from the plan review (round 2, medium 1: the archive directory name is anchored to <YYYY-MM-DD>-<name>) (no tasks text changed)

Approved content: tasks sha256 7577f63696e85a3079f383404ff49e93a096f250bcbbc88644a37c53003bc275

## Pass — turn-end amendment (2026-11-20)

Owner-approved normative change: OpenCode 2.3.2 never emits `session.idle`; the stop gate uses
`session.execution.succeeded` (`spike.md` §2A). Amendment commits `654bf8f1` and `2cd8cfa4`.

Upstream hashes for this pass: brainstorm sha256 5039765682f6a99acc07db91825def99937d9ae65de38cb7846896775cdfdabd; design sha256 0ca6076e0be0a98b98fbef9a89bb5ebe39a157201a267302de9d71c1977704da; proposal sha256 7b49970f3a2828fea7be10679a7476652c2bbe5e6cac42b0a3a49f72440a3bb2; specs/agent-guidance-layout/spec.md sha256 3f7a81bb1aea06018990e639633a701fe4c8a44bc2f468769ff4cdd2ded772df; specs/hooked-rule-delivery/spec.md sha256 00af153571247080ce2e25317843ac53a1db07eba563bb4314052744c816be4e; specs/real-device-testing/spec.md sha256 b8716a68bb192a8e770570ee1931679798a6db6cdb45bf3fa389b66e828f721a; specs/superspec-closeout/spec.md sha256 ee5e4641db075e9ee0515f294ed2d100e874e2e694f477f63ab8370772b9a993; specs/superspec-review-gate/spec.md sha256 881683095bb0707a09580191479b7913c2b3fcb4a2cc5277a79d56634d01bd33

### Round 1 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | M1: the checked spike items said "clone" and "press Esc", which the spike did not do. | Fixed: "disposable repo", headless SIGINT, Esc noted unverified. |
| medium | M2: the plugin's `parentID` source and its fail-open were unstated. | Fixed: 4.2 names both. |
| medium | M3: the final OpenCode run lacked an interactive abort case. | Fixed: 8.4 adds it. |

Approved content: tasks sha256 58e8ebe7103c182e550163fedd4305fdd0503e6765b8a1dac284d782c96e4ff8
