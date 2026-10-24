#!/usr/bin/env node
// Independent OpenSSL cross-check for Veles OTP-02 crypto vectors (Task 5).
//
// TEST-ONLY. Cross-checks the noble-based candidate against Node's built-in
// OpenSSL: RFC 5869 HKDF over OpenSSL HMAC, ECDH shared-secret X coordinates
// (SEC1 DER private key import; the public point is derived by OpenSSL, not
// by noble), SHA-256/HMAC confirmation chains over the RFC 9382 published
// transcript, ECDSA P1363 verification with exact single-prehash semantics
// (Node signs/verifies SHA-256(rbase) exactly once), and AES-256-GCM
// ciphertext+tag with the 23-byte header as AAD.
//
// Not an independent P-256 SPAKE2 derivation: point arithmetic (M/N blinding,
// K) is cross-checked by crosscheck-vectors/oracle.py instead. This check and
// the oracle are mutually independent; they must never be presented as
// independent of each other, and this check is ONE implementation (OpenSSL)
// cross-checking the pure-JS noble engine.

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import crypto from "node:crypto";
import {
  recordDeviation,
  canonicalJson,
  fixtureContentDigest,
  fixtureDigestMap,
  fixturesArrayDigest,
} from "../../checks/crypto.mjs";

const toolsDir = dirname(fileURLToPath(import.meta.url));
const ROOT = join(toolsDir, "..", "..", "..");
const DEFAULT_INPUT = join(ROOT, "build", "protocol", "crypto-candidate.json");
// Reports always land under build/protocol — never next to the committed
// fixtures (the fixture loader parses every *.json under fixtures/).
const defaultReportPath = (inputPath) => {
  const base = inputPath.split("/").pop();
  if (base === "crypto-candidate.json") {
    return inputPath === DEFAULT_INPUT ? join(ROOT, "build", "protocol", "crosscheck-openssl.json") : `${inputPath.replace(/\.json$/, "")}-openssl-report.json`;
  }
  if (base === "crypto.json") {
    return join(ROOT, "build", "protocol", "crosscheck-openssl-committed.json");
  }
  return inputPath.replace(/\.json$/, "-openssl-report.json");
};
const positional = process.argv.slice(2);
const CANDIDATE = positional[0] ?? DEFAULT_INPUT;
const REPORT = positional[1] ?? defaultReportPath(CANDIDATE);

const LABELS = {
  confirmation_keys: "ConfirmationKeys",
  enrollment_salt: "Veles SPAKE2 enrollment salt v1",
  enrollment_act_a: "Veles enrollment A confirmation v1",
  reconnect_salt: "Veles reconnect salt v1",
  reconnect_ba_aes: "Veles reconnect B-to-A AES key v1",
  reconnect_ba_nonce: "Veles reconnect B-to-A nonce v1",
  reconnect_ba_confirmation: "Veles reconnect B-to-A confirmation v1",
  reconnect_ab_aes: "Veles reconnect A-to-B AES key v1",
  reconnect_ab_nonce: "Veles reconnect A-to-B nonce v1",
  reconnect_ab_confirmation: "Veles reconnect A-to-B confirmation v1",
  reconnect_session_id: "Veles reconnect session ID v1",
  reconnect_confirm_input: "Veles reconnect confirmation input v1",
  recovery_client_activation: "Veles recovery client activation v1",
  recovery_phone_activation: "Veles recovery phone activation v1",
};
// Exact ASCII byte lengths asserted before any use (contract-mandated).
const LABEL_LENGTHS = {
  confirmation_keys: 16,
  enrollment_salt: 31,
  enrollment_act_a: 34,
  reconnect_salt: 23,
  reconnect_ba_aes: 33,
  reconnect_ba_nonce: 31,
  reconnect_ba_confirmation: 38,
  reconnect_ab_aes: 33,
  reconnect_ab_nonce: 31,
  reconnect_ab_confirmation: 38,
  reconnect_session_id: 29,
  reconnect_confirm_input: 37,
  recovery_client_activation: 35,
  recovery_phone_activation: 34,
};
for (const [key, length] of Object.entries(LABEL_LENGTHS)) {
  if (Buffer.byteLength(LABELS[key]) !== length) {
    throw new Error(`openssl-check: label ${key} is ${Buffer.byteLength(LABELS[key])} bytes, contract says ${length}`);
  }
}

