# Review record: specs

- Artifact: specs
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 cc91af6286ab0753e157b3cdeaad59feee8388d31ddcb689b22ebbdbdece7a53
  - proposal.md sha256 37823956307c9e8063bfd26113d734ea50a1034671261e99142e6697460ee20d
  - design.md sha256 29ce2174aecf5ad1fc6da5c3366f47e7259b3da537098190706616be8f2aae45

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
