# Review record: design

- Artifact: design
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 cc91af6286ab0753e157b3cdeaad59feee8388d31ddcb689b22ebbdbdece7a53
  - proposal.md sha256 37823956307c9e8063bfd26113d734ea50a1034671261e99142e6697460ee20d

## Pass 1

### Round 1 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Actions concurrency keeps one pending run, so a PR-event-only milestone copy could be lost permanently | fixed (the `pr` subcommand is removed; every event runs the complete `sync`; dropped-event test) |
| medium | Partially created follow-ups had no recovery or retry protocol | fixed (resumable, idempotent steps; a concurrent clash surfaces as a tie) |
| medium | Nested paginated reads were not guarded | fixed (complete reads, fail closed) |
| medium | Finalize's In Review confirmation for this change before enablement | fixed (this change is untracked; `start` refuses non-enabled projects) |
| medium | The empty-milestone report had no execution path | fixed (part of `sync`) |
| low | The owner node is `tooling`, not `scripts` | fixed |

### Round 2 — approve
| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | `followup --resume` had no recovery-input contract | fixed after approval (same arguments re-issued; stop on disagreement) |
| medium | `start` did not refuse Backlog | fixed after approval (refuses anything not Ready; `--force` = explicit user authorization) |
| medium | The milestone copy covered only open PRs | fixed after approval (open or merged PRs closing project issues) |

Approved content: design sha256 29ce2174aecf5ad1fc6da5c3366f47e7259b3da537098190706616be8f2aae45 (re-pointed after 3 minor fixes made after the round-2 approval: the resume contract, `start` refusing Backlog, the milestone copy for merged PRs)
