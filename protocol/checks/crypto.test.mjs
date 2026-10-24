// Cryptographic known-answer tests for the Veles protocol contract (OTP-02
// Plan Task 5). Never imported by product code (OTP2-FIX-006).
//
// These tests do NOT generate expected values: every crypto fixture's expected
// bytes are committed in protocol/v1/fixtures/crypto.json and must survive
// (a) independent recomputation by checks/crypto.mjs, (b) the stdlib-affine
// P-256 oracle and (c) Node/OpenSSL cross-checks — the latter two invoked
// against the COMMITTED file, live, inside this suite. Missing or incorrect
// expected values fail; nothing is skipped (OTP2-FIX-004/005).
//
//   node --test protocol/checks/crypto.test.mjs

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

import { loadFixtures } from "./fixture.mjs";
import {
  LABELS,
  P256_N,
  POINT,
  REQUIRED_OUTPUTS,
  RFC9382_ANCHORS,
  RFC9382_M_COMPRESSED,
  RFC9382_N_COMPRESSED,
  buildSignatureVariants,
  bytesToHex,
  derivePositiveOutputs,
  fixtureContentDigest,
  fixturesArrayDigest,
  checkNegative,
  gcmDecrypt,
  recordDeviation,
  spake2Exchange,
  validateP1363LowS,
  validatePointUncompressed,
  verifyCryptoFixtures,
} from "./crypto.mjs";

const checksDir = dirname(fileURLToPath(import.meta.url));
const v1Root = join(checksDir, "..", "v1");
const repoRoot = join(checksDir, "..", "..");
const buildProtocol = join(repoRoot, "build", "protocol");
const committedFile = join(v1Root, "fixtures", "crypto.json");
const candidateFile = join(buildProtocol, "crypto-candidate.json");

// ---------- 1. normative label byte lengths (exact, asserted in the suite) ----------

const EXPECTED_LABEL_LENGTHS = {
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
};

test("every normative label is quoted byte-exactly and has its contract byte length", () => {
  assert.deepEqual(Object.keys(EXPECTED_LABEL_LENGTHS).length, Object.keys(LABELS).length);
  for (const [key, [text, length]] of Object.entries(EXPECTED_LABEL_LENGTHS)) {
    const engine = LABELS[key];
    const actual = Buffer.byteLength(text, "utf8");
    assert.equal(actual, length, `label "${text}" must be ${length} ASCII bytes`);
    assert.equal(engine[0], text, `engine label ${key} drifted from the committed quote`);
    assert.equal(engine[1], length, `engine length for ${key} drifted`);
  }
});

test("RFC 9382 P-256 generator points are quoted verbatim from the RFC", () => {
  assert.equal(
    RFC9382_M_COMPRESSED,
    "02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f",
  );
  assert.equal(
    RFC9382_N_COMPRESSED,
    "03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49",
  );
  // The subgroup order must equal the published P-256 order.
  assert.equal(P256_N, 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551n);
});

// ---------- 2. committed fixture set ----------

function loadCommitted() {
  return loadFixtures(v1Root);
}

function cryptoFixtures(fixtures) {
  return fixtures.filter((f) => f.category === "crypto");
}

test("committed crypto fixture set is present with positives and negatives", () => {
  const fixtures = loadCommitted();
  const crypto = cryptoFixtures(fixtures);
  assert.ok(crypto.length >= 20, `expected the Task 5 crypto fixture set, got ${crypto.length}`);
  const positives = crypto.filter((f) => f.expected.rejected !== true);
  const negatives = crypto.filter((f) => f.expected.rejected === true);
  assert.ok(positives.length >= 8, "positives (2 published anchors + 6 Veles vectors) must be present");
  assert.ok(negatives.length >= 10, "negatives (code/context/role/point/signature/record) must be present");
  const anchors = positives.filter((f) => f.input.kind === "rfc9382_anchor");
  assert.equal(anchors.length, 2, "both committed RFC 9382 P-256 anchors must be present");
});

