## Design Summary

OTP-09 builds the **encrypted transport layer** between an Android phone and a Chrome
installation that already trust each other. It covers:

- versioned reconnect hellos and the canonical transcript
- P1363/low-S ECDSA signatures
- ephemeral P-256 ECDH and the HKDF reconnect key schedule
- encrypted `reconnect_confirm` key confirmation
- directional AES-256-GCM protected records with exact sequence enforcement
- protected terminal errors, teardown with key erasure, and fresh-key reconnect

There is no plaintext compatibility path. The wire contract is already frozen in
`docs/protocol/veles-v1.md` §4, §6, §7, §11.3 and §11.4. OTP-09 implements it; it does not
redesign it.

Where the pieces run (fixed by the RFC):

- **Android:** inside the existing `connectedDevice` sharing service (`SharingService` /
  `SharingGattServer`), using Android Keystore and JCA platform crypto.
- **Chrome:** inside the connector tab, using Web Crypto and the non-extractable IndexedDB
  identity key. The native bridge only carries opaque `record_base64` frames and never sees
  keys or plaintext.
- **Rust/WASM:** not used. They are reserved for SPAKE2 enrollment.

What counts as done (user): the real phone and the real Chrome send messages to each other
over the encrypted channel. A synthetic payload must visibly make the round trip.

## Alternatives Considered

### Alternative A: Shared Rust core for handshake and records (JNI + WASM)
- **Approach**: Put the transcript, key schedule and record layer in `rust/veles-crypto`, as
  is done for SPAKE2.
- **Pros**: One implementation; cross-language agreement comes for free.
- **Cons**: Contradicts the RFC. Normal sessions use platform crypto, and identity private
  keys "never pass through Rust or WASM on Android and desktop". Signing would still have to
  leave Rust, which splits the handshake across a boundary that handles secrets.
- **Why not chosen**: It conflicts with the RFC and only removes work that the existing
  cross-language vectors already cover.

### Alternative B: Split delivery into separate Android and Chrome PRs
- **Approach**: Land the Kotlin session first, then the TypeScript session.
- **Pros**: Smaller reviews.
- **Cons**: Neither PR can show the end-to-end encrypted round trip, so the real-hardware
  evidence would wait for the second PR.
- **Why not chosen**: The user asked for one fully tested PR.

### Alternative C: Prove the channel with heartbeats only
- **Approach**: The physical check uses only `app_heartbeat` (empty body) plus a reconnect.
- **Pros**: No synthetic payload source needed; it already proves encryption and ordering.
- **Cons**: Nothing visible makes the round trip, and the `full`-class application records
  are never exercised.
- **Why not chosen**: The user wants a synthetic payload to visibly make the round trip.

## Agreed Approach

Two independent platform-crypto implementations: Kotlin/JCA on Android and TypeScript/Web
Crypto in the connector tab. Both are held to the same committed OTP-02 vectors and state
scenarios in `protocol/v1/fixtures/`, and both are wired into the real transport. Everything
ships in **one fully tested PR**.

Proving it on real hardware needs two **debug-only** pieces that are absent from release
builds:

1. **Trust injection.** Before OTP-10 enrollment exists, a debug hook on each side exports
   the local identity and installs the other side's record directly as `active`.
2. **Synthetic probe.**
   - The phone answers `pull_request` with `pull_events` carrying one made-up §8.1 event.
     It can also send an adb-triggered `push_event`.
   - Chrome decrypts and strictly parses the event, then sends `event_ack`.
   - The connector tab shows the result in a debug-only readout, and the phone's debug
     logcat prints a matching line.

Claude runs the physical check itself through the homepc tools (Chrome, Windows, macOS,
Android emulator, two real devices; adb for the phone) and records the evidence.

## Key Decisions

- **The OTP-02 reconnect vector is corrected in a clearly labelled first commit of the
  OTP-09 PR** (user). An external review (GPT-6.1 Sol) found that the generator and oracle
  put the identity key into the hello where OTP2-REC-020 requires the ephemeral key. See
  design.md D0.
- **Platform crypto, written twice.** Kotlin and TypeScript each implement the codec,
  transcript, key schedule and record layer, and both must pass the same vectors. (RFC)
- **The Chrome session lives in the connector tab**, not the service worker or the bridge.
  (RFC)
- **One fully tested PR.** (user)
- **Debug-only trust injection is acceptable** for the physical check before OTP-10.
  (user) A release-build guard proves it is absent from the release APK and the production
  extension bundle. (Claude's call)
- **Synthetic payload round trip:** pull → pull_events → ack, plus a phone-initiated push.
  It uses a debug-only one-event synthetic source, separate from OTP-12's Gate B
  five-record fixture and from OTP-13's buffer. (user and Claude)
- **The result is visible in a debug-only connector readout and in debug logcat.** (user)
- **Claude runs the physical verification through homepc/adb.** (user)
- **Scope boundary** (Claude's call; see design.md):
  - Session class selection implements every row of the §11.3 table.
  - Only `full` sessions and the class-independent framework records do anything in
    OTP-09.
  - Activation-recovery exchanges go to OTP-10.
  - Revoking a connected peer and the removal exchange go to OTP-11.
  - OTP-09 still fixes the correct class and rejects records that class does not
    authorize, so those paths fail closed.
- **The phone-side session must work for any client.** Nothing in it may assume the peer is
  Chrome, so the watch client and the OTP-27 harness can reuse it. (RFC, OTP-12)

## Open Questions

- None blocking. The builder check items and the external review findings (1–8) are folded
  into design.md.
