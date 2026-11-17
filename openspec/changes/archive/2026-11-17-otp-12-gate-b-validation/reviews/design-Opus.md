# Review record: design

- Artifact: design
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm sha256 15ffc62437f43196c3514816b442e9466dfb7d267dba2ece59572da1158982a2; proposal sha256 20e007e82fe0f8817d7cadb6ef2799085b415d3624da07a5767ee49d677e0318

## Pass 1

### Round 1 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| critical | C1 dev connector decodes exactly one event (D10 smoke infeasible) | fixed (D9 pullAll) |
| high | H1 timing rule; H2 missing RFC criteria; H3 probe-spec conflict; H4 import guard | fixed |
| medium | M1-M7 | fixed |
| low | L1-L5 | fixed |

### Round 2 — approve
| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | N1 scripted client must not import trust/repository.ts | fixed |
| low | N2 decoder by request kind; N3 loopback helpers; N4 exact-alarm permission, ack owner, journey-id check, bond field | fixed |
| low | rust-wasm/pkg on fallback allow-list | fixed |

Approved content: design sha256 48b5898188c2257727c07c6b69819349c678f33fb139557314d25e94dfb69a14 non-triggering fixes N1-N4 and the rust-wasm note applied after the approving round

## Re-point after a non-triggering edit

The D7 action list gained `seed` and `schedule_push` after the specs review (one minor fix, applied to keep the design consistent with the journey spec).

Approved content: design sha256 7c263a20870b63b6aadd1c1ad6138c7b9131d6b5ba345dd2217f277251e0268e re-pointed after one non-triggering fix: the D7 action list now names seed and schedule_push
