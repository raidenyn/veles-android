// OTP-02 Plan Task 2 — canonical fixture and binary validation contract suite.
// Test-only; never imported by product code. Run: node --test protocol/checks/contract.test.mjs
//
// Every expectation below is derived from docs/protocol/veles-v1.md (normative)
// and protocol/v1/{limits,manifest}.json. The committed fixtures under
// protocol/v1/fixtures/ are the shared truth; this suite is their interpreter.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  readFileSync,
  mkdirSync,
  mkdtempSync,
  copyFileSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { loadFixtures, decodeHex } from "./fixture.mjs";
import { validateBinary, validateFragmented } from "./binary.mjs";
import { buildEvidenceInventory } from "./evidence.mjs";
import {
  fragmentRecord,
  mergeFixtures,
  payloadBudget,
  sampleRecord,
} from "../tools/generate-fragmentation-vectors.mjs";

const checksDir = dirname(fileURLToPath(import.meta.url));
const v1Root = join(checksDir, "..", "v1");

function loadJson(name, dir = v1Root) {
  return JSON.parse(readFileSync(join(dir, name), "utf8"));
}

test("evidence inventory covers all requirements and rejects dangling anchors and false acceptance", () => {
  const root = mkdtempSync(join(tmpdir(), "veles-evidence-"));
  try {
    const v1 = join(root, "protocol", "v1");
    mkdirSync(join(v1, "fixtures"), { recursive: true });
    mkdirSync(join(root, "docs", "protocol"), { recursive: true });
    copyFileSync(join(v1Root, "manifest.json"), join(v1, "manifest.json"));
    copyFileSync(join(checksDir, "..", "..", "docs", "protocol", "veles-v1.md"), join(root, "docs", "protocol", "veles-v1.md"));
    const normal = buildEvidenceInventory(v1);
    assert.equal(normal.entries.length, 156);
    assert.ok(normal.documentary_only_count > 0, "documentary-only requirements must be explicit, not papered over");
    const manifest = loadJson("manifest.json", v1);
    manifest.entries[0].document_anchor = "veles-v1.md#sec-missing";
    writeFileSync(join(v1, "manifest.json"), JSON.stringify(manifest));
    assert.throws(() => buildEvidenceInventory(v1), /dangling document anchor/);
    manifest.entries[0].document_anchor = "veles-v1.md#sec-conventions";
    manifest.entries[0].status = "accepted";
    writeFileSync(join(v1, "manifest.json"), JSON.stringify(manifest));
    assert.throws(() => buildEvidenceInventory(v1), /false accepted status/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

let layoutsCache = null;
function layouts() {
  if (!layoutsCache) layoutsCache = loadJson("binary-layouts.json");
  return layoutsCache;
}

let limitsCache = null;
function limits() {
  if (!limitsCache) limitsCache = loadJson("limits.json");
  return limitsCache;
}

// ---------- network-order (big-endian) hand-encoding helpers ----------

function u16be(n) {
  return n.toString(16).padStart(4, "0");
}

function toHex(u8) {
  return Array.from(u8, (b) => b.toString(16).padStart(2, "0")).join("");
}

function mutateOneByte(hex) {
  const digits = "0123456789abcdef";
  const i = hex.length - 1;
  return hex.slice(0, i) + digits[(digits.indexOf(hex[i]) + 1) % 16];
}

function assertAccept(res) {
  assert.equal(res.accepted, true, `expected accept, got: ${res.error}`);
  assert.equal(res.error, null);
  assert.ok(res.fields && typeof res.fields === "object");
}

function assertRejectStage(res, stage) {
  assert.equal(res.accepted, false);
  assert.equal(res.fields, null, "rejected input must not report fields");
  assert.equal(typeof res.error, "string");
  assert.ok(
    res.error.includes(`stage=${stage}`),
    `expected stage=${stage}, error was: ${res.error}`,
  );
}

// ---------- shared synthetic byte values (binary format stage only;
// ---------- cryptographic validity is Task 5 and is not claimed here) ----------

const ID_A = "000102030405060708090a0b0c0d0e0f";
const ID_B = "fedcba98765432100011223344556677";
const NONCE_HI = "ff00ff00ff00ff00ff00ff00ff00ff00";
const POINT_OK = "04" + "11".repeat(32) + "22".repeat(32);
const POINT_COMPRESSED = "03" + "11".repeat(32) + "22".repeat(32);
const SIG64 = "aa55".repeat(32);
const SIG63 = "aa55".repeat(31) + "aa";
const CONFIRM32 = "c0".repeat(32);
const TAG16 = "aa".repeat(16);
const PREFIX8 = "0e1d2c3b4a59687f";
const FP32 = "f0".repeat(32);
const LABEL32 = "61".repeat(32);
const P = "0x";

// field-value convention (fixture field objects): 0x-prefixed lowercase hex for
// byte/enum fields, canonical unsigned decimal strings for numeric fields,
// plain strings for text fields — see binary-layouts.json value_rules.

// Canonical pairing_request frame: type 0x01, payload_len 21,
// client_nonce, protocol_version 1, caps_len 1, caps [0x01]
const SAMPLE_FRAME_BYTES = "01" + u16be(21) + ID_A + u16be(1) + u16be(1) + "01";
const SAMPLE_FIELDS = {
  client_nonce: P + ID_A,
  protocol_version: "1",
  client_caps_len: "1",
  client_caps: ["0x01"],
};

function sampleFixture(overrides = {}) {
  return {
    id: "temp-sample-pairing-request",
    fixture_version: 1,
    category: "encoding",
    requirement_ids: ["OTP2-ENC-010a"],
    input: { layout: "pairing_request", bytes: SAMPLE_FRAME_BYTES },
    expected: { accepted: true, fields: SAMPLE_FIELDS },
    ...structuredClone(overrides),
  };
}

// Minimal valid protected record: heartbeat, client→phone, seq 0, empty ciphertext
const HEARTBEAT_RECORD_BYTES =
  "01" + "01" + "01" + "0002" + "00".repeat(8) + "0000" + PREFIX8 + TAG16;
const HEARTBEAT_RECORD_FIELDS = {
  protocol_version: "1",
  schema_version: "1",
  direction: "0x01",
  record_type: "0x0002",
  sequence_number: "0",
  ciphertext_length: "0",
  session_id_prefix: P + PREFIX8,
};

// push_event record with an all-ones u64 sequence number (wire u64 maximum)
const MAX_SEQ_RECORD_BYTES =
  "01" + "01" + "02" + "0203" + "ff".repeat(8) + "0020" + PREFIX8 + "77".repeat(32) + TAG16;
const MAX_SEQ_RECORD_FIELDS = {
  protocol_version: "1",
  schema_version: "1",
  direction: "0x02",
  record_type: "0x0203",
  sequence_number: "18446744073709551615",
  ciphertext_length: "32",
  session_id_prefix: P + PREFIX8,
};

// enroll_identity body: phone record, empty label (132-byte minimum)
const IDENTITY_PHONE_MIN_BYTES = ID_A + "0000" + "00" + POINT_OK + FP32 + NONCE_HI;
const IDENTITY_PHONE_MIN_FIELDS = {
  installation_id: P + ID_A,
  label_len: "0",
  label: "",
  device_type: "0x00",
  identity_pubkey: P + POINT_OK,
  fingerprint: P + FP32,
  nonce: P + NONCE_HI,
};

function tempTree(files) {
  const dir = mkdtempSync(join(tmpdir(), "veles-fx-"));
  for (const name of [
    "manifest.json",
    "limits.json",
    "fixture.schema.json",
    "binary-layouts.json",
  ]) {
    copyFileSync(join(v1Root, name), join(dir, name));
  }
  mkdirSync(join(dir, "fixtures"));
  for (const [name, body] of Object.entries(files)) {
    const value = Array.isArray(body)
      ? { version: 1, schema: "veles.protocol.fixtures", fixtures: body }
      : body;
    writeFileSync(join(dir, name), JSON.stringify(value, null, 2) + "\n");
  }
  return dir;
}

function withTempTree(files, fn) {
  const dir = tempTree(files);
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

// ---------- decodeHex ----------

test("decodeHex accepts lowercase even-length hex, including empty", () => {
  assert.deepEqual(Array.from(decodeHex("")), []);
  assert.deepEqual(Array.from(decodeHex("00ff0a")), [0x00, 0xff, 0x0a]);
});

test("decodeHex rejects uppercase hex", () => {
  assert.throws(() => decodeHex("AB"), /malformed hex/);
});

test("decodeHex rejects odd-length hex", () => {
  assert.throws(() => decodeHex("abc"), /malformed hex/);
});

test("decodeHex rejects non-hex characters", () => {
  assert.throws(() => decodeHex("zz"), /malformed hex/);
});

test("decodeHex rejects whitespace, separators, and 0x prefixes", () => {
  assert.throws(() => decodeHex("00 ff"), /malformed hex/);
  assert.throws(() => decodeHex("0x00ff"), /malformed hex/);
});

// ---------- validateBinary: layout resolution and frame envelope ----------

test("validateBinary rejects an unknown layout id", () => {
  const res = validateBinary("definitely_not_a_layout", new Uint8Array(0), layouts());
  assert.equal(res.accepted, false);
  assert.equal(res.fields, null);
  assert.ok(res.error.includes("definitely_not_a_layout"));
});

test("validateBinary rejects non-byte input", () => {
  const res = validateBinary("pairing_request", "not bytes", layouts());
  assert.equal(res.accepted, false);
  assert.ok(/Uint8Array/.test(res.error));
});

test("canonical pairing_request decodes to exact fields (DataView/BigInt, network order)", () => {
  const res = validateBinary("pairing_request", decodeHex(SAMPLE_FRAME_BYTES), layouts());
  assertAccept(res);
  assert.deepEqual(res.fields, SAMPLE_FIELDS);
});

test("validateBinary accepts a plain Uint8Array (not only Buffer)", () => {
  const res = validateBinary("preauth_reject", Uint8Array.from([0x0f, 0x00, 0x00]), layouts());
  assertAccept(res);
  assert.deepEqual(res.fields, {});
});

test("bytes after the declared payload length reject with stage trailing", () => {
  const res = validateBinary("pairing_request", decodeHex(SAMPLE_FRAME_BYTES + "aa"), layouts());
  assertRejectStage(res, "trailing");
});

test("payload shorter than declared payload_len rejects with stage length", () => {
  const truncated = SAMPLE_FRAME_BYTES.slice(0, SAMPLE_FRAME_BYTES.length - 4);
  const res = validateBinary("pairing_request", decodeHex(truncated), layouts());
  assertRejectStage(res, "length");
});

test("oversized frame rejects at framing before allocation", () => {
  const big = "0f" + "fffe" + "00".repeat(4094);
  assert.equal(big.length / 2, 4097);
  const res = validateBinary("preauth_reject", decodeHex(big), layouts());
  assertRejectStage(res, "framing");
  assert.ok(res.error.length < 300, "bounds errors must be informative, not a byte dump");
});

test("unknown frame type rejects with stage framing", () => {
  const bytes = "08" + u16be(21) + "00".repeat(21);
  const res = validateBinary("pairing_request", decodeHex(bytes), layouts());
  assertRejectStage(res, "framing");
});

// ---------- capability set rules ----------

test("little-endian caps_len prefix rejects with stage length (wrong endian lengths)", () => {
  const bytes = "01" + u16be(21) + ID_A + u16be(1) + "0100" + "01";
  const res = validateBinary("pairing_request", decodeHex(bytes), layouts());
  assertRejectStage(res, "length");
});

test("little-endian label_len prefix rejects with stage length", () => {
  const bytes = "00".repeat(16) + "0100";
  const res = validateBinary("enroll_identity", decodeHex(bytes), layouts());
  assertRejectStage(res, "length");
});

test("caps: largest valid set accepted exactly; zero/unknown/unsorted/duplicate rejected", () => {
  // The largest representable valid set is the 3 known values; the 16-entry
  // count ceiling is unreachable positively because any 4th entry must be
  // unknown (enum) or duplicate.
  const ok = "01" + u16be(23) + ID_A + u16be(1) + u16be(3) + "010203";
  const res = validateBinary("pairing_request", decodeHex(ok), layouts());
  assertAccept(res);
  assert.deepEqual(res.fields, {
    client_nonce: P + ID_A,
    protocol_version: "1",
    client_caps_len: "3",
    client_caps: ["0x01", "0x02", "0x03"],
  });

  const rejectCases = [
    ["caps-zero", "01" + u16be(20) + ID_A + u16be(1) + u16be(0), "length", false],
    ["caps-unknown", "01" + u16be(24) + ID_A + u16be(1) + u16be(4) + "0102037f", "enum", true],
    ["caps-unsorted", "01" + u16be(22) + ID_A + u16be(1) + u16be(2) + "0201", "enum", true],
    ["caps-duplicate", "01" + u16be(22) + ID_A + u16be(1) + u16be(2) + "0101", "enum", true],
  ];
  for (const [name, bytes, stage, mentionsCaps] of rejectCases) {
    const res2 = validateBinary("pairing_request", decodeHex(bytes), layouts());
    assertRejectStage(res2, stage);
    if (mentionsCaps) assert.ok(/caps/.test(res2.error), name);
  }
});

// ---------- validateBinary: protected record envelope ----------

test("minimal heartbeat record decodes to exact header fields (ciphertext/tag opaque)", () => {
  const res = validateBinary("protected_record", decodeHex(HEARTBEAT_RECORD_BYTES), layouts());
  assertAccept(res);
  assert.deepEqual(res.fields, HEARTBEAT_RECORD_FIELDS);
});

test('wire u64 sequence "18446744073709551615" is preserved as a canonical decimal string', () => {
  const res = validateBinary("protected_record", decodeHex(MAX_SEQ_RECORD_BYTES), layouts());
  assertAccept(res);
  assert.equal(res.fields.sequence_number, "18446744073709551615");
  assert.deepEqual(res.fields, MAX_SEQ_RECORD_FIELDS);
});

test("record type 0x0200 (client→phone only) with direction 0x02 rejects (direction)", () => {
  const bytes = "01" + "01" + "02" + "0200" + "00".repeat(8) + "0000" + PREFIX8 + TAG16;
  const res = validateBinary("protected_record", decodeHex(bytes), layouts());
  assertRejectStage(res, "direction");
});

test("enroll_identity body minimum decodes exactly with device_type 0x00", () => {
  const res = validateBinary("enroll_identity", decodeHex(IDENTITY_PHONE_MIN_BYTES), layouts());
  assertAccept(res);
  assert.equal(IDENTITY_PHONE_MIN_BYTES.length / 2, 132);
  assert.deepEqual(res.fields, IDENTITY_PHONE_MIN_FIELDS);
});

// ---------- loadFixtures: schema, ids, requirements, canonical u64 ----------

test("loadFixtures loads the committed tree (all categories, schema-validated)", () => {
  const fixtures = loadFixtures(v1Root);
  assert.ok(Array.isArray(fixtures) && fixtures.length >= 50);
  const categories = new Set(fixtures.map((f) => f.category));
  assert.ok(categories.has("encoding"));
  assert.ok(categories.has("fragmentation"));
  const ids = fixtures.map((f) => f.id);
  assert.equal(new Set(ids).size, ids.length, "committed fixture ids must be unique");
});

test("fixture field value 18446744073709551616 is rejected (u64 overflow)", () => {
  const bad = sampleFixture({
    id: "temp-u64-overflow",
    input: { layout: "protected_record", bytes: MAX_SEQ_RECORD_BYTES },
    expected: {
      accepted: true,
      fields: { ...MAX_SEQ_RECORD_FIELDS, sequence_number: "18446744073709551616" },
    },
  });
  withTempTree({ "fixtures/bad.json": [bad] }, (dir) => {
    assert.throws(() => loadFixtures(dir), /18446744073709551616/);
  });
});

test("fixture fields preserve the canonical u64 max value 18446744073709551615", () => {
  const ok = sampleFixture({
    id: "temp-u64-max-ok",
    input: { layout: "protected_record", bytes: MAX_SEQ_RECORD_BYTES },
    expected: { accepted: true, fields: MAX_SEQ_RECORD_FIELDS },
  });
  withTempTree({ "fixtures/ok.json": [ok] }, (dir) => {
    const loaded = loadFixtures(dir);
    assert.equal(loaded[0].expected.fields.sequence_number, "18446744073709551615");
  });
});

test("non-canonical or non-decimal strings in numeric field classes are rejected", () => {
  for (const value of ["01", "0100", "-5", "1.5", "1e4", "+1", " 1", "18446744073709551616"]) {
    const bad = sampleFixture({
      id: `temp-bad-decimal-${value.replace(/[^a-z0-9]/gi, "x")}`,
      input: { layout: "protected_record", bytes: MAX_SEQ_RECORD_BYTES },
      expected: {
        accepted: true,
        fields: { ...MAX_SEQ_RECORD_FIELDS, protocol_version: value },
      },
    });
    withTempTree({ "fixtures/bad.json": [bad] }, (dir) => {
      assert.throws(
        () => loadFixtures(dir),
        new RegExp("protocol_version|schema"),
        `value: ${JSON.stringify(value)}`,
      );
    });
  }
});

test("duplicate fixture ids in one file are rejected", () => {
  withTempTree({ "fixtures/dup.json": [sampleFixture(), sampleFixture()] }, (dir) => {
    assert.throws(() => loadFixtures(dir), /duplicate_id/);
  });
});

test("duplicate fixture ids across files are rejected", () => {
  const a = sampleFixture({ id: "temp-shared-id" });
  const b = sampleFixture({ id: "temp-shared-id", requirement_ids: ["OTP2-ENC-010b"] });
  withTempTree({ "fixtures/a.json": [a], "fixtures/b.json": [b] }, (dir) => {
    assert.throws(() => loadFixtures(dir), /duplicate_id/);
  });
});

test("unknown requirement ids are rejected", () => {
  const bad = sampleFixture({ requirement_ids: ["OTP2-XYZ-999"] });
  withTempTree({ "fixtures/bad.json": [bad] }, (dir) => {
    assert.throws(() => loadFixtures(dir), /unknown_requirement.*OTP2-XYZ-999/s);
  });
});

test("fixtures with an empty requirement_ids list are rejected (schema)", () => {
  const bad = sampleFixture({ requirement_ids: [] });
  withTempTree({ "fixtures/bad.json": [bad] }, (dir) => {
    assert.throws(() => loadFixtures(dir), /schema/);
  });
});

test("fixtures missing required fields are rejected (schema)", () => {
  const { expected, ...noExpected } = sampleFixture({ id: "temp-no-expected" });
  const { category, ...noCategory } = sampleFixture({ id: "temp-no-category" });
  withTempTree({ "fixtures/bad.json": [noExpected, noCategory] }, (dir) => {
    assert.throws(() => loadFixtures(dir), /schema/);
  });
});

test("fixtures with unknown or extra properties are rejected (schema)", () => {
  const bad = { ...sampleFixture(), notes: "extra" };
  withTempTree({ "fixtures/bad.json": [bad] }, (dir) => {
    assert.throws(() => loadFixtures(dir), /schema/);
  });
});

test("fixtures with a wrong fixture_version are rejected", () => {
  const bad = sampleFixture({ fixture_version: 2 });
  withTempTree({ "fixtures/bad.json": [bad] }, (dir) => {
    assert.throws(() => loadFixtures(dir), /schema/);
  });
});

test("malformed hex in fixture bytes is rejected (uppercase, odd length, non-hex)", () => {
  for (const [tag, bytes] of [
    ["uppercase", (SAMPLE_FRAME_BYTES + "00").toUpperCase()],
    ["odd", SAMPLE_FRAME_BYTES + "1"],
    ["nonhex", SAMPLE_FRAME_BYTES + "zz"],
  ]) {
    const bad = sampleFixture({ id: `temp-bad-hex-${tag}`, input: { layout: "pairing_request", bytes } });
    withTempTree({ "fixtures/bad.json": [bad] }, (dir) => {
      assert.throws(() => loadFixtures(dir), /schema|hex/, tag);
    });
  }
});

test("fixtures referencing unknown layouts are rejected", () => {
  const bad = sampleFixture({
    id: "temp-unknown-layout",
    input: { layout: "not_a_layout", bytes: "" },
  });
  withTempTree({ "fixtures/bad.json": [bad] }, (dir) => {
    assert.throws(() => loadFixtures(dir), /unknown_layout/);
  });
});

test("duplicate JSON keys in a fixture file are rejected (raw text inspected before parsing)", () => {
  withTempTree({}, (dir) => {
    const path = join(dir, "fixtures", "bad.json");
    writeFileSync(
      path,
      '{"version":1,"schema":"veles.protocol.fixtures","fixtures":[{"id":"dup","id":"dup2"}]}',
    );
    assert.throws(() => loadFixtures(dir), /duplicate/i);
  });
});

test("layout key-map cache is scoped to each root's layouts object, not layoutId alone", () => {
  const probeLayout = (fieldName) => ({
    layouts: { probe: { fields: [{ name: fieldName, type: "bytes", opaque: false }] } },
  });
  const spec = (fieldName, hex) => ({
    version: 1,
    schema: "veles.protocol.fixtures",
    fixtures: [
      {
        id: `temp-probe-${fieldName}`,
        fixture_version: 1,
        category: "encoding",
        requirement_ids: ["OTP2-ENC-010a"],
        input: { layout: "probe", bytes: hex },
        expected: { accepted: true, fields: { [fieldName]: `0x${hex}` } },
      },
    ],
  });
  const dirA = tempTree({
    "binary-layouts.json": probeLayout("alpha"),
    "fixtures/a.json": spec("alpha", "aa"),
  });
  const dirB = tempTree({
    "binary-layouts.json": probeLayout("beta"),
    "fixtures/b.json": spec("beta", "bb"),
  });
  const dirRogue = tempTree({
    "binary-layouts.json": probeLayout("beta"),
    "fixtures/r.json": spec("alpha", "aa"),
  });
  try {
    // Loading root A first must not poison root B: the caches are keyed by the
    // layouts object identity per loadFixtures call, never by layoutId alone.
    assert.deepEqual(loadFixtures(dirA)[0].expected.fields, { alpha: "0xaa" });
    assert.deepEqual(
      loadFixtures(dirB)[0].expected.fields,
      { beta: "0xbb" },
      "second root must resolve its own layout fields, not reuse the first root's cache",
    );
    assert.throws(
      () => loadFixtures(dirRogue),
      /alpha/,
      "root B's own layout map must reject root A's field names",
    );
  } finally {
    for (const dir of [dirA, dirB, dirRogue]) {
      rmSync(dir, { recursive: true, force: true });
    }
  }
});

// ---------- committed fixtures: generic executor ----------

test("every committed encoding fixture: positives decode exactly, negatives reject at stage", () => {
  for (const f of loadFixtures(v1Root).filter((x) => x.category === "encoding")) {
    const res = validateBinary(f.input.layout, decodeHex(f.input.bytes), layouts());
    if (f.expected.accepted === true) {
      assert.ok(
        f.expected.fields && !("stage" in f.expected),
        `fixture ${f.id}: accepted expectation must carry exact fields`,
      );
      assertAccept(res);
      assert.deepEqual(res.fields, f.expected.fields, `fixture ${f.id} fields mismatch`);
    } else {
      assert.ok(
        f.expected.stage && f.expected.no_state_mutation === true,
        `fixture ${f.id}: rejected expectation must name the stage and absence of mutation`,
      );
      assertRejectStage(res, f.expected.stage);
    }
  }
});

test("every committed rejected fixture names absence of state mutation", () => {
  for (const f of loadFixtures(v1Root)) {
    if (f.category === "encoding" && f.expected.accepted === false) {
      assert.equal(f.expected.no_state_mutation, true, f.id);
    }
    if (f.category === "fragmentation") {
      for (const tx of f.expected.transactions) {
        if (tx.completed === false) {
          assert.equal(tx.no_state_mutation, true, f.id);
        }
      }
    }
  }
});

test("every committed fragmentation fixture: outcomes, reassembly bytes, chained layouts", () => {
  for (const f of loadFixtures(v1Root).filter((x) => x.category === "fragmentation")) {
    assert.equal(f.expected.transactions.length, f.input.transactions.length, f.id);
    const res = validateFragmented(f.input.transactions, f.input.mtu, layouts());
    assert.equal(res.accepted, f.expected.accepted, `${f.id}: unexpected ${res.error}`);
    if (res.accepted) assert.equal(res.error, null);
    for (let i = 0; i < f.input.transactions.length; i++) {
      const expect = f.expected.transactions[i];
      const got = res.transactions[i];
      assert.equal(got.transaction_id, f.input.transactions[i].transaction_id, f.id);
      if (expect.completed === true) {
        assert.ok(got.completed, `${f.id} tx[${i}]: unexpected rejection: ${got.stage}`);
        assert.equal(toHex(got.reassembled), expect.reassembled, `${f.id} tx[${i}] bytes`);
        if (expect.reassembled_layout) {
          assertAccept(validateBinary(expect.reassembled_layout, got.reassembled, layouts()));
        }
      } else if (expect.ignored) {
        assert.equal(got.ignored, true, `${f.id} tx[${i}] expected ignored`);
        assert.equal(got.completed, false);
      } else if (expect.incomplete) {
        assert.equal(got.completed, false, `${f.id} tx[${i}]`);
        assert.equal(got.stage, null, `${f.id} tx[${i}]`);
      } else {
        assert.equal(got.completed, false, `${f.id} tx[${i}]`);
        assert.equal(got.stage, expect.stage, `${f.id} tx[${i}] stage mismatch: ${got.stage}`);
      }
    }
  }
});

// ---------- coverage guards ----------

const REQUIRED_FIXTURE_IDS = [
  "enc-frame-pairing-request-min",
  "enc-frame-pairing-request-caps-three",
  "enc-frame-pairing-request-highbits",
  "enc-frame-pairing-request-unknown-type",
  "enc-frame-pairing-request-truncated",
  "enc-frame-pairing-request-trailing",
  "enc-frame-pairing-request-bad-protocol-version",
  "enc-frame-pairing-request-caps-zero",
  "enc-frame-pairing-request-caps-fourth-unknown",
  "enc-frame-pairing-request-caps-unsorted",
  "enc-frame-pairing-request-caps-duplicate",
  "enc-frame-pairing-request-caps-le-endian",
  "enc-frame-pairing-offer-min",
  "enc-frame-pairing-offer-point-compressed",
  "enc-frame-pairing-client-point-min",
  "enc-frame-pairing-client-point-compressed",
  "enc-frame-pairing-client-point-short",
  "enc-frame-pairing-client-point-long",
  "enc-frame-pairing-confirm-a-min",
  "enc-frame-pairing-confirm-b-min",
  "enc-frame-pairing-confirm-a-trailing",
  "enc-frame-reconnect-hello-phone-min",
  "enc-frame-reconnect-hello-client-min",
  "enc-frame-reconnect-hello-wrong-role",
  "enc-frame-reconnect-signature-phone",
  "enc-frame-reconnect-signature-client-short",
  "enc-frame-preauth-reject-min",
  "enc-frame-preauth-reject-nonempty",
  "enc-frame-preauth-reject-oversize",
  "enc-record-heartbeat-client-min",
  "enc-record-push-max-seq-u64",
  "enc-record-error-max-ct",
  "enc-record-ct-over-max",
  "enc-record-ct-mismatch-extra",
  "enc-record-ct-mismatch-short",
  "enc-record-type-unknown",
  "enc-record-type-reserved-zero",
  "enc-record-direction-invalid-byte",
  "enc-record-direction-conflict",
  "enc-record-protocol-version-2",
  "enc-record-schema-version-2",
  "enc-record-short-tag",
  "enc-body-identity-phone-empty-label",
  "enc-body-identity-desktop-max-label",
  "enc-body-identity-watch-mid-label",
  "enc-body-identity-label-over-max",
  "enc-body-identity-body-under-min",
  "enc-body-identity-trailing",
  "enc-body-identity-device-type-unknown",
  "enc-body-identity-point-compressed",
  "enc-body-identity-label-nonutf8",
  "enc-body-identity-label-control-char",
  "enc-body-otp-event-min",
  "enc-body-otp-event-max-bounds",
  "enc-body-otp-event-multibyte-code",
  "enc-body-otp-event-code-over-max",
  "enc-body-otp-event-code-empty",
  "enc-body-otp-event-lifetime-over-max",
  "enc-body-otp-event-scale-over-max",
  "enc-body-otp-event-currency-lowercase",
  "enc-body-otp-event-currency-short",
  "enc-body-otp-event-code-nonutf8",
  "enc-body-otp-event-code-control-char",
  "enc-body-otp-event-merchant-over-max",
  "enc-body-reconnect-confirm-phone-active",
  "enc-body-reconnect-confirm-peer-inactive",
  "enc-body-reconnect-confirm-unknown-peer-state",
  "enc-body-app-error-min",
  "enc-body-app-error-reason-max",
  "enc-body-app-error-reason-over-max",
  "enc-body-app-error-unknown-code",
  "enc-body-app-error-reason-nonutf8",
  "enc-body-activation-commit-mac",
  "enc-body-activation-complete-mac",
  "enc-body-activation-ack-mac",
  "enc-body-activation-commit-mac-short",
  "enc-body-pull-request-empty",
  "enc-body-pull-request-trailing",
  "enc-body-phone-policy-state-always-eligible",
  "enc-body-phone-policy-state-response-flag",
  "enc-body-phone-policy-state-reserved-flags",
  "enc-body-pull-events-empty",
  "enc-body-pull-events-five",
  "enc-body-pull-events-six",
  "enc-body-push-event-event",
  "enc-body-event-ack-ok",
  "enc-body-event-ack-short",
  "enc-body-removal-request-empty",
  "enc-body-removal-response-empty",
  "enc-body-removal-request-trailing",
  "enc-context-min",
  "enc-context-caps-max",
  "enc-context-unknown-cap",
  "enc-context-caps-unsorted",
  "enc-context-wrong-marker",
  "enc-context-bad-ciphersuite",
  "enc-context-label-mutated",
  "frag-mtu185-single-full-budget",
  "frag-mtu185-total-min-1",
  "frag-mtu185-two-fragments",
  "frag-mtu185-max-total-4096",
  "frag-mtu185-final-zero-payload",
  "frag-reassembled-heartbeat-record",
  "frag-txid-zero",
  "frag-reserved-nonzero",
  "frag-total-zero",
  "frag-total-over-max",
  "frag-short-header",
  "frag-payload-over-budget",
  "frag-nonfinal-payload-short",
  "frag-sum-mismatch",
  "frag-index-gap",
  "frag-index-duplicate",
  "frag-first-fragment-nonzero-index",
  "frag-final-then-extra-fragment",
  "frag-two-concurrent-incomplete",
  "frag-third-transaction-cap",
  "frag-live-transaction-duplicate-ignored",
];

test("required coverage fixture ids all exist", () => {
  const ids = new Set(loadFixtures(v1Root).map((f) => f.id));
  for (const required of REQUIRED_FIXTURE_IDS) {
    assert.ok(ids.has(required), `missing required coverage fixture: ${required}`);
  }
});

test("every binary layout id is exercised by at least one committed encoding fixture", () => {
  const exercised = new Set(
    loadFixtures(v1Root)
      .filter((f) => f.category === "encoding")
      .map((f) => f.input.layout),
  );
  for (const layoutId of Object.keys(layouts().layouts)) {
    assert.ok(exercised.has(layoutId), `layout ${layoutId} has no committed fixture`);
  }
});

// ---------- descriptor constants vs limits and normative document ----------

test("binary-layouts constants match limits.json inventory", () => {
  const L = Object.fromEntries(limits().limits.map((l) => [l.name, l.value]));
  const c = layouts();

  assert.deepEqual(c.constants.frame, {
    header_bytes: 3,
    total_max_bytes: L.frame_total_max_bytes,
    document_anchor: "veles-v1.md#sec-plain-framing",
  });
  assert.equal(c.constants.record.header_bytes, 23);
  assert.equal(c.constants.record.tag_bytes, 16);
  assert.equal(c.constants.record.total_max_bytes, L.record_max_bytes);
  assert.equal(c.constants.record.ciphertext_max_bytes, L.record_ciphertext_max_bytes);
  assert.equal(c.constants.point.size_bytes, 65);
  assert.equal(c.constants.point.uncompressed_prefix_byte, 4);
  assert.equal(c.constants.signature.size_bytes, 64);
  const caps = c.constants.caps;
  assert.equal(caps.set_min_entries, 1);
  assert.equal(caps.set_max_entries, 16);
  assert.deepEqual([...caps.known_values].sort(), [1, 2, 3]);
  assert.equal(caps.mandatory_value, 1);
  assert.equal(caps.sorted_unique, true);
  const ident = c.constants.identity;
  assert.equal(ident.label_min_bytes, 0);
  assert.equal(ident.label_max_bytes, L.identity_label_max_bytes);
  assert.equal(ident.body_min_bytes, 132);
  assert.equal(ident.body_max_bytes, L.pending_record_body_max_bytes);
  assert.deepEqual(ident.device_types, { phone: 0, desktop: 1, watch: 2 });
  const ev = c.constants.otp_event;
  assert.equal(ev.code_min_bytes, 1);
  assert.equal(ev.code_max_bytes, L.otp_code_max_bytes);
  assert.equal(ev.merchant_min_bytes, 0);
  assert.equal(ev.merchant_max_bytes, L.otp_merchant_max_bytes);
  assert.equal(ev.lifetime_max_seconds, L.otp_max_lifetime_seconds);
  assert.equal(ev.amount_scale_max, L.otp_amount_scale_max);
  assert.equal(ev.currency_bytes, 3);
  assert.equal(ev.count_min, 0);
  assert.equal(ev.count_max, L.otp_events_global);
  const err = c.constants.app_error;
  assert.equal(err.reason_min_bytes, 0);
  assert.equal(err.reason_max_bytes, L.app_error_reason_max_bytes);
  assert.deepEqual(err.known_codes, [1, 2, 3, 4, 5]);

  const fr = c.fragmentation;
  assert.equal(fr.header_bytes, 4);
  assert.equal(fr.final_flag_bit, 7);
  assert.equal(fr.index_bit_width, 7);
  assert.equal(fr.index_max, L.fragment_index_max);
  assert.equal(fr.transaction_id_min, L.fragment_transaction_id_min);
  assert.equal(fr.transaction_id_max, 255);
  assert.equal(fr.total_min_bytes, 1);
  assert.equal(fr.total_max_bytes, L.record_max_bytes);
  assert.equal(fr.att_header_bytes, 3);
  assert.equal(fr.mtu_min, L.gatt_mtu_min);
  assert.equal(fr.test_mtu, L.gatt_mtu_min);
  assert.equal(fr.payload_budget_expression, "min(negotiated_MTU - 3, 512) - 4");
  assert.equal(fr.attribute_budget_expression, "min(negotiated_MTU - 3, 512)");
  assert.equal(fr.profile_revision, 2);
  assert.equal(fr.attribute_value_max_bytes, L.gatt_attribute_value_max_bytes);
  assert.equal(fr.mtu_max, L.gatt_mtu_max);
  assert.equal(fr.max_mtu_payload_bytes, L.fragment_payload_bytes_at_attribute_max);
  assert.equal(L.fragment_payload_bytes_at_attribute_max, 512 - 4);
  assert.equal(fr.max_fragments_at_max_mtu, Math.ceil(L.record_max_bytes / fr.max_mtu_payload_bytes));
  // M1: the tested conservative chunk at the MTU floor and the fragment bound it implies
  assert.equal(fr.fragment_payload_bytes_at_test_mtu, L.fragment_payload_bytes_at_min_mtu);
  assert.equal(L.fragment_payload_bytes_at_min_mtu, 185 - 3 - 4);
  assert.equal(Math.ceil(L.record_max_bytes / L.fragment_payload_bytes_at_min_mtu), 24);
  assert.ok(fr.max_fragments_at_test_mtu === 24);
  assert.equal(fr.reassembly.incomplete_per_peer_max, L.reassembly_incomplete_max_per_peer);
  assert.equal(fr.reassembly.bytes_per_peer_max, L.reassembly_bytes_max_per_peer);
  assert.equal(fr.reassembly.incomplete_global_max, L.reassembly_incomplete_max_global);
  assert.equal(fr.reassembly.bytes_global_max, L.reassembly_bytes_max_global);
});

// record type table: exact types and directions from sec-record-types
const RECORD_TYPE_TABLE = {
  reconnect_confirm: { value: 1, directions: [1, 2] },
  app_heartbeat: { value: 2, directions: [1, 2] },
  app_error: { value: 3, directions: [1, 2] },
  enroll_identity: { value: 256, directions: [1, 2] },
  activation_commit: { value: 257, directions: [1] },
  activation_complete: { value: 258, directions: [2] },
  activation_ack: { value: 259, directions: [1] },
  enrollment_prepare: { value: 260, directions: [2] },
  pull_request: { value: 512, directions: [1] },
  pull_events: { value: 514, directions: [2] },
  push_event: { value: 515, directions: [2] },
  event_ack: { value: 516, directions: [1] },
  removal_request: { value: 517, directions: [1, 2] },
  removal_response: { value: 518, directions: [1, 2] },
  phone_policy_state: { value: 519, directions: [2] },
};

test("protected_record descriptor matches the normative record type table", () => {
  const c = layouts();
  assert.deepEqual(c.record_types, RECORD_TYPE_TABLE);
  const recordTypeField = c.layouts.protected_record.fields.find(
    (f) => f.name === "record_type",
  );
  const expectedEnum = Object.values(RECORD_TYPE_TABLE)
    .map((t) => t.value)
    .sort((a, b) => a - b);
  assert.deepEqual(
    [...recordTypeField.enum].sort((a, b) => a - b),
    expectedEnum,
    "record_type enum must equal the normative type table values",
  );
  assert.equal(c.layouts.protected_record.direction_check, true);
});

// ---------- descriptor field sums vs normative payload formulas ----------

/**
 * Fixed-prefix-only sum (variable caps/label/code bytes excluded): the doc's
 * "payload_length = <n> + caps_len" constant part.
 */
function layoutPrefixSum(layout, layoutsRef = layouts()) {
  return layout.fields.reduce((sum, f) => {
    switch (f.type) {
      case "length_prefixed_u16":
      case "count_prefixed_u8":
      case "bytes_dynamic":
        return sum + (f.type === "length_prefixed_u16" ? 2 : f.type === "count_prefixed_u8" ? 1 : 0);
      case "inline_layout":
        return sum + layoutPrefixSum(layoutsRef.layouts[f.layout], layoutsRef);
      default:
        return sum + fieldFixedBytes(f);
    }
  }, 0);
}

function fieldFixedBytes(field) {
  switch (field.type) {
    case "bytes":
      return field.size;
    case "literal":
      return field.hex.length / 2;
    case "u8":
      return 1;
    case "u16":
      return 2;
    case "u32":
      return 4;
    case "u64":
      return 8;
    case "point":
      return 65;
    case "signature":
      return 64;
    case "currency":
      return 3;
    default:
      throw new Error(`field type ${field.type} has no fixed prefix size here`);
  }
}

function fieldBoundMin(field, layoutsRef) {
  switch (field.type) {
    case "length_prefixed_u16":
      return 2 + (field.min ?? 0);
    case "count_prefixed_u8": {
      const item = layoutsRef.layouts[field.item_layout];
      return 1 + (field.min ?? 0) * layoutBoundMin(item, layoutsRef);
    }
    case "inline_layout":
      return layoutBoundMin(layoutsRef.layouts[field.layout], layoutsRef);
    case "bytes_dynamic":
      return field.min ?? 0;
    default:
      return fieldFixedBytes(field);
  }
}

function layoutBoundMin(layout, layoutsRef = layouts()) {
  return layout.fields.reduce((sum, f) => sum + fieldBoundMin(f, layoutsRef), 0);
}

function fieldBoundMax(field, layoutsRef) {
  switch (field.type) {
    case "length_prefixed_u16":
      return 2 + field.max;
    case "count_prefixed_u8": {
      const item = layoutsRef.layouts[field.item_layout];
      return 1 + field.max * layoutBoundMax(item, layoutsRef);
    }
    case "inline_layout":
      return layoutBoundMax(layoutsRef.layouts[field.layout], layoutsRef);
    case "bytes_dynamic":
      return field.max;
    default:
      return fieldFixedBytes(field);
  }
}

function layoutBoundMax(layout, layoutsRef = layouts()) {
  return layout.fields.reduce((sum, f) => sum + fieldBoundMax(f, layoutsRef), 0);
}

test("descriptor fixed sums match the normative payload/body formulas", () => {
  const ls = layouts().layouts;
  // sec-plain-framing payload formulas: payload_length = <n> + caps_len
  assert.equal(layoutPrefixSum(ls.pairing_request), 20);
  assert.equal(layoutPrefixSum(ls.pairing_offer), 101);
  assert.equal(layoutPrefixSum(ls.reconnect_hello), 102);
  assert.equal(layoutPrefixSum(ls.pairing_client_point), 65);
  assert.equal(layoutPrefixSum(ls.pairing_confirm_a), 32);
  assert.equal(layoutPrefixSum(ls.pairing_confirm_b), 32);
  assert.equal(layoutPrefixSum(ls.reconnect_signature), 65);
  assert.equal(layoutPrefixSum(ls.preauth_reject), 0);
  // ...with the caps minimum of 1 entry, the wire payload minima are:
  assert.equal(layoutBoundMin(ls.pairing_request), 21);
  assert.equal(layoutBoundMin(ls.pairing_offer), 102);
  assert.equal(layoutBoundMin(ls.reconnect_hello), 103);
  // sec-identity-record body bounds (OTP2-REC-010)
  assert.equal(layoutBoundMin(ls.enroll_identity), 132);
  assert.equal(layoutBoundMax(ls.enroll_identity), 164);
  // sec-record-format: 23-byte header + 0..4057 ciphertext + 16-byte tag
  assert.equal(layoutBoundMin(ls.protected_record), 39);
  assert.equal(layoutBoundMax(ls.protected_record), 4096);
  // sec-otp-event bounds (OTP2-REC-030)
  assert.equal(layoutBoundMin(ls.otp_event), 61);
  assert.equal(layoutBoundMax(ls.otp_event), 140);
  assert.equal(layoutBoundMin(ls.pull_events), 1);
  assert.equal(layoutBoundMax(ls.pull_events), 1 + 5 * 140);
  assert.equal(layoutBoundMin(ls.push_event), 61);
  assert.equal(layoutBoundMax(ls.push_event), 140);
  // framework, activation, removal bodies (OTP2-ENC-020)
  assert.equal(layoutBoundMin(ls.app_heartbeat), 0);
  assert.equal(layoutBoundMin(ls.app_error), 3);
  assert.equal(layoutBoundMax(ls.app_error), 67);
  assert.equal(layoutBoundMin(ls.reconnect_confirm), 33);
  assert.equal(layoutBoundMin(ls.activation_commit), 32);
  assert.equal(layoutBoundMin(ls.activation_complete), 32);
  assert.equal(layoutBoundMin(ls.activation_ack), 32);
  assert.equal(layoutBoundMin(ls.pull_request), 0);
  // C2: phone_policy_state is exactly policy||flags (2 bytes); the retired 0x0201 tag is unassigned.
  assert.equal(layoutBoundMin(ls.phone_policy_state), 2);
  assert.ok(!ls.pull_phone_state, "pull_phone_state layout must be retired");
  assert.ok(!Object.values(layouts().record_types).some((t) => t.value === 513), "record type 513 must be retired everywhere");
  assert.equal(layoutBoundMin(ls.event_ack), 32);
  assert.equal(layoutBoundMin(ls.removal_request), 0);
  assert.equal(layoutBoundMin(ls.removal_response), 0);
  // sec-spake-context canonical AAD
  assert.equal(layoutBoundMin(ls.spake_context), 92);
});

// ---------- deliberate mutation sensitivity ----------

test("mutating one expected byte in a canonical fixture is detected by the comparator", () => {
  const f = loadFixtures(v1Root).find(
    (x) => x.id === "enc-frame-pairing-request-min" && x.category === "encoding",
  );
  assert.ok(f, "mutation test needs the canonical pairing_request fixture");
  const mutated = mutateOneByte(f.input.bytes);
  assert.notEqual(mutated, f.input.bytes);
  const res = validateBinary(f.input.layout, decodeHex(mutated), layouts());
  const detected =
    res.accepted !== f.expected.accepted ||
    JSON.stringify(res.fields) !== JSON.stringify(f.expected.fields);
  assert.ok(detected, "comparator failed to detect a deliberate expected-bytes mutation");
});

// ---------- OTP-23 fragmentation profile revision 2 (OTP2-GAT-010 / OTP2-GAT-011) ----------

// Literal, independent statement of the budget: unclamped up to ATT MTU 515, 508 above.
const literalPayloadBudget = (mtu) => (mtu <= 515 ? mtu - 7 : 508);

test("OTP2-GAT-010 committed fragmentation fixtures are exactly what the reviewed generator produces", () => {
  const text = readFileSync(join(v1Root, "fixtures", "fragmentation.json"), "utf8");
  assert.equal(JSON.stringify(mergeFixtures(JSON.parse(text)), null, 2) + "\n", text);
});

test("OTP2-GAT-010 payload budget is min(MTU-3,512)-4 for every MTU 185..517 and never exceeds a 512-byte attribute", () => {
  for (let mtu = 185; mtu <= 517; mtu++) {
    assert.equal(payloadBudget(mtu), literalPayloadBudget(mtu), `mtu ${mtu}`);
    assert.ok(payloadBudget(mtu) + 4 <= 512, `mtu ${mtu}`);
  }
  assert.equal(payloadBudget(185), 178);
  assert.equal(payloadBudget(515), 508);
  assert.equal(payloadBudget(517), 508);
});

test("OTP2-GAT-010 canonical sender and strict receiver agree byte-exactly at every boundary of every MTU", () => {
  const L = layouts();
  for (let mtu = 185; mtu <= 517; mtu++) {
    const p = literalPayloadBudget(mtu);
    const lengths = new Set([1, 2, p - 1, p, p + 1, 2 * p - 1, 2 * p, 2 * p + 1, 4095, 4096]);
    for (const len of lengths) {
      if (len < 1 || len > 4096) continue;
      const record = sampleRecord(len, mtu);
      for (const emptyFinal of [false, true]) {
        const frames = fragmentRecord(record, mtu, 77, { emptyFinal });
        const full = Math.floor(len / p);
        const canonicalCount = Math.ceil(len / p);
        const expectCount = emptyFinal ? Math.floor(len / p) + 1 : canonicalCount;
        // emptyFinal is only a distinct form for exact multiples; otherwise the tail is non-empty anyway
        if (emptyFinal && len % p !== 0) continue;
        assert.equal(frames.length, expectCount, `mtu ${mtu} len ${len}`);
        frames.forEach((f, i) => {
          const bytes = decodeHex(f);
          assert.ok(bytes.length <= Math.min(mtu - 3, 512), `mtu ${mtu} len ${len} frame ${i}`);
          const final = (bytes[0] & 0x80) !== 0;
          assert.equal(final, i === frames.length - 1);
          if (!final) assert.equal(bytes.length - 4, p, `non-final payload mtu ${mtu} len ${len}`);
        });
        if (!emptyFinal && len % p === 0) {
          assert.equal(decodeHex(frames.at(-1)).length - 4, p, "exact multiple ends in a full final fragment");
          assert.equal(frames.length, full);
        }
        const res = validateFragmented([{ transaction_id: 77, fragments: frames }], mtu, L);
        assert.equal(res.accepted, true, `mtu ${mtu} len ${len} empty=${emptyFinal}: ${res.error}`);
        assert.equal(toHex(res.transactions[0].reassembled), toHex(record), `mtu ${mtu} len ${len}`);
      }
    }
  }
});

test("OTP2-GAT-010 revision-1 frames at MTU 516 and 517 fail closed in the revision-2 receiver, and vice versa", () => {
  const L = layouts();
  for (const mtu of [516, 517]) {
    const legacyPayload = mtu - 7; // revision 1: 509 / 510
    const record = sampleRecord(4096, 5);
    const frames = [];
    for (let off = 0, i = 0; off < record.length; i++) {
      const chunk = record.subarray(off, Math.min(off + legacyPayload, record.length));
      off += chunk.length;
      const head = Uint8Array.of((off === record.length ? 0x80 : 0) | i, 9, i === 0 ? 0x10 : 0, 0);
      frames.push(toHex(Uint8Array.from([...head, ...chunk])));
    }
    const res = validateFragmented([{ transaction_id: 9, fragments: frames }], mtu, L);
    assert.equal(res.accepted, false, `mtu ${mtu}`);
    assert.equal(res.transactions[0].completed, false);
    assert.equal(res.transactions[0].stage, "fragment_total");
    // revision-2 frames judged by a revision-1 rule (non-final payload must be mtu-7) differ: proof the
    // two profiles are genuinely incompatible at these MTUs and never silently interchangeable
    assert.notEqual(literalPayloadBudget(mtu), legacyPayload);
  }
  for (const mtu of [185, 247, 514, 515]) {
    assert.equal(literalPayloadBudget(mtu), mtu - 7, `unchanged below the clamp: mtu ${mtu}`);
  }
});

test("OTP2-GAT-003 negotiated MTU outside 185..517 is refused before any fragment is read", () => {
  const frames = fragmentRecord(sampleRecord(10, 1), 185, 3);
  for (const mtu of [0, 23, 184, 518, 1000]) {
    const res = validateFragmented([{ transaction_id: 3, fragments: frames }], mtu, layouts());
    assert.equal(res.accepted, false, `mtu ${mtu}`);
    assert.equal(res.transactions[0].completed, false);
  }
});

test("OTP2-GAT-010 exact final handling: empty final only closes a fully received record", () => {
  const L = layouts();
  const p = 508;
  const record = sampleRecord(2 * p, 6);
  const ok = fragmentRecord(record, 517, 5, { emptyFinal: true });
  assert.equal(ok.length, 3);
  assert.equal(validateFragmented([{ transaction_id: 5, fragments: ok }], 517, L).accepted, true);
  // the same empty final after a record that is not fully received is a length mismatch
  const short = fragmentRecord(sampleRecord(2 * p + 1, 6), 517, 5).slice(0, 2);
  short.push(ok[2]);
  assert.equal(validateFragmented([{ transaction_id: 5, fragments: short }], 517, L).accepted, false);
  // nothing may follow a final fragment
  const trailing = [...fragmentRecord(sampleRecord(p, 7), 517, 5), ok[2]];
  const res = validateFragmented([{ transaction_id: 5, fragments: trailing }], 517, L);
  assert.equal(res.accepted, false);
  assert.equal(res.transactions[0].stage, "fragment_final");
});
