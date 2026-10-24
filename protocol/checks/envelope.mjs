// Test-only Native Messaging envelope validation and bridge lifecycle checks
// for the Veles protocol contract (OTP-02 Plan Task 3). Never imported by
// product code; not a reference codec and not a competing bridge
// implementation. Normative source: docs/protocol/veles-v1.md section 10
// (Native Messaging envelope v1, OTP2-BRD-*) via protocol/v1/envelope.schema.json.

import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import { parseJsonStrict } from "./fixture.mjs";

export { parseJsonStrict };

// ---------- fixed ceilings (mirrors of protocol/v1/limits.json, see sec-bridge-bounds) ----------

export const MESSAGE_MAX_BYTES = 65536; // bridge_message_max_bytes
export const RECORD_MAX_BYTES = 5120; // bridge_record_max_bytes (decoded)
export const ADVERT_MAX_BYTES = 256; // bridge_advertisement_max_bytes (decoded)
export const API_VERSION_MAX = 1048576;
export const OUTSTANDING_MAX = 64;
export const OPEN_SCANS_MAX = 2;
export const OPEN_CONNECTIONS_MAX = 32;
export const REQUEST_TIMEOUT_MS = 30_000;
export const SCAN_DURATION_MAX_MS = 300_000;
export const FOUND_RATE_PER_SECOND = 64;
export const FOUND_PER_SCAN_MAX = 1024;
export const QUEUE_MESSAGES_MAX = 128;
export const QUEUE_BYTES_MAX = 262144;
export const DISCOVERY_CACHE_MAX = 1024; // currently discovered device handles per process
export const API_SUPPORTED = [1]; // production supported set (OTP2-CON-003)

export const REQUEST_TYPES = new Set([
  "hello",
  "start_scan",
  "stop_scan",
  "connect",
  "disconnect",
  "send",
  "shutdown",
]);
export const EVENT_TYPES = new Set([
  "ready",
  "device_found",
  "scan_state",
  "connected",
  "disconnected",
  "sent",
  "message",
  "shutdown_complete",
  "error",
]);

class StageError extends Error {
  constructor(stage, message) {
    super(`stage=${stage}: ${message}`);
    this.stage = stage;
  }
}

function fail(stage, message) {
  return { accepted: false, error: `stage=${stage}: ${message}`, fields: null, stage };
}

// ---------- envelope schema (strict Ajv 2020-12) ----------

const checksDir = dirname(fileURLToPath(import.meta.url));
const envelopeSchemaPath = join(checksDir, "..", "v1", "envelope.schema.json");

const ajv = new Ajv2020({ strict: true, allErrors: true });
let validateMessage = null;

function schemaValidator() {
  if (!validateMessage) {
    validateMessage = ajv.compile(JSON.parse(readFileSync(envelopeSchemaPath, "utf8")));
  }
  return validateMessage;
}

function ajvErrors(errors) {
  const list = (errors ?? []).slice(0, 3).map((e) => `${e.instancePath || "/"} ${e.message}`);
  return `envelope schema rejected the message: ${list.join("; ")}`;
}

// ---------- strict base64 (RFC 4648 alphabet, padding, zero pad bits) ----------

const B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const B64_REVERSE = new Map([...B64_ALPHABET].map((ch, i) => [ch, i]));
const B64_SHAPE = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

export function encodeBase64(bytes) {
  return Buffer.from(bytes).toString("base64");
}

function decodeBase64Bytes(text, where) {
  if (typeof text !== "string" || !B64_SHAPE.test(text)) {
    throw new StageError("base64", `${where} is not canonical RFC 4648 base64 with padding`);
  }
  if (text.length % 4 !== 0) {
    throw new StageError("base64", `${where} has a non-canonical unpadded length`);
  }
  let padding = 0;
  if (text.endsWith("==")) padding = 2;
  else if (text.endsWith("=")) padding = 1;
  // canonical form requires the zero tail (pad) bits to actually be zero
  if (padding > 0) {
    // the last data character carries the leftover bits:
    // padding 2 -> 4 overflow bits, padding 1 -> 2 overflow bits
    const idx = text.length - padding - 1;
    const kept = B64_REVERSE.get(text[idx] ?? "?");
    if (kept === undefined) throw new StageError("base64", `${where} has a malformed payload character`);
    const padBits = padding === 1 ? kept & 0x03 : kept & 0x0f;
    if (padBits !== 0) {
      throw new StageError("base64", `${where} has nonzero trailing (pad) bits`);
    }
  }
  const out = new Uint8Array((text.length / 4) * 3 - padding);
  let o = 0;
  let carry = 0;
  let bits = 0;
  for (const ch of text) {
    if (ch === "=") break;
    const v = B64_REVERSE.get(ch);
    if (v === undefined) throw new StageError("base64", `${where} has a malformed payload character`);
    carry = (carry << 6) | v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[o++] = (carry >>> bits) & 0xff;
    }
  }
  return out;
}

export function decodeBase64(text, bounds = {}) {
  const bytes = decodeBase64Bytes(text, "base64 value");
  const min = bounds.min ?? 0;
  const max = bounds.max ?? Infinity;
  if (bytes.length < min || bytes.length > max) {
    throw new StageError(
      "bounds",
      `base64 value decodes to ${bytes.length} bytes, allowed ${min}..${max === Infinity ? "unbounded" : max}`,
    );
  }
  return bytes;
}

// ---------- framing (the transport writes u32le length || UTF-8 JSON; these are the text rules) ----------

const utf8Decoder = new TextDecoder("utf-8", { fatal: true });

// OTP2-BRD-002a: every string bound in the envelope is a UTF-8 BYTE length;
// the byte-exact checks live here (JSON Schema's code-unit maxLength is only
// the outer shape gate).
export function checkUtf8ByteBounds(value, minBytes, maxBytes, where) {
  if (typeof value !== "string") {
    throw new StageError("schema", `${where} must be a string`);
  }
  let bytes;
  try {
    bytes = new TextEncoder().encode(value);
  } catch {
    throw new StageError("utf8", `${where} is not valid UTF-8`);
  }
  if (bytes.length < minBytes || bytes.length > maxBytes) {
    throw new StageError(
      "length",
      `${where} is ${bytes.length} UTF-8 byte(s), allowed ${minBytes}..${maxBytes} (OTP2-BRD-002a)`,
    );
  }
  return true;
}

export function decodeHostFrame(bytes) {
  if (!(bytes instanceof Uint8Array)) {
    return fail("framing", "frame input must be a Uint8Array");
  }
  const length = bytes.length;
  if (length === 0) return fail("framing", "empty frame (byte_length must be 1..65536, OTP2-BRD-001)");
  if (length > MESSAGE_MAX_BYTES) {
    return fail(
      "length",
      `frame of ${length} bytes exceeds the ${MESSAGE_MAX_BYTES}-byte message limit (OTP2-BRD-001)`,
    );
  }
  let text;
  try {
    text = utf8Decoder.decode(bytes);
  } catch {
    return fail("utf8", "frame is not valid UTF-8 (OTP2-BRD-002)");
  }
  return { accepted: true, error: null, text };
}

