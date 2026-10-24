#!/usr/bin/env node
// Deterministic crypto vector generator (OTP-02 Plan Task 5, test-only).
//
// Recomputes every Veles-specific protocol vector from FIXED synthetic inputs
// (no randomness, no clock) using the exact-pinned JS engine in
// protocol/checks/crypto.mjs (@noble/curves, @noble/hashes, @noble/ciphers)
// and writes the complete candidate fixture set — inputs AND expected bytes —
// to build/protocol/crypto-candidate.json.
//
// Invariants:
//   - Reproducing the RFC 9382 Appendix B P-256 published anchors is checked
//     FIRST and verbatim; a mismatch aborts generation before anything else.
//   - The generator NEVER writes to protocol/v1/fixtures/ — accepted expected
//     fixtures are committed by review, not regenerated (plan Task 5).
//   - Two byte-identical runs produce byte-identical output (deterministic).
//
// Usage: node protocol/tools/generate-vectors.mjs
// Next:  python3 protocol/tools/crosscheck-vectors/oracle.py build/protocol/crypto-candidate.json
//        node protocol/tools/crosscheck-vectors/openssl-check.mjs

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  RFC9382_ANCHORS,
  RFC9382_M_COMPRESSED,
  RFC9382_N_COMPRESSED,
  LABELS,
  codeToW,
  mulScalar,
  POINT,
  deriveRfcAnchor,
  deriveSpakePairing,
  deriveEnrollment,
  deriveReconnect,
  deriveRecordGcm,
  buildSignatureVariants,
  bytesToHex,
} from "../checks/crypto.mjs";

const toolsDir = dirname(fileURLToPath(import.meta.url));
const ROOT = join(toolsDir, "..", "..");
const OUT_DIR = join(ROOT, "build", "protocol");
const OUT_FILE = join(OUT_DIR, "crypto-candidate.json");

// ---------- fixed synthetic inputs (no randomness anywhere) ----------
// All values are printable-ASCII "veles"-themed patterns or simple hex
// patterns, well below the P-256 subgroup order; they exist ONLY to make the
// vectors deterministic and carry no secret meaning (OTP2-FIX-003).

const KEYS = {
  phone_identity_priv: "0x101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e30",
  client_identity_priv: "0xa0a1a2a3a4a5a6a7a8a9aaabacadaeafb0b1b2b3b4b5b6b7b8b9babbbcbdbebf",
  phone_ephemeral_priv: "0x020406080a0c0e10121416181a1c1e20222426282a2c2e30323436383a3c3e40",
  client_ephemeral_priv: "0x030507090b0d0f11131517191b1d1f21232527292b2d2f31333335373b3d3f41",
};

const PAIRING_01 = {
  kind: "spake2_pairing",
  code: "000000",
  pairing_id: "0x76656c65732d70616972696e672d3031", // "veles-pairing-01"
  phone_nonce: "0x70686f6e652d6e6f6e63652d30303031", // "phone-nonce-0001"
  client_nonce: "0x636c69656e742d6e6f6e63652d613031", // "client-nonce-a01"
  phone_role: "0x01",
  client_role: "0x02",
  phone_caps: ["0x01", "0x02"],
  client_caps: ["0x01", "0x03"],
  protocol_version: 1,
  schema_version: 1,
  ciphersuite_id: 1,
  x: "0x0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20",
  y: "0xa1a2a3a4a5a6a7a8a9aaabacadaeafb0b1b2b3b4b5b6b7b8b9babbbcbdbebec0",
};

const PAIRING_02 = {
  ...PAIRING_01,
  code: "123456",
  pairing_id: "0x76656c65732d70616972696e672d3032", // "veles-pairing-02"
  phone_nonce: "0x70686f6e652d6e6f6e63652d30303032", // "phone-nonce-0002"
  client_nonce: "0x636c69656e742d6e6f6e63652d623031", // "client-nonce-b01"
  x: "0x2a2b2c2d2e2f303132333435363738393a3b3c3d3e3f40414243444546474849",
  y: "0xb2b3b4b5b6b7b8b9babbbcbdbebec0c1c2c3c4c5c6c7c8c9cacbcccdcecfd0d1",
};

