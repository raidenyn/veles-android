# Review record: specs

- **Artifact**: `specs` (`specs/mutual-peer-removal/spec.md`)
- **Reviewer**: Opus (Opus-5.5, medium)
- **Session preset**: [Opus]
- **Cap**: 5 rounds
- **Upstream**: brainstorm.md sha256 `d95c221184f9475e95e94a369245fecc303d15fd7067234105fdfd7f859c1e09`; proposal.md sha256 `8c447272e7419f88407e4f868d382f8b1f8950d082bbb0b57d788016ff0205e8`; design.md sha256 `04b8de0aca9d48d41568abb80d8fc7424e0dd4ab21d87b9c9f9d7a773b6b6e6c`

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | H1: the hold trigger ("a completed write that is not an application acknowledgement") and the "engine MUST mark" wording reached into Android, contradicting the design's Chrome-only scope. | fixed — the ADDED hold and the MODIFIED "Close after reply" now name the Chrome endpoint / Chrome session engine and state the Android responder is unchanged. |
| high | H2: revocation cleanup was not limited to the revoked peer, contradicting peer-revocation's "touches only the revoked peer". | fixed — a revocation finishes only the existing holds of that peer; a scenario asserts another peer's hold is untouched. |
| high | H3: the responder's own revocation finish releasing its hold had no scenario. | fixed — a "Responder's own revocation finish" scenario asserts no disconnect before the requester's connection end or the bound. |
| medium | M1: the "connection ends" evidence was framed as delivery, with requester-side facts the responder cannot observe. | fixed — the trigger is "the requester's connection ends, for any reason", no delivery is claimed, and a separate scenario covers loss/user disconnect. |
| medium | M2: no port-close scenario. | fixed — a port-close scenario clears every timer and mark with no disconnect request. |
| medium | M3: fail-closed was left out. | fixed — fail-closed is named in the cleanup requirement and has its own scenario. |
| medium | M4: "admitted" versus the design's synchronous snapshot at entry. | fixed — the snapshot is defined as the moment the operation starts terminating sessions, before any await. |
| medium | M5: the concurrent-request scenario omitted the requester-side result and the both-on-the-air case. | fixed — two scenarios state the own-removal result and the existing authorization failure with both records kept. |
| low | L1: the in-flight reset scenario was permissive. | fixed — it now states the concrete end conditions and the exactly-one-disconnect bound. |
| low | L2: the hold's end conditions were split across paragraphs. | fixed — the cleanup rules moved to a second ADDED requirement; the hold requirement lists its own ends. |
| low | L3: "no session key or record remains addressable" was vague. | fixed — no session remains addressable for the handle and the engine is erased. |
| low | L4: "delivered removal_response" was confusing. | fixed — "validated `removal_response` received by the requester". |
| low | L5: the no-reconnect claim reached peer-wide. | fixed — it is handle-scoped; the peer-wide consequence follows from the deleted trust record. |
| low | L6: the ADDED requirement exceeded 500 characters. | fixed — the cleanup rules split into "Removal delivery-hold cleanup". The two remaining long-requirement warnings match the repository's existing spec style and are accepted (non-strict validation). |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | M1: the in-flight scenario's THEN could be read as requiring a disconnect when the requester's connection ends, contradicting the hold rule. | fixed — "ends at the requester's connection end with no disconnect request, or at the bound with exactly one disconnect request". |
| low | L1: the concurrent-request scenario did not name the Chrome endpoint. | fixed. |
| low | L2: the hold opening read as a conditional even though the transport limitation is always true. | fixed — reworded as a statement of fact. |
| low | L3: the scenario title claimed requester facts the responder cannot observe. | fixed — "Requester connection ends". |
| low | L4: the snapshot-point scenario omitted the connection-end exit. | fixed — "governed by the hold's end conditions". |
| low | L5: hold selection was not tied to the authenticated installation id. | fixed — a hold keeps the authenticated peer installation id and a revocation selects holds only by that id. |
| low | L6: fail-closed in-flight had no scenario. | fixed — the in-flight scenario now covers reset or fail-closed recovery. |

### Round 3 — cosmetic confirmation, approve

The author made only the round-2 wording edits above. The confirmation found no normative behavior
change; the round-2 approval stands. The two remaining `--strict` failures are the long-requirement
warnings, which the repository's non-strict verification accepts.

#### specs/mutual-peer-removal/spec.md

Reviewed hash: `00150e06aaf91d1ffc0cb9d577cdd2c02dbc5cee147de0aba15bb05328b3dcd2`.

Approved content: specs/mutual-peer-removal/spec.md sha256 00150e06aaf91d1ffc0cb9d577cdd2c02dbc5cee147de0aba15bb05328b3dcd2

### Specs artifact approval

Approved content: specs sha256 00150e06aaf91d1ffc0cb9d577cdd2c02dbc5cee147de0aba15bb05328b3dcd2
