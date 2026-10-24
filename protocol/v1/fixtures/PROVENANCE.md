# Crypto fixture provenance (OTP-02 Plan Task 5)

Normative source: `docs/protocol/veles-v1.md` (the sole frozen authority for
wire encodings). This PROVENANCE file, the Task 5 fixture set, generators and
cross-checkers are a DRAFT pending controller/final review: they record
evidence, make no acceptance claim of their own, and can never override the
normative document. This file records implementations, dependency pins, exact
generation/verification commands and run evidence for the deterministic
cryptographic vectors in `crypto.json`. Rules this provenance implements: OTP2-FIX-005 (published RFC
vectors or independently cross-checked derivations; missing cross-checks leave
vectors explicitly unverified and block acceptance) and OTP2-FIX-004 (a state
or trace pass is never cryptographic evidence).

## 1. What the fixtures contain and where each class of expected value comes from

| Fixture class | Covers | Expected-value source |
|---|---|---|
| `rfc9382-p256-sha256-hkdf-hmac-{server-client,nil-identities}` | SEC1 uncompressed points, RFC `len()` u64 **little**-endian TT framing, fixed-width BE `w`, `Ke`/`Ka` split of `Hash(TT)`, `KcA`/`KcB` HKDF with `"ConfirmationKeys"` info (16+16 bytes for SHA-256), `cA`/`cB` HMACs | **Published RFC 9382 Appendix B vectors, quoted verbatim** (first vector `A='server', B='client'`; fourth `A='', B=''`). Reproduced byte-for-byte by each of the three implementations listed in section 2 (per-fixture canonical SHA-256 digests bind every report to the exact committed bytes). No Veles-specific value appears in these vectors. |
| `veles-pairing-code-{000000,123456}` | code→`w` transform (OTP2-CRY-004a) incl. leading-zero code, public context bytes (OTP2-CRY-012), TT, `K`, Ke/Ka, KcA/KcB, cA/cB with Veles identities and `ctx` as AAD | Derived from fixed synthetic inputs; **every intermediate independently recomputed and compared** by the Python oracle (affine P-256 from scratch) against the noble-JS derivation. |
| `veles-enrollment-schedule` | enrollment salt/PRK/six Expand outputs (OTP2-CRY-007a/b), nonces (007c), session prefix (020a), public ET (020b), identity bodies + `fp_self` (REC-010/011a), `fp_pair` + display (REC-011), activation MACs (CRY-019a) | Same dual-implementation derivation. Label byte lengths asserted by generator, oracle, openssl-check and tests. |
| `veles-reconnect-authentication` | hello payloads, `rbase`, digest, deterministic RFC 6979 ECDSA P1363 low-S signatures over `SHA-256(rbase)` hashed **exactly once**, ECDH `ss` (32-byte X), reconnect salt/PRK and all seven Expand outputs, recovery activation keys (CRY-019f), confirm input, `conf_a`/`conf_b` | noble-JS derivation; Python oracle independently signs (its own RFC 6979) and verifies; **Node/OpenSSL independently recomputes ECDH/HKDF/HMAC and verifies both signatures** — including the prehash-semantics probe (a signature over `SHA-256(SHA-256(rbase))` must NOT verify over `rbase`). |
| `veles-record-{reconnect-confirm,push-event}` | 23-byte header as AAD, nonce = 4-byte prefix ‖ u64be sequence, AES-256-GCM ciphertext/tag, full record bytes | noble-ciphers derivation; **Node/OpenSSL recomputes ciphertext+tag and round-trips the decryption** (OpenSSL vs noble-ciphers are two independent implementations; stdlib Python has no AES). |
| 16 negatives | wrong code, context mismatch, role swap, role mismatch, off-curve point, identity element, compressed point, shared-element-identity abort (OTP2-CRY-003a: a peer point `pB = w·N` / `pA = w·M` collapses the shared group element; the attempt abort is proven generically before any KDF output), high-S, DER, truncated signature, tag/session/direction/sequence mutations | Each negative carries a `deviation` referencing a positive base; rejection is independently proven: derived-and-compared intermediates (oracle re-derivation of the mutated inputs), strict validators in BOTH implementations, or OpenSSL AEAD/verify failures. `high_s`/`der_encoding` additionally record the premise that OpenSSL **verifies** the mathematically valid variant — the rejection is Veles's canonical-encoding policy. |

## 2. Implementations

1. **JS engine (generation + in-suite recomputation)** — pure-JS bigint
   arithmetic from the `@noble` family: `@noble/curves@2.4.0` (P-256 points,
   RFC 6979 ECDSA via hmac-drbg), `@noble/hashes@2.4.0` (SHA-256, HMAC, HKDF
   extract/expand), `@noble/ciphers@2.4.0` (AES-256-GCM). All three are exact-
   pinned (no ranges) in `protocol/package.json` and locked in
   `protocol/package-lock.json`; each package declares MIT individually
   (`@noble/curves`, `@noble/hashes`, `@noble/ciphers`); `@noble/curves`
   depends only on `@noble/hashes@2.4.0`.
