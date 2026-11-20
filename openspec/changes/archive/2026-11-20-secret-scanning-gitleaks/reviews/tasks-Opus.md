# Review record: tasks — Opus

- Artifact: tasks
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: opus = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream:
  - brainstorm.md sha256 490e0eb287dc4ef2ae56fad35981b90928aedf37321deaf1384f75a4aeac9705
  - proposal.md sha256 3526ef09aa9b5255a6dd1671f1c846592ed925dfed6e68906e3e6db44c1bbfc5
  - design.md sha256 de5c0515eb63d0a294e482895f2340a5e07c3a0b6bae881813585f87fec1f536 — re-pointed from 0eafbff0… after design pass 3
  - specs/secret-scanning/spec.md sha256 e2662b890d09368bcf369ff076f9e4b393aa0012f533c6a84044e197bf080871 — re-pointed from 10205abe… after specs pass 3

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

## Pass 2 — consistency revalidation after the line-scoped allowlist amendment

### Round 1 — changes-required

Reviewed content sha256 a9dacc71d18dd06639a792cca805da5861bbfde8b916ec9c4fe20947ac638ae2.

| Severity | Finding | Disposition |
|---|---|---|
| high | 4.2 has no negative-control step; the new scenario's "a different flagged line still blocks" has no task behind it. | fixed: negative-control step added. |
| medium | Triage has no category for a tracked non-test-data false positive, and 4.4 counts only a–c. | fixed: category (d) and counts a–d. |
| low | 8.1 does not ask for line-scoped entries in the PR body. | fixed. |

### Round 2 — changes-required

Reviewed content sha256 2a6dcabdbd8b220a6176227db466bebde2c41c2f19a2e6ef7cd7e725415f260c.

| Severity | Finding | Disposition |
|---|---|---|
| high | The negative control used a copy of the covered file and named no scan that could see the change. | fixed: the fake line goes into the covered file itself and a staged scan runs. |
| low | 4.1's category order contradicts the a–d labels used at 4.4 and 8.1. | fixed. |
| low | 8.1 still asked for the token text. | fixed: name the file and the entry's description. |

### Round 3 — changes-required

Reviewed content sha256 e7d5061d704c3ae79402764b063140276b3f1424b4ede969f91f45397b88c6bc.

| Severity | Finding | Disposition |
|---|---|---|
| medium | The restore used `git restore <file>`, which keeps the staged line, and `git diff` still looks clean. | fixed: `git restore --staged --worktree` and `git status --porcelain`. |
| low | The direct-scan alternative lacked `--redact`. | fixed. |
| low | Singular "a covered file" left it unclear whether every covered file needs the control. | fixed: "each (d) file". |

### Round 4 — approve

Reviewed content sha256 ce79e036d29c6a6fc1f5513be2753dac86ed4f9b6187c9a7016576365dc584bb.

| Severity | Finding | Disposition |
|---|---|---|
| low | "each covered file" could also read as including path-only files. | fixed: "each (d) file" (non-triggering wording fix). |
| low | The restore also discards other edits to that file. | deferred: covered files are fixtures/source not otherwise touched; no action. |

Approved content: tasks sha256 1e8216823ad8df871208df9dda8171bac7083e529f3df77e3e3ee9f03e7090b9 — re-pointed from ce79e036… after one non-triggering wording fix (negative control applies to each (d) file).