// ---------- validateEnvelope: connector -> host requests ----------

export function validateEnvelope(jsonText, phase, apiVersion) {
  if (phase !== "pre" && phase !== "ready") {
    return fail("phase", `phase must be "pre" or "ready", got ${JSON.stringify(phase)}`);
  }
  if (phase === "pre" && apiVersion !== null) {
    return fail("phase", "selected api_version must be null before ready");
  }
  if (typeof jsonText !== "string") {
    return fail("framing", `message must arrive as JSON text, got ${typeof jsonText}`);
  }
  if (jsonText.length === 0) return fail("framing", "empty message text");
  if (jsonText.length > MESSAGE_MAX_BYTES) {
    return fail(
      "length",
      `message of ${jsonText.length} bytes exceeds the ${MESSAGE_MAX_BYTES}-byte message limit (OTP2-BRD-001)`,
    );
  }
  let msg;
  try {
    msg = parseJsonStrict(jsonText, "envelope message");
  } catch (e) {
    if (/duplicate JSON key/.test(e.message)) return fail("duplicate_key", e.message);
    return fail("json", `not parseable JSON: ${e.message.slice(0, 120)}`);
  }
  if (msg === null || typeof msg !== "object" || Array.isArray(msg)) {
    return fail("json", "message must be a JSON object");
  }
  if (msg.envelope_version !== 1) {
    return fail("version", `envelope_version must be 1, got ${JSON.stringify(msg.envelope_version)}`);
  }
  const isRequest = REQUEST_TYPES.has(msg.type);
  const isEvent = EVENT_TYPES.has(msg.type);
  if (!isRequest && !isEvent) {
    const validate = schemaValidator();
    if (!validate(msg)) return fail("schema", ajvErrors(validate.errors));
    return fail("schema", `unknown message type ${JSON.stringify(msg.type)}`);
  }
  if (!isRequest) {
    return fail(
      "phase",
      `${phase === "pre" ? "pre-ready" : "post-ready"} input must be a request, not host event type ${JSON.stringify(msg.type)}`,
    );
  }
  if (msg.request_id === undefined || !/^[A-Za-z0-9._-]{1,64}$/.test(String(msg.request_id))) {
    return fail("schema", "request_id missing or malformed (not usable for correlation)");
  }
  if (phase === "pre") {
    if (msg.api_version !== undefined) {
      return fail(
        "phase",
        `pre-ready message must not carry api_version: no version has been negotiated (OTP2-BRD-011)`,
      );
    }
    if (msg.type !== "hello") {
      return fail(
        "phase",
        `only hello is legal before negotiation, got type ${JSON.stringify(msg.type)} (correlated error, OTP2-BRD-010)`,
      );
    }
  } else {
    if (msg.api_version !== apiVersion) {
      return fail(
        "phase",
        `post-ready message must carry the negotiated api_version (${apiVersion}), got ${JSON.stringify(msg.api_version)} (OTP2-BRD-011)`,
      );
    }
    if (msg.type === "hello") {
      return fail(
        "phase",
        "hello is negotiated exactly once; repeated hello after ready is rejected (OTP2-BRD-010a)",
      );
    }
  }
  const validate = schemaValidator();
  if (!validate(msg)) return fail("schema", ajvErrors(validate.errors));
  try {
    checkOffer(msg);
    if (typeof msg.record_base64 === "string") {
      decodeBase64(msg.record_base64, { min: 1, max: RECORD_MAX_BYTES });
    }
  } catch (e) {
    return { accepted: false, error: e.message, fields: null };
  }
  return { accepted: true, error: null, fields: msg };
}

function checkOffer(msg) {
  if (msg.type !== "hello") return;
  if (msg.api_versions !== undefined) {
    if (msg.api_versions.length > 16) {
      throw new StageError(
        "negotiation",
        `api_versions carries ${msg.api_versions.length} entries, the limit is 16 (OTP2-BRD-010a)`,
      );
    }
    if (msg.api_versions.some((v, i, a) => i > 0 && a[i - 1] >= v)) {
      throw new StageError(
        "negotiation",
        `api_versions must be strictly ascending, got ${JSON.stringify(msg.api_versions)}`,
      );
    }
  } else if (msg.api_version_range !== undefined) {
    const { min, max } = msg.api_version_range;
    if (min > max) {
      throw new StageError("negotiation", `api_version_range min (${min}) exceeds max (${max})`);
    }
  }
}

// ---------- validateEvent: host -> connector events ----------

export function validateEvent(jsonText, apiVersion = null) {
  if (typeof jsonText !== "string") return fail("framing", "event must be JSON text");
  if (jsonText.length === 0) return fail("framing", "empty event text");
  if (jsonText.length > MESSAGE_MAX_BYTES) {
    return fail(
      "length",
      `event of ${jsonText.length} bytes exceeds the ${MESSAGE_MAX_BYTES}-byte message limit (OTP2-BRD-001)`,
    );
  }
  let msg;
  try {
    msg = parseJsonStrict(jsonText, "event");
  } catch (e) {
    if (/duplicate JSON key/.test(e.message)) return fail("duplicate_key", e.message);
    return fail("json", `not parseable JSON: ${e.message.slice(0, 120)}`);
  }
  if (msg === null || typeof msg !== "object" || Array.isArray(msg)) {
    return fail("json", "event must be a JSON object");
  }
  if (msg.envelope_version !== 1) {
    return fail("version", `envelope_version must be 1, got ${JSON.stringify(msg.envelope_version)}`);
  }
  if (!EVENT_TYPES.has(msg.type)) {
    if (REQUEST_TYPES.has(msg.type)) {
      return fail(
        "phase",
        `type ${JSON.stringify(msg.type)} is a connector request; host events must use event types (OTP2-BRD-020/021)`,
      );
    }
    return fail("schema", `unknown message type ${JSON.stringify(msg.type)}`);
  }
  if (apiVersion !== null && msg.api_version !== apiVersion) {
    return fail(
      "phase",
      `post-ready event must carry the negotiated api_version (${apiVersion}), got ${JSON.stringify(msg.api_version)} (OTP2-BRD-011)`,
    );
  }
  if (apiVersion === null && msg.type !== "ready" && msg.api_version !== undefined) {
    return fail("phase", "api_version present but no version has been negotiated");
  }
  const validate = schemaValidator();
  if (!validate(msg)) return fail("schema", ajvErrors(validate.errors));
  try {
    if (msg.type === "ready") {
      const defined = new Set(["veles_record_v1"]);
      for (const cap of msg.capabilities) {
        if (!defined.has(cap)) {
          throw new StageError(
            "negotiation",
            `ready carries unknown capability ${JSON.stringify(cap)} — fail closed (OTP2-BRD-010b)`,
          );
        }
      }
      checkUtf8ByteBounds(msg.bridge_version, 1, 64, "ready bridge_version");
    }
    if (typeof msg.label === "string") {
      checkUtf8ByteBounds(msg.label, 1, 64, "device label");
    }
    if (typeof msg.platform_hint === "string") {
      checkUtf8ByteBounds(msg.platform_hint, 1, 256, "platform_hint");
    }
    if (typeof msg.stage === "string") {
      checkUtf8ByteBounds(msg.stage, 1, 32, "error stage");
    }
    if (typeof msg.record_base64 === "string") {
      decodeBase64(msg.record_base64, { min: 1, max: RECORD_MAX_BYTES });
    }
    if (typeof msg.advertisement === "string") {
      decodeBase64(msg.advertisement, { min: 1, max: ADVERT_MAX_BYTES });
    }
  } catch (e) {
    return { accepted: false, error: e.message, fields: null };
  }
  return { accepted: true, error: null, fields: msg };
}

