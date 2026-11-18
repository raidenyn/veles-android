# Review record: plan

- Artifact: plan
- Reviewer: custom — GPT-6.2 Sol (`openai/gpt-6.2-sol` via OpenCode), effort medium (session-only custom model)
- Session preset: [custom]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm sha256 3d530fff7e7efe707e722eea4a33a11b7cb899b9078a23b2c870d736c2da680d
  - proposal sha256 3a7847a5793ff7bd1a0c852607edbe64ab2a2f25667e6c10655b7cf82bfedc9e
  - design sha256 28dc53c714da46769e60d65d7c65a98d93a74947fafac241b5a5812921052682
  - specs/agent-guidance-layout/spec.md sha256 7e4239d22334214377b4d969b36eb2917f931dbe9d338eeb83b7ac16c0bd48ec
  - specs/ci-pipeline/spec.md sha256 b055d91072bbaf13bab5534ec065018deed0ee459d47b4e07ab0f315fc59eec0
  - specs/commit-conventions/spec.md sha256 b89b157e646c0ec65d553f034d799dab1706ae445ecf5c5c884d7612b1dde75c
  - specs/hooked-rule-delivery/spec.md sha256 d6a1eb0d08de54aea1dee03c3862c6a231ab9a0fc64bf2cb95420f040afb7fe5
  - tasks sha256 231fb43c663f478ddb6dc6c91b916836e7288a57b5bf3d771b578eacbd654afb

## Pass 1

### Round 1 — changes-required

Reviewed content sha256 5c878d0b5ab8bc4f632c144f240bbf6683c6a789928768225583f59e0a148846

| Severity | Finding | Disposition |
|---|---|---|
| high | The `python3 -I -m unittest scripts.tests.…` commands cannot import under `-I`, so the red and green steps prove nothing. | Fixed. `discover -s scripts/tests -p` is used throughout, as a Global Constraint; the form was checked locally. |
| high | Task 7 does not register OpenCode's `session.hook('prompt')`, and nothing tests it. | Fixed. A prompt registration and a test were added. |
| high | The fail-open contract contradicts design §8: the post-write switch must exit with an error; empty stdout and transport failure are conflated; a malformed event passes silently. | Fixed. The switch returns `("", 1)` after committing state, and the engine never exits 2. Recoverable failures give the notice. The plugin separates an empty-stdout pass from a `null` failure. |
| high | The checker migration is ambiguous ("move" the parser), the shared clean fixture would break unrelated tests, and `CODEOWNER_SAMPLE_PATHS` is not extended. | Fixed. The parser is copied in Task 2 and switched in Task 8. Each checker task updates the clean fixture in the same commit (a Global Constraint). Task 6 extends both CODEOWNERS lists. |
| medium | The `checkout_for` and common-dir handoff, the plain event mapping, `void` fields, Python 3.8 annotations and lock ordering are unpinned. | Fixed (Tasks 3, 4, Global Constraints). |
| medium | The quoted-`pointer` extension, the hooked-rule checker cases, the per-hook negatives, and identity over raw bytes with a multibyte cap are missing. | Fixed (Tasks 2, 6, 8). |
| medium | The evidence lacks a real dummy change, an explicit do-not-execute instruction, a cleanup compared against the starting status, the report and PR matrix, and the review-record audit. | Fixed (Tasks 1, 10). |
| low | `docs/AGENTS.md` has no old-path reference, while the checker's samples do; `TYPES` multiplicity is not checked. | Fixed (Task 8). |

### Round 2 — changes-required

Reviewed content sha256 aa9ff2954c1182c0c6b094e47ceaa458fa13f20500c416741ffb19a69538def2

Round-1 status per the reviewer:
- Resolved: findings 1, 2, 5, 6 and 8.
- Not resolved, carried below: finding 3 (malformed-Claude-event channel), finding 4 (test helpers), finding 7 (final
  baseline).

| Severity | Finding | Disposition |
|---|---|---|
| high | A malformed Claude event has no `hook_event_name`, so the renderer cannot produce a notice the agent sees. | Fixed (Task 5). With no channel, the engine gives empty stdout, the notice on stderr and exit 0. The spec requires the notice only "where a channel exists"; `docs/AGENTS.md` documents it. The plain format still returns the notice. |
| high | The existing CODEOWNERS tests rebuild the fixture from `EXPECTED_CODEOWNER_PATTERNS`, and `test_opencode_second_plugin_fails` asserts "exactly one". | Fixed (Tasks 6, 7). Both are migrated in the same commits. |
| medium | The final re-run has no fresh starting status. | Fixed (Task 10). A fresh baseline; only the `spike.md` modification is allowed. |
| medium | New: the exit-1 rule contradicts `pointers --check`. | Fixed. The rule is scoped to `hook`. |

### Round 3 — approve

Reviewed content sha256 f97d643aa6f437aa0c03801ccb5646607261465ea3c2dfb71817b9db60f59d35

Round-2 status per the reviewer: all four findings resolved. No critical or high finding remains.

| Severity | Finding | Disposition |
|---|---|---|
| medium | Tests that call `opencode_text()` with its default one-plugin config would fail once the second entry is required. | Fixed. The helper's default gains the local entry in the same commit; explicit overrides stay. |

Approved content: plan sha256 fc401004c866c161e7610d421ebb82defe3429502aee713b541bf0671f822f2f — re-pointed from f97d643aa6f437aa0c03801ccb5646607261465ea3c2dfb71817b9db60f59d35 after one non-triggering minor fix (the round-3 `opencode_text()` default finding)