const ENROLLMENT_NONCE_PHONE = "0x404142434445464748494a4b4c4d4e4f";
const ENROLLMENT_NONCE_CLIENT = "0x505152535455565758595a5b5c5d5e5f";
const PHONE_INSTALLATION_ID = "0x70686f6e652d696e7374616c6c2d3031"; // "phone-install-01"
const CLIENT_INSTALLATION_ID = "0x636c69656e742d696e7374616c6c2d31"; // "client-install-1"
const EVENT_ID = "0x6576656e742d6576742d303030303031"; // "event-evt-000001"

const RECONNECT_INPUT = (etHex) => ({
  kind: "reconnect",
  et: etHex,
  phone_identity_priv: KEYS.phone_identity_priv,
  client_identity_priv: KEYS.client_identity_priv,
  phone_ephemeral_priv: KEYS.phone_ephemeral_priv,
  client_ephemeral_priv: KEYS.client_ephemeral_priv,
  phone_installation_id: PHONE_INSTALLATION_ID,
  client_installation_id: CLIENT_INSTALLATION_ID,
  phone_nonce: "0x72636f6e6e2d6e6f6e63652d70683031", // "rconn-nonce-ph01"
  client_nonce: "0x72636f6e6e2d6e6f6e63652d636c3031", // "rconn-nonce-cl01"
  phone_caps: ["0x01", "0x02"],
  client_caps: ["0x01", "0x03"],
});

function baseInput(baseId) {
  const base = candidate.fixtures.find((f) => f.id === baseId);
  if (!base) throw new Error(`internal: base fixture ${baseId} missing`);
  return structuredClone(base.input);
}

// ---------- step 1: RFC 9382 published anchors, verbatim ----------

console.error("[1/4] reproducing RFC 9382 Appendix B published anchors verbatim...");
const anchorOutputs = new Map();
for (const anchor of RFC9382_ANCHORS) {
  const got = deriveRfcAnchor(anchor.inputs);
  for (const [name, published] of Object.entries(anchor.published)) {
    if (got[name] !== published) {
      throw new Error(
        `RFC 9382 anchor check failed for ${anchor.id}: ${name} recomputed ${got[name].slice(0, 24)}... != published ${published.slice(0, 24)}...`,
      );
    }
  }
  anchorOutputs.set(anchor.id, got);
  console.error(`      ok: ${anchor.id}`);
}

// ---------- step 2: Veles-specific vectors ----------

console.error("[2/4] deriving Veles-specific vectors from fixed synthetic inputs...");
const pairing01 = deriveSpakePairing(PAIRING_01);
const pairing02 = deriveSpakePairing(PAIRING_02);
// Structural sanity: caps are [0x01,0x02]/[0x01,0x03], so the canonical public
// context is 30+2+2+2+16+2+16+16+(2+2)+(2+2) = 94 bytes (OTP2-CRY-012).
for (const [name, outputs] of [["pairing01", pairing01], ["pairing02", pairing02]]) {
  if (outputs.ctx_len !== "94") throw new Error(`${name}: unexpected public-context length ${outputs.ctx_len} (expected 94)`);
  if (outputs.w.length !== "0x".length + 64) throw new Error(`${name}: w must be a fixed 32-byte big-endian value`);
}
console.error("      ok: spake2 pairing (code 000000, code 123456)");

const enrollment = deriveEnrollment({
  kind: "enrollment",
  ke: pairing01.ke,
  ctx: pairing01.ctx,
  p_a: pairing01.p_a,
  p_b: pairing01.p_b,
  c_a: pairing01.c_a,
  c_b: pairing01.c_b,
  phone_identity: {
    installation_id: PHONE_INSTALLATION_ID,
    label: "phone-label-32b-0123456789abcdef", // exactly 32 bytes (upper bound)
    device_type: "0x00",
    nonce: ENROLLMENT_NONCE_PHONE,
    priv: KEYS.phone_identity_priv,
  },
  client_identity: {
    installation_id: CLIENT_INSTALLATION_ID,
    label: "", // empty label (lower bound; min body 132 bytes)
    device_type: "0x01",
    nonce: ENROLLMENT_NONCE_CLIENT,
    priv: KEYS.client_identity_priv,
  },
});
console.error("      ok: enrollment schedule, ET, fingerprints");

