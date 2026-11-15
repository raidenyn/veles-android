## Why

Android and Chrome can pair (OTP-10) and reconnect (OTP-09), but neither endpoint can remove a
trusted peer: Android only has a bare repository `revoke`, Chrome can only cancel pending peers or
wipe everything, and the mutual-removal records (OTP2-REC-013) have no handler. Lost or corrupt
trust state is also not routed to a fail-closed action, so live sessions can outlive it. OTP-11
closes these gaps so real OTP delivery is not built on trust that cannot be withdrawn.

## What Changes

- **Peer listing and local revocation (both endpoints).** A revocation coordinator lists peers
  (fingerprint, label, device type, state, created and last-seen, connected) and revokes one peer
  in a fixed order: fence the id, delete the durable record (Chrome: its platform hint with it),
  terminate only that peer's authenticated sessions and bound enrollment connections, then sweep
  and release the fence. Other peers are untouched; the removed peer cannot reconnect. Labels,
  native handles and platform hints never select a peer or change identity, including for
  session termination.
- **Mutual removal (both endpoints).** Handlers for `removal_request` (0x0205) and
  `removal_response` (0x0206) on full sessions, with a new engine "close after reply" effect so the
  response is the last record, and a requester that deletes only after a valid response. Both
  sides call an OTP-history cleanup extension point (OTP2-REC-013) with no consumer yet; the
  history buffer itself stays with OTP-13.
- **Fail-closed recovery.** Store-level failures (missing key, mismatch, corruption, storage write
  errors) are split from peer-level rejections and routed to a `failClosed()` action that
  terminates every session and cancels every pairing connection, bound or not. Android gains an explicit, idempotent identity reset (decode-free
  `TrustStorage.reset()` plus `IdentityKeyStore.delete()`); Chrome reuses `reset()`. Reset runs
  only on an explicit user action, cancels every pairing connection and terminates every session
  first, and a failed delete or reset still terminates the affected sessions.
- **Re-pairing.** After revocation or reset, a fresh OTP-10 enrollment creates explicit new trust;
  no tombstone and no silent replacement.
- **Debug-only controls.** The existing Android debug pairing screen and Chrome debug pairing panel
  gain a peer list with Revoke, Mutual remove and Reset actions, kept out of release artifacts by
  the existing guards.

**Clear incomplete record**
- From: the debug clear reuses `REVOKE_PEER`/`repository.revoke` directly and may refuse while a
  session is open.
- To: it goes through the revocation coordinator, so it terminates matching sessions instead of
  refusing.
- Reason: revocation now owns session termination.
- Impact: debug-only, non-breaking.

No protocol, fixture or wire change: the removal frames, record policy and conformance fixture
already exist.

## Capabilities

### New Capabilities
- `peer-revocation`: peer listing, ordered local revocation with a per-peer fence, termination of
  only the matching sessions and enrollment connections, hint cleanup and explicit re-pairing.
- `mutual-peer-removal`: authenticated mutual removal records, the close-after-reply effect and
  the history-cleanup extension point (no consumer until OTP-13).
- `trust-store-failure-recovery`: store-failure classification, fail-closed routing and explicit
  identity reset on both endpoints.

### Modified Capabilities
- `protected-record-session` (three requirements):
  - "Teardown and key erasure" says ending a session for any reason leaves trust unchanged;
    local revocation, mutual removal and identity reset become explicit, scoped exceptions (the
    session ends because trust was deleted, never the reverse), and ordinary teardown still
    preserves trust.
  - "Authorized records without a handler fail closed" says release builds have no application
    handler; the removal handler is now installed in production for 0x0205/0x0206 only, while every
    other unhandled OTP application record still fails closed.
  - "Chrome serialized session execution" says one engine ending never affects other phones;
    a store-level trust failure becomes a scoped exception that terminates every session, while
    peer-level errors stay isolated.
- `pairing-debug-harness`: adds peer-management debug controls, changes the clear control to go
  through the coordinator, and extends the release exclusion guards to the new strings.

## Impact

- Android: `trust/` (exceptions, `TrustStorage.reset`, `IdentityKeyStore.delete`, fence,
  `onStoreFailure`), `sharing/SessionHost` (`closePeer`), `pairing/` (`boundPeerId`), `session/`
  (`closeAfterReply`, removal handler, composite forwarding), `SharingService` wiring and the debug
  source set; `releaseHookForbidden`.
- Chrome extension: `trust/` (error classes, fence, `onStoreFailure`), `connector/sessions.ts`
  (`closePeer`), `pairing/link.ts` (`boundPeerId`, `cancel`), `session/` (handler hook), the
  coordinator and the debug pairing panel; `bundle.test.ts` forbidden list.
- Tests and conformance suites on both endpoints; no new dependencies and no protocol changes.
