# Review record: design

- Artifact: design.md
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 4b4463045b9a541eada0adab14f893891adf1c61e70af3d14470ec4613492983

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | D8 has no effective shared concurrency boundary (private per-connection mutex) | fixed: per-peer fence, awaited termination, final sweep |
| high | Pending enrollment connections omitted from session selection | fixed: `PairingConnection.boundPeerId()` and enrollment cancellation in `closePeer` |
| high | Handler API cannot reply-then-close or terminate all matching sessions | fixed: defaulted `closeAfterReply`, terminal-reply engine path, asynchronous closePeer for other generations |
| high | Failure paths can leave live authorization | fixed: unconditional termination in `finally`; store-failure routing |
| medium | Android reset storage mechanism missing | fixed: decode-free `TrustStorage.reset()` plus `IdentityKeyStore.delete()`, idempotent order |

### Round 2 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Revoke before `addPending` can resurrect the record | fixed: fence blocks `addPending`; sweep; test revoke-before-addPending |
| high | Global store-failure routing and classification missing | fixed: store vs peer failure split with `onStoreFailure` (D6) |
| high | Failure-path deadlock inside a handler | fixed: current generation never awaited inside a handler (D2, D4) |
| medium | Chrome enrollment link ownership unspecified | fixed: link exposes `boundPeerId()`/`cancel()`, registered with the coordinator |
| medium | Composite handler would not forward `closeAfterReply` | fixed: forwarding plus composed-handler test required |

### Round 3 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Store-failure routing misses storage and identity boundaries (Room decode, `keys.read`) | fixed: classification at one choke point per repository |
| medium | Fence completion ambiguous for handler-driven removal | fixed: detached `finishRevocation` awaiting the current generation's `ended` signal |

### Round 4 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Classification missed failures after the operation (post-validation, durable write) | fixed: classify by what failed; write-failure tests on both endpoints |

### Round 5 — approve

No critical or high findings remain. The reviewer notes that implementation must keep peer-rejection classification through Room's blanket exception wrapper and apply Chrome's classification before post-operation validation (carried into the tasks).

Approved content: design sha256 555e091665837b759c16541d6fc2dbfcef0689491dd33300ae1c5c16a5f9280b

## Pass 2 — audit repair: complete upstream set

The Pass 1 approval header named only `brainstorm.md`. Under *Revision binding* the upstream set for
`design` is every planning file earlier in the order, so `proposal.md` belongs in it. Design content is
unchanged; this pass re-answers the three questions against the full upstream set so the latest approval
is correctly bound.

- Artifact: design.md
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes (this pass): brainstorm.md sha256 4b4463045b9a541eada0adab14f893891adf1c61e70af3d14470ec4613492983; proposal.md sha256 47323e92d7693f47187fa07f59bd21998e8eb736b9f6899b2bcedaff65e60d4c

### Round 1 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | design.md:137-145 — reset ordering omits admission blocking, overlapping-reset serialization and the cached-identity refresh that both implementations now include | deferred: the behaviour is implemented and covered (`SessionHost` reset admission barrier, `PeerRevocationCoordinator` serialized outcomes, identity cache refresh); editing design.md now would reopen the whole approval chain, so the text is corrected at the next design touch |
| medium | design.md:72-77 — the design names destructive `cancelPairings()` / link `cancel()`, while the implementation deliberately uses non-destructive `closeAll()` / `terminate()` for fail-closed and reset | rebutted: the approved plan's refinement (plan.md line 13) and ruling R8 require non-destructive teardown, because cancellation would delete incomplete trust records that fail-closed must keep |
| low | design.md:182-186 — D9 covers the new debug controls but not the proposal's migration of the existing Clear action through the coordinator | fixed in implementation (Task 12); the design text gap is deferred with the medium items |

VERDICT: approve. No critical or high findings.

Approved content: design sha256 555e091665837b759c16541d6fc2dbfcef0689491dd33300ae1c5c16a5f9280b