const reconnect = deriveReconnect(RECONNECT_INPUT(enrollment.et));
// Hello payload bounds (OTP2-REC-020): 102 fixed bytes + caps bytes.
for (const key of ["phone_hello_payload", "client_hello_payload"]) {
  const len = (reconnect[key].length - 2) / 2;
  if (len !== 104) throw new Error(`reconnect: ${key} is ${len} bytes (expected 102 + 2 caps = 104)`);
}
console.error("      ok: reconnect rbase/ECDH/signatures/HKDF/recovery keys");

const confirmBody = "0x" + reconnect.conf_b.slice(2) + "01"; // conf_B || peer_state=0x01 (active)
const recordConfirm = deriveRecordGcm({
  kind: "record_gcm",
  key: reconnect.k_ba_aes,
  nonce_prefix: reconnect.k_ba_nonce_prefix,
  sequence: "0",
  direction: "0x01", // client→phone (B-to-A): reconnect_confirm is sent by the client at sequence 0
  record_type: "0x0001",
  session_id_prefix: reconnect.session_id_prefix,
  body: confirmBody,
});
// push_event with a complete OTP event body at a nonzero sequence (OTP2-REC-030)
const u64beHex = (n) => {
  const b = Buffer.alloc(8);
  b.writeBigUInt64BE(BigInt(n));
  return b.toString("hex");
};
const otpEventBodyParts = [
  PHONE_INSTALLATION_ID.replace(/^0x/, ""),
  EVENT_ID.replace(/^0x/, ""),
  u64beHex(1750000000), // receipt_epoch_seconds (u64be)
  "000001f4", // remaining_lifetime 500 (u32be)
  "0006" + "363534333231", // code_len 6, "654321"
  "0005" + "4b494f534b", // merchant_len 5, "KIOSK"
  "000000000012d687", // amount_magnitude 1234567 minor units (u64be)
  "00", // amount_scale 0
  "544842", // currency "THB" (ISO 4217 alpha-3, exactly 3 bytes A-Z)
];
const otpEventBodyHex = otpEventBodyParts.join("");
const recordPush = deriveRecordGcm({
  kind: "record_gcm",
  key: reconnect.k_ab_aes,
  nonce_prefix: reconnect.k_ab_nonce_prefix,
  sequence: "5",
  direction: "0x02", // phone→client (A-to-B)
  record_type: "0x0203",
  session_id_prefix: reconnect.session_id_prefix,
  body: "0x" + otpEventBodyHex,
});
console.error("      ok: protected records (reconnect_confirm at seq 0, push_event at seq 5)");

// ---------- step 3: negative deviations ----------

console.error("[3/4] deriving negative deviation vectors...");
const sigVariants = buildSignatureVariants(reconnect.phone_sig);
const offCurvePoint =
  // Flip the last y-coordinate byte of the committed pB: valid SEC1 shape,
  // off the curve.
  pairing01.p_b.slice(0, -2) + (parseInt(pairing01.p_b.slice(-2), 16) ^ 0x01).toString(16).padStart(2, "0");
const infinityPoint = "0x00"; // SEC1 point-at-infinity octet (the identity's wire encoding)
const zeroPoint = "0x" + "04" + "00".repeat(64); // all-zero 65-byte shape: off-curve, NOT the encoded identity
const compressedPoint = "0x" + "02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f";

const R = ["OTP2-SCP-001", "OTP2-CRY-001"];
const PAIRING_REQS = ["OTP2-SCP-002", "OTP2-CRY-004a", "OTP2-CRY-004b", "OTP2-CRY-009", "OTP2-CRY-012", "OTP2-CRY-012a", "OTP2-CRY-002", "OTP2-CRY-003", "OTP2-CRY-006"];
const ENROLL_REQS = ["OTP2-CRY-007a", "OTP2-CRY-007b", "OTP2-CRY-007c", "OTP2-CRY-020a", "OTP2-CRY-020b", "OTP2-REC-010", "OTP2-REC-011a", "OTP2-REC-011", "OTP2-CRY-019a"];
const REC_REQS = ["OTP2-REC-020", "OTP2-REC-020a", "OTP2-REC-020b", "OTP2-REC-020c", "OTP2-CRY-019f", "OTP2-SID-001"];
const REC_REQS_RECOVERY = [...REC_REQS];
const REC_REQS_RECORD = [...new Set([...REC_REQS, "OTP2-CRY-016a", "OTP2-CRY-016b", "OTP2-CRY-017", "OTP2-SID-001"])];