const bytes = (value) => Buffer.from(String(value).replace(/^0x/, ""), "hex");
const hex = (buf) => "0x" + Buffer.from(buf).toString("hex");

// RFC 5869 over OpenSSL primitives: Extract = HMAC(salt, IKM); nil salt for
// SHA-256 equals a 32-zero salt (confirmed against the published anchors).
const hkdfExtract = (salt, ikm) => crypto.createHmac("sha256", salt).update(ikm).digest();
function hkdfExpand(prk, info, length) {
  const blocks = [];
  let t = Buffer.alloc(0);
  for (let counter = 1; blocks.reduce((n, b) => n + b.length, 0) < length; counter++) {
    t = crypto.createHmac("sha256", prk).update(Buffer.concat([t, info, Buffer.from([counter])])).digest();
    blocks.push(t);
  }
  return Buffer.concat(blocks).subarray(0, length);
}

// prime256v1 OID (1.2.840.10045.3.1.7), published constant.
const P256_NAMED_CURVE_OID = Buffer.from("2a8648ce3d030107", "hex");

function privKeyFrom(dBytes) {
  // Minimal SEC1 (RFC 5915) ECPrivateKey DER with the named-curve parameter,
  // no embedded public key: OpenSSL derives the public point itself.
  const inner = Buffer.concat([
    Buffer.from("020101", "hex"),
    Buffer.concat([Buffer.from("0420", "hex"), dBytes]),
    Buffer.from("a00a0608", "hex"), // [0] EXPLICIT + OID tag + length
    P256_NAMED_CURVE_OID,
  ]);
  const sec1 = Buffer.concat([Buffer.from([0x30, inner.length]), inner]);
  return crypto.createPrivateKey({ key: sec1, format: "der", type: "sec1" });
}

function pubKeyFrom(pub65) {
  return crypto.createPublicKey({
    key: {
      kty: "EC",
      crv: "P-256",
      x: pub65.subarray(1, 33).toString("base64url"),
      y: pub65.subarray(33).toString("base64url"),
    },
    format: "jwk",
  });
}

function diffieHellmanX(privBytes, peerPub65) {
  return crypto.diffieHellman({ privateKey: privKeyFrom(privBytes), publicKey: pubKeyFrom(peerPub65) });
}

const candidate = JSON.parse(readFileSync(CANDIDATE, "utf8"));
if (!Array.isArray(candidate.fixtures)) {
  console.error("openssl-check: input has no fixtures array");
  process.exit(2);
}
const report = {
  tool: "crosscheck-vectors/openssl-check.mjs",
  node: process.version.replace(/^v/, ""),
  openssl: process.versions.openssl,
  independent_implementation:
    "Node built-in OpenSSL (crypto.createHmac/hash/diffieHellman/sign/verify, AES-256-GCM via createCipheriv); RFC 5869 expand is an HMAC loop over OpenSSL primitives because Node ships no expand-only API",
  input_file: CANDIDATE,
  input_sha256: crypto.createHash("sha256").update(readFileSync(CANDIDATE)).digest("hex"),
  fixtures_sha256: fixturesArrayDigest(candidate.fixtures),
  fixture_sha256: fixtureDigestMap(candidate.fixtures),
  fixtures: {},
};

function check(fixtureId, fn) {
  try {
    const outputs = fn(); // name list of the outputs this OpenSSL run verified
    if (!Array.isArray(outputs) || outputs.length === 0) throw new Error("internal: check returned no outputs");
    report.fixtures[fixtureId] = {
      status: "ok",
      outputs: [...new Set(outputs)].sort(),
      fixture_sha256: fixtureDigestMapRef()[fixtureId],
    };
  } catch (err) {
    report.fixtures[fixtureId] = { status: "fail", errors: [String(err.message ?? err)] };
  }
}

let digestMapCache = null;
function fixtureDigestMapRef() {
  digestMapCache ??= fixtureDigestMap(candidate.fixtures);
  return digestMapCache;
}

