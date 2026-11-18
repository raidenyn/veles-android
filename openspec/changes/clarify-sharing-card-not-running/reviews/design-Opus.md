# Review record: design — Opus

- Artifact: design (`design.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: brainstorm sha256 a1cf3d73b6f5cfa15b1e49d067453ad7ec9795b9ea66654f529fd2054522f6d9; proposal sha256 f9bcdc7a2631c03203ea3f7aec1271c44f92cb64beebcb66dee80d173b5a3da0 — the proposal hash re-pointed from the pre-fix version the reviewer read, after the proposal's 5 non-triggering minor fixes (see `proposal-Opus.md`)

## Pass 1

### Round 1 — approve

Reviewed together with the proposal in one reviewer run; the reviewed hash was not computed before the fixes below. The reviewer confirmed that the function's rule reproduces today's rendering for the other 13 combinations.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | medium | Risks: "a single frame at most" is unsupported; the window lasts until `onStartCommand` publishes `Starting`, and there is a second window between `setEnabled(true)` and `update(Starting)`. | fixed: both windows are named and the conclusion kept. |
| 2 | medium | The doc sentence is only paraphrased, and "explicit failure" in the same process shows `Failed`, not the new line. | fixed: the exact sentence is fixed and limited to a restart with the preference still on. |
| 3 | medium | The new capability departs from the approved brainstorm without saying so. | fixed: stated as a deliberate departure that supersedes the brainstorm's decision. |
| 4 | low | "Robolectric, since the function returns resource ids" is the wrong reason; ids are plain ints. | fixed: the test compares ids without a runtime and uses Robolectric to check the exact text of `sharing_stopped`. |
| 5 | low | The real-phone check lacks the precondition of enabling sharing and reaching `Advertising` first. | fixed: precondition added. |
| 6 | low | On a cold start `enabled` is `false` until the `LaunchedEffect` loads it, so the first frame shows the plain `Disabled` line. | deferred: already true today, harmless, and unchanged by this change. |
| 7 | cosmetic | The return type should be `List<@StringRes Int>`. | fixed (cosmetic; not counted). |

Approved content: design sha256 0f1bdc0aa9a8a92bfaf76de8823d5ad2f77e7c33234fc5580996f835dc7c0f13 — after 5 non-triggering minor fixes (findings 1–5) and cosmetic edits (finding 7, then its revert) re-pointed from the hash before the plan review: the return type `List<@StringRes Int>` was reverted to `List<Int>`, because `@StringRes` has no type-use target and does not compile on a type argument (plan review round 1); a type-annotation edit with no change of meaning, treated as cosmetic.
