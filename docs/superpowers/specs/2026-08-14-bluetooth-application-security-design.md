# Bluetooth application-layer security design

## Status

Approved on 2026-08-14. This design supersedes the encrypted-GATT and
hard-coded HMAC authentication assumptions in the earlier Bluetooth roadmap and
spike documents. It does not authorize real OTP transport until the release
gates in this document are complete.

## Context

Physical testing established that Chrome Web Bluetooth on Windows cannot
reliably use GATT attributes protected by authenticated BLE pairing. The same
transport works with plain GATT permissions, while macOS handles the protected
attributes correctly. Production confidentiality, integrity, peer
authentication, and replay protection therefore have to be independent of BLE
link-layer security.

The application pairing challenge must happen once. Chrome will still require
the user to select a Bluetooth device after a connector page is reopened, but
that selection must be followed by fast automatic authentication with no new
code entry or approval prompt.

## Goals

- Authenticate a specific Veles Android installation to a specific Chrome
  extension installation over an actively attacked plaintext BLE link.
- Use a short one-time code without exposing it to offline guessing from the
  BLE transcript.
- Persist trust after first pairing while deriving fresh traffic keys for every
  connection.
- Encrypt and authenticate every production command, response, heartbeat,
  error, and OTP event.
- Reject replayed, reordered, duplicated, malformed, and cross-session records.
- Support multiple trusted Chrome installations per phone and multiple trusted
  phones per extension, with explicit per-peer revocation.
- Require re-pairing after app-data loss, extension removal, or trust-store
  loss; trust keys are not backed up or synchronized.

## Non-goals

- Hiding traffic timing, connection metadata, service UUIDs, or message sizes.
- Protecting an OTP after either endpoint, Android OS, browser profile, or
  extension process is compromised.
- Replacing Chrome's mandatory `requestDevice()` chooser.
- Relying on Bluetooth addresses as durable identity.
- Designing new cryptographic primitives or implementing PAKE independently in
  Kotlin and JavaScript.
- Sending real OTPs through the current synthetic spike.

## Threat model

The protocol protects against passive BLE capture, active man-in-the-middle,
message modification, replay, peer impersonation, and an untrusted nearby BLE
client. It assumes the user reads the one-time code from the unlocked phone and
enters it into the intended connector page. Online code guesses remain possible
and are controlled through short expiry, attempt limits, and user-visible
pairing state.

An attacker with the Android Keystore key, Chrome profile access sufficient to
use the non-extractable browser key, endpoint code execution, screen access, or
notification access is outside this transport threat boundary.

## Cryptographic implementation

### OPAQUE core

First pairing uses OPAQUE from RFC 9807. A single pinned Rust wrapper around
`opaque-ke` is compiled as an Android NDK library behind a narrow JNI API and as
locally packaged WASM for the MV3 extension. No remote code, runtime download,
or independent PAKE implementation is allowed.

The baseline profile is OPAQUE-3DH with the RFC 9807 Ristretto255/SHA-512
configuration and Argon2id v1.3 key stretching (64 MiB, three iterations,
parallelism one). The wrapper fixes and versions the profile, identities,
context bytes, RFC binary serialization, maximum input sizes, dependency
versions, and error mapping. A dependency upgrade that changes any encoded
OPAQUE state is a protocol migration.

The current `opaque-ke` release is standards-based and predecessor versions
have received security review, but that does not establish audit coverage for
the exact production wrapper and pinned dependency graph. A focused external
review of the Rust wrapper, JNI boundary, WASM artifact, and protocol use is a
release gate.

### Platform crypto

OPAQUE is limited to first pairing. Persistent identity and normal-session
cryptography use platform APIs:

- P-256 ECDSA for persistent installation identity signatures.
- P-256 ECDH for fresh per-connection key agreement.
- HKDF-SHA-256 for session key separation.
- AES-256-GCM for application records.
- SHA-256 for transcript and identity fingerprints.
- Cryptographically secure platform randomness for all keys and nonces.

Android uses JCA and Android Keystore. Chrome uses Web Crypto. Private identity
keys are generated non-exportable and are never passed through Rust/WASM.

