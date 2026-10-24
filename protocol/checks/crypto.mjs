// Test-only cryptographic vector verification for the Veles protocol contract
// (OTP-02 Plan Task 5). Never imported by product code (OTP2-FIX-006).
//
// verifyCryptoFixtures(fixtures) recomputes every committed `crypto` fixture
// from its inputs using established, exact-pinned JS primitives
// (@noble/curves 2.4.0, @noble/hashes 2.4.0, @noble/ciphers 2.4.0) and
// compares byte-for-byte against the committed expected bytes. A wrong input,
// label, transcript byte, or expected byte makes recomputation fail loudly —
// nothing is skipped and expected values are never regenerated here.
//
// The JS implementation alone is NOT sufficient provenance (OTP2-FIX-005):
// every fixture must additionally be confirmed by at least one independent
// implementation whose ACCEPTANCE report was produced under build/protocol/
// against the COMMITTED expected fixture bytes:
//   - protocol/tools/crosscheck-vectors/oracle.py         (stdlib affine P-256)
//   - protocol/tools/crosscheck-vectors/openssl-check.mjs (Node/OpenSSL)
// The same tools also cross-check the generated candidate before commit
// (crosscheck-oracle.json / crosscheck-openssl.json there); a fixture without
// a passing committed-report entry for its kind is returned in unverified_ids,
// which blocks OTP-02 acceptance (plan Task 5/6).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { concatBytes, hexToBytes, utf8ToBytes } from "@noble/hashes/utils.js";
import { sha256 } from "@noble/hashes/sha2.js";
import { hmac } from "@noble/hashes/hmac.js";
import { extract as hkdfExtract, expand as hkdfExpand } from "@noble/hashes/hkdf.js";
import { gcm } from "@noble/ciphers/aes.js";
import { p256 } from "@noble/curves/nist.js";
import { decodeHex } from "./binary.mjs";

export const POINT = p256.Point;

// ---------- byte helpers ----------

export function bytesToHex(bytes) {
  return "0x" + Buffer.from(bytes).toString("hex");
}

// ---------- canonical content digests (report↔fixture binding) ----------
// Stable JSON: recursively key-sorted, compact separators, and non-ASCII
// escaped as \uXXXX with surrogate pairs — byte-identical with the Python
// oracle's json.dumps(value, sort_keys=True, separators=(",", ":"),
// ensure_ascii=True). Fixture strings are ASCII-only; the escaping is the
// safety net for future content.

function escapeJsonString(s) {
  let out = '"';
  for (const ch of s) {
    const code = ch.codePointAt(0);
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (code < 0x20) {
      const simple = { 8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r" }[code];
      out += simple ?? `\\u${code.toString(16).padStart(4, "0")}`;
    } else if (code > 0x7e) {
      if (code > 0xffff) {
        // surrogate pair, both escaped like Python's ensure_ascii
        const high = 0xd800 + ((code - 0x10000) >> 10);
        const low = 0xdc00 + ((code - 0x10000) & 0x3ff);
        out += `\\u${high.toString(16).padStart(4, "0")}\\u${low.toString(16).padStart(4, "0")}`;
      } else {
        out += `\\u${code.toString(16).padStart(4, "0")}`;
      }
    } else {
      out += ch;
    }
  }
  return out + '"';
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${escapeJsonString(k)}:${canonicalJson(value[k])}`)
      .join(",")}}`;
  }
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return String(value);
  if (value === null) return "null";
  return escapeJsonString(String(value));
}

export function sha256Hex(value) {
  // Content digests are bare lowercase hex (internal metadata, not protocol
  // byte fields — deliberate contrast with the "0x" fixture convention).
  return Buffer.from(sha256(utf8(value))).toString("hex");
}

// Whole-array binding: canonical SHA-256 over the fixtures array.
export function fixturesArrayDigest(cryptoFixtures) {
  return sha256Hex(canonicalJson(cryptoFixtures));
}

// Per-fixture binding: canonical SHA-256 over {id, input, expected} — a stale
// report (produced against older bytes) can never approve a changed fixture.
export function fixtureContentDigest(fixture) {
  return sha256Hex(canonicalJson({ id: fixture.id, input: fixture.input, expected: fixture.expected }));
}

export function fixtureDigestMap(cryptoFixtures) {
  const map = {};
  for (const f of cryptoFixtures) map[f.id] = fixtureContentDigest(f);
  return map;
}

// The exact output set each positive fixture kind must commit. The verifier
// enforces this in BOTH directions: no committed key may be missing from the
// engine derivation and no engine output may be silently dropped from the
// committed expected bytes.
export const REQUIRED_OUTPUTS = Object.freeze({
  rfc9382_anchor: [
    "p_a", "p_b", "k", "tt", "hash_tt", "ke", "ka", "kc_a", "kc_b", "c_a", "c_b",
  ],
  spake2_pairing: [
    "ctx", "ctx_len", "ctx_len_le", "password_prk", "password_okm", "w",
    "p_a", "p_b", "k", "tt", "hash_tt", "ke", "ka", "kc_a", "kc_b", "c_a", "c_b",
  ],
  enrollment: [
    "phone_identity_pub", "client_identity_pub", "phone_identity_body", "client_identity_body",
    "fp_self_phone", "fp_self_client", "fp_pair", "fp_pair_display",
    "enrollment_salt", "enrollment_prk", "k_ab_aes", "k_ab_nonce_prefix", "k_act_a",
    "k_ba_aes", "k_ba_nonce_prefix", "k_act_b",
    "act_prepare_mac", "act_commit_mac", "act_complete_mac", "act_ack_mac",
    "enrollment_nonce_ab_seq0", "enrollment_nonce_ba_seq7", "session_id_prefix", "et",
  ],
  reconnect: [
    "phone_identity_pub", "client_identity_pub", "phone_ephemeral_pub", "client_ephemeral_pub",
    "phone_hello_payload", "client_hello_payload", "rbase", "rbase_digest",
    "phone_sig", "phone_sig_r", "phone_sig_s", "client_sig", "ss",
    "reconnect_salt", "reconnect_prk",
    "k_ba_aes", "k_ba_nonce_prefix", "k_ba_confirmation",
    "k_ab_aes", "k_ab_nonce_prefix", "k_ab_confirmation",
    "session_id", "session_id_prefix", "reconnect_confirm_input",
    "conf_a", "conf_b", "k_rec_client", "k_rec_phone",
  ],
  record_gcm: ["header", "nonce", "ciphertext", "tag", "record"],
});

function hb(value) {
  // Accepts "0x"-prefixed or bare lowercase even-length hex.
  return decodeHex(typeof value === "string" && value.startsWith("0x") ? value.slice(2) : value);
}

export function u16be(n) {
  const b = Buffer.alloc(2);
  b.writeUInt16BE(n);
  return b;
}

export function u64be(n) {
  const b = Buffer.alloc(8);
  b.writeBigUInt64BE(BigInt(n));
  return b;
}

// RFC 9382 len(): unsigned 64-bit LITTLE-endian (OTP2-ENC-001).
export function len64le(n) {
  const b = Buffer.alloc(8);
  b.writeBigUInt64LE(BigInt(n));
  return b;
}

export function utf8(text) {
  return utf8ToBytes(text);
}

function bnOf(bytes) {
  return BigInt(`0x${Buffer.from(bytes).toString("hex")}`);
}

// ---------- Normative ASCII labels with exact byte lengths (OTP2-CRY-004a,
// OTP2-CRY-006, OTP2-CRY-012, OTP2-CRY-020a/020b, OTP2-REC-011a/011,
// OTP2-REC-020a/020b/020c, OTP2-CRY-019f, OTP2-CRY-007a). Tests assert every
// length. ----------