// ---------- negotiation: highest overlapping bridge API (OTP2-BRD-010b) ----------

export function selectApi(offer, supportedApi) {
  const offered =
    offer.api_versions !== undefined
      ? new Set(offer.api_versions)
      : new Set(
          Array.from(
            { length: Math.max(0, offer.api_version_range.max - offer.api_version_range.min + 1) },
            (_, i) => offer.api_version_range.min + i,
          ).filter((v) => v >= 1 && v <= API_VERSION_MAX),
        );
  let best = null;
  for (const v of new Set(supportedApi)) {
    if (offered.has(v) && (best === null || v > best)) best = v;
  }
  return best;
}

// ---------- test-only bridge host: actual request/event negotiation ----------

const BRIDGE_VERSION = "veles-bridge-test-1.0.0";
const PLATFORM = "windows";
const HANDLE_RE = /^[0-9a-f]{16}$/;
const REQUEST_ID_RE = /^[A-Za-z0-9._-]{1,64}$/;

export function createBridgeHost({ supportedApi = API_SUPPORTED, handles = [], seed = 0 } = {}) {
  const state = {
    phase: "pre",
    apiVersion: null,
    exitCode: null,
    outstanding: new Map(), // request_id -> {type, t, connection_handle?}
    scans: new Map(), // scan_handle -> {found, open, rateTimes}
    connections: new Map(), // connection_handle -> {open}
    devices: new Map(), // device_handle -> {label, hint, connected} (bounded cache)
    queue: [],
    queueBytes: 0,
    pendingCompletionIds: new Set(), // ids whose terminal event is queued, not yet delivered
    rateDropped: 0,
    scanEventsDropped: 0,
    emitted: [],
    portClosed: false,
    portSynthesized: [],
    frozen: false,
    writesPending: false,
    pendingWrites: [],
    lastCompletionOutputs: [],
    ble: [],
    handleIdx: 0,
  };

  // Union of every request id whose terminal outcome the connector may never
  // see on stdout: completions queued-but-undelivered, still-outstanding
  // requests, plus the id of the request whose bytes triggered a fail close.
  // Bounded by construction (outstanding cap + queue ceiling), no growing
  // dropped-id list is retained anywhere.
  function snapshotSynthesis(extraIds = []) {
    const union = [];
    const seen = new Set();
    for (const id of [
      ...state.pendingCompletionIds,
      ...state.outstanding.keys(),
      ...extraIds,
    ]) {
      if (typeof id === "string" && id !== "" && !seen.has(id)) {
        seen.add(id);
        union.push(id);
      }
    }
    return union;
  }

  // Fail-closed close (OTP2-BRD-030a/BRD-003): release everything and close
  // the port. Already-delivered output keeps its order; nothing further is
  // emitted; the connector synthesizes the unresolved requests once (the
  // synthesis snapshot is taken BEFORE any state clearing).
  function failClose(exitCode, extraSynthesizedIds = []) {
    state.portSynthesized = snapshotSynthesis(extraSynthesizedIds);
    for (const requestId of state.outstanding.keys()) cancelPendingWrite(requestId);
    state.outstanding.clear();
    state.scans.clear();
    state.connections.clear();
    state.devices.clear();
    state.pendingCompletionIds.clear();
    state.queue.length = 0;
    state.queueBytes = 0;
    state.lastCompletionOutputs = [];
    state.portClosed = true;
    state.exitCode = exitCode;
  }

  function releaseAllSilently() {
    // orderly release with NO further JSON (uncorrelatable-input exit, EOF):
    // stop scans, disconnect every link, drop handles and retained state;
    // the connector synthesizes the unresolved requests once
    state.portSynthesized = snapshotSynthesis();
    for (const requestId of state.outstanding.keys()) cancelPendingWrite(requestId);
    state.outstanding.clear();
    state.scans.clear();
    state.connections.clear();
    state.devices.clear();
    state.pendingCompletionIds.clear();
  }

  const textLen = (t) => Buffer.byteLength(t, "utf8");

  function nextHandle() {
    if (state.handleIdx < handles.length) {
      const h = handles[state.handleIdx++];
      if (!HANDLE_RE.test(h)) throw new Error(`test bug: handle ${h} violates ^[0-9a-f]{16}$`);
      return h;
    }
    let n1 = seed + state.handleIdx;
    state.handleIdx += 1;
    let hex = n1.toString(16).padStart(16, "0").slice(-16);
    while (!HANDLE_RE.test(hex)) {
      n1 += 1;
      hex = n1.toString(16).padStart(16, "0").slice(-16);
    }
    return hex;
  }

  function emit(text) {
    if (state.portClosed || state.exitCode !== null) return;
    if (!state.frozen) {
      state.emitted.push(text);
      return;
    }
    const nextBytes = state.queueBytes + textLen(text);
    if (state.queue.length + 1 > QUEUE_MESSAGES_MAX || nextBytes > QUEUE_BYTES_MAX) {
      let overflowId = null;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed.request_id === "string") overflowId = parsed.request_id;
      } catch {
        overflowId = null;
      }
      failClose(4, overflowId !== null ? [overflowId] : []); // orderly fail-closed close
      return;
    }
    state.queue.push(text);
    state.queueBytes = nextBytes;
    let requestId = null;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed.request_id === "string") requestId = parsed.request_id;
    } catch {
      requestId = null;
    }
    if (requestId !== null) state.pendingCompletionIds.add(requestId);
  }

  function msgString(parts) {
    const obj = {};
    for (const [key, value] of parts) {
      if (value !== undefined) obj[key] = value;
    }
    return JSON.stringify(obj);
  }

  const envelopeParts = (type, requestId) => {
    const parts = [["envelope_version", 1]];
    if (state.phase === "post") parts.push(["api_version", state.apiVersion]);
    parts.push(["type", type]);
    if (requestId !== undefined) parts.push(["request_id", requestId]);
    return parts;
  };

  const correlatedError = (requestId, code, stage, recoverability = "fatal", extra = []) =>
    msgString([
      ...envelopeParts("error", requestId),
      ["code", code],
      ["recoverability", recoverability],
      ["stage", stage],
      ...extra,
    ]);

  function eventText(type, requestId, fields) {
    const text = msgString([...envelopeParts(type, requestId), ...fields]);
    emit(text);
    return text;
  }

  function cancelPendingWrite(requestId) {
    const i = state.pendingWrites.findIndex((w) => w.request_id === requestId);
    if (i >= 0) state.pendingWrites.splice(i, 1);
  }

  function terminateForConnection(connectionHandle) {
    for (const [requestId, req] of [...state.outstanding]) {
      if (req.connection_handle === connectionHandle) {
        state.outstanding.delete(requestId);
        cancelPendingWrite(requestId);
        emit(
          correlatedError(
            requestId,
            req.type === "connect" ? "connect_failed" : "send_failed",
            "disconnect",
            "fatal",
            [["connection_handle", connectionHandle]],
          ),
        );
      }
    }
  }

  function completePendingWrites() {
    state.lastCompletionOutputs = [];
    if (state.writesPending) return [];
    const completed = state.pendingWrites.splice(0);
    const ids = [];
    for (const w of completed) {
      const conn = state.connections.get(w.connection_handle);
      if (state.outstanding.has(w.request_id) && conn && conn.open) {
        state.outstanding.delete(w.request_id);
        ids.push(w.request_id);
        const text = eventText("sent", w.request_id, [["connection_handle", w.connection_handle]]);
        state.lastCompletionOutputs.push(text);
      }
    }
    return ids;
  }

  function expireOutstanding(now) {
    for (const [requestId, req] of [...state.outstanding]) {
      if (req.type === "start_scan" || req.type === "stop_scan" || req.type === "shutdown") continue;
      if (now - req.t > REQUEST_TIMEOUT_MS) {
        state.outstanding.delete(requestId);
        cancelPendingWrite(requestId);
        emit(correlatedError(requestId, "timeout", "timeout", "retryable"));
      }
    }
  }

  function negotiate(helloMsg, requestId) {
    const selected = selectApi(helloMsg, supportedApi);
    if (selected === null) {
      emit(
        msgString([
          ["envelope_version", 1],
          ["type", "error"],
          ["request_id", requestId],
          ["code", "incompatible_api"],
          ["recoverability", "fatal"],
          ["stage", "negotiation"],
        ]),
      );
      state.exitCode = 3;
      return;
    }
    state.phase = "post";
    state.apiVersion = selected;
    emit(
      msgString([
        ["envelope_version", 1],
        ["api_version", selected],
        ["type", "ready"],
        ["request_id", requestId],
        ["bridge_version", BRIDGE_VERSION],
        ["platform", PLATFORM],
        ["capabilities", ["veles_record_v1"]],
      ]),
    );
  }

  function dispatchPost(msg, now) {
    const requestId = msg.request_id;
    const type = msg.type;
    if (state.outstanding.has(requestId) || state.pendingCompletionIds.has(requestId)) {
      // Ambiguous double terminal: a second live use of a request id that is
      // outstanding or whose terminal completion is queued-but-undelivered
      // can never be correlated. No second correlated error is emitted; the
      // host fails closed (BRD-003) and the connector synthesizes once.
      failClose(2);
      return;
    }
    if (type === "hello") {
      emit(correlatedError(requestId, "invalid_request", "post_ready", "fatal"));
      return;
    }
    if (state.outstanding.size >= OUTSTANDING_MAX) {
      emit(correlatedError(requestId, "cap_exceeded", "capacity", "retryable"));
      return;
    }
    switch (type) {
      case "start_scan": {
        if ([...state.scans.values()].filter((s) => s.open).length >= OPEN_SCANS_MAX) {
          emit(correlatedError(requestId, "cap_exceeded", "capacity", "retryable"));
          return;
        }
        const scanHandle = nextHandle();
        state.scans.set(scanHandle, { found: 0, open: true, rateTimes: [] });
        state.outstanding.set(requestId, { type, t: now });
        state.ble.push({ kind: "scan_start", handle: scanHandle, request_id: requestId });
        eventText("scan_state", requestId, [["scan_handle", scanHandle], ["status", "started"]]);
        state.outstanding.delete(requestId);
        return;
      }
      case "stop_scan": {
        const scan = state.scans.get(msg.scan_handle);
        if (!scan || !scan.open) {
          emit(
            correlatedError(requestId, "unknown_handle", "handle", "fatal", [["scan_handle", msg.scan_handle]]),
          );
          return;
        }
        scan.open = false;
        state.scans.delete(msg.scan_handle); // released handles must not accumulate
        state.ble.push({ kind: "scan_stop", handle: msg.scan_handle, request_id: requestId });
        eventText("scan_state", requestId, [["scan_handle", msg.scan_handle], ["status", "stopped"]]);
        return;
      }
      case "connect": {
        let deviceHandle = null;
        let hint;
        if (msg.device_handle !== undefined) {
          const device = state.devices.get(msg.device_handle);
          if (!device) {
            emit(
              correlatedError(requestId, "unknown_handle", "handle", "fatal", [
                ["device_handle", msg.device_handle],
              ]),
            );
            return;
          }
          if (device.connected) {
            emit(correlatedError(requestId, "busy", "connect", "retryable"));
            return;
          }
          deviceHandle = msg.device_handle;
          hint = device.hint;
        } else {
          hint = msg.platform_hint;
        }
        if ([...state.connections.values()].filter((c) => c.open).length >= OPEN_CONNECTIONS_MAX) {
          emit(correlatedError(requestId, "cap_exceeded", "capacity", "retryable"));
          return;
        }
        const connectionHandle = nextHandle();
        state.connections.set(connectionHandle, { open: true });
        if (deviceHandle) state.devices.get(deviceHandle).connected = true;
        state.outstanding.set(requestId, { type, t: now, connection_handle: connectionHandle });
        state.ble.push({ kind: "connect", handle: connectionHandle, request_id: requestId });
        const fields = [["connection_handle", connectionHandle]];
        if (hint !== undefined) fields.push(["platform_hint", hint]);
        eventText("connected", requestId, fields);
        state.outstanding.delete(requestId);
        return;
      }
      case "disconnect": {
        const conn = state.connections.get(msg.connection_handle);
        if (!conn || !conn.open) {
          emit(
            correlatedError(requestId, "unknown_handle", "handle", "fatal", [
              ["connection_handle", msg.connection_handle],
            ]),
          );
          return;
        }
        state.outstanding.set(requestId, { type, t: now });
        state.ble.push({ kind: "disconnect", handle: msg.connection_handle, request_id: requestId });
        terminateForConnection(msg.connection_handle);
        conn.open = false;
        state.connections.delete(msg.connection_handle); // released entries never accumulate
        eventText("disconnected", requestId, [["connection_handle", msg.connection_handle]]);
        state.outstanding.delete(requestId);
        return;
      }
      case "send": {
        const conn = state.connections.get(msg.connection_handle);
        if (!conn || !conn.open) {
          emit(
            correlatedError(requestId, "unknown_handle", "handle", "fatal", [
              ["connection_handle", msg.connection_handle],
            ]),
          );
          return;
        }
        state.outstanding.set(requestId, { type, t: now, connection_handle: msg.connection_handle });
        state.ble.push({ kind: "send", handle: msg.connection_handle, request_id: requestId });
        state.pendingWrites.push({ request_id: requestId, connection_handle: msg.connection_handle });
        return;
      }
      case "shutdown": {
        state.outstanding.set(requestId, { type, t: now });
        for (const [pendingId] of state.outstanding) {
          if (pendingId === requestId) continue;
          state.outstanding.delete(pendingId);
          cancelPendingWrite(pendingId);
          emit(correlatedError(pendingId, "internal", "shutdown", "fatal"));
        }
        for (const [scanHandle, scan] of [...state.scans]) {
          if (scan.open) {
            scan.open = false;
            state.scans.delete(scanHandle);
            eventText("scan_state", undefined, [["scan_handle", scanHandle], ["status", "stopped"]]);
          } else {
            state.scans.delete(scanHandle);
          }
        }
        for (const [connHandle, conn] of [...state.connections]) {
          if (conn.open) {
            conn.open = false;
            eventText("disconnected", undefined, [["connection_handle", connHandle]]);
          }
          state.connections.delete(connHandle);
        }
        state.outstanding.delete(requestId);
        eventText("shutdown_complete", requestId, []);
        state.exitCode = 0;
        return;
      }
      default:
        emit(correlatedError(requestId, "invalid_request", "post_ready", "fatal"));
    }
  }

  function classifyUsable(jsonText) {
    // usable for correlation: parseable, envelope 1, known request type, valid
    // request_id (BRD-010/011). Post-ready api mismatch stays correlated.
    let msg = null;
    try {
      msg = parseJsonStrict(jsonText, "host input");
    } catch {
      return null;
    }
    if (msg === null || typeof msg !== "object" || Array.isArray(msg)) return null;
    if (
      msg.envelope_version === 1 &&
      REQUEST_TYPES.has(msg.type) &&
      typeof msg.request_id === "string" &&
      REQUEST_ID_RE.test(msg.request_id)
    ) {
      return msg;
    }
    return null;
  }

  return {
    feed(jsonText, now = 0) {
      state.lastCompletionOutputs = [];
      if (state.portClosed || state.exitCode !== null) {
        // closed host (shutdown, fail-closed overflow, or input rejection):
        // stdin is dead — further input is a bounded no-op, nothing grows
        return { outputs: [] };
      }
      const before = state.emitted.length;
      expireOutstanding(now);
      const validation =
        state.phase === "pre"
          ? validateEnvelope(jsonText, "pre", null)
          : validateEnvelope(jsonText, "ready", state.apiVersion);
      if (validation.accepted) {
        const msg = validation.fields;
        if (state.phase === "pre") negotiate(msg, msg.request_id);
        else dispatchPost(msg, now);
      } else {
        const usable = classifyUsable(jsonText);
        if (usable) {
          // A malformed request remains correlatable, but it cannot be given a
          // second terminal result while that ID is still live.
          if (
            state.phase === "post" &&
            (state.outstanding.has(usable.request_id) || state.pendingCompletionIds.has(usable.request_id))
          ) {
            failClose(2);
            return { outputs: [] };
          }
          emit(
            correlatedError(usable.request_id, "invalid_request", state.phase === "pre" ? "pre_ready" : "post_ready"),
          );
          if (state.phase === "pre") {
            releaseAllSilently();
            state.exitCode = 2;
          }
        } else {
          // uncorrelatable input: fail closed with the full orderly release
          // (stop scans, disconnect every link, drop retained state) and no
          // further JSON (OTP2-BRD-010/011)
          releaseAllSilently();
          state.exitCode = 2;
        }
      }
      return { outputs: state.frozen ? [] : state.emitted.slice(before) };
    },
    feedBytes(bytes, now = 0) {
      const frame = decodeHostFrame(bytes);
      if (!frame.accepted) {
        // unusable framing: no JSON response, no BLE work, full release, fail closed (BRD-010/011)
        releaseAllSilently();
        state.exitCode = 2;
        return { outputs: [] };
      }
      return this.feed(frame.text, now);
    },
    deviceFound(scanHandle, { label, platform_hint, advertisement, t_ms = 0, device_handle } = {}) {
      if (state.phase === "pre") {
        throw new Error("stage=phase: no device_found before ready — no BLE work precedes negotiation");
      }
      const scan = state.scans.get(scanHandle);
      if (!scan) throw new Error(`stage=handle: unknown scan handle ${scanHandle}`);
      if (!scan.open) throw new Error(`stage=handle: scan ${scanHandle} is stopped`);
      scan.rateTimes = scan.rateTimes.filter((t) => t_ms - t < 1000);
      if (scan.rateTimes.length >= FOUND_RATE_PER_SECOND) {
        state.rateDropped += 1;
        return { outputs: [] };
      }
      if (scan.found >= FOUND_PER_SCAN_MAX) {
        state.scanEventsDropped += 1;
        return { outputs: [] };
      }
      scan.rateTimes.push(t_ms);
      scan.found += 1;
      const handle = device_handle ?? nextHandle();
      // bounded discovery cache: at most DISCOVERY_CACHE_MAX currently
      // discovered device handles per process (independent of the per-scan
      // event cap); FIFO eviction of the oldest entry that is not currently
      // connected
      while (state.devices.size >= DISCOVERY_CACHE_MAX) {
        const evictKey = [...state.devices.keys()].find((k) => !state.devices.get(k).connected);
        if (evictKey === undefined) break;
        state.devices.delete(evictKey);
      }
      if (state.devices.size >= DISCOVERY_CACHE_MAX) {
        // pathological: cache full of connected devices cannot happen at the
        // 32-connection cap, but fail the discovery rather than exceed the bound
        state.scanEventsDropped += 1;
        return { outputs: [] };
      }
      state.devices.set(handle, { label, hint: platform_hint, connected: false });
      const before = state.emitted.length;
      const fields = [["scan_handle", scanHandle], ["device_handle", handle], ["label", label]];
      if (platform_hint !== undefined) fields.push(["platform_hint", platform_hint]);
      if (advertisement !== undefined) fields.push(["advertisement", advertisement]);
      eventText("device_found", undefined, fields);
      return { outputs: state.frozen ? [] : state.emitted.slice(before) };
    },
    externalDisconnect(connectionHandle, now = 0) {
      if (state.phase === "pre") throw new Error("stage=phase: no link before ready");
      const conn = state.connections.get(connectionHandle);
      if (!conn || !conn.open) throw new Error(`stage=handle: unknown or released connection ${connectionHandle}`);
      const before = state.emitted.length;
      state.lastCompletionOutputs = [];
      terminateForConnection(connectionHandle);
      conn.open = false;
      state.connections.delete(connectionHandle);
      eventText("disconnected", undefined, [["connection_handle", connectionHandle]]);
      return { outputs: state.emitted.slice(before) };
    },
    scanExpired(scanHandle, now = 0) {
      const scan = state.scans.get(scanHandle);
      if (!scan || !scan.open) throw new Error(`stage=handle: scan ${scanHandle} not open`);
      const before = state.emitted.length;
      state.lastCompletionOutputs = [];
      scan.open = false;
      state.scans.delete(scanHandle);
      eventText("scan_state", undefined, [["scan_handle", scanHandle], ["status", "stopped"]]);
      return { outputs: state.emitted.slice(before) };
    },
    portClosed(now = 0) {
      if (state.portClosed) throw new Error("port already closed");
      state.portSynthesized = snapshotSynthesis();
      for (const requestId of state.outstanding.keys()) cancelPendingWrite(requestId);
      state.outstanding.clear();
      state.scans.clear();
      state.connections.clear();
      state.devices.clear();
      state.pendingCompletionIds.clear();
      state.lastCompletionOutputs = [];
      state.portClosed = true;
      return { outputs: [] };
    },
    stdoutClosed() {
      if (state.exitCode === null) state.exitCode = 0;
    },
    setFrozen(frozen) {
      state.frozen = frozen;
    },
    setWritesPending(pending) {
      state.writesPending = pending;
    },
    flushWrites() {
      return completePendingWrites();
    },
    takeWriteCompletions() {
      if (state.lastCompletionOutputs.length === 0) completePendingWrites();
      return { outputs: state.lastCompletionOutputs };
    },
    drainQueue() {
      // deliver retained queued messages in their original order; nothing is
      // synthesized or resurrected (fail-close policy removed that path)
      const drained = state.queue.splice(0);
      state.queueBytes = 0;
      const texts = [];
      for (const text of drained) {
        state.emitted.push(text);
        let requestId = null;
        try {
          const parsed = JSON.parse(text);
          if (parsed && typeof parsed.request_id === "string") requestId = parsed.request_id;
        } catch {
          requestId = null;
        }
        if (requestId !== null) state.pendingCompletionIds.delete(requestId);
        texts.push(text);
      }
      return texts;
    },
    textOutputs() {
      return [...state.emitted];
    },
    synthesizedErrors() {
      return [...state.portSynthesized];
    },
    get exitCode() {
      return state.exitCode;
    },
    state() {
      return {
        phase: state.phase,
        api_version: state.apiVersion,
        outstanding: state.outstanding.size,
        queue_messages: state.queue.length,
        queue_bytes: state.queueBytes,
        queued_completions: state.pendingCompletionIds.size,
        open_scans: [...state.scans.values()].filter((s) => s.open).length,
        open_connections: [...state.connections.values()].filter((c) => c.open).length,
        discovered_devices: state.devices.size,
        handles_issued: state.handleIdx,
        synthesized_errors: state.portSynthesized.length,
        rate_dropped: state.rateDropped,
        scan_events_dropped: state.scanEventsDropped,
      };
    },
    bleOperations() {
      return [...state.ble];
    },
  };
}

