# Review record: tasks

- Artifact: tasks
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

## Pass 1

### Round 1 — changes-required

Reviewed content sha256 3d97cba7fca97964bfb220d2f552da502092742344f529e6c2823517fb8b665c

| Severity | Finding | Disposition |
|---|---|---|
| high | The spike cannot prove the ship gate: a local-override stub does not exercise the tracked wiring and lacks the pointer, state and fault injection. | Fixed (1.1). A disposable worktree with stubs at the real tracked paths, a stub pointer, state, `void` and a post-write failure switch. |
| high | Verification can perform real writes (commits, PR creation and edits), and only the local settings file is kept out of staging. | Fixed (1.2, 1.6, 1.7, 7.2). A throwaway local-only branch, a recording `gh` shim, a dummy change, raw logs in the scratchpad, sanitized results, and cleanup on both pass and stop. |
| high | The engine–adapter contract (the plain response with entries) and the single locked transaction are unassigned; infrastructure errors could become denials. | Fixed (3.1, 3.2, 4.2). The contract is defined and tested first; the store and transaction wrapper come before the gates; the plugin treats only a `deny` decision as a denial. |
| high | The tracked-file sweep would rewrite approved artifacts, review records and main specs. | Fixed (5.4, 5.5). Named live consumers only; main specs are synced at closeout; artifacts, reviews and archives are left alone. |
| medium | Required red tests and their owning steps are implicit; checks are split from their config changes. | Fixed. Explicit failing-test lists per task; each check lands with its config in group 6. |
| medium | The node checks omit scripts' `node --test` and `project_flow.py check`, and docs' Gate B validator; the selector must run on the committed diff. | Fixed (7.1). |
| medium | No fresh-session observation that the rule is not loaded before a trigger; the evidence and failure handling of the final re-run are unclear. | Fixed (1.5, 7.2). All three tools; both runs recorded in the report and the PR; a failed re-run stops for the owner. |

### Round 2 — changes-required

Reviewed content sha256 5893a88608bfc2d6f47a600c7f8f778a61e51b9c4d58544150f10a9d85688fae

Round-1 status per the reviewer:
- Resolved: findings 3, 4, 6 and 7.
- Not resolved, carried below: 1 (injection fault), 2 (isolation) and 5 (green ordering).

| Severity | Finding | Disposition |
|---|---|---|
| high | No controlled OpenCode host-injection fault for behaviour 6. | Fixed (1.1, 7.2). A test-only plugin switch fails the injection after a successful engine response, in both the spike and the re-run. |
| high | A linked worktree shares the git common directory, so the re-run can write and prune the live hook store; the returned finalize workflow includes a push. | Fixed (1.1, 1.2, 1.7, 7.2). A `--no-hardlinks` clone with its own git directory and no remote, and only the trigger command is run, never the returned workflow. |
| medium | The second plugin and the rule move land before the checker that enforces the old layout. | Fixed (group 4 note, 4.1, 4.3, 5.2, 6.1). Each config change lands with its invariant and tests. |
| high | The evidence written by the re-run is neither committed nor covered by the checks. | Fixed (7.1–7.3). A candidate commit is verified, then the evidence is committed and all selected checks are re-run on the final diff. |
| medium | `global-overrides` matches exact file names, so one plugin-file test does not cover the others. | Fixed (6.2). One entry per tracked plugin file, and a test over every tracked plugin file. |

### Round 3 — approve

Reviewed content sha256 3d05e3dded154e0eb952d40e4ca49b80b597349c0f96b029c09e035609acce71

Round-2 status per the reviewer: all five findings resolved. No critical or high finding remains.

| Severity | Finding | Disposition |
|---|---|---|
| medium | The wiring can land before CODEOWNERS covers the engine and plugin. | Fixed: 4.1 and 4.3 land no earlier than 6.3. |

Approved content: tasks sha256 231fb43c663f478ddb6dc6c91b916836e7288a57b5bf3d771b578eacbd654afb — re-pointed from 3d05e3dded154e0eb952d40e4ca49b80b597349c0f96b029c09e035609acce71 after one non-triggering minor fix (the round-3 CODEOWNERS ordering finding)
