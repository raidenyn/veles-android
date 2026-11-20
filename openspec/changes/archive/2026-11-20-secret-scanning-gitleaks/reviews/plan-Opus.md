# Review record: plan — Opus

- Artifact: plan
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: opus = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream:
  - brainstorm.md sha256 490e0eb287dc4ef2ae56fad35981b90928aedf37321deaf1384f75a4aeac9705
  - proposal.md sha256 3526ef09aa9b5255a6dd1671f1c846592ed925dfed6e68906e3e6db44c1bbfc5
  - design.md sha256 de5c0515eb63d0a294e482895f2340a5e07c3a0b6bae881813585f87fec1f536 — re-pointed from 0eafbff0… after design pass 3
  - specs/secret-scanning/spec.md sha256 e2662b890d09368bcf369ff076f9e4b393aa0012f533c6a84044e197bf080871 — re-pointed from 10205abe… after specs pass 3
  - tasks.md sha256 1e8216823ad8df871208df9dda8171bac7083e529f3df77e3e3ee9f03e7090b9 — re-pointed from ce79e036… after one non-triggering wording fix

## Pass 1

### Round 1 — changes-required

Reviewed content sha256 375278bbdb0e3465868a7a886db394467ca9485c455ded4247af30fd6a395fea.

| Severity | Finding | Disposition |
|---|---|---|
| high | `python3 -I -m unittest scripts.tests.…` cannot import under `-I`; TDD fail/pass steps meaningless. | fixed: `discover -s scripts/tests -p …` only. |
| high | "No archive in `downloads/`" is wrong: the git-cliff archive stays there. | fixed: no `gitleaks_8.30.1_*` archive. |
| medium | Dry-run commit branch tests nothing. | fixed: run the hook directly. |
| medium | Version error message names the pin, not the expected output. | fixed. |
| medium | Staged-deletion test passes vacuously without the binary. | fixed: same skip as refused/clean. |
| medium | Owner ask comes last though Task 4 depends on it. | fixed: Task 0 sends the ask; Task 5 records it. |
| low | `fake_key()` called twice. | fixed: deterministic constant. |
| low | `set -e` would lose `rc`. | fixed: no `set -e` stated. |
| low | No recovery if the scratch commit slips through. | fixed. |
| low | Non-executable sub-check lacks Windows skip. | fixed. |
| low | Commit type for the hook. | no change: `build(tooling)` accepted by the reviewer. |
| low | Use `Refs #<number>` instead of the placeholder. | rebutted: the tracker-intake rule forbids issue numbers in committed files; the real reference goes in the PR body. Accepted by the reviewer in round 2. |
| low | Docs-node checks vague. | fixed: commands named. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | Task 4 "revisit in Task 5" has no step. | fixed: Task 5 Step 1 re-edits and re-checks. |
| low | A shell-set `VELES_GITLEAKS` defeats the missing-binary check. | fixed: `env -u VELES_GITLEAKS`. |

Approved content: plan sha256 ae3b3b4a3ef4f02d96eacfb79c5afc7b79eaebe67490a603cc4edb62a62e57ae — re-pointed after two non-triggering minor fixes from round 2 (Task 5 re-edit step, `env -u VELES_GITLEAKS`).

## Pass 2 — consistency revalidation after the line-scoped allowlist amendment

### Round 1 — changes-required

Reviewed content sha256 2df2e76ebc0696380b55c8bff7d0caf88b4ed1eddf5d35fd2fb5bb5176419bb7.

| Severity | Finding | Disposition |
|---|---|---|
| high | Task 2 Step 3 has no negative-control step, so the new scenario is untested. | fixed: control added. |
| medium | Step 2 triage and Step 5 counts have no category (d). | fixed: category (d) and counts a–d. |
| low | Task 5 Step 4 does not list line-scoped entries. | fixed. |

### Round 2 — changes-required

Reviewed content sha256 66ee26276a4225668124cb354660a07d976dfaa12c3a1648f7d56d36570b94de.

| Severity | Finding | Disposition |
|---|---|---|
| high | The control used a copy of a covered file and named no scan that could see the change. | fixed: the fake line goes into the covered file itself and a staged scan runs. |
| medium | The PR body asked for the benign token, conflicting with the no-values rule. | fixed: file and entry description, not the token. |

### Round 3 — changes-required

Reviewed content sha256 f97ac2e7a63d32b310a8e82be8647f22506c07cea873cac8c565f9576f0db98d.

| Severity | Finding | Disposition |
|---|---|---|
| medium | `git restore <file>` keeps the staged fake line and `git diff` still looks clean. | fixed: `git restore --staged --worktree` and `git status --porcelain`. |
| low | The direct-scan alternative lacked `--redact`. | fixed. |
| low | Singular "one covered file" left the scope unclear. | fixed: "each (d) file". |

### Round 4 — approve

Reviewed content sha256 de392d92ded6b16da4c5dfe7d96aa6adb72a9ce7457d254e200c32b681440c02.

| Severity | Finding | Disposition |
|---|---|---|
| low | "each covered file" could also read as including path-only files. | fixed: "each (d) file" (non-triggering wording fix). |
| low | The restore also discards other edits to that file. | deferred: covered files are not otherwise touched; no action. |

Approved content: plan sha256 3ab337694c0ebc0272c373c721174326df85eeac20b1125f92473113ad959ffe — re-pointed from de392d92… after one non-triggering wording fix (control applies to each (d) file).
