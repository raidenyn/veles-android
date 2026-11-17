# Review record: design

- Artifact: design
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 cc91af6286ab0753e157b3cdeaad59feee8388d31ddcb689b22ebbdbdece7a53
  - proposal.md sha256 6a91d7ee3c7aaf368e6c6796593c19914a9f98da6a195fcdf9abb945ac3bcb52

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

Approved content: design sha256 c350e1488ff4b27ebd570ae1bfcb6f3ed7d042e829cab7dbbf3582a72739bdd4 (re-pointed after 3 minor fixes: the setup-check workflow facts, the trust qualification of rule 2 and of the milestone copy)

Upstream (proposal) re-pointed after the minor trust-qualification edit.