export const LABELS = Object.freeze({
  password_info: ["Veles SPAKE2 password input v1", 30],
  public_context: ["Veles SPAKE2 public context v1", 30],
  identity_a: ["Veles Android SPAKE2 party A v1", 31],
  identity_b: ["Veles client SPAKE2 party B v1", 30],
  confirmation_keys: ["ConfirmationKeys", 16],
  enrollment_salt: ["Veles SPAKE2 enrollment salt v1", 31],
  enrollment_ab_aes: ["Veles enrollment A-to-B AES key v1", 34],
  enrollment_ab_nonce: ["Veles enrollment A-to-B nonce v1", 32],
  enrollment_act_a: ["Veles enrollment A confirmation v1", 34],
  enrollment_ba_aes: ["Veles enrollment B-to-A AES key v1", 34],
  enrollment_ba_nonce: ["Veles enrollment B-to-A nonce v1", 32],
  enrollment_act_b: ["Veles enrollment B confirmation v1", 34],
  enrollment_session: ["Veles enrollment session v1", 27],
  enrollment_transcript: ["Veles enrollment transcript v1", 30],
  record_fingerprint: ["Veles enrollment record fingerprint v1", 38],
  pair_fingerprint: ["Veles identity fingerprint v1", 29],
  reconnect_transcript: ["Veles reconnect transcript v1", 29],
  reconnect_salt: ["Veles reconnect salt v1", 23],
  reconnect_ba_aes: ["Veles reconnect B-to-A AES key v1", 33],
  reconnect_ba_nonce: ["Veles reconnect B-to-A nonce v1", 31],
  reconnect_ba_confirmation: ["Veles reconnect B-to-A confirmation v1", 38],
  reconnect_ab_aes: ["Veles reconnect A-to-B AES key v1", 33],
  reconnect_ab_nonce: ["Veles reconnect A-to-B nonce v1", 31],
  reconnect_ab_confirmation: ["Veles reconnect A-to-B confirmation v1", 38],
  reconnect_session_id: ["Veles reconnect session ID v1", 29],
  reconnect_confirm_input: ["Veles reconnect confirmation input v1", 37],
  recovery_client_activation: ["Veles recovery client activation v1", 35],
  recovery_phone_activation: ["Veles recovery phone activation v1", 34],
});

function label(key) {
  const [text, length] = LABELS[key];
  const bytes = utf8(text);
  if (bytes.length !== length) {
    throw new Error(`label "${text}" is ${bytes.length} bytes, contract says ${length}`);
  }
  return bytes;
}

// RFC 9382 P-256 generator points M and N (RFC 9382 section 6, verbatim
// compressed SEC1; OTP2-CRY-002). Decompressed internally; wire points are
// uncompressed.
export const RFC9382_M_COMPRESSED = "02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f";
export const RFC9382_N_COMPRESSED = "03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49";

const M_POINT = POINT.fromBytes(hb(RFC9382_M_COMPRESSED));
const N_POINT = POINT.fromBytes(hb(RFC9382_N_COMPRESSED));

// P-256 parameters, published constants (NIST SP 800-186 / SEC2 v2).
export const P256_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551n;
if (POINT.Fn.ORDER !== P256_N) {
  throw new Error("@noble/curves P-256 order does not match the published P-256 subgroup order");
}

// ---------- RFC 9382 Appendix B published vectors (quoted verbatim from
// https://www.rfc-editor.org/rfc/rfc9382.txt, Appendix B, P256-SHA256-HKDF-HMAC;
// first vector A='server', B='client' and fourth vector A='', B=''; the nil
// identity pair exercises RFC 9382's zero-length identity framing). ----------

export const RFC9382_ANCHORS = [
  {
    id: "rfc9382-p256-sha256-hkdf-hmac-server-client",
    source:
      "RFC 9382 Appendix B, first P256-SHA256-HKDF-HMAC test vector (A='server', B='client'); values quoted verbatim from https://www.rfc-editor.org/rfc/rfc9382.txt",
    inputs: {
      kind: "rfc9382_anchor",
      ciphersuite: "SPAKE2-P256-SHA256-HKDF-HMAC",
      id_a: "server",
      id_b: "client",
      w: "0x2ee57912099d31560b3a44b1184b9b4866e904c49d12ac5042c97dca461b1a5f",
      x: "0x43dd0fd7215bdcb482879fca3220c6a968e66d70b1356cac18bb26c84a78d729",
      y: "0xdcb60106f276b02606d8ef0a328c02e4b629f84f89786af5befb0bc75b6e66be",
    },
    published: {
      p_a: "0x04a56fa807caaa53a4d28dbb9853b9815c61a411118a6fe516a8798434751470f9010153ac33d0d5f2047ffdb1a3e42c9b4e6be662766e1eeb4116988ede5f912c",
      p_b: "0x0406557e482bd03097ad0cbaa5df82115460d951e3451962f1eaf4367a420676d09857ccbc522686c83d1852abfa8ed6e4a1155cf8f1543ceca528afb591a1e0b7",
      k: "0x0412af7e89717850671913e6b469ace67bd90a4df8ce45c2af19010175e37eed69f75897996d539356e2fa6a406d528501f907e04d97515fbe83db277b715d3325",
      tt: "0x06000000000000007365727665720600000000000000636c69656e74410000000000000004a56fa807caaa53a4d28dbb9853b9815c61a411118a6fe516a8798434751470f9010153ac33d0d5f2047ffdb1a3e42c9b4e6be662766e1eeb4116988ede5f912c41000000000000000406557e482bd03097ad0cbaa5df82115460d951e3451962f1eaf4367a420676d09857ccbc522686c83d1852abfa8ed6e4a1155cf8f1543ceca528afb591a1e0b741000000000000000412af7e89717850671913e6b469ace67bd90a4df8ce45c2af19010175e37eed69f75897996d539356e2fa6a406d528501f907e04d97515fbe83db277b715d332520000000000000002ee57912099d31560b3a44b1184b9b4866e904c49d12ac5042c97dca461b1a5f",
      hash_tt: "0x0e0672dc86f8e45565d338b0540abe6915bdf72e2b35b5c9e5663168e960a91b",
      ke: "0x0e0672dc86f8e45565d338b0540abe69",
      ka: "0x15bdf72e2b35b5c9e5663168e960a91b",
      kc_a: "0x00c12546835755c86d8c0db7851ae86f",
      kc_b: "0xa9fa3406c3b781b93d804485430ca27a",
      c_a: "0x58ad4aa88e0b60d5061eb6b5dd93e80d9c4f00d127c65b3b35b1b5281fee38f0",
      c_b: "0xd3e2e547f1ae04f2dbdbf0fc4b79f8ecff2dff314b5d32fe9fcef2fb26dc459b",
    },
  },
  {
    id: "rfc9382-p256-sha256-hkdf-hmac-nil-identities",
    source:
      "RFC 9382 Appendix B, fourth P256-SHA256-HKDF-HMAC test vector (A='', B=''); values quoted verbatim from https://www.rfc-editor.org/rfc/rfc9382.txt",
    inputs: {
      kind: "rfc9382_anchor",
      ciphersuite: "SPAKE2-P256-SHA256-HKDF-HMAC",
      id_a: "",
      id_b: "",
      w: "0x7bf46c454b4c1b25799527d896508afd5fc62ef4ec59db1efb49113063d70cca",
      x: "0x8cef65df64bb2d0f83540c53632de911b5b24b3eab6cc74a97609fd659e95473",
      y: "0xd7a66f64074a84652d8d623a92e20c9675c61cb5b4f6a0063e4648a2fdc02d53",
    },
    published: {
      p_a: "0x04a65b367a3f613cf9f0654b1b28a1e3a8a40387956c8ba6063e8658563890f46ca1ef6a676598889fc28de2950ab8120b79a5ef1ea4c9f44bc98f585634b46d66",
      p_b: "0x04589f13218822710d98d8b2123a079041052d9941b9cf88c6617ddb2fcc0494662eea8ba6b64692dc318250030c6af045cb738bc81ba35b043c3dcb46adf6f58d",
      k: "0x041a3c03d51b452537ca2a1fea6110353c6d5ed483c4f0f86f4492ca3f378d40a994b4477f93c64d928edbbcd3e85a7c709b7ea73ee97986ce3d1438e135543772",
      tt: "0x00000000000000000000000000000000410000000000000004a65b367a3f613cf9f0654b1b28a1e3a8a40387956c8ba6063e8658563890f46ca1ef6a676598889fc28de2950ab8120b79a5ef1ea4c9f44bc98f585634b46d66410000000000000004589f13218822710d98d8b2123a079041052d9941b9cf88c6617ddb2fcc0494662eea8ba6b64692dc318250030c6af045cb738bc81ba35b043c3dcb46adf6f58d4100000000000000041a3c03d51b452537ca2a1fea6110353c6d5ed483c4f0f86f4492ca3f378d40a994b4477f93c64d928edbbcd3e85a7c709b7ea73ee97986ce3d1438e13554377220000000000000007bf46c454b4c1b25799527d896508afd5fc62ef4ec59db1efb49113063d70cca",
      hash_tt: "0xfc6374762ba5cf11f4b2caa08b2cd1b9907ae0e26e8d6234318d91583cd74c86",
      ke: "0xfc6374762ba5cf11f4b2caa08b2cd1b9",
      ka: "0x907ae0e26e8d6234318d91583cd74c86",
      kc_a: "0x5dbd2f477166b7fb6d61febbd77a5563",
      kc_b: "0x7689b4654407a5faeffdc8f18359d8a3",
      c_a: "0xdfb4db8d48ae5a675963ea5e6c19d98d4ea028d8e898dad96ea19a80ade95dca",
      c_b: "0xd0f0609d1613138d354f7e95f19fb556bf52d751947241e8c7118df5ef0ae175",
    },
  },
];

