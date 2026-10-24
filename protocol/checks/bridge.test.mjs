// OTP-02 Plan Task 3 — Native Messaging envelope and bridge lifecycle contract
// suite. Test-only; never imported by product code.
// Run: node --test protocol/checks/bridge.test.mjs
//
// Normative source: docs/protocol/veles-v1.md section 10 (Native Messaging
// envelope v1, OTP2-BRD-*) and protocol/v1/{envelope.schema,limits}.json.
// Tests DRIVE actual request/event negotiation (createBridgeHost), actual byte
// framing (decodeHostFrame) and actual trace-derived ordering/counters
// (checkBridgeTrace); committed bridge fixtures are executed generically at the
// end. Expected wire texts are hand-written, never produced by the validators.
//
// Serialization convention (test-only; BRD-002 says field order is irrelevant):
// host outputs are compact JSON with fields in the order the normative tables
// list them, starting envelope_version, api_version (post-ready), type,
// request_id (when correlated).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { loadFixtures, parseJsonStrict } from "./fixture.mjs";
import {
  ADVERT_MAX_BYTES,
  DISCOVERY_CACHE_MAX,
  FOUND_PER_SCAN_MAX,
  FOUND_RATE_PER_SECOND,
  MESSAGE_MAX_BYTES,
  OPEN_CONNECTIONS_MAX,
  OPEN_SCANS_MAX,
  OUTSTANDING_MAX,
  QUEUE_BYTES_MAX,
  QUEUE_MESSAGES_MAX,
  RECORD_MAX_BYTES,
  REQUEST_TIMEOUT_MS,
  decodeHostFrame,
  validateEnvelope,
  validateEvent,
  selectApi,
  encodeBase64,
  decodeBase64,
  createBridgeHost,
  checkBridgeTrace,
} from "./envelope.mjs";

const checksDir = dirname(fileURLToPath(import.meta.url));
const v1Root = join(checksDir, "..", "v1");

const j = JSON.stringify;
const UUID = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"; // OTP2-GAT-001 service
const HANDLES = [
  "a000000000000001",
  "a000000000000002",
  "a000000000000003",
  "a000000000000004",
  "a000000000000005",
  "a000000000000006",
  "a000000000000007",
  "a000000000000008",
];

const hello = j({
  envelope_version: 1,
  type: "hello",
  request_id: "hello-1",
  api_versions: [1],
});
const readyOf = j({
  envelope_version: 1,
  api_version: 1,
  type: "ready",
  request_id: "hello-1",
  bridge_version: "veles-bridge-test-1.0.0",
  platform: "windows",
  capabilities: ["veles_record_v1"],
});

function assertAccept(res, why) {
  assert.equal(res.accepted, true, `${why}: ${res.error}`);
  assert.equal(res.error, null);
  assert.ok(res.fields && typeof res.fields === "object");
}

function assertRejectStage(res, stage, why) {
  assert.equal(res.accepted, false, `${why}: unexpectedly accepted`);
  assert.equal(res.fields, null);
  assert.ok(
    res.error.includes(`stage=${stage}`),
    `${why}: expected stage=${stage}, error was: ${res.error}`,
  );
}

function newTextHost(options = {}) {
  return createBridgeHost({
    supportedApi: [1],
    handles: HANDLES,
    ...options,
  });
}

// ---------- decodeHostFrame: u32le framing, UTF-8, 1..65536 bytes ----------

test("decodeHostFrame accepts framing exactly within 1..65536 bytes and rejects outside", () => {
  const small = decodeHostFrame(Buffer.from("{}", "utf8"));
  assert.equal(small.accepted, true);
  assert.equal(small.text, "{}");
  const at = decodeHostFrame(Buffer.alloc(65536, 0x20));
  assert.equal(at.accepted, true, "65536 is the exact ceiling; JSON validity is a separate stage");
  assert.equal(at.text.length, 65536);
  assertRejectStage(validateEnvelope(at.text, "pre", null), "json", "65536 spaces are not JSON");
  const over = decodeHostFrame(Buffer.alloc(65537, 0x20));
  assert.equal(over.accepted, false);
  assert.ok(over.error.includes("stage=length"), over.error);
  const none = decodeHostFrame(new Uint8Array(0));
  assert.equal(none.accepted, false);
  assert.ok(none.error.includes("stage=framing"));
});

test("decodeHostFrame rejects invalid UTF-8 bytes and never produces partial text", () => {
  // JSON-valid framing length, but the bytes are neither valid UTF-8 nor JSON
  const res = decodeHostFrame(Uint8Array.from([0x22, 0x00, 0xff, 0x80, 0x81, 0x22]));
  assert.equal(res.accepted, false);
  assert.ok(res.error.includes("stage=utf8"), res.error);
});

test("decodeHostFrame enforces the message-length limit at the byte level with stage length", () => {
  assert.equal(MESSAGE_MAX_BYTES, 65536);
});

// ---------- strict text validation with duplicate-key raw detection ----------

test("duplicate JSON keys in a hello are rejected from raw text before parsing", () => {
  const text =
    '{"envelope_version":1,"type":"hello","request_id":"hello-1","api_versions":[1],"envelope_version":1}';
  assertRejectStage(validateEnvelope(text, "pre", null), "duplicate_key");
});

test("nested duplicate keys are also rejected (raw text scan covers the whole document)", () => {
  const text =
    '{"envelope_version":1,"type":"connect","request_id":"c1","api_version":1,"connect":{"a":1,"a":2},"device_handle":"a000000000000001"}';
  assertRejectStage(validateEnvelope(text, "ready", 1), "duplicate_key");
});

// ---------- validateEnvelope: negotiation-shaped requests ----------

test("hello with api_versions is valid pre-ready", () => {
  assertAccept(validateEnvelope(hello, "pre", null), "hello api_versions");
  const parsed = validateEnvelope(hello, "pre", null).fields;
  assert.equal(parsed.type, "hello");
  assert.deepEqual(parsed.api_versions, [1]);
});

test("hello with api_version_range is valid pre-ready", () => {
  const text = j({
    envelope_version: 1,
    type: "hello",
    request_id: "hello-2",
    api_version_range: { min: 1, max: 2 },
  });
  assertAccept(validateEnvelope(text, "pre", null), "hello range");
});

