# Review record: specs

- Artifact: specs
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm sha256 15ffc62437f43196c3514816b442e9466dfb7d267dba2ece59572da1158982a2; proposal sha256 20e007e82fe0f8817d7cadb6ef2799085b415d3624da07a5767ee49d677e0318; design sha256 7c263a20870b63b6aadd1c1ad6138c7b9131d6b5ba345dd2217f277251e0268e

## Pass 1

### Round 1 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| high | matrix initial state vs permanent rule; undefined row set | fixed (initial_state pin, explicit row table) |
| medium | WAKE_LOCK release-guard; wake-lock binding; CI skip rule; vocabulary matching; loopback commands; NOT RUN evidence; missing RFC evidence; provenance anchor; ack routing scenarios | fixed |
| low | 12-18 | fixed or accepted |

### Round 2 — approve
| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | BLOCKED status contradicts evidence rule | fixed (dropped, NOT RUN with blocker) |
| low | wake resource wording; push action meaning; all keys; bundle test string | fixed |
| low | other wording notes (timing inputs for the background rule, decision vocabulary) | deferred: to be fixed in the validator's own tests and docstring |

### specs/gate-b-history-fixture/spec.md
Approved content: specs/gate-b-history-fixture/spec.md sha256 c489a7092a6b8ea111fcd1dbe4106a7919afa9c3b51f28572168888078351ff4 non-triggering fixes applied after the approving round
### specs/gate-b-journey-harness/spec.md
Approved content: specs/gate-b-journey-harness/spec.md sha256 ec460894c4c69e6bfb491016e144e7c6e923b1afe4b81e5c796e3b1eb547815b non-triggering fixes applied after the approving round
### specs/gate-b-evidence-matrix/spec.md
Approved content: specs/gate-b-evidence-matrix/spec.md sha256 3fbee1417b410f77190c7f302f921968f8188634ef97842a5436a6ab9812e255 non-triggering fixes applied after the approving round
### specs/synthetic-session-probe/spec.md
Approved content: specs/synthetic-session-probe/spec.md sha256 f140e04fb1d135fbe2ef2daacc2309a960fee87c4fa6da5bc1e9616b1e61708e non-triggering fixes applied after the approving round