// ---------- HKDF (RFC 5869) via @noble/hashes ----------
// RFC 5869 argument order is Extract(salt, IKM); noble's extract() takes
// (hash, ikm, salt), so this wrapper translates to keep RFC call sites plain.

function extract(salt, ikm) {
  return hkdfExtract(sha256, ikm, salt);
}

function expand(prk, info, length) {
  return hkdfExpand(sha256, prk, info, length);
}

// ---------- Veles code-to-w transform (OTP2-CRY-004a) ----------

export function codeToW(codeBytes, pairingIdBytes) {
  if (codeBytes.length !== 6) throw new Error("code must be exactly 6 ASCII bytes");
  const prk = extract(pairingIdBytes, codeBytes);
  const okm = expand(prk, label("password_info"), 40);
  const w = bnOf(okm) % P256_N;
  return { prk, okm, w, wBytes: hexToBytes(w.toString(16).padStart(64, "0")) };
}

// ---------- Public context (OTP2-CRY-012) ----------

export function buildPublicContext(ctxInput) {
  return concatBytes(
    label("public_context"),
    u16be(ctxInput.protocol_version),
    u16be(ctxInput.schema_version),
    u16be(ctxInput.ciphersuite_id),
    hb(ctxInput.pairing_id),
    hb(ctxInput.phone_role),
    hb(ctxInput.client_role),
    hb(ctxInput.phone_nonce),
    hb(ctxInput.client_nonce),
    u16be(ctxInput.phone_caps.length),
    concatBytes(...ctxInput.phone_caps.map(hb)),
    u16be(ctxInput.client_caps.length),
    concatBytes(...ctxInput.client_caps.map(hb)),
  );
}

// ---------- SPAKE2 core (RFC 9382 sec 3.3; OTP2-CRY-001/002/003/005a/006) ----------
// blinding: "fixed" = A blinds with M, B with N (OTP2-CRY-002 roles);
// "swap_roles" = A with N, B with M (both swapped, still agreeing);
// "role_mismatch" = client blinds with M too (parties disagree on generators).
//
// Zero-scalar edge policy (recorded as a Veles profile choice): the RFC draws
// x, y uniformly from [0, p) and Veles's transform yields w in [0, n) with
// w = 0 processed normally (OTP2-CRY-004b), so zero scalars are ALGEBRAICALLY
// valid inputs. noble's multiply() rejects scalar 0, so mulScalar substitutes
// the identity directly instead of silently contradicting the RFC. A derived
// pA/pB/K that DECODES as the identity only arises in astronomic degenerate
// cases (e.g. x = 0 making K the identity); the engine treats such a result
// as a bad generated artifact and rejects it — the endpoint must restart the
// attempt before any exposure — rather than transmitting it.

export function mulScalar(basePoint, scalar) {
  if (typeof scalar !== "bigint") scalar = bnOf(scalar);
  if (scalar === 0n) return POINT.ZERO;
  return basePoint.multiply(scalar);
}

export function spake2Exchange({ idA, idB, wBytes, x, y, aad, blinding = "fixed", forcePeerA = null, forcePeerB = null }) {
  const wInt = bnOf(wBytes);
  const xInt = bnOf(x);
  const yInt = bnOf(y);
  const mPoint = blinding === "swap_roles" ? N_POINT : M_POINT;
  const nPoint = blinding === "swap_roles" ? M_POINT : N_POINT;
  const bPoint = blinding === "role_mismatch" ? M_POINT : nPoint;
  if (forcePeerA !== null) {
    if (forcePeerB !== null) {
      throw new Error("only one peer point may be forced per exchange");
    }
    validatePointUncompressed(forcePeerA); // the peer point arrives over the wire
  }
  if (forcePeerB !== null) {
    validatePointUncompressed(forcePeerB);
  }
  const pA = forcePeerA !== null
    ? POINT.fromBytes(hb(forcePeerA))
    : mulScalar(POINT.BASE, xInt).add(mulScalar(mPoint, wInt));
  const pB = forcePeerB !== null
    ? POINT.fromBytes(hb(forcePeerB))
    : mulScalar(POINT.BASE, yInt).add(mulScalar(bPoint, wInt));
  if (pA.equals(POINT.ZERO) || pB.equals(POINT.ZERO)) {
    throw new Error("degenerate generated point (identity): reject and restart the attempt before exposure");
  }
  // K = h*x*(pB - w*N) with h = 1 for P-256 (cofactor); both role views must
  // agree to equal for a valid execution — asserted below unless intentionally
  // mismatched.
  const kFromA = mulScalar(pB.subtract(mulScalar(nPoint, wInt)), xInt);
  const kFromB = mulScalar(pA.subtract(mulScalar(mPoint, wInt)), yInt);
  // OTP2-CRY-003a: a shared element that collapses to the IDENTITY (the peer
  // sent w·N on the phone's view / w·M on the client's view) aborts the
  // exchange with the generic pre-auth failure BEFORE any KDF output or
  // confirmation value is produced, exposed, or transmitted; a consumed
  // attempt is not refunded (OTP2-CRY-008).
  if (kFromA.equals(POINT.ZERO) || kFromB.equals(POINT.ZERO)) {
    throw new Error(
      "degenerate shared group element after peer input: generic abort before any KDF output or confirmation (OTP2-CRY-003a; a consumed attempt is not refunded)",
    );
  }
  if (blinding !== "role_mismatch" && !kFromA.equals(kFromB)) {
    throw new Error("SPAKE2 shared group element disagrees between roles (invalid execution)");
  }
  const kBytes = kFromA.toBytes(false);
  const tt = concatBytes(
    len64le(idA.length), idA,
    len64le(idB.length), idB,
    len64le(65), pA.toBytes(false),
    len64le(65), pB.toBytes(false),
    len64le(65), kBytes,
    len64le(32), wBytes,
  );
  const hashTT = sha256(tt);
  const ke = hashTT.slice(0, 16);
  const ka = hashTT.slice(16, 32);
  // KcA || KcB = HKDF-SHA256(IKM=Ka, salt=nil, info="ConfirmationKeys"||AAD, L=32),
  // each 16 bytes (RFC 9382 section 4: |KcA| = |KcB| = 128 bits for SHA-256).
  const confirmPrk = extract(new Uint8Array(0), ka);
  const confirmOkm = expand(confirmPrk, concatBytes(label("confirmation_keys"), aad), 32);
  const kcA = confirmOkm.slice(0, 16);
  const kcB = confirmOkm.slice(16, 32);
  const cA = hmac(sha256, kcA, tt);
  const cB = hmac(sha256, kcB, tt);
  return { p_a: pA, p_b: pB, k: kFromA, tt, hash_tt: hashTT, ke, ka, kc_a: kcA, kc_b: kcB, c_a: cA, c_b: cB };
}

// ---------- Enrollment schedule, ET, fingerprints (OTP2-CRY-007a/b/c,
// OTP2-CRY-020a/020b, OTP2-REC-010/011a/011, OTP2-CRY-019a) ----------

export function validatePointUncompressed(bytes) {
  if (!(bytes instanceof Uint8Array)) bytes = hb(bytes);
  // SEC1 encodes the point at infinity as the SINGLE 0x00 octet — recognize
  // and reject it explicitly as the identity (OTP2-CRY-005a).
  if (bytes.length === 1 && bytes[0] === 0x00) {
    throw new Error("point: identity element (SEC1 point-at-infinity octet 0x00) is not a valid group element");
  }
  if (bytes.length !== 65) throw new Error("point_format: SEC1 uncompressed point must be 65 bytes");
  if (bytes[0] !== 0x04) throw new Error("point_format: compressed or hybrid encodings are rejected");
  let point;
  try {
    point = POINT.fromBytes(bytes);
  } catch {
    throw new Error("point: byte string does not decode as an on-curve P-256 point");
  }
  if (point.equals(POINT.ZERO)) {
    throw new Error("point: identity element is not a valid group element");
  }
  // Subgroup membership for a cofactor-1 curve: x·(n-1) = -x ⟺ x·n = identity
  // (noble's multiply rejects the scalar n itself, so the n-1 form is used).
  if (!point.multiply(P256_N - 1n).equals(point.negate())) {
    throw new Error("point: element is not in the prime-order subgroup");
  }
  return true;
}