// ---------- validateRequestText: shape-only request acceptance for traces ----------
// Phase/api-version legality is NOT gated here: a repeated hello after ready or
// an api mismatch is itself a legal trace line whose correlated error completes
// the request (OTP2-BRD-010/011). Garbage the host could never correlate stays
// a checker violation.

function validateRequestText(jsonText) {
  if (typeof jsonText !== "string") return fail("framing", "request must be JSON text");
  if (jsonText.length === 0) return fail("framing", "empty request text");
  if (jsonText.length > MESSAGE_MAX_BYTES) {
    return fail("length", `request of ${jsonText.length} bytes exceeds the message limit (OTP2-BRD-001)`);
  }
  let req;
  try {
    req = parseJsonStrict(jsonText, "connector request");
  } catch (e) {
    if (/duplicate JSON key/.test(e.message)) return fail("duplicate_key", e.message);
    return fail("json", `not parseable JSON: ${e.message.slice(0, 120)}`);
  }
  if (req === null || typeof req !== "object" || Array.isArray(req)) {
    return fail("json", "request must be a JSON object");
  }
  if (req.envelope_version !== 1) {
    return fail("version", `envelope_version must be 1, got ${JSON.stringify(req.envelope_version)}`);
  }
  if (!REQUEST_TYPES.has(req.type)) {
    return fail("schema", `unknown or non-request message type ${JSON.stringify(req.type)}`);
  }
  if (
    req.request_id === undefined ||
    typeof req.request_id !== "string" ||
    !REQUEST_ID_RE.test(req.request_id)
  ) {
    return fail("schema", "request_id missing or malformed");
  }
  const validate = schemaValidator();
  if (!validate(req)) return fail("schema", ajvErrors(validate.errors));
  try {
    checkOffer(req);
    if (typeof req.record_base64 === "string") {
      decodeBase64(req.record_base64, { min: 1, max: RECORD_MAX_BYTES });
    }
  } catch (e) {
    return { accepted: false, error: e.message, fields: null };
  }
  return { accepted: true, error: null, fields: req };
}