test("committed RFC 9382 anchors quote the published vectors verbatim", () => {
  const fixtures = cryptoFixtures(loadCommitted());
  for (const anchor of RFC9382_ANCHORS) {
    const fixture = fixtures.find((f) => f.id === anchor.id);
    assert.ok(fixture, `${anchor.id} must exist in the committed fixture set`);
    assert.equal(fixture.expected.rejected, undefined, "anchors are positive fixtures");
    // The published constants are quoted in this file AND in checks/crypto.mjs;
    // the committed expected bytes must equal BOTH quotes.
    assert.deepEqual(fixture.expected.outputs, anchor.published, `${anchor.id} expected bytes must be the published vector verbatim`);
    assert.match(fixture.expected.source, /RFC 9382 Appendix B/, `${anchor.id} must cite its RFC source`);
    for (const key of Object.keys(anchor.published)) {
      assert.ok(fixture.expected.outputs[key].startsWith("0x"), `${anchor.id}.${key} must be 0x-prefixed hex`);
    }
  }
});

// ---------- 3. live independent cross-checks against the COMMITTED file ----------
// Cross-checks are MEMOIZED and invoked by whichever test needs them first —
// no test depends on another test having executed before it.

function oraclePath() {
  return join(v1Root, "..", "tools", "crosscheck-vectors", "oracle.py");
}
function opensslCheckPath() {
  return join(v1Root, "..", "tools", "crosscheck-vectors", "openssl-check.mjs");
}

let committedReportsDone = false;
let candidateReportsDone = false;

function runCommittedCrosschecks() {
  if (committedReportsDone) return;
  assert.ok(existsSync(oraclePath()), "oracle.py must exist");
  const oracleRun = spawnSync("python3", [oraclePath(), committedFile], { encoding: "utf8" });
  assert.equal(oracleRun.error, undefined, "python3 must be runnable (stdlib-only oracle)");
  assert.equal(oracleRun.status, 0, `committed oracle must pass; stderr=${oracleRun.stderr}`);
  assert.ok(existsSync(opensslCheckPath()), "openssl-check.mjs must exist");
  const opensslRun = spawnSync(process.execPath, [opensslCheckPath(), committedFile], { encoding: "utf8" });
  assert.equal(opensslRun.error, undefined);
  assert.equal(opensslRun.status, 0, `committed openssl-check must pass; stderr=${opensslRun.stderr}`);
  committedReportsDone = true;
}

function runCandidateCrosschecks() {
  if (candidateReportsDone) return;
  assert.ok(existsSync(candidateFile), "build/protocol/crypto-candidate.json must exist (run the generator first)");
  const oracleRun = spawnSync("python3", [oraclePath(), candidateFile], { encoding: "utf8" });
  assert.equal(oracleRun.status, 0, `candidate oracle must pass; stderr=${oracleRun.stderr}`);
  const opensslRun = spawnSync(process.execPath, [opensslCheckPath()], { encoding: "utf8" });
  assert.equal(opensslRun.status, 0, `candidate openssl-check must pass; stderr=${opensslRun.stderr}`);
  candidateReportsDone = true;
}

test("stdlib affine P-256 oracle independently verifies the committed expected bytes", () => {
  runCommittedCrosschecks();
  const report = JSON.parse(readFileSync(join(buildProtocol, "crosscheck-oracle-committed.json"), "utf8"));
  assert.equal(report.anchor_selfcheck, "ok", "oracle must reproduce the published RFC anchors from its own constants");
  assert.equal(report.tool, "crosscheck-vectors/oracle.py", "report must carry its tool identity");
  for (const [id, entry] of Object.entries(report.fixtures)) {
    assert.notEqual(entry.status, "fail", `${id}: ${entry.errors?.join("; ")}`);
  }
  assert.equal(report.input_sha256, createHash("sha256").update(readFileSync(committedFile)).digest("hex"),
    "the committed oracle report must attest the committed crypto.json bytes");
});

test("Node/OpenSSL cross-check independently verifies the committed expected bytes", () => {
  runCommittedCrosschecks();
  const report = JSON.parse(readFileSync(join(buildProtocol, "crosscheck-openssl-committed.json"), "utf8"));
  assert.equal(report.tool, "crosscheck-vectors/openssl-check.mjs", "report must carry its tool identity");
  for (const [id, entry] of Object.entries(report.fixtures)) {
    assert.notEqual(entry.status, "fail", `${id}: ${entry.errors?.join("; ")}`);
  }
  assert.equal(report.input_sha256, createHash("sha256").update(readFileSync(committedFile)).digest("hex"),
    "the committed openssl report must attest the committed crypto.json bytes");
});