export function pubFromPriv(privValue) {
  const pub = mulScalar(POINT.BASE, bnOf(hb(privValue)));
  if (pub.equals(POINT.ZERO)) {
    throw new Error("degenerate identity public key: private scalar 0 must restart key generation before exposure");
  }
  return bytesToHex(pub.toBytes(false));
}

export function identityBody({ installation_id, label: labelText, device_type, identity_pub, nonce }, fingerprintBytes) {
  const labelBytes = utf8(labelText);
  if (labelBytes.length > 32) throw new Error("identity label exceeds 32 bytes");
  return concatBytes(
    hb(installation_id),
    u16be(labelBytes.length),
    labelBytes,
    hb(device_type),
    hb(identity_pub),
    fingerprintBytes,
    hb(nonce),
  );
}

export function fpSelf(roleByte, installationId, identityPub) {
  // OTP2-REC-011a: role byte 0x01 = the phone's own record, 0x02 = the client's.
  return sha256(concatBytes(label("record_fingerprint"), hb(roleByte), hb(installationId), hb(identityPub)));
}

export function fpPairDisplay(fpPairBytes) {
  const groups = [];
  for (let i = 0; i < 3; i++) {
    groups.push(Buffer.from(fpPairBytes.slice(i * 2, i * 2 + 2)).toString("hex").toUpperCase());
  }
  return groups.join(" ");
}

export function enrollmentTranscript({ ctx, pA, pB, cA, cB, phoneBody, clientBody }) {
  return sha256(
    concatBytes(
      label("enrollment_transcript"),
      u16be(ctx.length), ctx,
      u16be(pA.length), pA,
      u16be(pB.length), pB,
      u16be(cA.length), cA,
      u16be(cB.length), cB,
      u16be(phoneBody.length), phoneBody,
      u16be(clientBody.length), clientBody,
    ),
  );
}

// ---------- Reconnect (OTP2-REC-020/020a/020b/020c, OTP2-CRY-019f) ----------

export function helloPayload({ installation_id, identity_pub, nonce, role, caps }) {
  const capsBytes = caps.map(hb);
  return concatBytes(
    hb(installation_id),
    hb(identity_pub),
    hb(nonce),
    hb(role),
    u16be(1), // protocol_version
    u16be(capsBytes.length),
    concatBytes(...capsBytes),
  );
}

export function ecdhX(ownPrivBytes, peerPubBytes) {
  const shared = mulScalar(POINT.fromBytes(hb(peerPubBytes)), bnOf(hb(ownPrivBytes)));
  if (shared.equals(POINT.ZERO)) throw new Error("ECDH result is the identity point");
  const affine = shared.toAffine();
  return Buffer.from(affine.x.toString(16).padStart(64, "0"), "hex");
}

// Deterministic ECDSA with RFC6979 k (noble's hmac-drbg), low-S normalized,
// P1363 fixed-width r||s (OTP2-REC-020a). The caller passes the signing digest;
// it is hashed EXACTLY ONCE by the caller (prehash: false means no rehash).
export function signP1363LowS(digest, privValue) {
  const sigBytes = p256.sign(digest, hb(privValue), { prehash: false });
  const parsed = p256.Signature.fromBytes(sigBytes);
  const half = P256_N >> 1n;
  let s = parsed.s;
  if (s > half) s = P256_N - s; // low-S normalization
  const bytes = concatBytes(
    Buffer.from(parsed.r.toString(16).padStart(64, "0"), "hex"),
    Buffer.from(s.toString(16).padStart(64, "0"), "hex"),
  );
  return { rBytes: bytes.slice(0, 32), sBytes: bytes.slice(32, 64), bytes };
}

// Strict P1363 low-S boundary (OTP2-REC-020a): exactly 64 bytes, r and s in
// [1, n-1], s in the lower half-order. DER encodings, high-S, truncated or
// extended forms all reject.
export function validateP1363LowS(value) {
  const bytes = value instanceof Uint8Array ? value : hb(value);
  if (bytes.length !== 64) throw new Error("signature_format: P1363 signature must be exactly 64 bytes");
  const half = P256_N >> 1n;
  const r = bnOf(bytes.slice(0, 32));
  const s = bnOf(bytes.slice(32));
  if (r === 0n || r >= P256_N) throw new Error("signature_format: r out of range");
  if (s === 0n || s >= P256_N) throw new Error("signature_format: s out of range");
  if (s > half) throw new Error("signature_format: high-S signature rejected");
  return true;
}

// ---------- Protected record / AES-256-GCM (OTP2-CRY-016a/016b) ----------

export function recordNonce(noncePrefix, sequence) {
  return concatBytes(noncePrefix, u64be(sequence));
}

export function recordHeader({ direction, record_type, sequence, ciphertext_length, session_id_prefix }) {
  return concatBytes(
    hb("01"), // protocol_version 0x01
    hb("01"), // schema_version 0x01
    hb(direction),
    u16be(parseInt(record_type, 16)),
    u64be(sequence),
    u16be(ciphertext_length),
    hb(session_id_prefix),
  );
}

export function gcmEncrypt(key, nonce, aad, plaintext) {
  const cipher = gcm(key, nonce, aad, { tagLength: 16 });
  const sealed = cipher.encrypt(plaintext); // ct || tag
  return { ciphertext: sealed.slice(0, sealed.length - 16), tag: sealed.slice(sealed.length - 16) };
}

export function gcmDecrypt(key, nonce, aad, ciphertext, tag) {
  const cipher = gcm(key, nonce, aad, { tagLength: 16 });
  return cipher.decrypt(concatBytes(ciphertext, tag));
}

// ---------- Derivation entry points (shared with the generator) ----------

export function deriveRfcAnchor(input) {
  if (input.kind !== "rfc9382_anchor") throw new Error("wrong fixture kind");
  if (input.deviation) throw new Error("anchor derivation does not take deviations");
  const out = spake2Exchange({
    idA: utf8(input.id_a),
    idB: utf8(input.id_b),
    wBytes: hb(input.w),
    x: hb(input.x),
    y: hb(input.y),
    aad: new Uint8Array(0), // RFC 9382 Appendix B vectors carry no AAD
  });
  return {
    p_a: bytesToHex(out.p_a.toBytes(false)),
    p_b: bytesToHex(out.p_b.toBytes(false)),
    k: bytesToHex(out.k.toBytes(false)),
    tt: bytesToHex(out.tt),
    hash_tt: bytesToHex(out.hash_tt),
    ke: bytesToHex(out.ke),
    ka: bytesToHex(out.ka),
    kc_a: bytesToHex(out.kc_a),
    kc_b: bytesToHex(out.kc_b),
    c_a: bytesToHex(out.c_a),
    c_b: bytesToHex(out.c_b),
  };
}

export function deriveSpakePairing(input, blinding = "fixed") {
  if (input.kind !== "spake2_pairing") throw new Error("wrong fixture kind");
  if (input.deviation) throw new Error("positive derivation must not carry a deviation");
  if (!/^[0-9]{6}$/.test(input.code)) throw new Error("code must be six ASCII digits");
  const ctx = buildPublicContext({
    protocol_version: input.protocol_version ?? 1,
    schema_version: input.schema_version ?? 1,
    ciphersuite_id: input.ciphersuite_id ?? 1,
    pairing_id: input.pairing_id,
    phone_role: input.phone_role,
    client_role: input.client_role,
    phone_nonce: input.phone_nonce,
    client_nonce: input.client_nonce,
    phone_caps: input.phone_caps,
    client_caps: input.client_caps,
  });
  const { prk: passwordPrk, okm: passwordOkm, wBytes } = codeToW(utf8(input.code), hb(input.pairing_id));
  const out = spake2Exchange({
    idA: label("identity_a"),
    idB: label("identity_b"),
    wBytes,
    x: hb(input.x),
    y: hb(input.y),
    aad: ctx,
    blinding,
  });
  return {
    ctx: bytesToHex(ctx),
    ctx_len: String(ctx.length),
    ctx_len_le: bytesToHex(len64le(ctx.length)),
    password_prk: bytesToHex(passwordPrk),
    password_okm: bytesToHex(passwordOkm),
    w: bytesToHex(wBytes),
    p_a: bytesToHex(out.p_a.toBytes(false)),
    p_b: bytesToHex(out.p_b.toBytes(false)),
    k: bytesToHex(out.k.toBytes(false)),
    tt: bytesToHex(out.tt),
    hash_tt: bytesToHex(out.hash_tt),
    ke: bytesToHex(out.ke),
    ka: bytesToHex(out.ka),
    kc_a: bytesToHex(out.kc_a),
    kc_b: bytesToHex(out.kc_b),
    c_a: bytesToHex(out.c_a),
    c_b: bytesToHex(out.c_b),
  };
}