const candidate = {
  version: 1,
  kind: "veles.protocol.crypto-candidate",
  description:
    "Deterministic candidate outputs for protocol/v1/fixtures/crypto.json (OTP-02 Plan Task 5). Generated by node protocol/tools/generate-vectors.mjs from fixed synthetic inputs; independently cross-checked by protocol/tools/crosscheck-vectors/oracle.py and openssl-check.mjs. This file is never an accepted fixture: committed expected values live in protocol/v1/fixtures/crypto.json and are compared against this candidate by tests.",
  tool: {
    name: "generate-vectors.mjs",
    engine: "checks/crypto.mjs (@noble/curves 2.4.0, @noble/hashes 2.4.0, @noble/ciphers 2.4.0 over Node/OpenSSL-independent pure-JS arithmetic)",
    node: process.version.replace(/^v/, ""),
    deterministic: true,
  },
  fixtures: [],
};

function addFixture(fixture) {
  candidate.fixtures.push(fixture);
}

for (const anchor of RFC9382_ANCHORS) {
  addFixture({
    id: anchor.id,
    fixture_version: 1,
    category: "crypto",
    requirement_ids: [...R, "OTP2-ENC-001"],
    input: { ...anchor.inputs },
    expected: {
      verified: true,
      source: anchor.source,
      outputs: anchorOutputs.get(anchor.id),
    },
  });
}

addFixture({
  id: "veles-pairing-code-000000",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: PAIRING_REQS,
  input: { ...PAIRING_01 },
  expected: { verified: true, outputs: pairing01 },
});

addFixture({
  id: "veles-pairing-code-123456",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: PAIRING_REQS,
  input: { ...PAIRING_02 },
  expected: { verified: true, outputs: pairing02 },
});

addFixture({
  id: "veles-enrollment-schedule",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ENROLL_REQS,
  input: {
    kind: "enrollment",
    ke: pairing01.ke,
    ctx: pairing01.ctx,
    p_a: pairing01.p_a,
    p_b: pairing01.p_b,
    c_a: pairing01.c_a,
    c_b: pairing01.c_b,
    phone_identity: {
      installation_id: PHONE_INSTALLATION_ID,
      label: "phone-label-32b-0123456789abcdef",
      device_type: "0x00",
      nonce: ENROLLMENT_NONCE_PHONE,
      priv: KEYS.phone_identity_priv,
    },
    client_identity: {
      installation_id: CLIENT_INSTALLATION_ID,
      label: "",
      device_type: "0x01",
      nonce: ENROLLMENT_NONCE_CLIENT,
      priv: KEYS.client_identity_priv,
    },
  },
  expected: { verified: true, outputs: enrollment },
});

addFixture({
  id: "veles-reconnect-authentication",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: REC_REQS,
  input: RECONNECT_INPUT(enrollment.et),
  expected: { verified: true, outputs: reconnect },
});

addFixture({
  id: "veles-record-reconnect-confirm",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: REC_REQS_RECORD,
  input: {
    kind: "record_gcm",
    key: reconnect.k_ba_aes,
    nonce_prefix: reconnect.k_ba_nonce_prefix,
    sequence: "0",
    direction: "0x01",
    record_type: "0x0001",
    session_id_prefix: reconnect.session_id_prefix,
    body: confirmBody,
  },
  expected: { verified: true, outputs: recordConfirm },
});

addFixture({
  id: "veles-record-push-event",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: [...REC_REQS_RECORD, "OTP2-ENC-020"],
  input: {
    kind: "record_gcm",
    key: reconnect.k_ab_aes,
    nonce_prefix: reconnect.k_ab_nonce_prefix,
    sequence: "5",
    direction: "0x02",
    record_type: "0x0203",
    session_id_prefix: reconnect.session_id_prefix,
    body: "0x" + otpEventBodyHex,
  },
  expected: { verified: true, outputs: recordPush },
});