test("post-ready requests carry envelope 1 and the selected api_version", () => {
  const text = j({
    envelope_version: 1,
    api_version: 1,
    type: "start_scan",
    request_id: "scan-1",
    service_uuid: UUID,
  });
  assertAccept(validateEnvelope(text, "ready", 1), "start_scan");
  assertRejectStage(validateEnvelope(text, "ready", 2), "phase", "api mismatch");
  assertRejectStage(validateEnvelope(j({ ...parseJsonStrict(text), api_version: 2 }), "ready", 1), "phase");
});

test("pre-ready accepts only hello; other request types reject at phase", () => {
  const text = j({
    envelope_version: 1,
    type: "start_scan",
    request_id: "scan-1",
    service_uuid: UUID,
  });
  assertRejectStage(validateEnvelope(text, "pre", null), "phase", "non-hello pre-ready");
  assertRejectStage(validateEnvelope(hello, "ready", 1), "phase", "post-ready hello");
});

test("unusable pre-ready input fails closed at validation level", () => {
  assertRejectStage(validateEnvelope('{"nope":1}', "pre", null), "version", "no envelope_version");
  assertRejectStage(validateEnvelope('{"nope":1,"type":"hello","request_id":"h","api_versions":[1]}', "pre", null), "version", "envelope_version 2");
  assertRejectStage(validateEnvelope('{"envelope_version":1,"type":"hello"}', "pre", null), "schema", "no request id, no offer");
  assertRejectStage(validateEnvelope('{"envelope_version":1,"type":"hello","request_id":"h"}', "pre", null), "schema", "no offer");
});

test("hello offers: bounds and exclusivity", () => {
  const base = { envelope_version: 1, type: "hello", request_id: "h" };
  assertRejectStage(validateEnvelope(j({ ...base, api_versions: [] }), "pre", null), "schema");
  // exactly one offer source
  const both = `{"envelope_version":1,"type":"hello","request_id":"h","api_versions":[1],"api_version_range":{"min":1,"max":1}}`;
  assertRejectStage(validateEnvelope(both, "pre", null), "schema");
  const neither = j({ envelope_version: 1, type: "hello", request_id: "h" });
  assertRejectStage(validateEnvelope(neither, "pre", null), "schema");
  // count ceiling 1..16 (17th entry rejects at negotiation, 16 is the at-limit)
  assertAccept(
    validateEnvelope(j({ ...base, api_versions: Array.from({ length: 16 }, (_, i) => i + 1) }), "pre", null),
    "api_versions at-limit 16",
  );
  assertRejectStage(
    validateEnvelope(j({ ...base, api_versions: Array.from({ length: 17 }, (_, i) => i + 1) }), "pre", null),
    "negotiation",
  );
  // ordering, uniqueness, range
  assertRejectStage(validateEnvelope(j({ ...base, api_versions: [2, 1] }), "pre", null), "negotiation");
  assertRejectStage(validateEnvelope(j({ ...base, api_versions: [1, 1] }), "pre", null), "negotiation");
  assertRejectStage(validateEnvelope(j({ ...base, api_version_range: { min: 2, max: 1 } }), "pre", null), "negotiation");
  assertRejectStage(validateEnvelope(j({ ...base, api_version_range: { min: 0, max: 1 } }), "pre", null), "schema", "min below 1");
  assertRejectStage(validateEnvelope(j({ ...base, api_version_range: { min: 1, max: 1048577 } }), "pre", null), "schema", "max above 1048576");
  assertRejectStage(validateEnvelope(j({ ...base, api_versions: [1.5] }), "pre", null), "schema", "non-integer offer");
});

// ---------- base64: canonical RFC 4648 with padding, zero pad bits ----------

test("base64 codec: canonical strings accepted, zero pad bits enforced", () => {
  assert.deepEqual(Array.from(decodeBase64("aGk=")), [0x68, 0x69]);
  assert.equal(encodeBase64(Uint8Array.from([0x68, 0x69])), "aGk=");
  assert.deepEqual(Array.from(decodeBase64("AQ==")), [0x01]); // valid pad bits
  // nonzero pad bits reject
  assert.throws(() => decodeBase64("AB=="), /stage=base64/);
  assert.throws(() => decodeBase64("aGh="), /stage=base64/);
  // whitespace, url alphabet, embedded padding, double padding, bare payload reject
  for (const bad of ["aGk =", "aG k", "aGk", "aGk==", "aG====", "a-_="]) {
    assert.throws(() => decodeBase64(bad), /stage=base64/, `text: ${JSON.stringify(bad)}`);
  }
});

test("record_base64 accepts decoded sizes below, at and above nothing beyond 5120", () => {
  assert.equal(RECORD_MAX_BYTES, 5120);
  for (const size of [1, 5119, 5120]) {
    const text = j({
      envelope_version: 1,
      api_version: 1,
      type: "send",
      request_id: "s",
      connection_handle: "a000000000000003",
      record_base64: encodeBase64(Uint8Array.from({ length: size }, () => 0x00)),
    });
    assertAccept(validateEnvelope(text, "ready", 1), `record size ${size}`);
  }
  const over = j({
    envelope_version: 1,
    api_version: 1,
    type: "send",
    request_id: "s",
    connection_handle: "a000000000000003",
    record_base64: encodeBase64(Uint8Array.from({ length: 5121 }, () => 0x00)),
  });
  assertRejectStage(validateEnvelope(over, "ready", 1), "bounds", "5121 decoded bytes");
  const empty = j({
    envelope_version: 1,
    api_version: 1,
    type: "send",
    request_id: "s",
    connection_handle: "a000000000000003",
    record_base64: "",
  });
  assertRejectStage(validateEnvelope(empty, "ready", 1), "bounds", "empty record");
});

test("exact base64 round trips preserve bytes that are neither JSON nor valid UTF-8", () => {
  const bytes = Uint8Array.from([0x00, 0xff, 0x80, 0x81, 0x0d, 0xfe]);
  try {
    new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    assert.fail("test setup: bytes were valid UTF-8");
  } catch {
    // expected
  }
  const b64 = encodeBase64(bytes);
  const text = j({
    envelope_version: 1,
    api_version: 1,
    type: "send",
    request_id: "s",
    connection_handle: "a000000000000003",
    record_base64: b64,
  });
  assertAccept(validateEnvelope(text, "ready", 1), "binary record send");
  assert.deepEqual(Array.from(decodeBase64(b64)), Array.from(bytes));
  assert.equal(encodeBase64(decodeBase64(b64)), b64, "re-encoding is byte-identical");
  // and the same bytes survive a host `message` event fixture-shaped text
  const eventText = j({
    envelope_version: 1,
    api_version: 1,
    type: "message",
    connection_handle: "a000000000000003",
    record_base64: b64,
  });
  assertAccept(validateEvent(eventText, 1), "binary record message event");
});