export function deriveEnrollment(input) {
  if (input.kind !== "enrollment") throw new Error("wrong fixture kind");
  if (input.deviation) throw new Error("positive derivation must not carry a deviation");
  const ke = hb(input.ke);
  const ctx = hb(input.ctx);
  const phonePub = pubFromPriv(input.phone_identity.priv);
  const clientPub = pubFromPriv(input.client_identity.priv);
  const fpPhone = fpSelf("01", input.phone_identity.installation_id, phonePub);
  const fpClient = fpSelf("02", input.client_identity.installation_id, clientPub);
  const phoneBody = identityBody({ ...input.phone_identity, identity_pub: phonePub }, fpPhone);
  const clientBody = identityBody({ ...input.client_identity, identity_pub: clientPub }, fpClient);
  // enrollment_salt = SHA-256(label31 || len64le(ctx) || ctx) (OTP2-CRY-007a)
  const enrollmentSalt = sha256(concatBytes(label("enrollment_salt"), len64le(ctx.length), ctx));
  const enrollmentPrk = extract(enrollmentSalt, ke);
  const k_ab_aes = expand(enrollmentPrk, label("enrollment_ab_aes"), 32);
  const k_ab_nonce_prefix = expand(enrollmentPrk, label("enrollment_ab_nonce"), 4);
  const k_act_a = expand(enrollmentPrk, label("enrollment_act_a"), 32);
  const k_ba_aes = expand(enrollmentPrk, label("enrollment_ba_aes"), 32);
  const k_ba_nonce_prefix = expand(enrollmentPrk, label("enrollment_ba_nonce"), 4);
  const k_act_b = expand(enrollmentPrk, label("enrollment_act_b"), 32);
  const et = enrollmentTranscript({
    ctx,
    pA: hb(input.p_a),
    pB: hb(input.p_b),
    cA: hb(input.c_a),
    cB: hb(input.c_b),
    phoneBody,
    clientBody,
  });
  const sessionPrefix = sha256(concatBytes(label("enrollment_session"), ctx)).slice(0, 8);
  const fpPair = sha256(concatBytes(label("pair_fingerprint"), hb(phonePub), hb(clientPub)));
  // Prepare proves the phone persisted its public pending record and confirmed
  // ET before the client may commit.
  const actPrepareMac = hmac(sha256, k_act_a, concatBytes(et, hb("00")));
  // Activation MACs (OTP2-CRY-019a): mac = HMAC-SHA-256(K_act, ET || stage_byte)
  const actCommitMac = hmac(sha256, k_act_b, concatBytes(et, hb("01")));
  const actCompleteMac = hmac(sha256, k_act_a, concatBytes(et, hb("02")));
  const actAckMac = hmac(sha256, k_act_b, concatBytes(et, hb("03")));
  // Enrollment nonces: 4-byte prefix || u64be sequence, starting at 0 (OTP2-CRY-007c)
  return {
    phone_identity_pub: phonePub,
    client_identity_pub: clientPub,
    phone_identity_body: bytesToHex(phoneBody),
    client_identity_body: bytesToHex(clientBody),
    fp_self_phone: bytesToHex(fpPhone),
    fp_self_client: bytesToHex(fpClient),
    fp_pair: bytesToHex(fpPair),
    fp_pair_display: fpPairDisplay(fpPair),
    enrollment_salt: bytesToHex(enrollmentSalt),
    enrollment_prk: bytesToHex(enrollmentPrk),
    k_ab_aes: bytesToHex(k_ab_aes),
    k_ab_nonce_prefix: bytesToHex(k_ab_nonce_prefix),
    k_act_a: bytesToHex(k_act_a),
    k_ba_aes: bytesToHex(k_ba_aes),
    k_ba_nonce_prefix: bytesToHex(k_ba_nonce_prefix),
    k_act_b: bytesToHex(k_act_b),
    act_prepare_mac: bytesToHex(actPrepareMac),
    act_commit_mac: bytesToHex(actCommitMac),
    act_complete_mac: bytesToHex(actCompleteMac),
    act_ack_mac: bytesToHex(actAckMac),
    enrollment_nonce_ab_seq0: bytesToHex(recordNonce(k_ab_nonce_prefix, 0)),
    enrollment_nonce_ba_seq7: bytesToHex(recordNonce(k_ba_nonce_prefix, 7)),
    session_id_prefix: bytesToHex(sessionPrefix),
    et: bytesToHex(et),
  };
}

export function deriveReconnect(input) {
  if (input.kind !== "reconnect") throw new Error("wrong fixture kind");
  if (input.deviation) throw new Error("positive derivation must not carry a deviation");
  const et = hb(input.et);
  const phonePub = pubFromPriv(input.phone_identity_priv);
  const clientPub = pubFromPriv(input.client_identity_priv);
  const phoneHello = helloPayload({
    installation_id: input.phone_installation_id,
    identity_pub: phonePub,
    nonce: input.phone_nonce,
    role: "01",
    caps: input.phone_caps,
  });
  const clientHello = helloPayload({
    installation_id: input.client_installation_id,
    identity_pub: clientPub,
    nonce: input.client_nonce,
    role: "02",
    caps: input.client_caps,
  });
  // rbase (OTP2-REC-020a): label(29) || phone_hello_payload || client_hello_payload
  const rbase = concatBytes(label("reconnect_transcript"), phoneHello, clientHello);
  const rbaseDigest = sha256(rbase);
  // ECDSA-P256-SHA256 over rbase hashed EXACTLY ONCE (OTP2-REC-020a): the
  // digest goes in with prehash:false, so no second hash occurs.
  const phoneSig = signP1363LowS(rbaseDigest, input.phone_identity_priv);
  const clientSig = signP1363LowS(rbaseDigest, input.client_identity_priv);
  const ephPubOf = (priv) => {
    const pub = mulScalar(POINT.BASE, bnOf(hb(priv)));
    if (pub.equals(POINT.ZERO)) throw new Error("degenerate identity ephemeral point: restart before exposure");
    return bytesToHex(pub.toBytes(false));
  };
  const phoneEphPub = ephPubOf(input.phone_ephemeral_priv);
  const clientEphPub = ephPubOf(input.client_ephemeral_priv);
  const ssPhone = ecdhX(input.phone_ephemeral_priv, clientEphPub);
  const ssClient = ecdhX(input.client_ephemeral_priv, phoneEphPub);
  if (Buffer.compare(Buffer.from(ssPhone), Buffer.from(ssClient)) !== 0) {
    throw new Error("reconnect ECDH disagrees between directions");
  }
  const salt = sha256(concatBytes(label("reconnect_salt"), hb(input.phone_nonce), hb(input.client_nonce)));
  const reconnectPrk = extract(salt, ssPhone);
  const k_ba_aes = expand(reconnectPrk, label("reconnect_ba_aes"), 32);
  const k_ba_nonce_prefix = expand(reconnectPrk, label("reconnect_ba_nonce"), 4);
  const k_ba_confirmation = expand(reconnectPrk, label("reconnect_ba_confirmation"), 32);
  const k_ab_aes = expand(reconnectPrk, label("reconnect_ab_aes"), 32);
  const k_ab_nonce_prefix = expand(reconnectPrk, label("reconnect_ab_nonce"), 4);
  const k_ab_confirmation = expand(reconnectPrk, label("reconnect_ab_confirmation"), 32);
  const sessionId = expand(reconnectPrk, label("reconnect_session_id"), 32);
  // Recovery activation keys (OTP2-CRY-019f), ET-bound infos.
  const k_rec_client = expand(reconnectPrk, concatBytes(label("recovery_client_activation"), et), 32);
  const k_rec_phone = expand(reconnectPrk, concatBytes(label("recovery_phone_activation"), et), 32);
  const confirmInput = sha256(concatBytes(label("reconnect_confirm_input"), rbase, phoneSig.bytes, clientSig.bytes));
  const confA = hmac(sha256, k_ab_confirmation, confirmInput);
  const confB = hmac(sha256, k_ba_confirmation, confirmInput);
  return {
    phone_identity_pub: phonePub,
    client_identity_pub: clientPub,
    phone_ephemeral_pub: phoneEphPub,
    client_ephemeral_pub: clientEphPub,
    phone_hello_payload: bytesToHex(phoneHello),
    client_hello_payload: bytesToHex(clientHello),
    rbase: bytesToHex(rbase),
    rbase_digest: bytesToHex(rbaseDigest),
    phone_sig: bytesToHex(phoneSig.bytes),
    phone_sig_r: bytesToHex(phoneSig.rBytes),
    phone_sig_s: bytesToHex(phoneSig.sBytes),
    client_sig: bytesToHex(clientSig.bytes),
    ss: bytesToHex(ssPhone),
    reconnect_salt: bytesToHex(salt),
    reconnect_prk: bytesToHex(reconnectPrk),
    k_ba_aes: bytesToHex(k_ba_aes),
    k_ba_nonce_prefix: bytesToHex(k_ba_nonce_prefix),
    k_ba_confirmation: bytesToHex(k_ba_confirmation),
    k_ab_aes: bytesToHex(k_ab_aes),
    k_ab_nonce_prefix: bytesToHex(k_ab_nonce_prefix),
    k_ab_confirmation: bytesToHex(k_ab_confirmation),
    session_id: bytesToHex(sessionId),
    session_id_prefix: bytesToHex(sessionId.slice(0, 8)),
    reconnect_confirm_input: bytesToHex(confirmInput),
    conf_a: bytesToHex(confA),
    conf_b: bytesToHex(confB),
    k_rec_client: bytesToHex(k_rec_client),
    k_rec_phone: bytesToHex(k_rec_phone),
  };
}