const negInput = (baseId, deviation) => ({
  ...baseInput(baseId),
  deviation,
});

// Pairing rejections (wrong code / context / roles)
const pairingDistinguishCode = ["password_prk", "password_okm", "w", "p_a", "p_b", "tt", "hash_tt", "ke", "ka", "kc_a", "kc_b", "c_a", "c_b"];
addFixture({
  id: "veles-neg-wrong-code",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-CRY-004a", "OTP2-CRY-006", "OTP2-SEC-002", "OTP2-SCP-003"],
  input: negInput("veles-pairing-code-000000", {
    type: "wrong_code",
    negates: "veles-pairing-code-000000",
    value: "654321",
  }),
  expected: {
    rejected: true,
    stage: "code",
    no_state_mutation: true,
    proof: "confirmation_mismatch",
    note: "a wrong pairing code yields different w and therefore different SPAKE2 confirmations; the phone rejects generically (OTP2-CRY-008)",
    distinguishes: pairingDistinguishCode,
  },
});

addFixture({
  id: "veles-neg-context-mismatch",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-CRY-012", "OTP2-CRY-006", "OTP2-SEC-002", "OTP2-SCP-004"],
  input: negInput("veles-pairing-code-000000", {
    type: "context_mismatch",
    negates: "veles-pairing-code-000000",
    value: "0xdeadbeefdeadbeefdeadbeefdeadbeef",
  }),
  expected: {
    rejected: true,
    stage: "context",
    no_state_mutation: true,
    proof: "confirmation_mismatch",
    note: "an endpoint constructing a different public context derives different confirmation keys while TT and K stay identical; the confirmation stage is where context binding is proven",
    distinguishes: ["ctx", "kc_a", "kc_b", "c_a", "c_b"],
  },
});

addFixture({
  id: "veles-neg-role-swap",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-SCP-002", "OTP2-CRY-002", "OTP2-CRY-006"],
  input: negInput("veles-pairing-code-000000", {
    type: "swap_roles",
    negates: "veles-pairing-code-000000",
  }),
  expected: {
    rejected: true,
    stage: "role",
    no_state_mutation: true,
    proof: "confirmation_mismatch",
    note: "A blinds with N and B with M: the shared element K is unchanged, but every transcript-derived key and confirmation differs from the fixed-role execution (OTP2-CRY-002 roles are not negotiable)",
    distinguishes: ["p_a", "p_b", "tt", "hash_tt", "ke", "ka", "kc_a", "kc_b", "c_a", "c_b"],
  },
});

addFixture({
  id: "veles-neg-role-mismatch",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-SCP-002", "OTP2-CRY-002", "OTP2-SEC-002"],
  input: negInput("veles-pairing-code-000000", {
    type: "role_mismatch",
    negates: "veles-pairing-code-000000",
  }),
  expected: {
    rejected: true,
    stage: "role",
    no_state_mutation: true,
    proof: "confirmation_mismatch",
    note: "both endpoints act as party A (the client blinds pB with w*M): the peers' shared elements disagree, confirmations fail, and the attempt budget is consumed generically",
    distinguishes: ["p_b", "k", "tt", "hash_tt", "ke", "ka", "kc_a", "kc_b", "c_a", "c_b"],
  },
});

// Point-validation rejections (OTP2-CRY-005a)
addFixture({
  id: "veles-neg-point-off-curve",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-CRY-005a"],
  input: {
    ...baseInput("veles-pairing-code-000000"),
    deviation: { type: "off_curve_point", negates: "veles-pairing-code-000000", value: offCurvePoint },
  },
  expected: {
    rejected: true,
    stage: "point",
    no_state_mutation: true,
    proof: "strict_validation",
    note: "the last y-coordinate byte of pB is flipped so the encoding is a valid SEC1-uncompressed shape but the point is not on P-256; complete validation must reject before any password-sensitive operation",
  },
});

