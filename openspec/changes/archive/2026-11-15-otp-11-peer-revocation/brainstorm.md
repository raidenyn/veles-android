## Design Summary

OTP-11 (issue #93, RFC section OTP-11) lets each endpoint, the phone and the Chrome extension,
inspect its trusted peers and remove one without affecting the others, and makes lost or corrupt
identity/trust state fail closed.

Each endpoint gets a small **revocation coordinator** above its existing trust repository and
session layer (Kotlin on Android, TypeScript in the extension). It provides:

- **Peer listing:** id, label, fingerprint, device type, state, created/last-seen, connected.
- **Local peer revocation, in a fixed order:** fence the peer, delete the durable record (the hint
  goes with it on Chrome), close only the matching sessions and enrollment connections, then
  sweep. (Identity reset runs in the reverse spirit: terminate everything first, then delete.)
- **Mutual removal over OTP2-REC-013:** `removal_request` (0x0205) / `removal_response` (0x0206) on a
  full-authorization session; the requester deletes only after a valid response.
- **Key-loss recovery:** an explicit user-initiated reset on Android (new `resetIdentity`, Chrome
  already has `reset()`); every unreadable-state error is reported as "re-pair required".
- **Re-pairing:** revoke first, then a fresh OTP-10 enrollment. No tombstone, no silent replacement.
- **Debug-only controls:** peer list with Revoke / Mutual remove / Reset on both endpoints,
  gated out of release builds like the OTP-10 pairing controls. The final UI belongs to OTP-16/17.

Observed in the code (not assumptions):

- Android `TrustRepository` already has `peers()`, fingerprints, labels, timestamps and a bare
  `revoke(peerId)`. `SessionHost` has `hasOpenSession` and `authenticatedSessions` but no way to
  close one peer's sessions. There is no identity reset: `AndroidIdentityKeyStore` can generate but
  not delete, and a surviving key without its metadata throws `TrustException`.
- `ChromeTrustRepository` has `revoke`, `forgetHint`, `revokePending` and `reset`, but nothing
  outside `trust/` composes them. `SessionManager` can close a handle but not by peer.
- The removal frames, record policy (FULL only), codec, `states.json` entries and the
  `st-rec-removal-mutual-authenticated` fixture exist. Neither endpoint has an application handler
  for them, and nothing sends them.
- There is no OTP history buffer yet (OTP-13), so "erase history references" has nothing to erase;
  the coordinator exposes an extension point for it.

## Alternatives Considered

### Alternative A: Revocation coordinator per endpoint
- **Approach**: A service above the trust repository and session layer that owns ordering
  (delete, then disconnect) and the mutual-removal exchange.
- **Pros**: One tested place for ordering and failure semantics; reuses existing repositories;
  handlers stay small; each endpoint testable without the other.
- **Cons**: Two coordinators and debug wiring to write.
- **Why not chosen**: n/a, this is the chosen approach.

### Alternative B: Reactive teardown
- **Approach**: The repository only deletes; the session layer watches the trust store and drops
  sessions whose peer vanished.
- **Pros**: Any deletion path disconnects for free.
- **Cons**: Adds an observer to both stacks and a window where a live session outlives its record;
  does not help the ordered mutual-removal exchange.
- **Why not chosen**: Weaker for fail-closed and harder to test than an explicit ordered flow.

### Alternative C: Everything inside the session engines
- **Approach**: Revocation becomes a session-engine operation.
- **Pros**: Single owner of session state.
- **Cons**: Couples storage, listing and sessions into protocol engines; largest rewrite; the
  Android and Chrome engines differ.
- **Why not chosen**: Hard to test in isolation and more invasive than the task needs.

## Agreed Approach

Alternative A, chosen by the user, with debug-only controls (option 2 of the scope question) so
the flows can be exercised on a device without final UI. The user picked these two options
explicitly; the remaining design choices in `design.md` are the assistant's calls.

## Key Decisions

- Delete the durable record before closing sessions, so a crash in between leaves no trust;
  sessions are terminated even when the delete or reset fails, and when trust is first observed
  unavailable.
- Revocation also terminates enrollment connections bound to the peer, not only authenticated ones.
- Mutual removal never deletes the requester's record until a valid `removal_response`; a lost
  response keeps it (offline removal semantics).
- The responder deletes, replies and closes through a new engine "close after reply" effect, so
  the response is the last record; the requester accepts only the response (and heartbeat/error)
  after sending the request; an unsolicited `removal_response` is a protocol violation.
- No protocol or fixture change; the contract already covers removal.
- Android gets `resetIdentity()`: a decode-free `TrustStorage.reset()` plus `IdentityKeyStore.delete()`,
  idempotent and fail-closed at every interruption, only on explicit user action.
- Guard lists (`releaseHookGuard`, `bundle.test.ts`) get the new debug strings.

## Open Questions

- None blocking. Concurrent revocation during an in-flight pairing is handled by a per-peer fence
  that blocks `addPending` while a revoke runs, termination of the connections bound to the peer
  (including enrollment connections) and a final sweep; see design.md D3 and D8. Store-level
  failures (as opposed to peer-specific rejections) are split into their own exception type and
  routed to a fail-closed action; see design.md D6.