test("cross-check reports are bound to the canonical content digest of every current fixture", () => {
  runCommittedCrosschecks();
  const fixtures = cryptoFixtures(loadCommitted());
  const oracleReport = JSON.parse(readFileSync(join(buildProtocol, "crosscheck-oracle-committed.json"), "utf8"));
  const opensslReport = JSON.parse(readFileSync(join(buildProtocol, "crosscheck-openssl-committed.json"), "utf8"));
  for (const fixture of fixtures) {
    for (const report of [oracleReport, opensslReport]) {
      const entry = report.fixtures?.[fixture.id];
      if (entry?.status !== "ok") continue; // delegated/skipped entries bind on verification
      assert.equal(
        report.fixture_sha256?.[fixture.id],
        fixtureContentDigest(fixture),
        `${report.tool} report digest must equal the CURRENT ${fixture.id} content digest`,
      );
    }
  }
});

test("verifyCryptoFixtures recomputes every committed fixture and reports full independent coverage", () => {
  runCommittedCrosschecks();
  const fixtures = loadCommitted();
  const crypto = cryptoFixtures(fixtures);
  const result = verifyCryptoFixtures(fixtures);
  assert.deepEqual(result.unverified_ids, [], `all crypto fixtures must be independently verified; unverified: ${result.unverified_ids.join(", ")}`);
  assert.deepEqual(
    [...result.verified_ids].sort(),
    crypto.map((f) => f.id).sort(),
    "verified_ids must cover exactly the committed crypto fixture set",
  );
});

test("engine recomputation matches committed outputs for every positive crypto fixture", () => {
  const crypto = cryptoFixtures(loadCommitted());
  for (const fixture of crypto.filter((f) => f.expected.rejected !== true)) {
    const recomputed = derivePositiveOutputs(fixture);
    for (const [name, committed] of Object.entries(fixture.expected.outputs)) {
      assert.equal(recomputed[name], committed, `${fixture.id}.${name} recomputed must equal committed`);
    }
  }
});

// ---------- 4. candidate ↔ committed determinism ----------

test("generated candidate is byte-equal to the committed fixtures (deterministic, never auto-overwritten)", () => {
  runCandidateCrosschecks();
  const candidate = JSON.parse(readFileSync(candidateFile, "utf8"));
  const committed = JSON.parse(readFileSync(committedFile, "utf8"));
  assert.deepEqual(candidate.fixtures, committed.fixtures,
    "committed expected values must be exactly the cross-checked candidate");
  const oracleReport = JSON.parse(readFileSync(join(buildProtocol, "crosscheck-oracle.json"), "utf8"));
  const opensslReport = JSON.parse(readFileSync(join(buildProtocol, "crosscheck-openssl.json"), "utf8"));
  for (const report of [oracleReport, opensslReport]) {
    assert.equal(report.fixtures_sha256, fixturesArrayDigest(committed.fixtures),
      "candidate report must bind the exact candidate fixture array");
    for (const [id, entry] of Object.entries(report.fixtures)) {
      if (entry.status !== "skipped") assert.equal(entry.status, "ok", `candidate report ${id}`);
    }
  }
  assert.equal(oracleReport.input_sha256, createHash("sha256").update(readFileSync(candidateFile)).digest("hex"));
});

// ---------- 5. mutation sensitivity (fail, not skip) ----------

function mutateOutputHex(hex, offsetFromLeft = 3) {
  // flip one nibble of a "0x..." string at the given char offset
  const chars = [...hex];
  const flip = (c) => (c === "0" ? "1" : c === "f" ? "e" : c === "a" ? "b" : "f");
  chars[offsetFromLeft] = flip(chars[offsetFromLeft]);
  return chars.join("");
}

test("mutating a fixture input (the x secret) makes verification fail", () => {
  const fixture = structuredClone(cryptoFixtures(loadCommitted()).find((f) => f.id === "veles-pairing-code-000000"));
  const chars = [...fixture.input.x];
  chars[10] = chars[10] === "0" ? "2" : "0";
  fixture.input.x = chars.join("");
  assert.throws(() => verifyCryptoFixtures([fixture]), /does not match committed/);
});

test("mutating an identity label byte makes verification fail", () => {
  const fixture = structuredClone(cryptoFixtures(loadCommitted()).find((f) => f.id === "rfc9382-p256-sha256-hkdf-hmac-server-client"));
  assert.equal(fixture.input.id_a, "server");
  fixture.input.id_a = "serveR"; // case change alters TT
  assert.throws(() => verifyCryptoFixtures([fixture]), /does not match committed/);
});