addFixture({
  id: "veles-neg-point-zero-coordinates",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-CRY-005a"],
  input: {
    ...baseInput("veles-pairing-code-000000"),
    deviation: { type: "zero_point", negates: "veles-pairing-code-000000", value: zeroPoint },
  },
  expected: {
    rejected: true,
    stage: "point",
    no_state_mutation: true,
    proof: "strict_validation",
    note: "a 65-byte uncompressed SEC1 shape carrying all-zero coordinates is NOT the wire encoding of the identity (SEC1 encodes the point at infinity as the single octet 0x00); x=y=0 does not satisfy the P-256 curve equation, so this rejects as an off-curve point at stage point",
  },
});

addFixture({
  id: "veles-neg-point-infinity",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-CRY-005a"],
  input: {
    ...baseInput("veles-pairing-code-000000"),
    deviation: { type: "identity_point", negates: "veles-pairing-code-000000", value: infinityPoint },
  },
  expected: {
    rejected: true,
    stage: "point",
    no_state_mutation: true,
    proof: "strict_validation",
    note: "the single 0x00 octet IS the SEC1 encoding of the point at infinity; OTP2-CRY-005a requires rejecting the identity element, so this octet is recognized and rejected as the identity before any password-sensitive operation (on a cofactor-1 curve it is the only group element outside the prime-order subgroup)",
  },
});

addFixture({
  id: "veles-neg-point-compressed",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-CRY-005a", "OTP2-CRY-001"],
  input: {
    ...baseInput("veles-pairing-code-000000"),
    deviation: { type: "compressed_point", negates: "veles-pairing-code-000000", value: compressedPoint },
  },
  expected: {
    rejected: true,
    stage: "point_format",
    no_state_mutation: true,
    proof: "strict_validation",
    note: "the on-curve RFC 9382 M point arrives in its 33-byte compressed form; the Veles wire sends only 65-byte uncompressed SEC1, so this rejects at point_format",
  },
});

// OTP2-CRY-003a: the identity shared group element aborts generically before
// any KDF output or confirmation value; the phone's consumed attempt is not
// refunded. The exact forced peer points are derived HERE from the base's
// committed w and committed into the fixture (deviation.value = w·N phone
// view, deviation.value2 = w·M client view) so OTP-03 implementers never
// regenerate them; the engine and the Python oracle both parse those
// committed bytes, validate them, and use them — never a local
// self-substituting recomputation (re-review I3).
addFixture({
  id: "veles-neg-shared-secret-identity",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-CRY-003a", "OTP2-CRY-005a", "OTP2-CRY-008"],
  input: (() => {
    const base = baseInput("veles-pairing-code-000000");
    const { wBytes } = codeToW(Buffer.from(base.code, "ascii"), Buffer.from(base.pairing_id.slice(2), "hex"));
    const wInt = BigInt(`0x${Buffer.from(wBytes).toString("hex")}`);
    const nPoint = POINT.fromBytes(Buffer.from(RFC9382_N_COMPRESSED, "hex"));
    const mPoint = POINT.fromBytes(Buffer.from(RFC9382_M_COMPRESSED, "hex"));
    return {
      ...base,
      deviation: {
        type: "shared_secret_identity",
        negates: "veles-pairing-code-000000",
        value: bytesToHex(mulScalar(nPoint, wInt).toBytes(false)), // pB := w·N (phone view)
        value2: bytesToHex(mulScalar(mPoint, wInt).toBytes(false)), // pA := w·M (client view)
      },
    };
  })(),
  expected: {
    rejected: true,
    stage: "shared_secret",
    no_state_mutation: true,
    proof: "strict_validation",
    note: "with the same code and both own scalars as the positive base, a peer point pB = w·N (phone view) or pA = w·M (client view) collapses the shared group element to the identity: both endpoint views must abort with the generic pre-auth failure BEFORE any Ke/Ka, KDF output, or confirmation value is produced or transmitted, and the phone's already-consumed attempt is not refunded (OTP2-CRY-008). deviation.value/value2 are the exact committed forced peer points (pB/cB-wire = w·N; pA/cA-wire = w·M), derived from the base's committed w; both implementations parse, validate and USE them (never a local tautological re-derivation).",
  },
});