// ---------- validateEvent: host output shape ----------

test("ready events validate; unknown capabilities reject at negotiation; api_version rules hold", () => {
  assertAccept(validateEvent(readyOf, 1), "ready");
  const badCaps = j({
    envelope_version: 1,
    api_version: 1,
    type: "ready",
    request_id: "hello-1",
    bridge_version: "b",
    platform: "windows",
    capabilities: ["veles_record_v1", "time_travel"],
  });
  assertRejectStage(validateEvent(badCaps, 1), "negotiation", "unknown capability");
  // an event carrying api_version before any selected version is a phase error
  const withApi = j({
    envelope_version: 1,
    api_version: 1,
    type: "device_found",
    scan_handle: HANDLES[0],
    device_handle: HANDLES[1],
    label: "Phone",
  });
  assertRejectStage(validateEvent(withApi, null), "phase", "api_version before negotiation");
});

test("events carry api_version 1 when ready is selected; mismatch rejects", () => {
  const text = j({
    envelope_version: 1,
    api_version: 1,
    type: "device_found",
    scan_handle: "a000000000000001",
    device_handle: "a000000000000002",
    label: "Phone",
  });
  assertAccept(validateEvent(text, 1), "device_found");
  assertRejectStage(validateEvent(text, 2), "phase", "event api mismatch");
  assertRejectStage(validateEvent(j({ ...parseJsonStrict(text), api_version: 2 }), 1), "phase");
});

test("unsolicited events must not carry request_id; correlated events must", () => {
  const unsolicitedOk = j({
    envelope_version: 1,
    api_version: 1,
    type: "device_found",
    scan_handle: "a000000000000001",
    device_handle: "a000000000000002",
    label: "Phone",
  });
  assertAccept(validateEvent(unsolicitedOk, 1), "unsolicited without request_id");
  const solicitedLike = j({
    ...parseJsonStrict(unsolicitedOk),
    request_id: "x",
  });
  assertRejectStage(validateEvent(solicitedLike, 1), "schema", "request_id on unsolicited event");
  const sentNoId = j({
    envelope_version: 1,
    api_version: 1,
    type: "sent",
    connection_handle: "a000000000000003",
  });
  assertRejectStage(validateEvent(sentNoId, 1), "schema", "sent without request_id");
});

test("label, platform_hint, advertisement and error-stage bounds hold at the schema level", () => {
  const event = (label, extra = {}) =>
    j({
      envelope_version: 1,
      api_version: 1,
      type: "device_found",
      scan_handle: "a000000000000001",
      device_handle: "a000000000000002",
      label,
      ...extra,
    });
  assertAccept(validateEvent(event("x".repeat(64)), 1), "label 64");
  assertRejectStage(validateEvent(event("x".repeat(65)), 1), "schema", "label 65");
  assertAccept(
    validateEvent(event("ok", { platform_hint: "y".repeat(256) }), 1),
    "platform_hint 256",
  );
  assertRejectStage(
    validateEvent(event("ok", { platform_hint: "y".repeat(257) }), 1),
    "schema",
    "platform_hint 257",
  );
  const adv = (bytes) => ({ advertisement: encodeBase64(bytes) });
  assertAccept(validateEvent(event("ok", adv(Uint8Array.from({ length: 256 }, () => 1))), 1), "advertisement 256");
  assertRejectStage(
    validateEvent(event("ok", adv(Uint8Array.from({ length: 257 }, () => 1))), 1),
    "bounds",
    "advertisement 257",
  );
  const err = (stage, extra = {}) =>
    j({
      envelope_version: 1,
      api_version: 1,
      type: "error",
      request_id: "r",
      code: "invalid_request",
      recoverability: "fatal",
      stage,
      ...extra,
    });
  assertAccept(validateEvent(err("x".repeat(32)), 1), "stage 32 chars");
  assertRejectStage(validateEvent(err("x".repeat(33)), 1), "schema", "stage 33 chars");
  assertRejectStage(validateEvent(err("BAD-UPPER"), 1), "schema", "stage charset");
  const unknownCode = j({ ...parseJsonStrict(err("pre_ready")), code: "not_a_code" });
  assertRejectStage(validateEvent(unknownCode, 1), "schema", "unknown error code");
  const bothHandles = j({
    ...parseJsonStrict(err("pre_ready")),
    scan_handle: "a000000000000001",
    connection_handle: "a000000000000003",
  });
  assertRejectStage(validateEvent(bothHandles, 1), "schema", "error with two handles");
});

test("handles and request ids carry their declared formats", () => {
  const badHandle = j({
    envelope_version: 1,
    api_version: 1,
    type: "send",
    request_id: "s",
    connection_handle: "A000000000000003",
    record_base64: encodeBase64(Uint8Array.from([1])),
  });
  assertRejectStage(validateEnvelope(badHandle, "ready", 1), "schema", "uppercase handle");
  const longId = j({
    envelope_version: 1,
    api_version: 1,
    type: "start_scan",
    request_id: "r".repeat(65),
    service_uuid: UUID,
  });
  assertRejectStage(validateEnvelope(longId, "ready", 1), "schema", "request_id 65");
  const id64 = j({
    envelope_version: 1,
    api_version: 1,
    type: "start_scan",
    request_id: "r".repeat(64),
    service_uuid: UUID,
  });
  assertAccept(validateEnvelope(id64, "ready", 1), "request_id 64");
});

// ---------- selectApi: highest overlapping version ----------

test("selectApi picks the highest mutually supported version", () => {
  assert.equal(selectApi({ api_versions: [1] }, [1]), 1);
  assert.equal(selectApi({ api_versions: [2, 1] }, [1, 2]), 2);
  assert.equal(selectApi({ api_versions: [1, 3] }, [1, 2, 3]), 3);
  assert.equal(selectApi({ api_version_range: { min: 1, max: 4 } }, [3]), 3);
  assert.equal(selectApi({ api_version_range: { min: 2, max: 2 } }, [1, 2]), 2);
  assert.equal(selectApi({ api_version_range: { min: 2, max: 9 } }, [1]), null);
  assert.equal(selectApi({ api_versions: [2, 3] }, [1]), null);
});