test("mutating a committed transcript byte makes verification fail", () => {
  const fixture = structuredClone(cryptoFixtures(loadCommitted()).find((f) => f.id === "veles-pairing-code-000000"));
  fixture.expected.outputs.tt = mutateOutputHex(fixture.expected.outputs.tt, 100);
  assert.throws(() => verifyCryptoFixtures([fixture]), /tt .+ does not match committed/);
});

test("mutating a committed expected output (c_a) makes verification fail", () => {
  const fixture = structuredClone(cryptoFixtures(loadCommitted()).find((f) => f.id === "veles-pairing-code-000000"));
  fixture.expected.outputs.c_a = mutateOutputHex(fixture.expected.outputs.c_a);
  assert.throws(() => verifyCryptoFixtures([fixture]), /c_a .+ does not match committed/);
});

test("negatives are not vacuous: wrong_code with the CORRECT code fails the distinguishing check", () => {
  const fixtures = cryptoFixtures(loadCommitted());
  const positive = fixtures.find((f) => f.id === "veles-pairing-code-000000");
  const negative = structuredClone(fixtures.find((f) => f.id === "veles-neg-wrong-code"));
  negative.input.deviation.value = positive.input.code; // deviation becomes a no-op
  assert.throws(() => verifyCryptoFixtures([positive, negative]), /expected to differ but matched: w\b/);
});

test("committed-level mutation: a flipped expected byte fails the independent oracle", () => {
  const committed = JSON.parse(readFileSync(committedFile, "utf8"));
  committed.fixtures.find((f) => f.id === "veles-pairing-code-000000").expected.outputs.c_a =
    mutateOutputHex(committed.fixtures.find((f) => f.id === "veles-pairing-code-000000").expected.outputs.c_a);
  const dir = mkdtempSync(join(tmpdir(), "veles-crypto-mutation-"));
  const mutatedFile = join(dir, "crypto-mutated.json");
  writeFileSync(mutatedFile, JSON.stringify(committed));
  const run = spawnSync("python3", [oraclePath(), mutatedFile, "-o", join(dir, "report.json")], { encoding: "utf8" });
  assert.notEqual(run.status, 0, "the oracle MUST reject mutated committed expected bytes");
  const mutated2 = JSON.parse(readFileSync(committedFile, "utf8"));
  mutated2.fixtures.find((f) => f.id === "veles-record-reconnect-confirm").expected.outputs.tag =
    mutateOutputHex(mutated2.fixtures.find((f) => f.id === "veles-record-reconnect-confirm").expected.outputs.tag);
  const mutatedFile2 = join(dir, "crypto-mutated-tag.json");
  writeFileSync(mutatedFile2, JSON.stringify(mutated2));
  const run2 = spawnSync(process.execPath, [opensslCheckPath(), mutatedFile2, join(dir, "report2.json")], { encoding: "utf8" });
  assert.notEqual(run2.status, 0, "the OpenSSL cross-check MUST reject a mutated committed tag");
});

// ---------- 6. strict engine boundaries ----------

test("strict point validation rejects off-curve, identity and compressed encodings", () => {
  const crypto = cryptoFixtures(loadCommitted());
  const pairing = crypto.find((f) => f.id === "veles-pairing-code-000000");
  assert.doesNotThrow(() => validatePointUncompressed(pairing.expected.outputs.p_b));
  // The SEC1 octet 0x00 IS the point-at-infinity encoding: rejected AS the
  // identity (OTP2-CRY-005a), not as a width error.
  assert.throws(() => validatePointUncompressed("0x00"), /identity element \(SEC1 point-at-infinity octet 0x00\)/);
  // A 65-byte all-zero-coordinate shape is NOT the encoded identity: x=y=0 is
  // simply off the curve.
  assert.throws(() => validatePointUncompressed("0x" + "04" + "00".repeat(64)), /does not decode as an on-curve/);
  // The published M point in its 33-byte compressed form: wrong width.
  assert.throws(() => validatePointUncompressed("0x" + RFC9382_M_COMPRESSED), /65 bytes/);
  verifyCryptoFixtures; // keep the verifier in scope for mutation tests below
  for (const neg of crypto.filter((f) => ["off_curve_point", "identity_point", "zero_point", "compressed_point"].includes(f.input.deviation?.type))) {
    assert.throws(() => validatePointUncompressed(neg.input.deviation.value), Error, `${neg.id} must reject`);
  }
});

