# Review record: closeout-docs

- Artifact: closeout-docs (the main specs written by the closeout's sync, reviewed before any push)
- Reviewer: Opus (Opus-5.5, medium, read-only subagent) — roster preset `opus`
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none
- Reviewed files, keyed by repository-relative path (hash of the content reviewed):
  - openspec/specs/hooked-rule-delivery/spec.md sha256 9059ea84e75a325dd8b176ea3f15db06816229cbda355c53d82e309df5b9a6b4
  - openspec/specs/agent-guidance-layout/spec.md sha256 0c847e3c4dc0672524e56d164d8bfe7e68dcc6e420f38f8c7bed32d83aaaf1dc
  - openspec/specs/ci-pipeline/spec.md sha256 563bf928dc07e001cee95a45eafdb418477252c71c06abb3352ebe89b9dcf230
  - openspec/specs/commit-conventions/spec.md sha256 e2bb66e35212ddb3f9f688054883aeaec7a74f3e8252490a8c138d07edb4f281
- Why Opus: the session's earlier reviewer (GPT-6.2 Sol, custom) could not start for this review ("ChatGPT usage limit
  reached"); the repo rule forbids substituting a model, the owner was asked and chose the roster's `opus` preset.

## Pass 1

### Round 1 — approve

Verdict approve: every delta requirement is word for word in its main spec (4 + 1 + 1 + 9), no other requirement
changed, no delta headers left, `openspec validate` passes. No critical or high finding.

| Severity | File | Finding | Disposition |
|---|---|---|---|
| medium | hooked-rule-delivery | The code rejects an empty or whitespace-only body and an empty `pointer`; the spec only sets the 4,096-byte maximum. | Deferred: a new rule is not a sync edit; follow-up spec change adding a non-empty body/pointer rule with a scenario. |
| low | hooked-rule-delivery | "No other prompt text MUST be matched" is ambiguous. | Fixed (wording: "No other prompt text is matched."). |
| low | hooked-rule-delivery | Purpose: "again after a conversation is summarized" is loose and "no context until needed" ignores the always-on pointer. | Fixed (Purpose rewritten: delivered again at the next moment or gate; a one-line pointer stays always on). |
| low | hooked-rule-delivery | The ship-condition requirement is specific to this change. | No action: consistent with repo practice (agent-guidance-layout does the same). |
| low | hooked-rule-delivery | Requirement bodies use bold labels, lists and a table, unlike the other specs. | No action: copied word for word from the approved delta; cosmetic. |
| medium | agent-guidance-layout | The check-enforces-the-layout requirement does not list the extra invariants the checker enforces (`disableAllHooks`, extra hook keys, one entry per event, engine file required when hooks are configured, strict JSON). | Deferred: adding invariants is a follow-up spec change, not a sync edit. |
| low | agent-guidance-layout | The Purpose (untouched by the delta) does not mention the hook-delivered scope. | Deferred: follow-up. |
| low | agent-guidance-layout | The required AGENTS.md notice does not say hooked delivery also depends on starting at the root. | Deferred: the notice text is fixed by the spec and the check; follow-up. |
| low | agent-guidance-layout | The moved-rule scenario names the old rule file. | No finding: correct, it describes the failure case enforced by `MOVED_RULES`. |
| low | ci-pipeline | A new file under `scripts/opencode-hooked-rules/` would select only `tooling`, not `docs`, because overrides list exact paths. | Rebutted: the routing test derives its file list from `git ls-files`, so a new file without an override fails the test; the gap cannot land silently. |
| medium | commit-conventions | The breaking-marker scenario and the guide say "`!` or a `BREAKING CHANGE:` footer only with `feat`"; the "or" suggests a footer alone is enough, but the validator requires `!` with a footer. | Deferred: the approved feat-only decision stands and the guide is implementation, not a sync edit; the validator rejects a footer without `!` with a clear message, so the mistake is self-correcting. Follow-up: reword to "`!`, optionally with a footer". |
| low | commit-conventions | The Purpose is still "TBD - created by archiving change…". | Deferred: pre-existing, not caused by this sync. |

Two minor wording fixes (below the five-fix allowance) were made after the review; they do not reopen it. The
hooked-rule-delivery hash is re-pointed with a note.

Approved content: openspec/specs/hooked-rule-delivery/spec.md sha256 f9e5be06741b0e67e6baada63a3c20a6fb22e714e824260e4ae5e624ac317e67 — re-pointed from 9059ea84e75a325dd8b176ea3f15db06816229cbda355c53d82e309df5b9a6b4 after two non-triggering minor wording fixes (the "MUST be matched" sentence and the Purpose)
Approved content: openspec/specs/agent-guidance-layout/spec.md sha256 0c847e3c4dc0672524e56d164d8bfe7e68dcc6e420f38f8c7bed32d83aaaf1dc
Approved content: openspec/specs/ci-pipeline/spec.md sha256 563bf928dc07e001cee95a45eafdb418477252c71c06abb3352ebe89b9dcf230
Approved content: openspec/specs/commit-conventions/spec.md sha256 e2bb66e35212ddb3f9f688054883aeaec7a74f3e8252490a8c138d07edb4f281