// ---------- driven negotiation: hello-only pre-ready, overlap, exits ----------

test("pre-ready hello carrying api_version is rejected by phase, never negotiated", () => {
  const text = j({
    envelope_version: 1,
    type: "hello",
    request_id: "h1",
    api_version: 1,
    api_versions: [1],
  });
  assertRejectStage(validateEnvelope(text, "pre", null), "phase", "api_version before negotiation");
  const host = newTextHost();
  const step = host.feed(text, 0);
  assert.deepEqual(JSON.parse(step.outputs[0]), {
    envelope_version: 1,
    type: "error",
    request_id: "h1",
    code: "invalid_request",
    recoverability: "fatal",
    stage: "pre_ready",
  });
  assert.equal(host.exitCode, 2);
});

test("pre-ready: hello negotiates ready with the highest overlap and moves the host post-ready", () => {
  const host = createBridgeHost({ supportedApi: [1, 2], handles: HANDLES });
  const step = host.feed(j({ envelope_version: 1, type: "hello", request_id: "h1", api_versions: [1, 2] }), 0);
  assert.deepEqual(step.outputs, [
    j({
      envelope_version: 1,
      api_version: 2,
      type: "ready",
      request_id: "h1",
      bridge_version: "veles-bridge-test-1.0.0",
      platform: "windows",
      capabilities: ["veles_record_v1"],
    }),
  ]);
  assert.equal(host.exitCode, null);
  assert.deepEqual(host.state(), {
    phase: "post",
    api_version: 2,
    outstanding: 0,
    queue_messages: 0,
    queue_bytes: 0,
    queued_completions: 0,
    open_scans: 0,
    open_connections: 0,
    discovered_devices: 0,
    handles_issued: 0,
    synthesized_errors: 0,
    rate_dropped: 0,
    scan_events_dropped: 0,
  });
});

test("pre-ready: a recognizable non-hello request gets one correlated error, flushes, exits 2", () => {
  const host = newTextHost();
  const step = host.feed(j({ envelope_version: 1, type: "stop_scan", request_id: "s0", scan_handle: "0".repeat(16) }), 0);
  assert.deepEqual(step.outputs, [
    j({
      envelope_version: 1,
      type: "error",
      request_id: "s0",
      code: "invalid_request",
      recoverability: "fatal",
      stage: "pre_ready",
    }),
  ]);
  assert.equal(host.exitCode, 2);
  assert.deepEqual(host.state().open_scans, 0, "no BLE work before negotiation");
});

test("pre-ready: unusable inputs produce no unsolicited JSON and exit 2 silently", () => {
  for (const [why, text] of [
    ["unparseable json", "{not json"],
    ["wrong envelope version", '{"envelope_version":2,"type":"hello","request_id":"h","api_versions":[1]}'],
    ["duplicate keys", '{"envelope_version":1,"envelope_version":1,"type":"hello","request_id":"h","api_versions":[1]}'],
    ["message over 65536 bytes", " ".repeat(65537)],
    ["invalid utf-8", undefined],
  ]) {
    const host = newTextHost();
    let res = host.feed(text, 0);
    if (text === undefined) {
      res = host.feedBytes(Uint8Array.from([0x7b, 0xff, 0x7d]), 0);
    }
    assert.deepEqual(res.outputs, [], `${why}: no unsolicited pre-ready response`);
    assert.equal(host.exitCode, 2, why);
    assert.deepEqual(host.bleOperations(), [], `host must not attempt BLE work for ${why}`);
  }
});

test("pre-ready: no host event is ever emitted before a hello (no unsolicited events)", () => {
  const host = newTextHost();
  // Any external BLE stimulus before negotiation must be impossible; the host
  // refuses to synthesize outputs pre-ready.
  assert.throws(() => host.deviceFound("0".repeat(16), { label: "early" }), /pre-ready|phase/);
  assert.equal(host.exitCode, null);
});

test("no overlap flushes incompatible_api then exits 3", () => {
  const host = newTextHost({ supportedApi: [1] });
  const step = host.feed('{"envelope_version":1,"type":"hello","request_id":"h2","api_versions":[2,3]}', 0);
  assert.deepEqual(step.outputs, [
    j({
      envelope_version: 1,
      type: "error",
      request_id: "h2",
      code: "incompatible_api",
      recoverability: "fatal",
      stage: "negotiation",
    }),
  ]);
  assert.equal(host.exitCode, 3);
});

test("duplicate hello after ready gets a correlated invalid_request and the host keeps running", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  const step = host.feed(hello, 1);
  assert.equal(step.outputs.length, 1);
  const err = JSON.parse(step.outputs[0]);
  assert.equal(err.type, "error");
  assert.equal(err.code, "invalid_request");
  assert.equal(err.stage, "post_ready");
  assert.equal(host.exitCode, null);
});

test("a live send request id reused by hello or invalid post-ready input fails closed without a second terminal", () => {
  for (const replacement of [
    () => j({ envelope_version: 1, api_version: 1, type: "hello", request_id: "r-send", api_versions: [1] }),
    () => j({ envelope_version: 1, api_version: 2, type: "send", request_id: "r-send", connection_handle: HANDLES[2], record_base64: "AQ==" }),
  ]) {
    const host = newTextHost({ handles: HANDLES });
    host.feed(hello, 0);
    host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "r-scan", service_uuid: UUID }), 1);
    host.deviceFound(HANDLES[0], { label: "Phone", t_ms: 2 });
    host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "r-connect", device_handle: HANDLES[1] }), 3);
    host.setWritesPending(true);
    host.feed(j({ envelope_version: 1, api_version: 1, type: "send", request_id: "r-send", connection_handle: HANDLES[2], record_base64: "AQ==" }), 4);

    const duplicate = host.feed(replacement(), 5);
    assert.deepEqual(duplicate.outputs, []);
    assert.equal(host.exitCode, 2);
    assert.deepEqual(host.synthesizedErrors(), ["r-send"]);
  }
});

test("post-ready unusable input fails closed silently with exit 2", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  for (const text of ["{ broken", '{"envelope_version":1}', " ".repeat(70000)]) {
    const res = host.feed(text, 1);
    assert.deepEqual(res.outputs, [], "no JSON response without a usable request id/correlation");
    assert.equal(host.exitCode, 2, "fail closed");
  }
});

// ---------- driven request/terminal-event table (OTP2-BRD-020) ----------