for (const fixture of candidate.fixtures) {
  const id = fixture.id;
  const input = fixture.input;
  const outputs = fixture.expected?.outputs ?? {};

  if (input.kind === "rfc9382_anchor") {
    check(id, () => {
      // Cross-check the published transcript's hash chain with OpenSSL.
      const tt = bytes(outputs.tt);
      const digest = crypto.createHash("sha256").update(tt).digest();
      if (hex(digest) !== outputs.hash_tt) throw new Error("SHA-256(TT) mismatch");
      const ke = digest.subarray(0, 16);
      const ka = digest.subarray(16, 32);
      if (hex(ke) !== outputs.ke || hex(ka) !== outputs.ka) throw new Error("Ke/Ka split mismatch");
      const confirmOkm = hkdfExpand(
        hkdfExtract(Buffer.alloc(0), ka), // RFC 9382 KDF: HKDF(salt=nil, IKM=Ka)
        Buffer.from(LABELS.confirmation_keys),
        32,
      );
      if (hex(confirmOkm.subarray(0, 16)) !== outputs.kc_a || hex(confirmOkm.subarray(16)) !== outputs.kc_b) {
        throw new Error("KcA/KcB HKDF mismatch (OpenSSL)");
      }
      const cA = crypto.createHmac("sha256", confirmOkm.subarray(0, 16)).update(tt).digest();
      const cB = crypto.createHmac("sha256", confirmOkm.subarray(16)).update(tt).digest();
      if (hex(cA) !== outputs.c_a || hex(cB) !== outputs.c_b) throw new Error("confirmation HMAC mismatch (OpenSSL)");
      return ["hash_tt", "ke", "ka", "kc_a", "kc_b", "c_a", "c_b"];
    });
    continue;
  }

  if (input.kind === "enrollment") {
    check(id, () => {
      const ctx = bytes(input.ctx);
      const len = Buffer.alloc(8);
      len.writeBigUInt64LE(BigInt(ctx.length));
      const salt = crypto.createHash("sha256").update(Buffer.from(LABELS.enrollment_salt)).update(len).update(ctx).digest();
      if (hex(salt) !== outputs.enrollment_salt) throw new Error("enrollment salt mismatch");
      const prk = hkdfExtract(salt, bytes(input.ke));
      if (hex(prk) !== outputs.enrollment_prk) throw new Error("enrollment PRK mismatch");
      const kActA = hkdfExpand(prk, Buffer.from(LABELS.enrollment_act_a), 32);
      if (hex(kActA) !== outputs.k_act_a) throw new Error("enrollment activation key mismatch");
      const prepare = crypto.createHmac("sha256", kActA).update(Buffer.concat([bytes(outputs.et), Buffer.from([0])])).digest();
      if (hex(prepare) !== outputs.act_prepare_mac) throw new Error("enrollment prepare MAC mismatch");
      return ["enrollment_salt", "enrollment_prk", "k_act_a", "act_prepare_mac"];
    });
    continue;
  }

  if (input.kind === "reconnect") {
    check(id, () => {
      const verified = [];
      // ECDH: each direction entirely through OpenSSL.
      const ssPhone = diffieHellmanX(bytes(input.phone_ephemeral_priv), bytes(outputs.client_ephemeral_pub));
      const ssClient = diffieHellmanX(bytes(input.client_ephemeral_priv), bytes(outputs.phone_ephemeral_pub));
      if (!ssPhone.equals(ssClient)) throw new Error("OpenSSL ECDH disagrees between directions");
      if (hex(ssPhone) !== outputs.ss) throw new Error("ss mismatch (OpenSSL vs noble)");
      verified.push("ss");
      // HKDF labels via OpenSSL over the recomputed salt.
      const salt = crypto
        .createHash("sha256")
        .update(Buffer.from(LABELS.reconnect_salt))
        .update(bytes(input.phone_nonce))
        .update(bytes(input.client_nonce))
        .digest();
      if (hex(salt) !== outputs.reconnect_salt) throw new Error("reconnect salt digest mismatch");
      const prk = hkdfExtract(salt, ssPhone);
      if (hex(prk) !== outputs.reconnect_prk) throw new Error("reconnect PRK mismatch (OpenSSL HMAC vs noble)");
      verified.push("reconnect_salt", "reconnect_prk");
      const derived = (labelBuf, len) => hkdfExpand(prk, labelBuf, len);
      const checks = [
        ["k_ba_aes", 32, LABELS.reconnect_ba_aes],
        ["k_ba_nonce_prefix", 4, LABELS.reconnect_ba_nonce],
        ["k_ba_confirmation", 32, LABELS.reconnect_ba_confirmation],
        ["k_ab_aes", 32, LABELS.reconnect_ab_aes],
        ["k_ab_nonce_prefix", 4, LABELS.reconnect_ab_nonce],
        ["k_ab_confirmation", 32, LABELS.reconnect_ab_confirmation],
        ["session_id", 32, LABELS.reconnect_session_id],
      ];
      for (const [name, len, name2] of checks) {
        if (hex(derived(Buffer.from(name2), len)) !== outputs[name]) {
          throw new Error(`reconnect HKDF ${name} mismatch (OpenSSL vs noble)`);
        }
        verified.push(name);
      }
      const et = bytes(input.et);
      if (hex(derived(Buffer.concat([Buffer.from(LABELS.recovery_client_activation), et]), 32)) !== outputs.k_rec_client) {
        throw new Error("k_rec_client mismatch (OpenSSL vs noble)");
      }
      if (hex(derived(Buffer.concat([Buffer.from(LABELS.recovery_phone_activation), et]), 32)) !== outputs.k_rec_phone) {
        throw new Error("k_rec_phone mismatch (OpenSSL vs noble)");
      }
      verified.push("k_rec_client", "k_rec_phone");
      // ECDSA verification with single-prehash semantics: OpenSSL verifies a
      // signature over SHA-256(rbase) exactly once — the pinned construction.
      const rbase = bytes(outputs.rbase);
      const phonePub = pubKeyFrom(bytes(outputs.phone_identity_pub));
      const clientPub = pubKeyFrom(bytes(outputs.client_identity_pub));
      if (!crypto.verify("sha256", rbase, { key: phonePub, dsaEncoding: "ieee-p1363" }, bytes(outputs.phone_sig))) {
        throw new Error("phone signature does not verify under OpenSSL ECDSA-P256-SHA256");
      }
      if (!crypto.verify("sha256", rbase, { key: clientPub, dsaEncoding: "ieee-p1363" }, bytes(outputs.client_sig))) {
        throw new Error("client signature does not verify under OpenSSL ECDSA-P256-SHA256");
      }
      verified.push("phone_sig", "client_sig");
      // Double-prehashing is an interop defect: crypto.sign("sha256", msg)
      // signs SHA-256(msg), so signing sha256(rbase) yields a signature over
      // SHA-256(SHA-256(rbase)) — exactly TWO hashes of rbase. That signature
      // must NOT verify against the single-hash rbase construction.
      const priv = privKeyFrom(bytes(input.phone_identity_priv));
      const singleDigest = crypto.createHash("sha256").update(rbase).digest();
      const wrongSig = crypto.sign("sha256", singleDigest, { key: priv, dsaEncoding: "ieee-p1363" });
      if (crypto.verify("sha256", rbase, { key: phonePub, dsaEncoding: "ieee-p1363" }, wrongSig)) {
        throw new Error("prehash semantics ambiguous: exactly-double-hash signature verified like single-hash");
      }
      // Confirmations.
      const confirmInput = crypto
        .createHash("sha256")
        .update(Buffer.from(LABELS.reconnect_confirm_input))
        .update(rbase)
        .update(bytes(outputs.phone_sig))
        .update(bytes(outputs.client_sig))
        .digest();
      if (hex(confirmInput) !== outputs.reconnect_confirm_input) throw new Error("reconnect confirm input mismatch");
      const confA = crypto.createHmac("sha256", derived(Buffer.from(LABELS.reconnect_ab_confirmation), 32)).update(confirmInput).digest();
      const confB = crypto.createHmac("sha256", derived(Buffer.from(LABELS.reconnect_ba_confirmation), 32)).update(confirmInput).digest();
      if (hex(confA) !== outputs.conf_a || hex(confB) !== outputs.conf_b) throw new Error("reconnect confirmations mismatch");
      verified.push("reconnect_confirm_input", "conf_a", "conf_b");
      return verified;
    });
    continue;
  }

  if (input.kind === "record_gcm" && fixture.expected?.rejected !== true) {
    check(id, () => {
      const key = bytes(input.key);
      const nonce = bytes(outputs.nonce);
      const header = bytes(outputs.header);
      if (header.length !== 23) throw new Error("header must be 23 bytes");
      const cipher = crypto.createCipheriv("aes-256-gcm", key, nonce);
      cipher.setAAD(header);
      const sealed = Buffer.concat([cipher.update(bytes(input.body)), cipher.final(), cipher.getAuthTag()]);
      const ciphertext = sealed.subarray(0, sealed.length - 16);
      const tag = sealed.subarray(sealed.length - 16);
      if (hex(ciphertext) !== outputs.ciphertext) throw new Error("GCM ciphertext mismatch (OpenSSL vs noble)");
      if (hex(tag) !== outputs.tag) throw new Error("GCM tag mismatch (OpenSSL vs noble)");
      if (hex(sealed) !== hex(bytes(outputs.record).subarray(23))) throw new Error("full record payload mismatch");
      // Round trip: decrypting the committed record reproduces the body.
      const decipher = crypto.createDecipheriv("aes-256-gcm", key, nonce);
      decipher.setAAD(header);
      decipher.setAuthTag(tag);
      const plain = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
      if (hex(plain) !== hex(bytes(input.body))) throw new Error("GCM round trip mismatch");
      return ["header", "nonce", "ciphertext", "tag", "record"];
    });
    continue;
  }
}

