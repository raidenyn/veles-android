# Review record: brainstorm

- **Artifact**: `brainstorm.md`
- **Reviewer**: Opus (Opus-5.5, medium)
- **Session preset**: [Opus]
- **Cap**: 5 rounds
- **Upstream**: none

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | H1: a held handle would schedule automatic recovery after the peer close because `ConnectorApp.handleChange` sees the previous status `connected`; the plan's "logical teardown unchanged" claim ignored this. | fixed — the hold-start mark and `handleChange` skip are now in Agreed Approach 7, with a connector-app regression test. |
| medium | M1: the role test using `session.removal` is fragile; `settleRemoval` runs before the `closeLink` branch, and a queued peer request can be misclassified as a requester. | fixed — the role now comes from the triggering record type carried by the engine's terminal effect. |
| medium | M2: the cleanup claim was wrong: `closeAll`/`closePeer` only see `byHandle`, so a held link would survive a reset or revocation. | fixed — the hold records the peer id; `closeAll` and `closePeer` finish selected holds with a disconnect, and `abortAll` clears without one. |
| medium | M3: missing tests for recovery, reset/revocation, role timing and the simultaneous-request race. | fixed — all four are listed in Agreed Approach 9 and the design's D7. |
| low | L1: the snapshot keeps the handle `connected` during the hold. | fixed — accepted and documented in Agreed Approach 6. |
| low | L2: the OTP-10 device run already supports the phone-close assumption. | fixed — cited in Open Questions. |
| low | L3: the WinRT path was asserted, not read. | fixed — now stated as not read in depth, with the hold independent of it. |
| low | L4: a failed response send was not addressed. | fixed — marked out of scope with the existing `send_failed` behavior. |
| low | L5: "Responser" typo. | fixed — "Responder". |

### Round 2 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | N1: reading `engine.removalPending` after the step cannot distinguish the roles; `#teardownInternal()` resets the guard before the owner applies the terminal effect. | fixed — the engine now sets an optional `holdForDelivery` on the `terminated` effect from the triggering record type (`REMOVAL_REQUEST` = responder), and the owner holds only for `STAGE_REMOVAL` with that flag. |
| low | N2: the hold mark's interaction with peer-keyed re-arming and with `closeAll`/`closePeer` was unstated. | fixed — Agreed Approach 7 now says the mark is consumed only when the handle disappears, so those disconnect paths also skip recovery, and a later unrelated search cannot target the deleted peer. |

### Round 3 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | L1: a hold whose handle already left `trackedConnections` could leave a stale mark until a full reset. | deferred — handles are not reused within a port generation and the mark is cleared on a full reset/port close; the design is expected to note the accepted stale mark. |

Reviewed hash: `d95c221184f9475e95e94a369245fecc303d15fd7067234105fdfd7f859c1e09`.

Approved content: brainstorm sha256 d95c221184f9475e95e94a369245fecc303d15fd7067234105fdfd7f859c1e09