test("strict P1363 low-S validation rejects high-S, DER and truncated encodings", () => {
  const crypto = cryptoFixtures(loadCommitted());
  const reconnect = crypto.find((f) => f.id === "veles-reconnect-authentication");
  assert.doesNotThrow(() => validateP1363LowS(reconnect.expected.outputs.phone_sig));
  const variants = buildSignatureVariants(reconnect.expected.outputs.phone_sig);
  assert.throws(() => validateP1363LowS(variants.high_s), /high-S/);
  assert.throws(() => validateP1363LowS(variants.der_encoding), /64 bytes/);
  assert.throws(() => validateP1363LowS(variants.truncated_sig), /64 bytes/);
  // The high-S mirror still satisfies s < n but exceeds the half-order.
  const nHalf = P256_N >> 1n;
  assert.ok(BigInt("0x" + variants.high_s.slice(2 + 64, 2 + 128)) > nHalf);
});

test("AEAD boundary: the committed record verifies and its mutated forms do not", () => {
  const crypto = cryptoFixtures(loadCommitted());
  const record = crypto.find((f) => f.id === "veles-record-reconnect-confirm");
  const key = Buffer.from(record.input.key.slice(2), "hex");
  const nonce = Buffer.from(record.expected.outputs.nonce.slice(2), "hex");
  const header = Buffer.from(record.expected.outputs.header.slice(2), "hex");
  const full = Buffer.from(record.expected.outputs.record.slice(2), "hex");
  const plain = gcmDecrypt(key, nonce, header, full.subarray(23, full.length - 16), full.subarray(full.length - 16));
  assert.equal(bytesToHex(plain), record.input.body);
  for (const type of ["bad_tag", "mutated_session", "mutated_direction"]) {
    const mutated = Buffer.from(recordDeviation(record.expected.outputs.record, type).slice(2), "hex");
    assert.throws(
      () => gcmDecrypt(
        key,
        Buffer.from(record.expected.outputs.nonce.slice(2), "hex"),
        mutated.subarray(0, 23),
        mutated.subarray(23, mutated.length - 16),
        mutated.subarray(mutated.length - 16),
      ),
      Error,
      `record mutation ${type} must fail AEAD verification`,
    );
  }
});

test("every negative crypto fixture references a positive base and names its rejection stage", () => {
  const crypto = cryptoFixtures(loadCommitted());
  const byId = new Map(crypto.map((f) => [f.id, f]));
  const positives = new Set(crypto.filter((f) => f.expected.rejected !== true).map((f) => f.id));
  for (const neg of crypto.filter((f) => f.expected.rejected === true)) {
    const base = byId.get(neg.input.deviation?.negates);
    assert.ok(base, `${neg.id}: deviation.negates must reference an existing fixture`);
    assert.ok(positives.has(neg.input.deviation.negates), `${neg.id}: base must be a positive fixture`);
    assert.ok(neg.expected.stage, `${neg.id}: must name its rejection stage`);
    assert.equal(neg.expected.no_state_mutation, true, `${neg.id}: must assert absence of state mutation`);
  }
});
// ---------- 7. controller-review hardening (round 1) ----------

test("committed output sets are exactly the per-kind required set: missing keys fail", () => {
  const fixture = structuredClone(cryptoFixtures(loadCommitted()).find((f) => f.id === "veles-pairing-code-000000"));
  assert.deepEqual(Object.keys(fixture.expected.outputs).sort(), [...REQUIRED_OUTPUTS.spake2_pairing].sort());
  delete fixture.expected.outputs.c_b; // silently dropping an expected key must fail
  assert.throws(() => verifyCryptoFixtures([fixture]), /missing required keys: c_b/);
});

test("committed output sets are exactly the per-kind required set: unknown keys fail", () => {
  const fixture = structuredClone(cryptoFixtures(loadCommitted()).find((f) => f.id === "veles-pairing-code-000000"));
  fixture.expected.outputs.attacker_controlled = fixture.expected.outputs.c_a;
  assert.throws(() => verifyCryptoFixtures([fixture]), /unknown keys: attacker_controlled/);
});