test("every request ends in exactly its terminal success event type", () => {
  const host = newTextHost({ handles: HANDLES });
  host.feed(hello, 0);

  const scan = host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "r-scan", service_uuid: UUID }), 1);
  assert.deepEqual(
    scan.outputs,
    [j({ envelope_version: 1, api_version: 1, type: "scan_state", request_id: "r-scan", scan_handle: HANDLES[0], status: "started" })],
  );

  host.deviceFound(HANDLES[0], { label: "Watch", t_ms: 2 });
  const stop = host.feed(j({ envelope_version: 1, api_version: 1, type: "stop_scan", request_id: "r-stop", scan_handle: HANDLES[0] }), 3);
  assert.deepEqual(
    stop.outputs,
    [j({ envelope_version: 1, api_version: 1, type: "scan_state", request_id: "r-stop", scan_handle: HANDLES[0], status: "stopped" })],
  );

  const connect = host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "r-conn", device_handle: HANDLES[1] }), 4);
  assert.deepEqual(
    connect.outputs,
    [j({ envelope_version: 1, api_version: 1, type: "connected", request_id: "r-conn", connection_handle: HANDLES[2] })],
  );

  const send = host.feed(j({ envelope_version: 1, api_version: 1, type: "send", request_id: "r-send", connection_handle: HANDLES[2], record_base64: "AQ==" }), 5);
  assert.deepEqual(send.outputs, [], "sent awaits completed GATT writes");
  assert.deepEqual(host.flushWrites(), [
    "r-send",
  ]);
  const sentStep = host.takeWriteCompletions();
  assert.deepEqual(sentStep.outputs, [
    j({ envelope_version: 1, api_version: 1, type: "sent", request_id: "r-send", connection_handle: HANDLES[2] }),
  ]);

  const disc = host.feed(j({ envelope_version: 1, api_version: 1, type: "disconnect", request_id: "r-disc", connection_handle: HANDLES[2] }), 6);
  assert.deepEqual(
    disc.outputs,
    [j({ envelope_version: 1, api_version: 1, type: "disconnected", request_id: "r-disc", connection_handle: HANDLES[2] })],
  );

  const shut = host.feed(j({ envelope_version: 1, api_version: 1, type: "shutdown", request_id: "r-shut" }), 7);
  assert.deepEqual(shut.outputs, [
    j({ envelope_version: 1, api_version: 1, type: "shutdown_complete", request_id: "r-shut" }),
  ]);
  assert.equal(host.exitCode, 0);
});

test("hello request terminates in ready with the selected api version", () => {
  const host = createBridgeHost({ supportedApi: [1, 2, 3], handles: HANDLES });
  const step = host.feed(j({ envelope_version: 1, type: "hello", request_id: "h", api_version_range: { min: 1, max: 3 } }), 0);
  assert.deepEqual(JSON.parse(step.outputs[0]).api_version, 3);
});

// ---------- handles: unknown, duplicate, post-release, busy, caps ----------

test("unknown, duplicate and post-release handles get correlated failures, never success", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  const unknown = host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c-unknown", device_handle: "b000000000000001" }), 2);
  assert.equal(JSON.parse(unknown.outputs[0]).code, "unknown_handle");
  const stopUnknown = host.feed(j({ envelope_version: 1, api_version: 1, type: "stop_scan", request_id: "s-unknown", scan_handle: "b000000000000002" }), 3);
  assert.equal(JSON.parse(stopUnknown.outputs[0]).code, "unknown_handle");
});

test("post-release handle use fails; a second disconnect is not a second lifecycle event", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
  host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 3);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "disconnect", request_id: "d1", connection_handle: HANDLES[2] }), 4);
  const after = host.feed(j({ envelope_version: 1, api_version: 1, type: "send", request_id: "s-after", connection_handle: HANDLES[2], record_base64: "AQ==" }), 5);
  assert.equal(JSON.parse(after.outputs[0]).code === "unknown_handle", true, "post-release handle use fails");
});

test("connect on an already-connected device fails correlated busy", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
  host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 2);
  const second = host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c2", device_handle: HANDLES[1] }), 3);
  const err = JSON.parse(second.outputs[0]);
  assert.equal(err.type, "error");
  assert.equal(err.code, "busy");
  assert.equal(err.recoverability, "retryable");
});

test("open scans cap at 2 and connections at 32; excess gets cap_exceeded", () => {
  const handles = HANDLES.concat(Array.from({ length: 64 }, (_, i) => `f${String(i).padStart(15, "0")}`));
  const host = createBridgeHost({ supportedApi: [1], handles });
  host.feed(hello, 0);
  for (let i = 0; i < 2; i++) {
    const res = host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: `sc${i}`, service_uuid: UUID }), 1);
    assert.equal(JSON.parse(res.outputs[0]).type, "scan_state");
  }
  const third = host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc2", service_uuid: UUID }), 2);
  assert.equal(JSON.parse(third.outputs[0]).code, "cap_exceeded");
  for (let i = 0; i < 32; i++) {
    const res = host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: `c${i}`, platform_hint: `hint-${i}` }), 3);
    assert.equal(JSON.parse(res.outputs[0]).type, "connected", `connect ${i}`);
  }
  const thirtyThird = host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c32", platform_hint: "hint-32" }), 4);
  assert.equal(JSON.parse(thirtyThird.outputs[0]).code, "cap_exceeded");
  assert.equal(host.state().open_connections, 32);
  assert.deepEqual(host.bleOperations().filter((op) => op.kind === "connect").length, 32, "no BLE connection attempt beyond the cap");
});

// ---------- backpressure: fail-closed overflow policy (OTP2-BRD-030a) ----------

