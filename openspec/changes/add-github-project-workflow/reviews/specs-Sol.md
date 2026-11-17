# Review record: specs

- Artifact: specs
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 cc91af6286ab0753e157b3cdeaad59feee8388d31ddcb689b22ebbdbdece7a53
  - proposal.md sha256 37823956307c9e8063bfd26113d734ea50a1034671261e99142e6697460ee20d
  - design.md sha256 090fa30202de7c840bb37d29a8c7ac32c99e1ed2268ffb9c25316464f7ccc682 (re-pointed in pass 2 after a minor edit narrowing the workflow facts)

## Pass 1

### Round 1 — changes-required
| Severity | File | Finding | Disposition |
| --- | --- | --- | --- |
| high | github-project-automation | The Action's repository boundary was missing | fixed (new requirement "Each caller reconciles a defined scope") |
| high | superspec-closeout | The unchanged monitoring requirement still promised the removed intake reconcile | fixed (full MODIFIED block; all original scenarios kept) |
| medium | github-project-automation | PRs as project cards were not prohibited | fixed (new requirement "A task has one card, linked to its pull requests") |
| medium | github-project-automation, superspec-tracker-intake | The missing-order outcome was ambiguous | fixed (`missing-order` stop; `--skip-unordered` after user agreement) |
| medium | superspec-tracker-intake | Skill delivery and the doc/skill split were unspecified | fixed (new requirement "The procedure is delivered as rules, a doc and a skill") |
| medium | superspec-tracker-intake | Context-only issues could enter the tracked lifecycle | fixed (untracked from then on) |
| low | superspec-closeout | The tracked-finalize scenario contradicted its exception | fixed (qualified; exception scenario added) |

### Round 2 — approve
| Severity | File | Finding | Disposition |
| --- | --- | --- | --- |
| low | superspec-closeout | The ordinary tracked-finalize scenario lacked "the issue is open" | fixed after approval |
| low | superspec-closeout | Unconditional status wording in the stop report and the closed-unmerged scenario contradicted the derivation precedence | fixed after approval (refers to the derived status) |

### specs/github-project-automation/spec.md
Approved content: specs/github-project-automation/spec.md sha256 1e832e9287d89541aa5091d41cc9d95e2716dc481aa201f4ac5b6b0cd124ba49

### specs/superspec-tracker-intake/spec.md
Approved content: specs/superspec-tracker-intake/spec.md sha256 9a01c09feedcedc9724f6b85dd18a8c503ce65fd75a2882afa52b0d8614086d6

### specs/superspec-closeout/spec.md
Approved content: specs/superspec-closeout/spec.md sha256 fcb933dc9975934b30972995022e415560ced5fff353e34ab306b9d086908a5f (re-pointed from 353fa1f483d1a1d2d72b259928b481c041520206ab43eabdb62a2c65df33e28a after 2 minor fixes made after the round-2 approval: the open-issue qualification and the derived-status wording)

## Pass 2 (triggering edit after the cycle: a high-severity fix from the final whole-branch review)

Edits made during implementation, found by the final whole-branch review: resume of a Ready task recorded in an active tracker.md (interrupted before the claim; several trackers or a repository mismatch -> ask); only the workflow *Item closed* is a setup fact and *Auto-add to project* an advisory hint; \`sync\` prints \`ready:\` lines and a final \`rate-limit:\` line, attempts every write and ends with the named stop \`write-failed\`; \`next\`/\`start\` stop with \`write-failed\`.

### Round 1 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| high | \`start\` failure wording promised the wrong ordering (the claim writes precede the reconciliation) | fixed (stop without announcing success; completed writes stay; scenario added) |
| medium | tasks.md and design.md still classified every disabled built-in workflow as a fact | fixed (non-triggering minor edits; hashes re-pointed with a note) |
| low | Auto-add advisory said "not present" only; a disabled one also warns | fixed ("missing or disabled" and a scenario) |

### Round 2 — approve
| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | The failed-claim scenario said the status stays in place, but a status write could be the completed one | fixed after approval (completed writes stay, no rollback) |

Downstream consistency check (tasks.md and plan.md against the edited specs) was part of both rounds: no contradiction remains.

### specs/github-project-automation/spec.md
Approved content: specs/github-project-automation/spec.md sha256 fa8987309ff5caca77f1e069e1f19a3328228fe5240ea08e6682230525204592 (pass 2; re-pointed after 1 minor fix made after the round-2 approval: the failed-claim scenario wording)

### specs/superspec-tracker-intake/spec.md
Approved content: specs/superspec-tracker-intake/spec.md sha256 d58d36e42c00d40a523137131468f42f300e829fd68a0e207a75e8e28953db2d (pass 2)

### specs/superspec-closeout/spec.md
Approved content: specs/superspec-closeout/spec.md sha256 fcb933dc9975934b30972995022e415560ced5fff353e34ab306b9d086908a5f (unchanged in pass 2)