test("stale or tampered independent reports cannot approve fixtures", () => {
  // Fresh reports in an isolated dir, produced by the real tools.
  const dir = mkdtempSync(join(tmpdir(), "veles-crypto-reports-"));
  const oracleRun = spawnSync("python3", [oraclePath(), committedFile, "-o", join(dir, "crosscheck-oracle-committed.json")], { encoding: "utf8" });
  assert.equal(oracleRun.status, 0, oracleRun.stderr);
  const opensslRun = spawnSync(process.execPath, [opensslCheckPath(), committedFile, join(dir, "crosscheck-openssl-committed.json")], { encoding: "utf8" });
  assert.equal(opensslRun.status, 0, opensslRun.stderr);
  const fixtures = loadCommitted();
  const fresh = verifyCryptoFixtures(fixtures, { reportsDir: dir });
  assert.deepEqual(fresh.unverified_ids, [], "freshly produced reports must verify every fixture");

  // Stale binding: a single per-fixture digest replaced by an older/unrelated
  // digest makes exactly that fixture unverifiable.
  const stale = JSON.parse(readFileSync(join(dir, "crosscheck-oracle-committed.json"), "utf8"));
  stale.fixture_sha256["veles-pairing-code-000000"] = "0".repeat(64);
  writeFileSync(join(dir, "crosscheck-oracle-committed.json"), JSON.stringify(stale));
  const res = verifyCryptoFixtures(fixtures, { reportsDir: dir });
  assert.ok(res.unverified_ids.includes("veles-pairing-code-000000"), "stale digest must unverify the changed fixture");
  assert.ok(res.verified_ids.includes("veles-neg-wrong-code"), "other fixtures stay verified");

  // Missing tool metadata: a report from any other writer is not verification.
  const untoolled = JSON.parse(readFileSync(join(dir, "crosscheck-openssl-committed.json"), "utf8"));
  untoolled.tool = "crosscheck-vectors/not-an-independent-implementation";
  writeFileSync(join(dir, "crosscheck-openssl-committed.json"), JSON.stringify(untoolled));
  const res2 = verifyCryptoFixtures(fixtures, { reportsDir: dir });
  // Only the openssl-exclusive fixtures lose coverage (the oracle still
  // verifies everything it covers, and either-of rules keep shared negatives).
  for (const id of ["veles-record-reconnect-confirm", "veles-record-push-event", "veles-neg-record-tag", "veles-neg-record-session", "veles-neg-record-direction", "veles-neg-record-sequence"]) {
    assert.ok(res2.unverified_ids.includes(id), `${id} must be unverifiable without openssl tool identity`);
  }
  assert.ok(res2.verified_ids.includes("veles-neg-sig-high-s"), "either-tool negatives stay verified via the oracle");
});

test("declared rejection stage must match the derived actual stage", () => {
  const crypto = cryptoFixtures(loadCommitted());
  const positive = crypto.find((f) => f.id === "veles-pairing-code-000000");
  const negative = structuredClone(crypto.find((f) => f.id === "veles-neg-wrong-code"));
  negative.expected.stage = "context"; // wrong: a wrong-code failure is a code failure
  assert.throws(
    () => verifyCryptoFixtures([positive, negative]),
    /actual rejection stage is "code" but the fixture declares "context"/,
  );
  // Strict-validation derivatives: point_format misdeclared as point fails.
  const pointNeg = structuredClone(crypto.find((f) => f.id === "veles-neg-point-compressed"));
  pointNeg.expected.stage = "point";
  assert.throws(
    () => verifyCryptoFixtures([positive, pointNeg]),
    /actual rejection stage is "point_format" but the fixture declares "point"/,
  );
});

test("zero-scalar edge policy preserves RFC sampling and distinguishes generated from peer-derived degeneracy", () => {
  // RFC 9382 draws x, y uniformly from [0, p) and Veles maps every code to a
  // w in [0, n) that is processed normally (OTP2-CRY-004b): scalar 0 must be
  // algebraically valid. noble's multiply rejects 0, so the engine substitutes
  // the identity element directly (mulScalar).
  const crypto = cryptoFixtures(loadCommitted());
  const pairing = crypto.find((f) => f.id === "veles-pairing-code-000000");
  const idA = Buffer.from(LABELS.identity_a[0]);
  const idB = Buffer.from(LABELS.identity_b[0]);
  const x = Buffer.from(pairing.input.x.slice(2), "hex");
  const y = Buffer.from(pairing.input.y.slice(2), "hex");
  const aad = new Uint8Array(0);
  const w0 = spake2Exchange({ idA, idB, wBytes: Buffer.alloc(32), x, y, aad });
  const x0 = Buffer.alloc(32);
  const pAExpected = POINT.BASE.multiply(BigInt("0x" + Buffer.from(x).toString("hex")));
  assert.ok(w0.p_a.equals(pAExpected), "w = 0 must yield pA = x*P exactly");
  assert.ok(!w0.k.equals(POINT.ZERO), "w = 0 must not collapse the shared element");
  // A generated identity pA/pB is retried before that point is emitted. A
  // peer-derived K cannot be known until pB arrived; that valid-pB attempt has
  // already been consumed and aborts before cA, not "before any exposure".
  assert.throws(
    () => spake2Exchange({ idA, idB, wBytes: Buffer.alloc(32), x: x0, y, aad }),
    /degenerate generated point|degenerate shared group element after peer input/,
  );
});

