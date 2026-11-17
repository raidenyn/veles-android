# Review record: brainstorm

- Artifact: brainstorm
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none (brainstorm is first in the order)

## Pass 1

### Round 1 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| high | No reliable migration gate: `next` syncs first and could pick from a board with unconverted prose dependencies | fixed (`PROJECT_URL` is the enable switch, set last; Action, `sync`, `next` and `start` refuse other projects; ordered setup in D12) |
| high | In Review had no full reconciliation: a removed closing keyword or a missed PR event left an item In Review | fixed (`sync` and `pr` re-derive every In Progress/In Review item) |
| medium | The follow-up order algorithm and its example disagreed | fixed (`n` excludes the parent's sub-issues at any depth; examples recomputed) |
| medium | The "no race" claim ignored the agent's own `sync` | fixed (superseded in round 2) |

### Round 2 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Re-read-before-write cannot stop a stale write landing after `start`'s verification read; concurrent starts both succeed | fixed (convergent model: claim by assignment, status derived from facts, `next` skips assigned items; same-account simultaneous claims explicitly accepted) |
| medium | `check` overstated certainty about converted prose dependencies | fixed (facts versus heuristic warnings; setting `PROJECT_URL` is the human attestation) |

### Round 3 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Blocked and Done existed only as Status, so a stale write destroyed them unrecoverably | fixed (closed → Done and the blocked label → Blocked become derivation rules 0 and 1) |
| medium | Claims by different accounts at the same time were unspecified | fixed (`start` re-reads assignees and stops on any other login) |

### Round 4 — approve
| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | Sibling `design.md` kept "except Blocked and Done" and partial rule ranges for `sync`/`pr` | fixed in `design.md` (the full derivation for every subcommand; add-assignees never replaces); brainstorm.md unchanged |

Approved content: brainstorm sha256 cc91af6286ab0753e157b3cdeaad59feee8388d31ddcb689b22ebbdbdece7a53