export function deriveRecordGcm(input) {
  if (input.kind !== "record_gcm") throw new Error("wrong fixture kind");
  if (input.deviation) throw new Error("positive derivation must not carry a deviation");
  const key = hb(input.key);
  const noncePrefix = hb(input.nonce_prefix);
  const sequence = BigInt(input.sequence);
  const body = hb(input.body);
  const header = recordHeader({
    direction: input.direction,
    record_type: input.record_type,
    sequence,
    ciphertext_length: body.length,
    session_id_prefix: input.session_id_prefix,
  });
  if (header.length !== 23) throw new Error("record header must be 23 bytes");
  const nonce = recordNonce(noncePrefix, sequence);
  if (nonce.length !== 12) throw new Error("record nonce must be 12 bytes");
  const { ciphertext, tag } = gcmEncrypt(key, nonce, header, body);
  return {
    header: bytesToHex(header),
    nonce: bytesToHex(nonce),
    ciphertext: bytesToHex(ciphertext),
    tag: bytesToHex(tag),
    record: bytesToHex(concatBytes(header, ciphertext, tag)),
  };
}

export function derivePositiveOutputs(fixture) {
  switch (fixture.input.kind) {
    case "rfc9382_anchor":
      return deriveRfcAnchor(fixture.input);
    case "spake2_pairing":
      return deriveSpakePairing(fixture.input);
    case "enrollment":
      return deriveEnrollment(fixture.input);
    case "reconnect":
      return deriveReconnect(fixture.input);
    case "record_gcm":
      return deriveRecordGcm(fixture.input);
    default:
      throw new Error(`unknown crypto fixture kind ${fixture.input?.kind}`);
  }
}

// ---------- Negative deviations ----------

export const NEGATIVE_DEVIATIONS = Object.freeze([
  "wrong_code",
  "context_mismatch",
  "swap_roles",
  "role_mismatch",
  "off_curve_point",
  "identity_point",
  "zero_point",
  "compressed_point",
  "shared_secret_identity",
  "high_s",
  "der_encoding",
  "truncated_sig",
  "bad_tag",
  "mutated_session",
  "mutated_direction",
  "mutated_sequence",
]);

function derEncode(r, s) {
  const integer = (v) => {
    let hex = v.toString(16);
    if (hex.length % 2 === 1) hex = `0${hex}`;
    let bytes = Buffer.from(hex, "hex");
    if (bytes[0] >= 0x80) bytes = concatBytes(hb("00"), bytes);
    return concatBytes(hb("02"), Buffer.from([bytes.length]), bytes);
  };
  const body = concatBytes(integer(r), integer(s));
  return concatBytes(hb("30"), Buffer.from([body.length]), body);
}

function buildSignatureVariant(type, base) {
  const phoneSig = hb(base.expected.outputs.phone_sig);
  const r = bnOf(phoneSig.slice(0, 32));
  const s = bnOf(phoneSig.slice(32));
  if (type === "high_s") {
    // Same r, s := n - s: mathematically valid ECDSA, non-canonical for Veles.
    return concatBytes(
      Buffer.from(r.toString(16).padStart(64, "0"), "hex"),
      Buffer.from((P256_N - s).toString(16).padStart(64, "0"), "hex"),
    );
  }
  if (type === "der_encoding") {
    return derEncode(r, s); // correct signature bytes, forbidden encoding
  }
  if (type === "truncated_sig") {
    return phoneSig.slice(0, 63);
  }
  throw new Error(`unknown signature variant ${type}`);
}

// Deterministic non-canonical signature encodings derived from a committed or
// freshly computed P1363 signature (single source shared with the generator).
export function buildSignatureVariants(sigHex) {
  const bytes = hb(sigHex);
  const r = bnOf(bytes.slice(0, 32));
  const s = bnOf(bytes.slice(32));
  return {
    high_s: bytesToHex(
      concatBytes(
        Buffer.from(r.toString(16).padStart(64, "0"), "hex"),
        Buffer.from((P256_N - s).toString(16).padStart(64, "0"), "hex"),
      ),
    ),
    der_encoding: bytesToHex(derEncode(r, s)),
    truncated_sig: bytesToHex(bytes.slice(0, 63)),
  };
}

function mutateRecordBytes(record, type) {
  const bytes = Buffer.from(record);
  if (type === "bad_tag") {
    bytes[bytes.length - 1] ^= 0x01; // flip one tag bit
    return bytes;
  }
  if (type === "mutated_session") {
    bytes[15] ^= 0xff; // session_id_prefix occupies header bytes 15..23
    return bytes;
  }
  if (type === "mutated_direction") {
    bytes[2] = bytes[2] === 0x01 ? 0x02 : 0x01; // direction at header offset 2
    return bytes;
  }
  if (type === "mutated_sequence") {
    bytes[12] = (bytes[12] + 1) & 0xff; // last sequence byte (offsets 5..13)
    return bytes;
  }
  throw new Error(`unknown record mutation ${type}`);
}

// Shared single-source record-deviation helper (used by the OpenSSL
// cross-checker and tests). Returns the mutated full record bytes.
export function recordDeviation(recordHex, type) {
  return bytesToHex(mutateRecordBytes(hb(recordHex), type));
}

// Stage is DERIVED from the actual failure kind, then compared independently
// against the committed expected.stage — never echoed from it.
export const DEVIATION_STAGE = Object.freeze({
  wrong_code: "code",
  context_mismatch: "context",
  swap_roles: "role",
  role_mismatch: "role",
  off_curve_point: "point",
  identity_point: "point",
  zero_point: "point",
  compressed_point: "point_format",
  shared_secret_identity: "shared_secret",
  high_s: "signature_format",
  der_encoding: "signature_format",
  truncated_sig: "signature_format",
  bad_tag: "tag",
  mutated_session: "session",
  mutated_direction: "direction",
  mutated_sequence: "sequence",
});

function assertionStage(error) {
  // Strict validators prefix their rejection stage in the message:
  // "point:", "point_format:", "signature_format:".
  const match = /^(point|point_format|signature_format):/.exec(error.message ?? "");
  if (!match) throw new Error(`internal: strict validator error has no stage prefix: ${error?.message}`);
  return match[1];
}

function expectedStageOf(fixture, derivedStage) {
  if (fixture.expected.stage !== derivedStage) {
    throw new Error(
      `negative fixture "${fixture.id}": actual rejection stage is "${derivedStage}" but the fixture declares "${fixture.expected.stage}"`,
    );
  }
  return derivedStage;
}

