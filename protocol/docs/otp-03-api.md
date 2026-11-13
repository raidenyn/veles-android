# Rust pairing boundary (OTP-03)

This is a process-local API, **not** a Bluetooth wire format. Both JNI
`VelesCrypto.pairingCommand(byte[])` and WASM `pairing_command(Uint8Array)` use
exactly this interface. All multibyte API integers are big-endian.

Input starts with `version=1 (u8), opcode (u8)`:

| Opcode | Remaining input | Successful payload (after status) |
|---|---|---|
| 1 start | role u8 (A=1, B=2), six ASCII code bytes, canonical frozen context | handle u64, public point (65 bytes) |
| 2 exchange | handle u64, peer uncompressed point (65 bytes) | A: cA (32 bytes); B: empty |
| 3 confirm | handle u64, peer confirmation (32 bytes) | A: empty; B: cB (32 bytes) |
| 4 finish | handle u64 | Ke (16 bytes), once only |
| 5 close | handle u64 | empty |

Every response starts with one status byte: 0 success, 1 generic failure
(including expired/unknown handle, malformed context, wrong password, bad point,
wrong order), 2 entropy unavailable, 4 capacity exhausted. Failures contain no
payload. No password-sensitive diagnostic crosses this interface.

Rust owns secret scalars, transcript and confirmation state. Handles are
monotonically allocated, nonzero, process-local, never reused within a process;
they are not security credentials. At most eight executions exist per loaded
module; each expires 300 seconds from start using a monotonic clock. A command
prunes expired state; callers **must close** on disconnect, cancel, timeout and
page teardown (idle memory has no autonomous timer). Any failed operation on
an identifiable handle destroys it. `finish` and `close` always consume it.
Android's pairing-window admission/rate limits belong to OTP-10, above this
library. They must narrow this generic multi-session capacity.

The context must already contain the protocol's fresh nonces and negotiated
capabilities. Rust validates its canonical form; context disagreement is bound
into confirmations. Rust generates the scalar itself, using fallible OS entropy
or browser Web Crypto with bounded rejection sampling (128 draws). Callers cannot
provide a scalar, entropy, clock, or identity string in a production build.

Maximum input size is 4105 bytes (9-byte start header + 4096-byte context).
JNI checks array length before copying. WASM accepts a JS-owned `Uint8Array`
reference and checks its length before copying into linear memory. Oversized
calls copy only the ten-byte command prefix into a fixed bounded buffer, so an
identifiable handle is invalidated without an input-sized allocation. WASM
responses are copied into a JS-owned typed array before the Rust-owned response
is zeroized; no secret response Vec is freed by generated glue without clearing.

`Ke` is not a traffic key. It is available only after the peer confirmation
verifies; B receives its outbound cB in that same successful operation and must
send it before beginning enrollment. OTP-10 must derive the frozen enrollment
schedule from Ke and context, then erase Ke. It must never send an identity
record before confirmation. A cannot prove B received an outbound message; this
is the RFC's ordered mutual-confirmation boundary, not a third confirmation.

The caller owns copies of code, returned MACs and Ke. Clear mutable buffers after
use and avoid logging them. Rust uses `Zeroizing` for owned secrets and erases
the shared point; dependency-internal SHA/HMAC/KDF state, compiler temporaries,
and managed-runtime copies cannot promise complete memory erasure.