test("frozen stdout pressure fails closed at the queue ceiling; synthesis covers queued completions; sustained 10k pressure stays closed and bounded", () => {
  const host = createBridgeHost({ supportedApi: [1], handles: HANDLES });
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
  const conn = host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 3);
  assert.equal(conn.outputs.length, 1);
  host.setFrozen(true);

  // 128 completions fit exactly; the 129th outgoing message triggers the close
  for (let i = 0; i < 128; i++) {
    const res = host.feed(sendText(i, HANDLES[2]), 4);
    assert.deepEqual(res.outputs, [], "frozen stdout emits nothing");
    host.flushWrites();
  }
  let st = host.state();
  assert.equal(st.queue_messages, 128, "queue retains exactly the 128 deliverable completions");
  assert.ok(st.queue_bytes <= 262144, `queue bytes ${st.queue_bytes} within the 256 KiB ceiling`);
  assert.equal(st.queued_completions, 128, "each queued completion holds its request id");

  // 129th outbound message: overflow -> fail closed, no drop-and-continue
  host.feed(sendText(128, HANDLES[2]), 4);
  host.flushWrites();
  st = host.state();
  assert.equal(host.exitCode, 4, "the overflow exit is its own fail-closed exit code (OTP2-BRD-030a/032)");
  assert.equal(st.queue_messages, 0, "nothing is retained past the close");
  assert.equal(st.queue_bytes, 0);
  assert.equal(st.outstanding, 0);
  assert.equal(st.open_scans, 0, "fail close released every scan");
  assert.equal(st.open_connections, 0, "fail close released every link");
  assert.equal(st.discovered_devices, 0, "fail close dropped retained state");
  assert.equal(st.queued_completions, 0);
  const synth = host.synthesizedErrors();
  assert.equal(synth.length, 129, "synthesis covers 128 queued completion ids + the overflowing request");
  assert.deepEqual(synth, Array.from({ length: 129 }, (_, i) => `s${i}`));
  assert.ok(!synth.includes("nope"));

  // sustained pressure beyond 10k requests: closed host is a bounded no-op
  const snapshot = host.state();
  for (let i = 0; i < 10_001; i++) {
    const res = host.feed(sendText(i, HANDLES[2]), 4);
    assert.deepEqual(res.outputs, []);
  }
  assert.deepEqual(host.state(), snapshot, "closed host retains nothing and does not grow");
  assert.equal(host.synthesizedErrors().length, 129);
});

test("fail close during frozen output synthesizes queued terminals, still-pending requests and the overflowing request, all bounded", () => {
  const host = createBridgeHost({ supportedApi: [1], handles: HANDLES });
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
  host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 3);
  host.setFrozen(true);
  host.setWritesPending(true);

  // 64 sends stay outstanding (cap); 65th is a correlated cap_exceeded error
  // terminal queued while frozen; queue fills with error terminals, then
  // overflows on the 129th queued message
  for (let i = 0; i < 200; i++) {
    host.feed(sendText(i, HANDLES[2]), 4);
  }
  assert.equal(host.exitCode, 4, "the overflow exit is its own fail-closed exit code (OTP2-BRD-030a/032)");
  const synth = host.synthesizedErrors();
  // union: 128 queued error terminals (ids s64..s191) + 64 outstanding (s0..s63) + overflowing (s192)
  assert.equal(synth.length, 193);
  assert.equal(synth[0], "s64");
  assert.equal(synth[127], "s191");
  assert.equal(synth[128], "s0");
  assert.equal(synth[191], "s63");
  assert.equal(synth[192], "s192");
  const st = host.state();
  assert.equal(st.outstanding, 0);
  assert.equal(st.queue_messages, 0);
  assert.equal(st.queued_completions, 0, "no unbounded dropped-id state is retained");
});

test("uncorrelatable post-ready input exits after the full orderly release and synthesizes its outstanding requests", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
  host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 3);
  host.setWritesPending(true);
  host.feed(sendText(0, HANDLES[2]), 4);
  const outputsBefore = host.textOutputs().length;
  const res = host.feed("{ uncorrelatable", 5);
  assert.deepEqual(res.outputs, [], "no JSON response for uncorrelatable input");
  assert.equal(host.exitCode, 2);
  const st = host.state();
  assert.equal(st.open_scans, 0, "release stops every scan");
  assert.equal(st.open_connections, 0, "release disconnects every link");
  assert.equal(st.discovered_devices, 0, "release drops retained state");
  assert.equal(st.outstanding, 0);
  assert.deepEqual(host.synthesizedErrors(), ["s0"], "the unresolved send is synthesized once");
  assert.equal(host.textOutputs().length, outputsBefore, "release emits nothing further");
  // sustained feeds after the exit stay no-ops
  for (let i = 0; i < 100; i++) assert.deepEqual(host.feed(sendText(i, HANDLES[2]), 6).outputs, []);
  host.setWritesPending(false);
  host.flushWrites();
  assert.equal(host.takeWriteCompletions().outputs.length, 0, "released writes never report sent");
});

test("a reuse of a live request id fails closed without a second correlated error", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
  host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 3);
  host.setWritesPending(true);
  host.feed(sendText(0, HANDLES[2]), 4); // s0 outstanding
  const outputsBefore = host.textOutputs().length;
  const res = host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "s0", service_uuid: UUID }), 5);
  assert.deepEqual(res.outputs, [], "no second correlated error for the ambiguous id");
  assert.equal(host.exitCode, 2, "fail closed");
  assert.ok(host.textOutputs().length === outputsBefore, "nothing is emitted");
  assert.deepEqual(host.synthesizedErrors(), ["s0"], "outstanding request synthesized once");
});

test("request id reuse after its terminal was delivered is accepted; reuse before delivery fails closed", () => {
  const mkReadyHost = () => {
    const host = newTextHost();
    host.feed(hello, 0);
    host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
    host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
    host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 3);
    return host;
  };
  const ok = mkReadyHost();
  const first = ok.feed(sendText(0, HANDLES[2]), 4);
  assert.equal(first.outputs.length, 0, "send awaits writes");
  ok.flushWrites();
  assert.equal(ok.takeWriteCompletions().outputs.length, 1, "terminal delivered");
  const reused = ok.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "s0", service_uuid: UUID }), 5);
  assert.equal(JSON.parse(reused.outputs[0]).type, "scan_state", "reuse after delivery is legal");
  assert.equal(JSON.parse(reused.outputs[0]).request_id, "s0");

  const reserved = mkReadyHost();
  reserved.setFrozen(true);
  reserved.feed(sendText(0, HANDLES[2]), 4);
  reserved.flushWrites(); // completion queued, not delivered: id stays reserved
  const res = reserved.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "s0", service_uuid: UUID }), 5);
  assert.deepEqual(res.outputs, [], "no second correlated error while the completion is queued");
  assert.equal(reserved.exitCode, 2, "fail closed until the completion is delivered");
});

