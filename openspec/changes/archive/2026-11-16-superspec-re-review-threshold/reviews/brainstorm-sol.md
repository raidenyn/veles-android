# Review record: brainstorm — reviewer Sol (GPT-6.1-Sol, medium)

Preset: sol (first Sol).
Upstream inputs: none.
Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: changes-required (1 high, 2 medium)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | Key Decisions said triggering fixes re-review every approving reviewer "as a new pass" without limiting that to completed cycles; during an active pass the same reviewer must continue the same counter, or the 5-round cap is defeated. | **Fixed.** The design now distinguishes an active pass (same reviewer, same counter) from a completed cycle (every approving reviewer, new pass) and keeps the two-reviewer confirmation pass as the only extra pass inside a cycle. |
| 2 | medium | "Since the last approval" and "a re-review resets the count" lacked a baseline for a two-reviewer preset: whose approval resets which count, and whether an intermediate round or a single reviewer's approval resets it. | **Fixed.** The count is now per artifact since the last completed review — every reviewer the preset lists approved; only a new completed review resets it, not an intermediate round or one reviewer's approval. |
| 3 | medium | The Agreed Approach omitted the apply pre-flight's "a recorded hash is never rewritten" sentence in `openspec/schemas/superspec/schema.yaml`, which contradicts the newly authorized hash re-pointing. | **Fixed.** The schema work now includes the apply pre-flight; the absolute sentence becomes "a re-pointed hash must carry its note; a triggering edit that was not re-reviewed refuses apply." |

## Round 2 — verdict: approve (no new findings)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | — | All three prior findings are resolved; no new finding. | — |

The principal trade-off — hash re-pointing relies on instruction-level classification and disposition notes rather than independently proving an edit was non-triggering — is acknowledged in Alternative A and needs no new mechanism.

Approved content: brainstorm.md sha256 c0a3f5ebe04e7bda7d877ff80bf07525e6ea897e51bf8c90c4370001f9bf3fd1