2. **Python oracle (test-only)** —
   `protocol/tools/crosscheck-vectors/oracle.py`: stdlib-only (`hashlib`,
   `hmac`, `json`), from-scratch **affine-variable-time** P-256 (chord/tangent
   formulas), independent HKDF/HMAC/SHA-256, independent RFC 6979 signing and
   affine ECDSA verification. It is deliberately variable-time and must NEVER
   be imported by product code. Python version in the acceptance environment:
   3.14.4 (stdlib APIs used exist across 3.8+; no third-party packages).
   Its P-256 generator `y` constant was verified against OpenSSL-generated
   `(d, Q)` keypairs and by reproducing the RFC anchors.
3. **Node/OpenSSL cross-check (test-only)** —
   `protocol/tools/crosscheck-vectors/openssl-check.mjs`: Node built-in
   `crypto` (OpenSSL) — HKDF primitives over OpenSSL HMAC (Node ships no
   expand-only API, so RFC 5869 expand is an HMAC loop over OpenSSL
   primitives), ECDH via SEC1 DER key import, ECDSA P1363 sign/verify,
   AES-256-GCM. Facts: the Python oracle and the OpenSSL check share no source
   code, no constants beyond the RFC-published points and published P-256
   curve parameters, and no derived intermediates; the noble engine is
   cross-checked by both of them independently. There is exactly ONE
   OpenSSL-backed implementation in this acceptance chain — the two OpenSSL
   uses elsewhere in the repository's verification tooling are not part of it
   and were never counted.

## 2a. Report binding (stale-report protection)

Both report writers and the verifier bind every report to the exact fixture
bytes via canonical JSON SHA-256 digests (`canonicalJson`: key-sorted, compact
separators, non-ASCII escaped like Python's `ensure_ascii`; digests are bare
hex). Each report carries `tool` (exact identity), `independent_implementation`,
`input_file`, `input_sha256`, `fixtures_sha256` (whole array) and a per-fixture
`fixture_sha256` map over canonical `{id, input, expected}`. `verifyCryptoFixtures`
recomputes the digests and refuses any entry whose digest, tool identity, or
output list does not match the current fixture set: stale reports produced
against older bytes can never approve a changed fixture, and the committed
output set must be EXACTLY the per-kind required set — dropped or extra
expected keys fail recomputation outright, and the union of outputs recorded
by the required tools must cover the full required set. Node v26.12.1, OpenSSL 3.5.x (bundled with
   Node; recorded in the report header).

Nothing in `protocol/checks/` or `protocol/tools/` is imported by product code
(OTP2-FIX-006); the Python oracle additionally must never be ported into a
runtime path.

## 3. Exact commands (acceptance chain, from the repository root)

```sh
# 1. Regenerate the deterministic candidate (fixed synthetic inputs; never
#    writes into protocol/v1/fixtures/). Fails immediately if the noble path
#    does not reproduce the published RFC 9382 anchors verbatim.
node protocol/tools/generate-vectors.mjs

# 2. Candidate cross-checks (provenance evidence for generation):
python3 protocol/tools/crosscheck-vectors/oracle.py build/protocol/crypto-candidate.json
node protocol/tools/crosscheck-vectors/openssl-check.mjs

# 3. ACCEPTANCE cross-checks against the COMMITTED expected bytes
#    (protocol/v1/fixtures/crypto.json). These run the same independent
#    implementations over the committed data; mutating any committed value,
#    label or input fails here before tests run.
python3 protocol/tools/crosscheck-vectors/oracle.py protocol/v1/fixtures/crypto.json
node protocol/tools/crosscheck-vectors/openssl-check.mjs protocol/v1/fixtures/crypto.json

# 4. Full crypto suite (includes live invocation of both committed
#    cross-checks, candidate↔committed equality and mutation sensitivity):
node --test protocol/checks/crypto.test.mjs

# 5. Whole contract suite (Tasks 2–5):
node --test protocol/checks/contract.test.mjs
node --test protocol/checks/bridge.test.mjs
node --test protocol/checks/state.test.mjs
node --test protocol/checks/crypto.test.mjs
```

Reports land under `build/protocol/` (gitignored, regenerated by the chain):
`crosscheck-oracle.json` + `crosscheck-openssl.json` (candidate),
`crosscheck-oracle-committed.json` + `crosscheck-openssl-committed.json`
(acceptance). `verifyCryptoFixtures` gates `verified_ids` on the committed
reports; the candidate reports attest the generation provenance and are tied
to the committed bytes by the suite's equality test.

## 4. Run evidence (actual logs, 2026-10-24)