## Installation identity and storage

Each installation creates one random 128-bit installation ID and one persistent
P-256 ECDSA identity key pair.

Android stores the non-exportable private key in Android Keystore. It stores
peer installation IDs, public keys, labels, fingerprints, creation time, and
last-seen time in app-local storage. Identity metadata and all trust records are
excluded from Android cloud backup and device-to-device transfer.

Chrome stores its non-extractable private `CryptoKey` in IndexedDB. It stores
peer public records in local, non-sync extension storage whose access is
restricted to trusted extension contexts. Content scripts cannot access either
store.

Public peer records are not confidential, but their integrity is security
critical. A record is accepted only when its installation ID and public key
were bound by a completed pairing transcript. Bluetooth names and addresses are
display hints, never trust identifiers.

Loss or corruption of either trust store fails closed and requires pairing
again. No recovery or key synchronization mechanism is provided.

## First pairing

### Pairing preparation

The user starts pairing from the unlocked Veles app. Android creates a random
six-digit decimal code, displays it with a five-minute countdown, and locally
pre-registers an ephemeral OPAQUE credential record for that code by running
both sides of the RFC registration calculation inside the trusted phone. This
avoids transmitting OPAQUE registration over BLE, because RFC 9807 requires a
server-authenticated confidential registration channel.

The pending record is bound to a random pairing ID, the Veles protocol context,
the phone installation ID, and the exact OPAQUE profile. It is held only while
the pairing screen is active and is deleted after success, expiry, cancellation,
app restart, or the attempt limit.

One pairing ID permits at most five online attempts. Android also enforces a
bounded global attempt rate and only one active pending pairing per phone. Rate
limits use monotonic time and generic failures so callers cannot distinguish
unknown, expired, malformed, and incorrect-code states.

### OPAQUE authentication

Chrome enters the code and acts as the OPAQUE client. Android acts as the
OPAQUE server using the pre-registered one-time record. Identities and context
bind both installation IDs, pairing ID, protocol version, role labels, and
ciphersuite. Both peers remain unauthenticated until OPAQUE key confirmation is
complete.

The short code is never sent, logged, persisted by Chrome, or used directly as
an encryption key. The BLE transcript does not support an offline code check.
The OPAQUE session key is used only for the enrollment exchange and is erased
after pairing.

### Identity enrollment

After OPAQUE confirmation, both sides exchange versioned enrollment records
containing installation ID, user-visible label, identity public key, public-key
fingerprint, and fresh nonce. The records are encrypted and authenticated under
direction-specific keys derived from the OPAQUE session key and the complete
pairing transcript.

Each side confirms a hash of both enrollment records before activating trust.
Android additionally requires the pairing screen to remain active. Each local
trust-store update is atomic, and a prepare/commit/ack exchange keeps records
pending until peer confirmation. A disconnect can leave one inactive pending
record, which expires without granting access; distributed atomicity is not
claimed. A pending pairing cannot replace an existing record for the same
installation ID with a different key; replacement requires explicit revocation
first.

The code, pending OPAQUE record, OPAQUE state, and enrollment traffic keys are
erased after final confirmation.

## Normal connection

After Chrome's device chooser and GATT setup, each peer sends a versioned
session hello containing its installation ID, a fresh P-256 ECDH public key, a
fresh 128-bit nonce, supported protocol version, and role. Unknown or revoked
installation IDs receive no protected application data.

The peers form one canonical binary transcript containing:

- protocol and schema version;
- role labels and both installation IDs;
- both ephemeral ECDH public keys;
- both random nonces;
- negotiated capabilities and ciphersuite;
- phone service identity and connector extension identity.

Android signs the transcript hash with its persistent ECDSA identity key.
Chrome verifies the stored phone public key, then signs the same transcript.
Android verifies the stored connector public key. Signatures use fixed-width
64-byte IEEE P1363 `r || s` encoding with strict scalar bounds and low-S
normalization so JCA and Web Crypto have one canonical representation. Neither
peer marks the session authenticated before both signatures validate.

The ECDH output is passed to HKDF-SHA-256 with the transcript hash as salt and
versioned purpose strings. It derives:

- client-to-phone AES-256-GCM key;
- phone-to-client AES-256-GCM key;
- one four-byte nonce prefix per direction;
- a 128-bit session ID;
- a key-confirmation value per direction.

The peers exchange encrypted key-confirmation records before any command or OTP
record. Persistent identity keys are reused across connections, but ECDH keys,
traffic keys, nonce prefixes, session IDs, and sequence counters are always
fresh. No user code or app-level approval is needed on a normal reconnect.

## Wire encoding

The security protocol does not sign or authenticate JSON. All multibyte
integers use network byte order. Variable fields use an unsigned 16-bit byte
length followed by exactly that many bytes; text is UTF-8 after rejecting
invalid encoding and control characters. Unknown enum values, duplicate fields,
trailing bytes, non-canonical lengths, and oversized values are errors.

Every complete message starts with this fixed outer header:

```text
magic[4] = "VLBT"
protocol_version: u16
message_type: u8
flags: u8
body_length: u32
body[body_length]
```

Protocol version 1 permits a maximum 4096-byte body. OPAQUE request, response,
and confirmation bytes are embedded exactly as emitted by the pinned RFC 9807
wrapper and are capped at 2048 bytes. Installation IDs and session IDs are 16
bytes, random session nonces are 16 bytes, P-256 public keys use 65-byte SEC1
uncompressed encoding, SHA-256 fingerprints are 32 bytes, and ECDSA signatures
use the canonical 64-byte P1363 encoding.

Cryptographic transcripts are not the outer encoding. They use the ASCII domain
separator `Veles Bluetooth v1`, one zero byte, then the specified fields in
protocol order, each encoded as `u16 length || bytes`. Fixed role labels are
`phone` and `connector`. Capabilities are sorted by numeric identifier before
encoding. This produces one unambiguous transcript on Rust, Android, and Chrome.

The fragmentation header is versioned separately from the security protocol and
uses a 16-bit chunk index and count. A peer may hold at most two incomplete
messages and 8192 reassembly bytes; the process may hold at most 16 incomplete
messages and 64 KiB. Incomplete messages expire after five seconds. Each peer's
outbound queue is capped at 32 messages and 32 KiB, with global caps of 128
messages and 128 KiB. The service accepts at most eight simultaneous clients.
Crossing any bound rejects the new work without evicting an authenticated
peer's existing complete record.

## Protected records

Every post-handshake command, response, push, pull, heartbeat, acknowledgement,
and error is an AES-256-GCM record. Its outer body contains this canonical
header followed by ciphertext and the 16-byte GCM tag:

```text
schema_version: u8
direction: u8
record_type: u8
reserved: u8 = 0
session_id[16]
sequence: u64
ciphertext_length: u16
ciphertext_and_tag[ciphertext_length + 16]
```

The 96-bit GCM nonce is the direction-specific four-byte prefix followed by the
unsigned 64-bit sequence number in network byte order. Sequence zero is the
first key-confirmation record; the counter increments once for every attempted
record and never wraps.

Associated data binds the protocol version, schema version, session ID,
direction, sequence number, record type, and ciphertext length. Record type is
therefore authenticated and cannot be changed by an intermediary.

Each receiver accepts exactly its next sequence number. Duplicate, skipped,
reordered, unauthenticated, oversized, unknown-type, or cross-session records
terminate the session and erase traffic keys. The protocol deliberately has no
replay window or in-session retransmission. GATT commands use writes with
response; a failed notification or missing record reconnects and creates a new
session.

The existing BLE fragmentation layer remains transport-only. Framing message
IDs are not security counters.

## Multiple peers and revocation

One phone may trust multiple connector installations and one extension may
trust multiple phones. Each peer has an independent public-key record, session,
sequence space, delivery queue, and last-seen value.

Both applications expose a paired-device list with label, installation ID
fingerprint, creation time, and last seen time. Revocation deletes only the
selected peer record and immediately disconnects any matching live session.
The next connection from that installation is unknown and requires a new
one-time pairing.

Changing a device label does not change identity. Reinstalling either endpoint
creates a new identity. Automatic trust migration, key replacement, and backup
restore are prohibited.

