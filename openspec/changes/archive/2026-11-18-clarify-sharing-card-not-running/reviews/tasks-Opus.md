# Review record: tasks — Opus

- Artifact: tasks (`tasks.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: brainstorm sha256 a1cf3d73b6f5cfa15b1e49d067453ad7ec9795b9ea66654f529fd2054522f6d9; proposal sha256 f9bcdc7a2631c03203ea3f7aec1271c44f92cb64beebcb66dee80d173b5a3da0; design sha256 0f1bdc0aa9a8a92bfaf76de8823d5ad2f77e7c33234fc5580996f835dc7c0f13 (re-pointed, see `design-Opus.md`); specs/sharing-card-copy/spec.md sha256 056f083de15cd2de00556f62ee2b2450e94c68d9dc2783fc7e86fb8ec493ab64 (re-pointed after the spec's 3 non-triggering minor fixes, see `specs-Opus.md`)

## Pass 1

### Round 1 — approve

Reviewed together with the specs in one reviewer run; the reviewed hash was not computed before the fixes below. The reviewer confirmed that every spec scenario, the design's docs sentence, the real-device rule and the pre-push rule are covered and that the tasks are ordered by dependency.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | medium | 3.2 allows an emulator silently; the real-device rule says say so and ask, and an emulator usually cannot reach `Advertising`. | fixed: a real phone is required; with none, stop and ask. |
| 2 | medium | 3.2 omits the signing steps of `android/AGENTS.md` (init script, fingerprint comparison, no uninstall). | fixed: the build command, the fingerprint comparison and the no-uninstall rule are named. |
| 3 | low | "see it fail" before the function exists is a compile error. | fixed: a compile error counts as failing. |
| 4 | low | Nothing checks that the buttons are unchanged. | fixed: 3.2 reads the card's buttons in the stopped state. |
| 5 | low | 3.2 does not say which control stops sharing. | fixed: the card's Stop sharing button. |

Approved content: tasks sha256 5218e3bfb5bdd2e7f5c8d1e871bee765af242008e4176cd72a912ca6e5c56862 — after 5 non-triggering minor fixes (findings 1–5 above) — re-pointed from the hash before the plan review: the return type `List<@StringRes Int>` was reverted to `List<Int>`, because `@StringRes` has no type-use target and does not compile on a type argument (plan review round 1); a type-annotation edit with no change of meaning, treated as cosmetic (same edit in `tasks.md` 1.3).