// Signature encodings (OTP2-REC-020a)
addFixture({
  id: "veles-neg-sig-high-s",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-REC-020a", "OTP2-SCP-008"],
  input: {
    ...baseInput("veles-reconnect-authentication"),
    deviation: { type: "high_s", negates: "veles-reconnect-authentication", value: sigVariants.high_s },
  },
  expected: {
    rejected: true,
    stage: "signature_format",
    no_state_mutation: true,
    proof: "strict_validation",
    note: "s := n - s of the authentic phone signature: the signature is mathematically valid ECDSA but violates the low-S canonical half-order rule",
  },
});

addFixture({
  id: "veles-neg-sig-der",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-REC-020a", "OTP2-SCP-008"],
  input: {
    ...baseInput("veles-reconnect-authentication"),
    deviation: { type: "der_encoding", negates: "veles-reconnect-authentication", value: sigVariants.der_encoding },
  },
  expected: {
    rejected: true,
    stage: "signature_format",
    no_state_mutation: true,
    proof: "strict_validation",
    note: "the same valid (r, s) in DER encoding: verification accepts only 64-byte P1363 fixed-width r||s",
  },
});

addFixture({
  id: "veles-neg-sig-truncated",
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-REC-020a", "OTP2-SCP-008"],
  input: {
    ...baseInput("veles-reconnect-authentication"),
    deviation: { type: "truncated_sig", negates: "veles-reconnect-authentication", value: sigVariants.truncated_sig },
  },
  expected: {
    rejected: true,
    stage: "signature_format",
    no_state_mutation: true,
    proof: "strict_validation",
    note: "the authentic signature truncated to 63 bytes: any width other than 64 rejects",
  },
});

// Protected-record AEAD rejections (OTP2-CRY-016b, OTP2-CRY-017)
const recordNeg = (baseId, id, type, reqExtra, note) => ({
  id,
  fixture_version: 1,
  category: "crypto",
  requirement_ids: ["OTP2-CRY-016b", ...reqExtra],
  input: negInput(baseId, { type, negates: baseId }),
  expected: {
    rejected: true,
    stage: type === "bad_tag" ? "tag" : type === "mutated_session" ? "session" : type === "mutated_direction" ? "direction" : "sequence",
    no_state_mutation: true,
    proof: "aead_failure",
    note,
  },
});

addFixture(
  recordNeg(
    "veles-record-reconnect-confirm",
    "veles-neg-record-tag",
    "bad_tag",
    ["OTP2-SCP-010"],
    "one bit of the committed authentication tag is flipped: AES-256-GCM verification must fail and the session tears down without state mutation",
  ),
);
addFixture(
  recordNeg(
    "veles-record-reconnect-confirm",
    "veles-neg-record-session",
    "mutated_session",
    ["OTP2-SCP-010", "OTP2-SCP-012"],
    "a header session_id_prefix byte is altered: the header is part of the AAD, so the tag no longer verifies; cross-session record injection is impossible",
  ),
);
addFixture(
  recordNeg(
    "veles-record-reconnect-confirm",
    "veles-neg-record-direction",
    "mutated_direction",
    ["OTP2-SCP-010", "OTP2-ENC-020"],
    "the direction byte is rewritten from 0x01 to 0x02 in the header only: the AAD binding makes the tag fail, and direction bytes are independently checked",
  ),
);
addFixture(
  recordNeg(
    "veles-record-push-event",
    "veles-neg-record-sequence",
    "mutated_sequence",
    ["OTP2-CRY-017", "OTP2-SCP-012"],
    "the u64 sequence number is bumped in the header of a record whose real sequence was 5: AAD binding makes the tag fail and the sequence rule terminates the session",
  ),
);

// ---------- step 4: write candidate ----------

console.error("[4/4] writing candidate to", OUT_FILE);
mkdirSync(OUT_DIR, { recursive: true });
writeFileSync(OUT_FILE, JSON.stringify(candidate, null, 2) + "\n");
const positives = candidate.fixtures.filter((f) => f.expected.rejected !== true).length;
console.error(`      ${candidate.fixtures.length} fixtures: ${positives} positive (incl. ${RFC9382_ANCHORS.length} published anchors), ${candidate.fixtures.length - positives} negative`);
console.error("done");