## State and failure handling

- Starting a pairing attempt never deauthenticates or replaces an established
  peer or live authenticated session.
- Starting a normal handshake clears any prior authentication state on that
  physical connection.
- All malformed cryptographic inputs are rejected before state mutation.
- Ephemeral PAKE, ECDH, HKDF, and traffic-key material is zeroized where the
  runtime permits and references are released on every terminal path.
- Logs contain protocol stage, stable non-secret error code, and timing only;
  never codes, keys, public-key encodings, plaintext OTPs, or ciphertext.
- Pre-authentication messages expose only bounded capabilities, random IDs and
  nonces, and public keys. OTP state is inaccessible before confirmation.
- Clock changes cannot extend pairing attempts or timeouts; monotonic clocks are
  used for runtime expiry.
- Unsupported versions fail closed. No downgrade to the synthetic public HMAC
  key or plaintext application messages exists in production builds.

## User experience

First pairing requires four explicit actions: start pairing on Android, select
the phone in Chrome's chooser, enter the phone's six-digit code in the connector,
and wait for both sides to confirm. Successful pairing names the peer and adds
it to both paired-device lists.

Later page openings still require Chrome's chooser because Web Bluetooth does
not persist a live connection. After selection, authentication and encryption
are automatic and should complete within the same perceived connection step.
The user sees a pairing prompt only for an unknown or revoked installation.

## Testing and release gates

### Protocol correctness

- RFC 9807 vectors and project-specific fixed-randomness vectors pass in native
  Rust, Android JNI, and browser WASM.
- The exact wrapper produces identical registration, login, transcript,
  enrollment, HKDF, record, and error outputs on both platforms.
- Tests cover wrong code, expired code, attempt exhaustion, malformed group
  elements, identity mismatch, transcript mismatch, unknown peer, revoked peer,
  signature failure, AEAD failure, duplicate/skip/reorder, counter boundary,
  reconnect, crash, and storage loss.
- Property and fuzz tests cover every binary decoder, state transition, frame
  reassembler, and JNI/WASM length boundary.

### Storage and lifecycle

- Android backup and transfer tests prove identity and peer stores are excluded.
- Android tests prove the private identity key is non-exportable and unusable
  after app data is cleared.
- Chrome tests prove the private key is non-extractable, survives browser
  restart, is not synchronized, is inaccessible to content scripts, and is
  removed with the extension/profile data.
- Revocation and re-pairing work independently for multiple phones and multiple
  connectors.
- No sequence or nonce state is reused after reconnect, tab closure, browser
  restart, process death, service restart, or counter failure.

### Performance and packaging

- The pinned OPAQUE KSF profile is benchmarked on representative minimum and
  median Android devices and supported desktop browsers. First pairing targets
  three seconds per endpoint; exceeding that budget blocks release or requires
  a separately reviewed protocol-profile revision rather than an unreviewed
  runtime downgrade.
- Normal signed-ECDH reconnect completes within the existing connection UI
  budget and does not require a user challenge.
- Final APKs include only intended Android ABIs. The extension packages WASM
  locally, declares only the required MV3 CSP, and contains no remote executable
  code.
- Dependency licenses, reproducible hashes, SBOM, and update policy are recorded.

### Security review

Before real OTP enablement, an external reviewer assesses the threat model,
OPAQUE profile and pre-registration use, Rust wrapper, dependency versions,
binary schema, identity enrollment, platform key storage, HKDF schedule,
AES-GCM nonce construction, replay policy, revocation, JNI, WASM, and build
artifacts. Findings are fixed and the exact release candidate is re-reviewed as
required.

## Rollout

The plaintext-GATT synthetic spike remains isolated from production OTP data.
Implementation proceeds behind a debug-only security harness with deterministic
cross-platform vectors. A second physical matrix validates first pairing,
automatic reconnect, multiple peers, revocation, Windows/macOS behavior,
background lifecycle, and failure recovery under the encrypted application
protocol.

Production OTP transport remains disabled until all automated, physical,
storage, packaging, and security-review gates pass. There is no compatibility
mode that sends OTPs using the current hard-coded test key or plaintext records.