// Evaluate a negative fixture against its positive base. Returns
// { stage, proof } when the deviation produces the specified rejection;
// throws when it does not or when the declared stage mismatches the actual
// failure kind.
export function checkNegative(fixture, fixturesById) {
  const deviation = fixture.input.deviation;
  if (!deviation?.negates) {
    throw new Error(`negative fixture "${fixture.id}" must reference its positive base (deviation.negates)`);
  }
  const base = fixturesById.get(deviation.negates);
  if (!base) {
    throw new Error(`negative fixture "${fixture.id}": base fixture "${deviation.negates}" not found`);
  }
  if (base.expected.rejected === true) {
    throw new Error(`negative fixture "${fixture.id}" references another negative as base`);
  }
  const type = deviation.type;

  switch (type) {
    case "wrong_code":
    case "context_mismatch":
    case "swap_roles":
    case "role_mismatch": {
      const mutated = structuredClone(fixture.input);
      delete mutated.deviation;
      if (type === "wrong_code") mutated.code = deviation.value ?? "654321";
      if (type === "context_mismatch") mutated.client_nonce = deviation.value;
      const blinding = type === "swap_roles" ? "swap_roles" : type === "role_mismatch" ? "role_mismatch" : "fixed";
      const deviated = deriveSpakePairing(mutated, blinding);
      const positives = base.expected.outputs;
      const distinctions = fixture.expected?.distinguishes;
      if (!Array.isArray(distinctions) || distinctions.length === 0) {
        throw new Error(`negative fixture "${fixture.id}" must list distinguishing outputs`);
      }
      if (!distinctions.includes("c_a") || !distinctions.includes("c_b")) {
        throw new Error(
          `negative fixture "${fixture.id}": distinguishing set must include BOTH peer confirmations c_a and c_b`,
        );
      }
      const errors = [];
      const nameSet = new Set([...Object.keys(deviated), ...Object.keys(positives)]);
      for (const name of nameSet) {
        const pos = positives[name];
        const dev = deviated[name];
        if (pos === undefined || dev === undefined) continue;
        const differs = pos !== dev;
        if (distinctions.includes(name) && !differs) errors.push(`expected to differ but matched: ${name}`);
        if (!distinctions.includes(name) && differs) errors.push(`unexpectedly differs: ${name}`);
      }
      if (errors.length > 0) {
        throw new Error(`negative fixture "${fixture.id}": ${errors.join("; ")}`);
      }
      // Peer-confirmation actual-equality check, beyond the distinguishing
      // list: BOTH the phone's (c_a) and the client's (c_b) confirmations MUST
      // be byte-different from the positive execution, so both peers reject.
      if (deviated.c_a === positives.c_a) {
        throw new Error(`negative fixture "${fixture.id}": phone confirmation c_a unexpectedly equals the positive execution`);
      }
      if (deviated.c_b === positives.c_b) {
        throw new Error(`negative fixture "${fixture.id}": client confirmation c_b unexpectedly equals the positive execution`);
      }
      return { stage: expectedStageOf(fixture, DEVIATION_STAGE[type]), proof: "confirmation_mismatch" };
    }
    case "off_curve_point":
    case "identity_point":
    case "zero_point":
    case "compressed_point": {
      try {
        validatePointUncompressed(deviation.value);
      } catch (e) {
        return { stage: expectedStageOf(fixture, assertionStage(e)), proof: "strict_validation", detail: e.message };
      }
      throw new Error(`negative fixture "${fixture.id}": expected point rejection but the point validated`);
    }
    case "shared_secret_identity": {
      // OTP2-CRY-003a normative probe, honestly bound to committed bytes
      // (re-review I3): the fixture carries the EXACT forced peer points
      // (deviation.value = pB forced phone view; deviation.value2 = pA
      // forced client view). Both are parsed, validated as real on-curve
      // points, and USED here — they must (a) be valid points, (b) differ
      // from the positive base's honest peer points, and (c) collapse the
      // shared element of their view to the identity, aborting with the
      // generic pre-auth failure BEFORE any KDF output or confirmation is
      // produced; the phone's already-consumed attempt is not refunded.
      const forcePeerB = deviation.value;
      const forcePeerA = deviation.value2;
      if (typeof forcePeerB !== "string" || typeof forcePeerA !== "string") {
        throw new Error(`negative fixture "${fixture.id}": forced peer point bytes must be committed`);
      }
      const baseOutputs = base.expected.outputs;
      if (forcePeerB === baseOutputs.p_b || forcePeerA === baseOutputs.p_a) {
        throw new Error(
          `negative fixture "${fixture.id}": the forced peer point must differ from the positive base's honest peer point`,
        );
      }
      try {
        validatePointUncompressed(forcePeerB);
        validatePointUncompressed(forcePeerA);
      } catch (e) {
        throw new Error(`negative fixture "${fixture.id}": committed forced peer points must be valid points: ${e.message}`);
      }
      const ctx = buildPublicContext({
        pairing_id: base.input.pairing_id,
        phone_role: base.input.phone_role,
        client_role: base.input.client_role,
        phone_nonce: base.input.phone_nonce,
        client_nonce: base.input.client_nonce,
        phone_caps: base.input.phone_caps,
        client_caps: base.input.client_caps,
      });
      const baseRecheck = deriveSpakePairing(base.input);
      if (baseRecheck.k !== baseOutputs.k || baseRecheck.c_a !== baseOutputs.c_a) {
        throw new Error(`negative fixture "${fixture.id}": positive base does not re-derive`);
      }
      const forcedInputs = { idA: label("identity_a"), idB: label("identity_b"), wBytes: hb(baseOutputs.w), x: hb(base.input.x), y: hb(base.input.y), aad: ctx };
      for (const [view, forced] of [["phone", forcePeerB], ["client", forcePeerA]]) {
        try {
          if (view === "phone") spake2Exchange({ ...forcedInputs, forcePeerB: forced });
          else spake2Exchange({ ...forcedInputs, forcePeerA: forced });
        } catch (e) {
          if (!/degenerate shared group element after peer input/.test(e.message) || !/OTP2-CRY-003a/.test(e.message)) {
            throw e; // a different failure: premise broken
          }
          continue; // generic abort before any KDF/confirmation output ✓
        }
        throw new Error(
          `negative fixture "${fixture.id}": ${view} did not abort on the identity shared element`,
        );
      }
      return { stage: expectedStageOf(fixture, DEVIATION_STAGE.shared_secret_identity), proof: "strict_validation", detail: "OTP2-CRY-003a: with the committed forced peer points both endpoint views abort before any KDF output or confirmation; attempt non-refundable" };
    }
    case "high_s":
    case "der_encoding":
    case "truncated_sig": {
      const bytes = deviation.value !== undefined ? hb(deviation.value) : buildSignatureVariant(type, base);
      try {
        validateP1363LowS(bytes);
      } catch (e) {
        return { stage: expectedStageOf(fixture, assertionStage(e)), proof: "strict_validation", detail: e.message };
      }
      throw new Error(`negative fixture "${fixture.id}": expected signature rejection but the encoding validated`);
    }
    case "bad_tag":
    case "mutated_session":
    case "mutated_direction":
    case "mutated_sequence": {
      const record = Buffer.from(deriveRecordGcm(base.input).record);
      const mutated = mutateRecordBytes(record, type);
      if (Buffer.compare(record, mutated) === 0) {
        throw new Error(`negative fixture "${fixture.id}": deviation did not alter the record bytes`);
      }
      // Scope assertion: tag mutation touches only the tag; header mutations
      // touch only the 23-byte header (ciphertext+tag remain authentic bytes).
      const payloadEqual =
        Buffer.compare(record.subarray(23, record.length - 16), mutated.subarray(23, mutated.length - 16)) === 0;
      const tagEqual = Buffer.compare(record.subarray(record.length - 16), mutated.subarray(record.length - 16)) === 0;
      const headerEqual = Buffer.compare(record.subarray(0, 23), mutated.subarray(0, 23)) === 0;
      if (type === "bad_tag" && (tagEqual || !payloadEqual)) {
        throw new Error(`negative fixture "${fixture.id}": bad_tag mutation must alter exactly the tag bytes`);
      }
      if (type !== "bad_tag" && (headerEqual || !payloadEqual || !tagEqual)) {
        throw new Error(`negative fixture "${fixture.id}": header mutation must alter exactly the AAD bytes`);
      }
      const key = hb(base.input.key);
      const nonce = recordNonce(hb(base.input.nonce_prefix), BigInt(base.input.sequence));
      const header = mutated.subarray(0, 23);
      const ciphertext = mutated.subarray(23, mutated.length - 16);
      const tag = mutated.subarray(mutated.length - 16);
      try {
        gcmDecrypt(key, nonce, header, ciphertext, tag);
      } catch {
        return { stage: expectedStageOf(fixture, DEVIATION_STAGE[type]), proof: "aead_failure" };
      }
      throw new Error(`negative fixture "${fixture.id}": expected AEAD failure but the record verified`);
    }
    default:
      throw new Error(`negative fixture "${fixture.id}": unknown deviation type ${type}`);
  }
}