test("discovery cache is bounded at 1024 current handles across scans with FIFO eviction", () => {
  const host = createBridgeHost({
    supportedApi: [1],
    handles: HANDLES,
  });
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc-a", service_uuid: UUID }), 1);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc-b", service_uuid: UUID }), 2);
  const scanA = HANDLES[0];
  const scanB = HANDLES[1];
  for (let i = 0; i < 1024; i++) {
    host.deviceFound(scanA, { label: `A${i}`, t_ms: i * 100 + 10, device_handle: `d${String(i).padStart(15, "0")}` });
  }
  assert.equal(host.state().discovered_devices, 1024, "at the cache bound");
  assert.equal(host.state().scan_events_dropped, 0, "independent of the per-scan event cap");
  for (let i = 0; i < 1024; i++) {
    host.deviceFound(scanB, { label: `B${i}`, t_ms: 200000 + i * 100, device_handle: `e${String(i).padStart(15, "0")}` });
  }
  const st = host.state();
  assert.equal(st.discovered_devices, 1024, "FIFO eviction keeps the cache at the bound");
  assert.equal(st.scan_events_dropped, 0);
  // oldest scan-A handle was evicted: connecting to it fails unknown_handle
  const evicted = host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c-evict", device_handle: "d" + "0".repeat(15) }), 500000);
  assert.equal(JSON.parse(evicted.outputs[0]).code, "unknown_handle");
  // the newest scan-B handle is still current: connect succeeds
  const fresh = host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c-fresh", device_handle: "e" + String(1023).padStart(15, "0") }), 500001);
  assert.equal(JSON.parse(fresh.outputs[0]).type, "connected");
});

test("host counters mirror the limits inventory (incl. the discovery cache)", () => {
  const limits = limitsMap();
  assert.equal(limits.bridge_discovered_devices_max, DISCOVERY_CACHE_MAX);
  assert.equal(limits.bridge_output_queue_messages_max, QUEUE_MESSAGES_MAX);
  assert.equal(limits.bridge_output_queue_bytes_max, QUEUE_BYTES_MAX);
  assert.equal(limits.bridge_outstanding_requests_max, OUTSTANDING_MAX);
  assert.equal(limits.bridge_open_scans_max, OPEN_SCANS_MAX);
  assert.equal(limits.bridge_open_connections_max, OPEN_CONNECTIONS_MAX);
  assert.equal(limits.bridge_device_found_rate_max, FOUND_RATE_PER_SECOND);
  assert.equal(limits.bridge_device_found_per_scan_max, FOUND_PER_SCAN_MAX);
  assert.equal(limits.bridge_request_timeout_seconds * 1000, REQUEST_TIMEOUT_MS);
  assert.equal(limits.bridge_message_max_bytes, MESSAGE_MAX_BYTES);
  assert.equal(limits.bridge_record_max_bytes, RECORD_MAX_BYTES);
  assert.equal(limits.bridge_advertisement_max_bytes, ADVERT_MAX_BYTES);
});

function limitsMap() {
  return Object.fromEntries(
    JSON.parse(readFileSync(join(v1Root, "limits.json"), "utf8")).limits.map((l) => [l.name, l.value]),
  );
}

function sendText(i, connectionHandle) {
  return j({
    envelope_version: 1,
    api_version: 1,
    type: "send",
    request_id: `s${i}`,
    connection_handle: connectionHandle,
    record_base64: "AQ==",
  });
}

test("outstanding requests cap at 64 while writes are pending; 65th gets cap_exceeded", () => {
  const host = newTextHost({ handles: HANDLES });
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
  host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 2);
  host.setWritesPending(true);
  for (let i = 0; i < 64; i++) {
    const res = host.feed(j({ envelope_version: 1, api_version: 1, type: "send", request_id: `s${i}`, connection_handle: HANDLES[2], record_base64: "AQ==" }), 3);
    assert.deepEqual(res.outputs, [], `send ${i} awaits writes`);
  }
  const sixtyFifth = host.feed(j({ envelope_version: 1, api_version: 1, type: "send", request_id: "s65", connection_handle: HANDLES[2], record_base64: "AQ==" }), 4);
  assert.equal(JSON.parse(sixtyFifth.outputs[0]).code, "cap_exceeded");
  host.setWritesPending(false);
  host.flushWrites();
  const completions = host.takeWriteCompletions();
  assert.equal(completions.outputs.length, 64, "only the 64 accepted sends complete");
  assert.ok(!JSON.stringify(completions.outputs).includes("s65"));
});

test("sent is emitted only after all GATT writes complete; disconnect before writes never misreports", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
  host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 2);
  host.setWritesPending(true);
  const send = host.feed(j({ envelope_version: 1, api_version: 1, type: "send", request_id: "s1", connection_handle: HANDLES[2], record_base64: "AQ==" }), 3);
  assert.deepEqual(send.outputs, []);
  const disc = host.feed(j({ envelope_version: 1, api_version: 1, type: "disconnect", request_id: "d1", connection_handle: HANDLES[2] }), 4);
  const types = disc.outputs.map((t) => JSON.parse(t).type);
  assert.equal(types[0], "error", "in-flight send terminated with correlated error first");
  assert.equal(types[1], "disconnected");
  assert.equal(JSON.parse(disc.outputs[0]).request_id, "s1");
  host.setWritesPending(false);
  host.flushWrites();
  const tail = host.takeWriteCompletions();
  assert.equal(tail.outputs.length, 0, "no sent after the connection was released: writes never completed");
});

test("per-request timeout fires at 30s for non-scan requests with a correlated timeout error", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  host.deviceFound(HANDLES[0], { label: "W", t_ms: 2 });
  host.feed(j({ envelope_version: 1, api_version: 1, type: "connect", request_id: "c1", device_handle: HANDLES[1] }), 3);
  host.setWritesPending(true);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "send", request_id: "s1", connection_handle: HANDLES[2], record_base64: "AQ==" }), 10);
  host.setWritesPending(false);
  const later = host.feed(j({ envelope_version: 1, api_version: 1, type: "app_noop", request_id: "noop" }), 30011);
  const err = JSON.parse(later.outputs[0]);
  assert.equal(err.type, "error");
  assert.equal(err.code, "timeout");
  assert.equal(err.request_id, "s1");
  host.flushWrites();
  const tail = host.takeWriteCompletions();
  assert.equal(tail.outputs.length, 0, "timed-out request never reports sent");
});

// ---------- device discovery rate and cap counters ----------

