# Review record: plan

- Artifact: plan
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: [Opus] (`opus`)
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm sha256 b62296bb5de9aeb9c3029e7326b6550ade0baec69505af32d54d8fe63c31185d; design sha256 ca3761903ad863813d9ca9c239f314e4e070cd8414d5eee90dd963db850fbb9d; proposal sha256 0bc580be089cc517daf25d7bf2f4a3304c99a58393434be89dc284129d08ee43; specs/agent-guidance-layout/spec.md sha256 3f7a81bb1aea06018990e639633a701fe4c8a44bc2f468769ff4cdd2ded772df; specs/hooked-rule-delivery/spec.md sha256 bf61182c3825dff69875c03e40bae04986c91f005f8efd0e80b61cd386877f17; specs/real-device-testing/spec.md sha256 b8716a68bb192a8e770570ee1931679798a6db6cdb45bf3fa389b66e828f721a; specs/superspec-closeout/spec.md sha256 ee5e4641db075e9ee0515f294ed2d100e874e2e694f477f63ab8370772b9a993; specs/superspec-review-gate/spec.md sha256 881683095bb0707a09580191479b7913c2b3fcb4a2cc5277a79d56634d01bd33; tasks sha256 7577f63696e85a3079f383404ff49e93a096f250bcbbc88644a37c53003bc275

## Pass 1

### Round 1 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | How the stop decision gets the checkout roots. | Fixed: `decide(..., roots=None)`, computed in `handle()`. |
| medium | No unit tests for the five real rules; regex escaping ambiguity. | Fixed: Task 9 Step 0 and an escaping note. |
| medium | Archive glob clears a suffix-named change. | Fixed: anchored name, negative test, Review Focus 6. |
| medium | Unreadable receipt: notice or silent. | Fixed: `StoreError`, fail open with the notice. |
| medium | Spike hook can loop. | Fixed: counter file and log. |
| medium | Idle handling can stall the OpenCode event stream. | Fixed: detached, flags cleared in `prompt()`. |
| medium | `decode` has no event kind. | Fixed: `decode(bytes, kind)`. |
| low | Eight lows (Event default, v1 test, emoji, budget matching, commit types, push note, path, grep scope, opsx:sync). | Fixed. |

More than five minor fixes: re-review.

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | Spec and design still used the unanchored archive glob. | Fixed in spec, design and brainstorm (records re-pointed with notes). |
| medium | An existing test asserts the old re-send. | Fixed: Task 3 Step 4 replaces it. |
| medium | OpenCode in-flight slot release unstated. | Fixed: freed at budget end; spike records resolve timing; test added. |
| low | Stop text with a 64-character name. | Fixed. |
| low | Non-boolean `stop_hook_active`. | Fixed: ignored. |
| low | `record_changes` before the early return. | Fixed. |
| low | `void` of a merged entry. | Fixed: test and doc. |
| low | Disposable clone for Task 12 Step 3. | Fixed. |
| low | `skills/apply/SKILL.md` trigger split. | Deferred: no such directory exists. |


### Round 3 — approve (re-review after more than five minor fixes)

| Severity | Finding | Disposition |
|---|---|---|
| low | Task 1 Step 4 omits the resolve timing Step 3 measures. | Fixed (cosmetic). |

Approved content: plan sha256 d649790393d7b431f145c694249782759b18e7a3bad6e11d19f36aa49289aa22 — re-pointed after one cosmetic fix (the round 3 low finding)

## Pass — turn-end amendment (2026-11-20)

Owner-approved normative change: OpenCode 2.3.2 never emits `session.idle`; the stop gate uses
`session.execution.succeeded` (`spike.md` §2A). Amendment commits `654bf8f1` and `2cd8cfa4`.

Upstream hashes for this pass: brainstorm sha256 5039765682f6a99acc07db91825def99937d9ae65de38cb7846896775cdfdabd; proposal sha256 7b49970f3a2828fea7be10679a7476652c2bbe5e6cac42b0a3a49f72440a3bb2; design sha256 0ca6076e0be0a98b98fbef9a89bb5ebe39a157201a267302de9d71c1977704da; specs/agent-guidance-layout/spec.md sha256 3f7a81bb1aea06018990e639633a701fe4c8a44bc2f468769ff4cdd2ded772df; specs/hooked-rule-delivery/spec.md sha256 00af153571247080ce2e25317843ac53a1db07eba563bb4314052744c816be4e; specs/real-device-testing/spec.md sha256 b8716a68bb192a8e770570ee1931679798a6db6cdb45bf3fa389b66e828f721a; specs/superspec-closeout/spec.md sha256 ee5e4641db075e9ee0515f294ed2d100e874e2e694f477f63ab8370772b9a993; specs/superspec-review-gate/spec.md sha256 881683095bb0707a09580191479b7913c2b3fcb4a2cc5277a79d56634d01bd33; tasks sha256 58e8ebe7103c182e550163fedd4305fdd0503e6765b8a1dac284d782c96e4ff8

### Round 1 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | M1: Task 1 Step 2 said "press Esc", which the spike did not do. | Fixed: headless SIGINT recorded; Esc unverified. |
| medium | M2: Task 8 did not say where `parentID` comes from or what a failed `session.get` does. | Fixed: named and fail-open; test added. |
| medium | M3: the final run lacked the interactive OpenCode abort. | Fixed in Task 12 Step 4. |
| low | L2: no test for a second success while one is in flight. | Fixed: test added. |

Approved content: plan sha256 3a73504bdc4167ae2620d44d8e8f7401c481120b7b1255590a1fe3dd531f6d4b