// Negative fixtures, driven against the candidate's own positive base.
const byId = new Map(candidate.fixtures.map((f) => [f.id, f]));
for (const fixture of candidate.fixtures) {
  const deviation = fixture.input?.deviation;
  if (!deviation || fixture.expected?.rejected !== true) continue;
  const base = byId.get(deviation.negates);
  if (!base) throw new Error(`${fixture.id}: base ${deviation.negates} missing`);

  const type = deviation.type;
  if (base.input.kind === "record_gcm") {
    check(fixture.id, () => {
      const mutated = bytes(recordDeviation(base.expected.outputs.record, type));
      const key = bytes(base.input.key);
      const nonce = bytes(base.expected.outputs.nonce);
      const header = mutated.subarray(0, 23);
      const ciphertext = mutated.subarray(23, mutated.length - 16);
      const tag = mutated.subarray(mutated.length - 16);
      const decipher = crypto.createDecipheriv("aes-256-gcm", key, nonce);
      decipher.setAAD(header);
      decipher.setAuthTag(tag);
      let failed = false;
      try {
        Buffer.concat([decipher.update(ciphertext), decipher.final()]);
      } catch {
        failed = true;
      }
      if (!failed) throw new Error("expected AEAD rejection but OpenSSL verified the mutated record");
      return [`aead_rejected:${type}`];
    });
  } else if (["high_s", "der_encoding", "truncated_sig"].includes(type) && base.input.kind === "reconnect") {
    check(fixture.id, () => {
      const outputs = base.expected.outputs;
      const rbase = bytes(outputs.rbase);
      const pub = pubKeyFrom(bytes(outputs.phone_identity_pub));
      const variant = bytes(deviation.value);
      let verified;
      try {
        verified = crypto.verify("sha256", rbase, { key: pub, dsaEncoding: "ieee-p1363" }, variant);
      } catch {
        verified = false; // OpenSSL throws on non-64-byte P1363 buffers
      }
      if (type === "truncated_sig") {
        if (verified) throw new Error("truncated P1363 unexpectedly verified");
        return ["truncated_sig_rejected_by_openssl"];
      }
      // high_s / der_encoding: the underlying (r, s) is mathematically valid —
      // OpenSSL (which does not enforce low-S and accepts DER) verifies it via
      // the matching encoding, proving the rejection is Veles's canonical-
      // encoding policy, not a broken signature.
      const dsaEncoding = type === "der_encoding" ? "der" : "ieee-p1363";
      if (!crypto.verify("sha256", rbase, { key: pub, dsaEncoding }, variant)) {
        throw new Error("premise broken: variant should be mathematically valid");
      }
      return [`premise_confirmed:${type}:openssl_verifies_variant`];
    });
  }
}

writeFileSync(REPORT, JSON.stringify(report, null, 2) + "\n");
const entries = Object.values(report.fixtures);
const ok = entries.filter((r) => r.status === "ok").length;
const failed = entries.filter((r) => r.status === "fail").length;
console.log(`openssl-check: ${ok} verified, ${failed} failed`);
process.exit(failed ? 1 : 0);