```
$ node protocol/tools/generate-vectors.mjs            # review fix wave run (2026-10-24)
[1/4] reproducing RFC 9382 Appendix B published anchors verbatim...
      ok: rfc9382-p256-sha256-hkdf-hmac-server-client
      ok: rfc9382-p256-sha256-hkdf-hmac-nil-identities
[2/4] deriving Veles-specific vectors from fixed synthetic inputs...
      ok: spake2 pairing (code 000000, code 123456)
      ok: enrollment schedule, ET, fingerprints
      ok: reconnect rbase/ECDH/signatures/HKDF/recovery keys
      ok: protected records (reconnect_confirm at seq 0, push_event at seq 5)
[3/4] deriving negative deviation vectors...
[4/4] writing candidate to .../build/protocol/crypto-candidate.json
      24 fixtures: 8 positive (incl. 2 published anchors), 16 negative
done

$ python3 protocol/tools/crosscheck-vectors/oracle.py protocol/v1/fixtures/crypto.json
oracle: 18 verified, 6 delegated to openssl-check, 0 failed (anchor self-check: ok)

$ node protocol/tools/crosscheck-vectors/openssl-check.mjs protocol/v1/fixtures/crypto.json
openssl-check: 13 verified, 0 failed

$ node --test protocol/checks/contract.test.mjs   # pass 45 / fail 0
$ node --test protocol/checks/bridge.test.mjs     # pass 51 / fail 0
$ node --test protocol/checks/state.test.mjs      # pass 32 / fail 0
$ node --test protocol/checks/crypto.test.mjs     # pass 27 / fail 0
# aggregate (`protocol/checks/check.mjs`): 155 tests / 0 failures + check-contract-text PASS

Mutation checkpoint (committed fixture `veles-enrollment-schedule`
`k_ab_aes` byte flipped): crypto suite 15 pass / 4 FAIL (committed oracle
cross-check, verifyCryptoFixtures coverage, engine recompute, candidate
equality) — mutated expected bytes cannot pass. Restored byte-identical
(sha256 checked), full chain and suite green again.
```

Tool versions in the acceptance environment: Node `v26.12.1`, npm `11.20.0`,
Python `3.14.4`, OpenSSL bundled with Node (see report header), `ajv@8.18.0`
(existing fixture-schema dependency), `@noble/curves@2.4.0`,
`@noble/hashes@2.4.0`, `@noble/ciphers@2.4.0`.

## 5. Dependency accounting / policy notes

- New dependency packages are exactly-pinned devDependencies of the test-only
  `protocol` package and enter the existing lockfile; Task 6 wires new roots
  into SBOM/license enumeration (plan Task 5 boundary respected here by
  recording the pins). Licenses: `@noble/*` MIT; `ajv` MIT.
- The Python oracle needs NO packages (stdlib only) and carries its own
  manifest here (`protocol/tools/crosscheck-vectors/README.md`); its only
  environment requirement is a Python ≥3.8 interpreter.
- Deliberately NOT done here (explicit non-goals): no Rust toolchain/package
  for generation (the plan's "select two independent implementations" is met
  by noble-JS ↔ Python-stdlib ↔ OpenSSL without adding a Rust dependency);
  no SPAKE2 third-party crate/library was used anywhere — the ciphersuite is
  implemented directly from RFC 9382's own text, so the "does the tool match
  the RFC profile" question never arises; and no expected value was ever
  regenerated inside a test — tests only recompute and compare.

## 5a. Recorded profile choices (builder choices under the approved design)

1. **Zero scalars are algebraically valid inputs.** RFC 9382 draws x, y
   uniformly from [0, p) and Veles's transform maps every 40-byte OKM to a
   w in [0, n) that is "processed normally" (OTP2-CRY-004b). Scalar 0 is
   therefore not an error: the derivation substitutes the identity element
   for any 0-scalar multiplication directly (noble's multiply() rejects the
   scalar 0, so `mulScalar` handles it; the Python oracle already maps 0 to
   infinity naturally). w = 0 yields pA = x*P and a normal, non-degenerate
   shared element; this is exercised explicitly by an engine test.
2. **Degenerate generated points/shared elements reject before exposure.**
   If a generated pA/pB decodes as the identity, the derivation treats it as
   a bad generated artifact and aborts: the endpoint restarts the attempt
   before any exposure (peer message, KDF output, or confirmation) rather
   than transmitting an identity term. The PEER-INPUT case (the derived
   shared element K collapses to the identity because the peer sent
   `w·N`/`w·M`) is now NORMATIVE, not a profile choice: OTP2-CRY-003a (the
   final fix wave) requires the generic abort before any KDF output or
   confirmation value, with the phone's consumed attempt NOT refunded, and
   `veles-neg-shared-secret-identity` proves it in both implementations.
   Nothing in the protocol ever encodes an identity as a 65-byte point; the
   wire rejects the SEC1 infinity octet 0x00 and any malformed point at the
   strict validator.
3. **Identity-encoding semantics.** SEC1's encoding of the point at infinity
   is the single 0x00 octet; a 65-byte uncompressed shape with all-zero
   coordinates is NOT the encoded identity (x=y=0 simply does not satisfy
   the curve equation). The fixture set keeps both negatives distinct: the
   infinity octet (recognized and rejected AS the identity) and the
   all-zero-coordinates shape (rejected as an off-curve point).

## 6. Known-good boundary (unchanged by this task)

Point on-curve membership, signature/low-S semantics and AEAD tag checks here
are REAL checks, not shape checks. What remains OTP-03 work: actual runtime
crypto in Android/Rust/WASM/bridge and the JNI/WASM conformance runs (Gate A
remains unclosed by design).