test("enrollment vectors bind the phone pending-record prepare confirmation before commit", () => {
  const enrollment = cryptoFixtures(loadCommitted()).find((f) => f.id === "veles-enrollment-schedule");
  assert.ok(enrollment.expected.outputs.act_prepare_mac, "prepare MAC must be committed as a deterministic vector");
});

test("a shared group element collapsing to the identity aborts generically before any KDF output or confirmation (OTP2-CRY-003a)", () => {
  // The committed negative keeps the positive base's code AND both own
  // scalars: only the peer point is forced (pB = w·N phone view, pA = w·M
  // client view), so the abort is the identity-element rule — never a wrong
  // password or malformed point. The forced points are COMMITTED bytes
  // (re-review I3): the check parses, validates and uses them.
  const crypto = cryptoFixtures(loadCommitted());
  const negative = crypto.find((f) => f.id === "veles-neg-shared-secret-identity");
  assert.ok(negative, "the committed K-identity negative must exist");
  const positive = crypto.find((f) => f.id === negative.input.deviation.negates);
  assert.equal(positive.input.code, negative.input.code, "same pairing code");
  assert.equal(positive.input.x, negative.input.x, "same phone scalar x");
  assert.equal(positive.input.y, negative.input.y, "same client scalar y");
  assert.equal(positive.input.pairing_id, negative.input.pairing_id, "same pairing_id");
  // The committed forced points are real, valid, and REAL deviations.
  const dev = negative.input.deviation;
  assert.match(dev.value ?? "", /^0x[0-9a-f]+$/, "the forced phone-view peer point must be committed bytes");
  assert.match(dev.value2 ?? "", /^0x[0-9a-f]+$/, "the forced client-view peer point must be committed bytes");
  assert.notEqual(dev.value, positive.expected.outputs.p_b, "pB := w·N must differ from the honest pB");
  assert.notEqual(dev.value2, positive.expected.outputs.p_a, "pA := w·M must differ from the honest pA");
  assert.ok(validatePointUncompressed(dev.value));
  assert.ok(validatePointUncompressed(dev.value2));
  const byId = new Map(crypto.map((f) => [f.id, f]));
  const proof = checkNegative(negative, byId);
  assert.equal(proof.stage, "shared_secret", "the deviation names its own stage");
  assert.equal(proof.proof, "strict_validation", "the abort is proven by the engines' own strict rule");
  assert.match(proof.detail, /OTP2-CRY-003a/);
  assert.match(proof.detail, /attempt/i, "the non-refundable attempt accounting is part of the rule");
});

test("K-identity mutation regression: an honest peer point must NOT trip the abort (OTP2-CRY-003a)", () => {
  // Replacing the committed forced peer point with the positive base's own
  // honest pB/pA (a valid NORMAL peer point) must FAIL the deviation: the
  // exchange completes (no identity collapse), so the check rejects the
  // mutated fixture instead of re-deriving the abort.
  const crypto = cryptoFixtures(loadCommitted());
  const negative = structuredClone(crypto.find((f) => f.id === "veles-neg-shared-secret-identity"));
  const positive = crypto.find((f) => f.id === negative.input.deviation.negates);
  negative.input.deviation.value = positive.expected.outputs.p_b;
  negative.input.deviation.value2 = positive.expected.outputs.p_a;
  const byId = new Map(crypto.map((f) => [f.id, f]));
  assert.throws(
    () => checkNegative(negative, byId),
    /must differ from the positive base/,
    "an honest peer point completes the exchange (no identity collapse) — " +
      "the check refuses the mutation as a no-op instead of re-deriving the abort",
  );
});
