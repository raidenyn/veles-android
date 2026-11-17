# Review record: brainstorm

- Artifact: brainstorm
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none (brainstorm is first in the order)

## Pass 1

### Round 1 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| critical | C1 push cancelled on session close defeats the minute-20 scenario | fixed |
| high | H1 timer may not survive a locked phone; clock unspecified | fixed (wake lock, elapsed-realtime deadline) |
| high | H2 fixture and probe would both answer one pull | fixed (armed mode, MODIFIED probe delta) |
| high | H3 no staggered expiry for retained/expired check | fixed (per-record lifetimes, reseed) |
| high | H4 ignored conformance format | fixed (rationale, same folder) |
| high | H5 scripted client home unclear | fixed (web-extension/test, import guard) |
| high | H6 testing rule vs no physical runs | fixed (non-evidentiary smoke run planned) |
| medium/low | M1-M6, L1-L4 | fixed |

### Round 2 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| critical | N1 reseed cancels push | fixed |
| high | N2 fixture cannot reach service scope | fixed (own scope, host-detach hook) |
| medium | N3 timer clock explanation, N4 pairing layer, N5 two loopback modes | fixed |
| low | N6, N7, L-a | fixed |

### Round 3 — approve
| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | M1 journeys do not share a trust record | fixed (stated independent) |
| low | L1 release stub wording | fixed |
| low | L2 wake lock release | fixed |
| low | L3 revocation source | fixed |

Approved content: brainstorm sha256 ef92e2a70af629931e8d37522470c426fdfcb62967753e717467dd07be6a0352 non-triggering fixes M1, L1, L2, L3 applied after the approving round

## Pass 1 continuation (consistency after downstream-forced edits)

Edits forced by the design and proposal reviews: Python validator in the docs node, revocation dropped as a push-cancel trigger, pull router, development-only multi-event pull, test-only loopback main in testDebug.

### Round 4 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | M1 detach order vs shutdownSessions | fixed (first statement, before host shutdown) |
| medium | M2 import-guard claim false against link.ts/storage.ts | fixed (matches design D8) |
| low | L1 duplicated wording; L2 private loopback helpers; L3 node count and release wording | fixed |

### Round 5 — approve
| Severity | Finding | Disposition |
| --- | --- | --- |
| low | rust-wasm/pkg needed on the D8 fallback allow-list | fixed in design D8 |

Approved content: brainstorm sha256 15ffc62437f43196c3514816b442e9466dfb7d267dba2ece59572da1158982a2