// ---------- checkBridgeTrace: independent lifecycle/order/rate validation ----------

const TRACE_HANDLE_FIELDS = {
  scan_state: "scan_handle",
  device_found: "scan_handle",
  connected: "connection_handle",
  disconnected: "connection_handle",
  sent: "connection_handle",
  message: "connection_handle",
};

export function checkBridgeTrace(trace) {
  try {
    return traceAccept(trace);
  } catch (e) {
    if (e instanceof StageError) return { accepted: false, error: e.message };
    return { accepted: false, error: `stage=synthesis: ${e.message}` };
  }
}

function traceError(stage, message) {
  throw new StageError(stage, message);
}

function traceAccept(trace) {
  if (!trace || typeof trace !== "object" || !Array.isArray(trace.steps) || trace.steps.length === 0) {
    traceError("synthesis", "trace must carry a non-empty steps array");
  }
  let readySeen = false;
  let selected = null;
  const outstanding = new Map(); // request_id -> {type, t, connection_handle?}
  const completed = new Set();
  const scans = new Map(); // scan_handle -> {open, found, rateTimes}
  const connections = new Map(); // connection_handle -> true while open
  const releasedConnections = new Set();
  const stoppedScans = new Set();
  const portErrorIds = new Set();
  let portClosed = false;
  let ended = false;
  let shutdownSeen = false;

  const connectionIsOpen = (handle) => connections.get(handle) === true;

  for (let i = 0; i < trace.steps.length; i++) {
    const step = trace.steps[i];
    if (typeof step !== "object" || step === null) {
      traceError("synthesis", `step ${i} must be an object`);
    }
    const t = step.t_ms ?? 0;
    if (ended) {
      traceError(
        shutdownSeen || step.synthetic === "stdout_close" ? "shutdown" : "synthesis",
        `no step may follow the terminal stdout event (step ${i})`,
      );
    }
    if (portClosed) {
      if (step.from === "host") {
        traceError("synthesis", `host cannot emit anything after native port loss (step ${i})`);
      }
      if (step.from === "connector") {
        traceError("synthesis", `connector stdin is dead after port loss (step ${i})`);
      }
      if (step.from === "synthetic" && step.synthetic !== "port_error" && step.synthetic !== "stdout_close") {
        traceError("synthesis", `only synthesized failures may follow port loss (step ${i})`);
      }
      if (step.synthetic === "port_error") {
        const requestId = step.request_id;
        if (!outstanding.has(requestId)) {
          if (portErrorIds.has(requestId)) {
            traceError("synthesis", `duplicate synthesized failure for ${JSON.stringify(requestId)}`);
          }
          traceError(
            "synthesis",
            `synthesized failure for ${JSON.stringify(requestId)} targets a request that already completed or was never outstanding at port loss`,
          );
        }
        outstanding.delete(requestId);
        portErrorIds.add(requestId);
      }
      if (step.synthetic === "stdout_close") ended = true;
      continue;
    }
    if (shutdownSeen) {
      traceError("shutdown", `no step may follow shutdown_complete (step ${i})`);
    }
    if (step.from === "connector") {
      const res = validateRequestText(step.text);
      if (!res.accepted) {
        traceError(res.stage === "duplicate_key" ? "duplicate_key" : res.stage, `connector step ${i}: ${res.error}`);
      }
      const req = res.fields;
      if (!readySeen && req.type !== "hello") {
        traceError("phase", `connector step ${i}: hello must precede ${JSON.stringify(req.type)}`);
      }
      if (outstanding.has(req.request_id)) {
        traceError("request_id", `request_id ${JSON.stringify(req.request_id)} is already outstanding`);
      }
      outstanding.set(req.request_id, {
        type: req.type,
        t,
        connection_handle: req.connection_handle,
        scan_handle: req.scan_handle,
      });
      continue;
    }
    if (step.from === "host") {
      const res = validateEvent(step.text, selected);
      if (!res.accepted) {
        traceError(res.stage, `host step ${i}: ${res.error}`);
      }
      const msg = res.fields;
      if (!readySeen && msg.type !== "ready") {
        traceError("phase", `host step ${i}: no host event may be emitted before ready (no unsolicited pre-ready events)`);
      }
      const handleField = TRACE_HANDLE_FIELDS[msg.type];
      const handle = handleField ? msg[handleField] : undefined;
      switch (msg.type) {
        case "ready": {
          if (readySeen) traceError("completion", "duplicate ready event");
          readySeen = true;
          selected = msg.api_version;
          completeRequest(msg.request_id, outstanding, completed, "ready", "hello");
          break;
        }
        case "scan_state": {
          if (!scans.has(msg.scan_handle)) {
            scans.set(msg.scan_handle, { open: true, found: 0, rateTimes: [] });
          }
          const scan = scans.get(msg.scan_handle);
          if (msg.status === "started") {
            if (msg.request_id === undefined) {
              traceError("completion", "scan_state started always correlates its start_scan request");
            }
            if (scan.found > 0 || scan.foundEvents) {
              traceError("handle", `scan_state started for ${msg.scan_handle} arrives after its scan started`);
            }
            scan.foundEvents = true;
            completeRequest(msg.request_id, outstanding, completed, "scan_state", "start_scan");
          } else {
            if (msg.request_id !== undefined) {
              completeRequest(msg.request_id, outstanding, completed, "scan_state", "stop_scan");
            }
            if (!scan.open) {
              traceError("completion", `duplicate scan_state stopped for ${msg.scan_handle}`);
            }
            scan.open = false;
            stoppedScans.add(msg.scan_handle);
          }
          break;
        }
        case "device_found": {
          const scan = scans.get(msg.scan_handle);
          if (!scan || !scan.open) {
            traceError(
              "handle",
              `device_found for ${msg.scan_handle} arrives ${scan ? "after its scan stopped" : "before the scan started"}`,
            );
          }
          scan.rateTimes = scan.rateTimes.filter((tt) => t - tt < 1000);
          scan.rateTimes.push(t);
          if (scan.rateTimes.length > FOUND_RATE_PER_SECOND) {
            traceError(
              "rate",
              `${FOUND_RATE_PER_SECOND + 1} device_found events within one rolling second for scan ${msg.scan_handle}`,
            );
          }
          scan.found += 1;
          if (scan.found > FOUND_PER_SCAN_MAX) {
            traceError("rate", `scan ${msg.scan_handle} exceeded ${FOUND_PER_SCAN_MAX} device_found events`);
          }
          if (stoppedScans.has(msg.scan_handle) && scan.open) {
            traceError("handle", `device_found for already-stopped scan ${msg.scan_handle}`);
          }
          break;
        }
        case "connected": {
          if (!outstanding.has(msg.request_id)) {
            traceError("completion", `connected correlates unknown request ${JSON.stringify(msg.request_id)}`);
          }
          if (connections.has(msg.connection_handle)) {
            traceError("handle", `connection handle ${msg.connection_handle} introduced twice`);
          }
          completeRequest(msg.request_id, outstanding, completed, "connected", "connect", msg.connection_handle);
          connections.set(msg.connection_handle, true);
          break;
        }
        case "disconnected": {
          if (!connections.has(msg.connection_handle)) {
            traceError("handle", `disconnected for unknown connection ${msg.connection_handle}`);
          }
          if (releasedConnections.has(msg.connection_handle)) {
            traceError(
              "completion",
              `duplicate disconnected for ${msg.connection_handle}: exactly one release event per connection`,
            );
          }
          if (msg.request_id !== undefined) {
            if (!outstanding.has(msg.request_id)) {
              traceError("completion", `disconnected correlates unknown request ${JSON.stringify(msg.request_id)}`);
            }
            completeRequest(msg.request_id, outstanding, completed, "disconnected", "disconnect", msg.connection_handle);
          }
          connections.set(msg.connection_handle, false);
          releasedConnections.add(msg.connection_handle);
          break;
        }
        case "sent": {
          if (!connectionIsOpen(msg.connection_handle)) {
            traceError("handle", `sent for released connection ${msg.connection_handle} (no post-release handle use)`);
          }
          completeRequest(msg.request_id, outstanding, completed, "sent", "send", msg.connection_handle);
          break;
        }
        case "message": {
          if (!connectionIsOpen(msg.connection_handle)) {
            traceError("handle", `message for released connection ${msg.connection_handle} (no post-release handle use)`);
          }
          break;
        }
        case "error": {
          if (msg.request_id !== undefined) {
            if (!outstanding.has(msg.request_id)) {
              traceError(
                "completion",
                `error correlates unknown request ${JSON.stringify(msg.request_id)}: unknown request ids fail closed (OTP2-BRD-011)`,
              );
            }
            outstanding.delete(msg.request_id);
            completed.add(msg.request_id);
          }
          if (msg.connection_handle !== undefined) {
            if (!connections.has(msg.connection_handle)) {
              traceError("handle", `error references unknown connection ${msg.connection_handle}`);
            }
            if (!connectionIsOpen(msg.connection_handle)) {
              traceError(
                "handle",
                `error for released connection ${msg.connection_handle} arrives after its disconnected event (errors precede disconnect)`,
              );
            }
          }
          if (msg.scan_handle !== undefined) {
            const scan = scans.get(msg.scan_handle);
            if (!scan) traceError("handle", `error references unknown scan ${msg.scan_handle}`);
          }
          break;
        }
        case "shutdown_complete": {
          if (!outstanding.has(msg.request_id)) {
            traceError("completion", `shutdown_complete correlates unknown request ${JSON.stringify(msg.request_id)}`);
          }
          if (outstanding.size > 1) {
            traceError(
              "shutdown",
              `shutdown_complete while ${outstanding.size - 1} request(s) still outstanding: every pending request must receive its correlated error first`,
            );
          }
          completeRequest(msg.request_id, outstanding, completed, "shutdown_complete", "shutdown");
          const openScan = [...scans.entries()].find(([, s]) => s.open);
          if (openScan) traceError("shutdown", `scan ${openScan[0]} still open at shutdown_complete`);
          const openConn = [...connections.keys()].find((h) => connectionIsOpen(h));
          if (openConn) traceError("shutdown", `connection ${openConn} still open at shutdown_complete`);
          shutdownSeen = true;
          ended = true; // shutdown_complete is the FINAL JSON message before stdout closes
          break;
        }
        default:
          traceError("schema", `host step ${i}: unexpected event type ${JSON.stringify(msg.type)}`);
      }
      continue;
    }
    if (step.from === "synthetic") {
      if (step.synthetic === undefined) {
        traceError("synthesis", `synthetic step ${i} without kind`);
      }
      switch (step.synthetic) {
        case "device_found":
        case "link_loss":
        case "scan_expired":
        case "writes_complete":
        case "freeze":
        case "unfreeze":
          // driving-only stimuli for the host model; the emitted events do the bookkeeping
          break;
        case "port_closed":
          portClosed = true;
          break;
        case "stdout_close":
          ended = true;
          break;
        default:
          traceError("synthesis", `unknown synthetic kind ${JSON.stringify(step.synthetic)}`);
      }
      continue;
    }
    traceError("synthesis", `step ${i} has unknown origin ${JSON.stringify(step.from)}`);
  }

  // per-request timeout bound (scans and hello exempt per OTP2-BRD-030)
  const terminalByRequest = new Map();
  for (const step of trace.steps) {
    if (step.from !== "host" || typeof step.text !== "string") continue;
    let msg;
    try {
      msg = parseJsonStrict(step.text, "trace host step");
    } catch {
      continue;
    }
    if (msg && typeof msg === "object" && EVENT_TYPES.has(msg.type) && msg.request_id !== undefined) {
      terminalByRequest.set(msg.request_id, step.t_ms ?? 0);
    }
  }
  for (const step of trace.steps) {
    if (step.from !== "connector" || typeof step.text !== "string") continue;
    let req;
    try {
      req = parseJsonStrict(step.text, "trace connector step");
    } catch {
      continue;
    }
    if (!req || typeof req !== "object" || !REQUEST_TYPES.has(req.type)) continue;
    if (req.type === "hello" || req.type === "start_scan" || req.type === "stop_scan") continue;
    const terminalT = terminalByRequest.get(req.request_id);
    if (terminalT !== undefined && terminalT - (step.t_ms ?? 0) > REQUEST_TIMEOUT_MS) {
      traceError(
        "timeout",
        `request ${JSON.stringify(req.request_id)} terminated after ${terminalT - (step.t_ms ?? 0)}ms, beyond the ${REQUEST_TIMEOUT_MS}ms bound (OTP2-BRD-030)`,
      );
    }
  }

  if (portClosed && outstanding.size > 0) {
    const missing = [...outstanding.keys()].filter((rid) => !portErrorIds.has(rid));
    if (missing.length > 0) {
      traceError(
        "synthesis",
        `port loss leaves ${missing.length} outstanding request(s) without a synthesized local failure: ${missing.join(", ")}`,
      );
    }
  }
  if (!portClosed && outstanding.size > 0) {
    traceError(
      "completion",
      `trace ends with ${outstanding.size} request(s) still outstanding: exactly-once terminal completion required`,
    );
  }
  return { accepted: true, error: null };
}

function completeRequest(requestId, outstanding, completed, eventType, expectedType, expectConnection) {
  const req = outstanding.get(requestId);
  if (!req) {
    traceError(
      "completion",
      `${eventType} correlates ${JSON.stringify(requestId)} which is not outstanding (duplicate terminal completion?)`,
    );
  }
  if (req.type !== expectedType) {
    traceError("completion", `${eventType} for ${JSON.stringify(requestId)} arrived for a ${req.type} request`);
  }
  if (expectConnection !== undefined && req.connection_handle !== undefined && req.connection_handle !== expectConnection) {
    traceError(
      "handle",
      `${eventType} for ${JSON.stringify(requestId)} arrives on connection ${expectConnection} but the request used ${req.connection_handle}`,
    );
  }
  outstanding.delete(requestId);
  completed.add(requestId);
}