// ---------- report plumbing ----------

const REPO_ROOT = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const DEFAULT_REPORTS_DIR = join(REPO_ROOT, "build", "protocol");

// Exact tool identities expected in the report `tool` field — a report from
// any other writer is not independent verification of this acceptance run.
export const REPORT_TOOLS = Object.freeze({
  oracle: "crosscheck-vectors/oracle.py",
  openssl: "crosscheck-vectors/openssl-check.mjs",
});
export const COMMITTED_REPORT_FILES = Object.freeze({
  oracle: "crosscheck-oracle-committed.json",
  openssl: "crosscheck-openssl-committed.json",
});

const POSITIVE_REQUIRED_REPORTS = Object.freeze({
  rfc9382_anchor: ["oracle"],
  spake2_pairing: ["oracle"],
  enrollment: ["oracle"],
  reconnect: ["oracle", "openssl"],
  record_gcm: ["openssl"],
});

// Negatives need any ONE independent implementation to have confirmed the
// rejection (oracle strict validation or OpenSSL AEAD/verify failure).
const NEGATIVE_REQUIRED_REPORTS = Object.freeze({
  wrong_code: ["oracle"],
  context_mismatch: ["oracle"],
  swap_roles: ["oracle"],
  role_mismatch: ["oracle"],
  off_curve_point: ["oracle"],
  identity_point: ["oracle"],
  zero_point: ["oracle"],
  compressed_point: ["oracle"],
  shared_secret_identity: ["oracle"],
  high_s: ["oracle", "openssl"],
  der_encoding: ["oracle", "openssl"],
  truncated_sig: ["oracle", "openssl"],
  bad_tag: ["openssl"],
  mutated_session: ["openssl"],
  mutated_direction: ["openssl"],
  mutated_sequence: ["openssl"],
});

function readReport(dir, name) {
  try {
    return JSON.parse(readFileSync(join(dir, name), "utf8"));
  } catch {
    return null;
  }
}

// A report entry accepts a fixture ONLY when:
//   - the report carries its exact tool identity, an input file reference and
//     an independent-implementation description, and
//   - the entry status is ok, AND
//   - the entry's per-fixture canonical content digest equals the digest of
//     the CURRENT committed fixture bytes — a stale report produced against
//     older bytes can never approve a changed fixture, and
//   - the outputs the tool recorded are all members of the fixture's required
//     output set (positives; negatives just need a non-empty proof list).
// Returns the entry so the verifier can union covered outputs across tools.
function reportOk(reportKey, reports, fixture, digest, requiredOutputs, negative) {
  const report = reports[reportKey];
  if (!report || report.tool !== REPORT_TOOLS[reportKey]) return false;
  if (typeof report.independent_implementation !== "string" || report.independent_implementation.length === 0) {
    return false;
  }
  if (typeof report.input_file !== "string" || report.input_file.length === 0) return false;
  const digestMap = report.fixture_sha256;
  if (digestMap?.[fixture.id] !== digest) return false;
  const entry = report.fixtures?.[fixture.id];
  if (!entry || entry.status !== "ok") return false;
  const recorded = entry.outputs;
  if (!Array.isArray(recorded) || recorded.length === 0) return false;
  if (requiredOutputs) {
    const allowed = new Set(requiredOutputs);
    if (!recorded.every((name) => allowed.has(name))) return false;
  }
  return entry;
}

export function buildReportsDir(reportsDir) {
  return reportsDir ?? process.env.VELES_CRYPTO_REPORTS_DIR ?? DEFAULT_REPORTS_DIR;
}

/**
 * Verify committed crypto fixtures.
 * @param {Array<object>} fixtures - fixtures from loadFixtures (crypto category is processed).
 * @param {{reportsDir?: string}} [opts]
 * @returns {{verified_ids: string[], unverified_ids: string[]}}
 * Throws when a positive fixture's committed expected bytes do not match the
 * recomputation, when the committed output set is not exactly the per-kind
 * required set, or a negative fixture fails to reject / names the wrong
 * stage. Missing, tool-mismatched, or STALE independent reports (bound by
 * canonical per-fixture SHA-256 digests) do not throw: the affected fixture
 * ids are returned in unverified_ids, which blocks OTP-02 acceptance
 * explicitly (OTP2-FIX-005).
 */
export function verifyCryptoFixtures(fixtures, opts = {}) {
  const cryptoFixtures = fixtures.filter((f) => f.category === "crypto");
  const fixturesById = new Map(cryptoFixtures.map((f) => [f.id, f]));
  const reportsDir = buildReportsDir(opts.reportsDir);
  const reports = {
    oracle: readReport(reportsDir, COMMITTED_REPORT_FILES.oracle),
    openssl: readReport(reportsDir, COMMITTED_REPORT_FILES.openssl),
  };

  const verifiedIds = [];
  const unverifiedIds = [];
  for (const fixture of cryptoFixtures) {
    // Structural recomputation — hard-fail on any wrong committed value and
    // on incomplete/extra committed output sets.
    const negative = fixture.expected.rejected === true;
    const requiredOutputs = negative ? null : REQUIRED_OUTPUTS[fixture.input.kind];
    if (!negative) {
      if (!requiredOutputs) throw new Error(`crypto fixture "${fixture.id}": unknown kind ${fixture.input.kind}`);
      const committedKeys = Object.keys(fixture.expected.outputs ?? {}).sort();
      const requiredKeys = [...requiredOutputs].sort();
      const missing = requiredKeys.filter((name) => !committedKeys.includes(name));
      const extra = committedKeys.filter((name) => !requiredKeys.includes(name));
      if (missing.length > 0) {
        throw new Error(`crypto fixture "${fixture.id}": committed outputs are missing required keys: ${missing.join(", ")}`);
      }
      if (extra.length > 0) {
        throw new Error(`crypto fixture "${fixture.id}": committed outputs carry unknown keys: ${extra.join(", ")}`);
      }
      const recomputed = derivePositiveOutputs(fixture);
      for (const [name, hex] of Object.entries(fixture.expected.outputs)) {
        if (recomputed[name] === undefined) {
          throw new Error(`crypto fixture "${fixture.id}": engine does not derive output ${name}`);
        }
        if (recomputed[name] !== hex) {
          throw new Error(
            `crypto fixture "${fixture.id}": recomputed ${name} ${recomputed[name].slice(0, 22)}... does not match committed ${hex.slice(0, 22)}...`,
          );
        }
      }
    } else {
      checkNegative(fixture, fixturesById);
    }
    // Independent-provenance coverage (OTP2-FIX-005): content-digest-bound
    // reports from exactly the named tools.
    const deviationType = negative ? fixture.input.deviation?.type ?? null : null;
    const required = negative
      ? NEGATIVE_REQUIRED_REPORTS[deviationType]
      : POSITIVE_REQUIRED_REPORTS[fixture.input.kind];
    if (!required) {
      throw new Error(
        `crypto fixture "${fixture.id}": no independent-report mapping (kind=${fixture.input.kind}, deviation=${deviationType})`,
      );
    }
    const digest = fixtureContentDigest(fixture);
    let covered;
    if (negative) {
      covered = required.some((r) => Boolean(reportOk(r, reports, fixture, digest, null, true)));
    } else {
      // Every required tool must accept, and the union of the outputs each
      // tool recorded must cover the FULL required output set.
      const coveredOutputs = new Set();
      const allAccepted = required.every((r) => {
        const entry = reportOk(r, reports, fixture, digest, requiredOutputs, false);
        if (!entry) return false;
        for (const name of entry.outputs) coveredOutputs.add(name);
        return true;
      });
      covered = allAccepted && requiredOutputs.every((name) => coveredOutputs.has(name));
    }
    if (covered) verifiedIds.push(fixture.id);
    else unverifiedIds.push(fixture.id);
  }
  return { verified_ids: verifiedIds, unverified_ids: unverifiedIds };
}