test("device_found rate: at most 64 per rolling second; excess is dropped, not queued", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  let emitted = 0;
  for (let i = 0; i < 65; i++) {
    const res = host.deviceFound(HANDLES[0], {
      label: `d${i}`,
      t_ms: 100,
      device_handle: `c${String(i).padStart(15, "0")}`,
    });
    emitted += res.outputs.length;
  }
  assert.equal(host.state().rate_dropped, 1, "65th device_found in one rolling second dropped");
  assert.equal(emitted, 64, "at most 64 device_found per rolling second reach the wire");
});

test("device_found per-scan cap: 1024 then drop", () => {
  const host = newTextHost();
  host.feed(hello, 0);
  host.feed(j({ envelope_version: 1, api_version: 1, type: "start_scan", request_id: "sc", service_uuid: UUID }), 1);
  for (let i = 0; i < 1024; i++) {
    host.deviceFound(HANDLES[0], {
      label: `d${i}`,
      t_ms: i * 100, // spread out so the per-second rate never fires
      device_handle: `c${String(i).padStart(15, "0")}`,
    });
  }
  let tail = 0;
  for (let i = 0; i < 5; i++) {
    tail += host.deviceFound(HANDLES[0], {
      label: `x${i}`,
      t_ms: 200000,
      device_handle: `e${String(i).padStart(15, "0")}`,
    }).outputs.length;
  }
  assert.equal(tail, 0, "beyond 1024 per scan everything is dropped");
  assert.equal(host.state().scan_events_dropped, 5);
});

// ---------- committed bridge fixtures: generic executors ----------

test("committed bridge fixtures load through the shared loader", () => {
  const fixtures = loadFixtures(v1Root).filter((f) => f.category === "bridge");
  assert.ok(fixtures.length >= 40, `expected the full bridge fixture set, got ${fixtures.length}`);
  assert.ok(fixtures.some((f) => f.input.kind === "envelope"));
  assert.ok(fixtures.some((f) => f.input.kind === "negotiation"));
  assert.ok(fixtures.some((f) => f.input.kind === "trace"));
});

test("committed envelope fixtures: valid texts accept, invalid reject at declared stage", () => {
  for (const f of loadFixtures(v1Root).filter((x) => x.category === "bridge" && x.input.kind === "envelope")) {
    const api = f.input.api_version ?? null;
    const res =
      f.input.direction === "request"
        ? validateEnvelope(f.input.text, f.input.phase, api)
        : validateEvent(f.input.text, api);
    if (f.expected.accepted === true) {
      assertAccept(res, `fixture ${f.id}`);
    } else {
      assertRejectStage(res, f.expected.stage, `fixture ${f.id}`);
    }
  }
});

test("committed negotiation fixtures: highest-overlap selection is recomputed, not trusted", () => {
  for (const f of loadFixtures(v1Root).filter((x) => x.category === "bridge" && x.input.kind === "negotiation")) {
    const selected = selectApi(f.input.offer, f.input.host_supported_api);
    assert.equal(
      selected,
      f.expected.selected_api,
      `fixture ${f.id}: recomputed selection ${selected} != expected ${f.expected.selected_api}`,
    );
  }
});

test("committed trace fixtures: positive ones are driven end-to-end and independently validated", () => {
  for (const f of loadFixtures(v1Root).filter((x) => x.category === "bridge" && x.input.kind === "trace")) {
    if (f.expected.accepted === true) {
      driveAndAssertTrace(f);
      const verdict = checkBridgeTrace(f.input);
      assert.equal(verdict.accepted, true, `fixture ${f.id} (trace): ${verdict.error}`);
    } else {
      const verdict = checkBridgeTrace(f.input);
      assert.equal(verdict.accepted, false, `fixture ${f.id}: negative trace unexpectedly accepted`);
      assert.ok(
        verdict.error.includes(`stage=${f.expected.stage}`),
        `fixture ${f.id}: expected stage=${f.expected.stage}, got ${verdict.error}`,
      );
    }
  }
});

// Drive the trace: connector steps feed the host; synthetic stimuli drive
// host-emitted events; fixture host steps are the independent expected stream.
// Nothing here trusts the fixture's host text — the host recomputes every byte.
function driveAndAssertTrace(fixture) {
  const input = fixture.input;
  const host = createBridgeHost({
    supportedApi: input.host_supported_api ?? [1],
    handles: input.handles ?? [],
  });
  const expectedHostTexts = [];
  let portClosed = false;
  for (let i = 0; i < input.steps.length; i++) {
    const step = input.steps[i];
    if (step.t_ms === undefined) {
      throw new Error(`trace ${fixture.id} step ${i}: driving requires t_ms`);
    }
    if (step.from === "connector") {
      let res;
      if (portClosed) {
        assert.fail(`trace ${fixture.id}: connector step after port loss must not exist`);
      }
      res = host.feed(step.text, step.t_ms);
      void res;
    } else if (step.from === "host") {
      expectedHostTexts.push(step.text);
    } else if (step.from === "synthetic") {
      switch (step.synthetic) {
        case "device_found":
          host.deviceFound(step.scan_handle, {
            label: step.label,
            platform_hint: step.platform_hint,
            advertisement: step.advertisement,
            t_ms: step.t_ms,
          });
          break;
        case "link_loss":
          host.externalDisconnect(step.connection_handle, step.t_ms);
          break;
        case "scan_expired":
          host.scanExpired(step.scan_handle, step.t_ms);
          break;
        case "writes_complete":
          host.flushWrites();
          break;
        case "port_closed":
          host.portClosed(step.t_ms);
          portClosed = true;
          break;
        case "stdout_close":
          host.stdoutClosed();
          break;
        case "port_error":
          // connector-side synthesis; asserted against host.synthesizedErrors() below
          break;
        default:
          throw new Error(`trace ${fixture.id}: driver cannot run step ${i}`);
      }
    }
  }
  assert.deepEqual(
    expectedHostTexts,
    host.textOutputs(),
    `fixture ${fixture.id}: driven host stream differs from expected host steps`,
  );
  if (portClosed) {
    assert.deepEqual(
      expectedPortErrorIds(fixture.input),
      host.synthesizedErrors(),
      `fixture ${fixture.id}: synthesized port-loss failures must cover exactly the outstanding requests`,
    );
  } else {
    assert.deepEqual(host.synthesizedErrors(), []);
  }
}

function expectedPortErrorIds(input) {
  return input.steps
    .filter((s) => s.from === "synthetic" && s.synthetic === "port_error")
    .map((s) => s.request_id);
}
