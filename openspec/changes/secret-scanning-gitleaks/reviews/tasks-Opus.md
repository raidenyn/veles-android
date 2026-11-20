# Review record: tasks — Opus

- Artifact: tasks
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: opus = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream:
  - brainstorm.md sha256 490e0eb287dc4ef2ae56fad35981b90928aedf37321deaf1384f75a4aeac9705
  - proposal.md sha256 3526ef09aa9b5255a6dd1671f1c846592ed925dfed6e68906e3e6db44c1bbfc5
  - design.md sha256 0eafbff0ece7e06778f6768499513d052421fa5d3201a388ea244fc65661e2b3
  - specs/secret-scanning/spec.md sha256 10205abe6d82140f2318f7a712c740ee372f08cbd20dc3fcf3b52323ed5bde8c

## Pass 1

### Round 1 — changes-required

Reviewed content sha256 b84d423b811124df615716c1b0c26ea86d369e41f36c878da70bc8ef43ee5052.

| Severity | Finding | Disposition |
|---|---|---|
| medium | No task writes the PR description that upstream requires to record several results. | fixed: group 8. |
| medium | 7.2 `components.py changed` without `--base`. | fixed: merge-base. |
| medium | Wrong-version and unsupported-platform scenarios untasked. | fixed: 2.1 messages, 2.7 one-off check; unsupported platform review-checked with reason. |
| low | Leak exit code 3 not confirmed unused. | fixed: 3.1. |
| low | Scan working directory `$root` missing. | fixed: 3.3. |
| low | 5.5 does not assert the exit code is named. | fixed. |
| low | Not-executable branch untested. | fixed: 5.4 second assertion. |
| low | 7.1 commit disposition unclear. | fixed: the change's own task commit stays. |
| low | 3.2 too large to tick as one item. | fixed: split into 3.2–3.4. |

### Round 2 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| medium | 2.6 "leaves no binary" fails: the binary from 2.5 remains, since the install dir is cleared only after the digest passes. | fixed: delete the install dir first; shared task keeps today's behaviour. |
| low | 2.7 leaves a wrong binary unless re-installed. | fixed: re-run install before group 3. |
| low | 7.1 never restores the renamed binary. | fixed. |
| low | `--help` cannot show exit codes. | fixed: README "Exit Codes" at the tag. |
| low | 8.1 does not name the three expected paths. | fixed. |

### Round 3 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | 2.6/2.7 edit tracked files temporarily without a leftover check. | fixed: `git diff` check in each. |

Approved content: tasks sha256 e5de7a417273cc66d11ef442d746fe6d43ba31bdf340e7effe18e58be5bbd549 — re-pointed after one non-triggering minor fix from round 3 (leftover-edit `git diff` checks in 2.6 and 2.7).
