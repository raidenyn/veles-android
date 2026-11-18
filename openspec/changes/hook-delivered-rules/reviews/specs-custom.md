# Review record: specs

- Artifact: specs
- Reviewer: custom — GPT-6.2 Sol (`openai/gpt-6.2-sol` via OpenCode), effort medium (session-only custom model)
- Session preset: [custom]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm sha256 3d530fff7e7efe707e722eea4a33a11b7cb899b9078a23b2c870d736c2da680d; design sha256 28dc53c714da46769e60d65d7c65a98d93a74947fafac241b5a5812921052682; proposal sha256 3a7847a5793ff7bd1a0c852607edbe64ab2a2f25667e6c10655b7cf82bfedc9e

## Pass 1

### Round 1 — changes-required

Reviewed content sha256:
- specs/hooked-rule-delivery/spec.md: 4b27e3bddefbcdca356f7e704bca0c7fab005049741372a990724fa284539ab8
- specs/agent-guidance-layout/spec.md: 5af0014b1c18f003e68d786004f1ddb0efafd36d3bf196dcb94dbf6a6ec23add
- specs/commit-conventions/spec.md: aadc161571de786ff7189e3d2cc47e44813d770954cf70988bf3df25085b9d48
- specs/ci-pipeline/spec.md: 054d5e65808061b7ec5e2cce93e79b4de6ee06a53105f9456ef74235e3e0914e

#### specs/hooked-rule-delivery/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| high | The ship gate drops design §8 outcomes: an edited-title retry, a pass after each moment, the child/whole-session compaction check, the stale-credit observation and OpenCode `void`; it is not tied to the spike as the first task and its re-run. | Fixed. The requirement makes the spike the first implementation task with the full §8 outcomes, re-run on the finished implementation, and its evidence recorded for both. |
| medium | The cap does not distinguish moments from denials, so a capped-out rule could become pending. | Fixed. A capped-out rule is never pending; a cap-at-denial scenario was added. |
| medium | The plain response, the write id per rule, multi-rule rollback and the index lock are unspecified. | Fixed. The plain response carries one entry per rule with its write id; one `void` per entry; the index file has its own lock. |
| medium | Command moments do not state `re.search` over the whole string; there is no positive workflow scenario; matcher forms are missing. | Fixed. The whole-string `re.search` is stated, a successful-workflow scenario added, and a table-driven matched-forms scenario added. |
| medium | The fallback scenario omits `CLAUDE.local.md`; the `docs/AGENTS.md` documentation requirement is missing. | Fixed. |

#### specs/agent-guidance-layout/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| medium | The legacy frontmatter and size scenarios are not qualified by directory. | Fixed. |
| medium | The hook-wiring invariant does not reject a wrong matcher or command; the no-allow property cannot be checked structurally. | Fixed. The exact matcher and command are checked; no-allow is tested through engine output and host verification. |
| low | "The three new entries" is ambiguous after adding two paths. | Fixed. The paths are named. |

#### specs/commit-conventions/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| medium | The format omits optional scope and `!`; the fix/perf/breaking bumps lack the touched-component qualifier. | Fixed. |

#### specs/ci-pipeline/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| low | The scenario does not assert that only `tooling` and `docs` run. | Fixed. The exact set is asserted, separately for an engine-only and a plugin-only change. |

### Round 2 — approve

Reviewed content sha256:
- specs/hooked-rule-delivery/spec.md: 446b11ba8ec29527d0e3f86c3726d4725e58961c28db67bd00c0fc4c24721936
- specs/agent-guidance-layout/spec.md: 31730a94316e90a448caf834f89fd1d2eb26b0ff67a4606851265ca2445c841e
- specs/commit-conventions/spec.md: b89b157e646c0ec65d553f034d799dab1706ae445ecf5c5c884d7612b1dde75c
- specs/ci-pipeline/spec.md: b055d91072bbaf13bab5534ec065018deed0ee459d47b4e07ab0f315fc59eec0

Round-1 status per the reviewer: eight of ten findings resolved, including the high one. No critical or high finding
remains, and the MODIFIED deltas preserve the existing requirements apart from the intended changes.

| Severity | Finding | Disposition |
|---|---|---|
| medium | hooked-rule-delivery: the gate text still writes pending unconditionally, and the two-rule cap scenario cannot exceed 9,000 bytes. | Fixed. Only rules included in the denial become pending; the scenario uses three rules. |
| medium | agent-guidance-layout: the three standalone size scenarios are not qualified by `.agents/rules/`. | Fixed. |
| low | hooked-rule-delivery: the plain-response clause does not exempt `pass` from emitting output. | Fixed. A `pass` emits nothing; the payload applies only when output is emitted. |

#### specs/hooked-rule-delivery/spec.md

Approved content: specs/hooked-rule-delivery/spec.md sha256 d6a1eb0d08de54aea1dee03c3862c6a231ab9a0fc64bf2cb95420f040afb7fe5 — re-pointed from 446b11ba8ec29527d0e3f86c3726d4725e58961c28db67bd00c0fc4c24721936 after non-triggering minor fixes (the round-2 pending-only-if-included, three-rule cap scenario and pass-emits-nothing findings)

#### specs/agent-guidance-layout/spec.md

Approved content: specs/agent-guidance-layout/spec.md sha256 7e4239d22334214377b4d969b36eb2917f931dbe9d338eeb83b7ac16c0bd48ec — re-pointed from 31730a94316e90a448caf834f89fd1d2eb26b0ff67a4606851265ca2445c841e after one non-triggering minor fix (the round-2 size-scenario qualification)

#### specs/commit-conventions/spec.md

Approved content: specs/commit-conventions/spec.md sha256 b89b157e646c0ec65d553f034d799dab1706ae445ecf5c5c884d7612b1dde75c

#### specs/ci-pipeline/spec.md

Approved content: specs/ci-pipeline/spec.md sha256 b055d91072bbaf13bab5534ec065018deed0ee459d47b4e07ab0f315fc59eec0
