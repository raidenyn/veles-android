// Test-only binary descriptor interpreter for the Veles protocol contract
// (OTP-02 Plan Task 2). Never imported by product code; not a reference codec.
// Wire rules come from docs/protocol/veles-v1.md via protocol/v1/binary-layouts.json.

class BinaryStageError extends Error {
  constructor(stage, message) {
    super(`stage=${stage}: ${message}`);
    this.stage = stage;
  }
}

const HEX_BYTE_STRING = /^([0-9a-f]{2})*$/;

export function decodeHex(text) {
  if (typeof text !== "string" || !HEX_BYTE_STRING.test(text)) {
    const shown =
      typeof text === "string"
        ? `${text.length} chars: ${text.slice(0, 12)}${text.length > 12 ? "..." : ""}`
        : String(text);
    throw new Error(
      `malformed hex byte string (${shown}); expected lowercase even-length hex`,
    );
  }
  return Buffer.from(text, "hex");
}

// ---------- wire integer readers (DataView/BigInt; Veles network order) ----------

function dataView(bytes) {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
}

function readU8(bytes, offset) {
  return dataView(bytes).getUint8(offset);
}
function readU16(bytes, offset) {
  return dataView(bytes).getUint16(offset, false);
}
function readU32(bytes, offset) {
  return dataView(bytes).getUint32(offset, false);
}
function readU64(bytes, offset) {
  return dataView(bytes).getBigUint64(offset, false);
}

// ---------- utf8 / caps / currency helpers ----------

const utf8Decoder = new TextDecoder("utf-8", { fatal: true });

function decodeUtf8String(bytes, start, end, name) {
  let text;
  try {
    text = utf8Decoder.decode(bytes.subarray(start, end));
  } catch {
    throw new BinaryStageError("utf8", `field "${name}" is not valid UTF-8`);
  }
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    if (cp <= 0x1f || cp === 0x7f) {
      throw new BinaryStageError(
        "utf8",
        `field "${name}" contains forbidden control character U+${cp.toString(16).padStart(4, "0")}`,
      );
    }
  }
  return text;
}

function readCapsSet(bytes, start, end, name, capsConstants) {
  const values = [];
  let previous = -1;
  for (let i = start; i < end; i++) {
    const value = bytes[i];
    if (!capsConstants.known_values.includes(value)) {
      throw new BinaryStageError(
        "enum",
        `field "${name}": unknown capability value 0x${value.toString(16).padStart(2, "0")}`,
      );
    }
    if (value <= previous) {
      throw new BinaryStageError(
        "enum",
        `field "${name}": capability set must be sorted ascending and unique, got 0x${value.toString(16).padStart(2, "0")}`,
      );
    }
    previous = value;
    values.push(`0x${value.toString(16).padStart(2, "0")}`);
  }
  return values;
}

function readCurrency(bytes, start, end, name) {
  const chars = [];
  for (let i = start; i < end; i++) {
    const value = bytes[i];
    if (value < 0x41 || value > 0x5a) {
      throw new BinaryStageError(
        "enum",
        `field "${name}": currency must be exactly 3 uppercase ASCII A-Z bytes, got 0x${value.toString(16).padStart(2, "0")}`,
      );
    }
    chars.push(String.fromCharCode(value));
  }
  return chars.join("");
}

// ---------- layout walker ----------

function numericOutput(field, value) {
  if (field.enum !== undefined) {
    const digits = field.type === "u16" ? 4 : 2;
    return `0x${value.toString(16).padStart(digits, "0")}`;
  }
  return value.toString();
}

function checkScalarBounds(field, value, name) {
  if (field.const !== undefined && value !== BigInt(field.const)) {
    throw new BinaryStageError(
      "enum",
      `field "${name}" must equal ${field.const}, got ${value.toString()}`,
    );
  }
  if (field.enum !== undefined && !field.enum.some((e) => BigInt(e) === value)) {
    throw new BinaryStageError(
      "enum",
      `field "${name}": value ${value.toString()} is not an allowed value`,
    );
  }
  if (field.min !== undefined && value < BigInt(field.min)) {
    throw new BinaryStageError(
      "length",
      `field "${name}": value ${value.toString()} below minimum ${field.min}`,
    );
  }
  if (field.max !== undefined && value > BigInt(field.max)) {
    throw new BinaryStageError(
      "length",
      `field "${name}": value ${value.toString()} above maximum ${field.max}`,
    );
  }
}

const INT_WIDTH = { u8: 1, u16: 2, u32: 4, u64: 8 };

function readField(field, bytes, offset, layouts, out, num) {
  const width = INT_WIDTH[field.type];
  if (width) {
    const end = offset + width;
    if (end > bytes.length) {
      throw new BinaryStageError(
        "length",
        `field "${field.name}" truncated: need ${width} byte(s) at offset ${offset}, ${bytes.length - offset} available`,
      );
    }
    const value =
      width === 1
        ? BigInt(readU8(bytes, offset))
        : width === 2
          ? BigInt(readU16(bytes, offset))
          : width === 4
            ? BigInt(readU32(bytes, offset))
            : readU64(bytes, offset);
    checkScalarBounds(field, value, field.name);
    out[field.name] = numericOutput(field, value);
    num[field.name] = value;
    return end;
  }

  switch (field.type) {
    case "bytes": {
      const end = offset + field.size;
      if (end > bytes.length) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}" truncated: need ${field.size} byte(s) at offset ${offset}, ${bytes.length - offset} available`,
        );
      }
      if (!field.opaque) {
        out[field.name] = `0x${Buffer.from(bytes.subarray(offset, end)).toString("hex")}`;
      }
      return end;
    }
    case "literal": {
      const size = field.hex.length / 2;
      const end = offset + size;
      if (end > bytes.length) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}" truncated: need ${size} byte(s) at offset ${offset}, ${bytes.length - offset} available`,
        );
      }
      const actual = Buffer.from(bytes.subarray(offset, end)).toString("hex");
      if (actual !== field.hex) {
        throw new BinaryStageError(
          "literal",
          `field "${field.name}" does not match its fixed constant bytes`,
        );
      }
      out[field.name] = `0x${field.hex}`;
      return end;
    }
    case "point": {
      const end = offset + 65;
      if (end > bytes.length) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}" truncated: need 65 byte(s) at offset ${offset}, ${bytes.length - offset} available`,
        );
      }
      if (bytes[offset] !== 4) {
        throw new BinaryStageError(
          "point_format",
          `field "${field.name}": point must be an uncompressed SEC1 encoding (0x04 prefix), got 0x${bytes[offset].toString(16).padStart(2, "0")}`,
        );
      }
      out[field.name] = `0x${Buffer.from(bytes.subarray(offset, end)).toString("hex")}`;
      return end;
    }
    case "signature": {
      const available = bytes.length - offset;
      if (available < 64) {
        throw new BinaryStageError(
          "signature_format",
          `field "${field.name}": signature must be exactly 64 bytes (P1363 fixed width), got ${available} available`,
        );
      }
      out[field.name] = `0x${Buffer.from(bytes.subarray(offset, offset + 64)).toString("hex")}`;
      return offset + 64;
    }
    case "currency": {
      const end = offset + 3;
      if (end > bytes.length) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}" truncated: need 3 byte(s) at offset ${offset}, ${bytes.length - offset} available`,
        );
      }
      out[field.name] = readCurrency(bytes, offset, end, field.name);
      return end;
    }
    case "length_prefixed_u16": {
      if (offset + 2 > bytes.length) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}_len" truncated: need 2 byte(s) at offset ${offset}, ${bytes.length - offset} available`,
        );
      }
      const length = readU16(bytes, offset);
      if (length < (field.min ?? 0) || length > field.max) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}": length prefix ${length} outside ${field.min ?? 0}..${field.max}`,
        );
      }
      const valueStart = offset + 2;
      const valueEnd = valueStart + length;
      if (valueEnd > bytes.length) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}" truncated: length prefix says ${length}, ${bytes.length - valueStart} available`,
        );
      }
      out[`${field.name}_len`] = length.toString();
      if (field.value_type === "utf8") {
        out[field.name] = decodeUtf8String(bytes, valueStart, valueEnd, field.name);
      } else if (field.value_type === "caps") {
        out[field.name] = readCapsSet(
          bytes,
          valueStart,
          valueEnd,
          field.name,
          layouts.constants.caps,
        );
      } else {
        throw new Error(`unknown value_type ${field.value_type}`);
      }
      return valueEnd;
    }
    case "count_prefixed_u8": {
      if (offset + 1 > bytes.length) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}_count" truncated: need 1 byte(s) at offset ${offset}, ${bytes.length - offset} available`,
        );
      }
      const count = readU8(bytes, offset);
      if (count < (field.min ?? 0) || count > field.max) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}": count ${count} outside ${field.min ?? 0}..${field.max}`,
        );
      }
      const itemLayout = layouts.layouts[field.item_layout];
      if (!itemLayout) throw new Error(`unknown item_layout ${field.item_layout}`);
      let position = offset + 1;
      const items = [];
      for (let i = 0; i < count; i++) {
        const itemOut = {};
        position = readFields(itemLayout, bytes, position, layouts, itemOut, num);
        items.push(itemOut);
      }
      out[`${field.name}_count`] = count.toString();
      out[field.name] = items;
      return position;
    }
    case "inline_layout": {
      const itemLayout = layouts.layouts[field.layout];
      if (!itemLayout) throw new Error(`unknown layout ${field.layout}`);
      const nested = {};
      const end = readFields(itemLayout, bytes, offset, layouts, nested, num);
      out[field.name] = nested;
      return end;
    }
    case "bytes_dynamic": {
      const declared = num[field.length_field];
      if (declared === undefined) {
        throw new Error(`unknown length field "${field.length_field}"`);
      }
      const length = Number(declared);
      if (length < (field.min ?? 0) || length > field.max) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}": declared length ${length} outside ${field.min ?? 0}..${field.max} (bounds checked before allocation)`,
        );
      }
      const end = offset + length;
      if (end > bytes.length) {
        throw new BinaryStageError(
          "length",
          `field "${field.name}" truncated: declared ${length}, ${bytes.length - offset} available`,
        );
      }
      if (!field.opaque) {
        out[field.name] = `0x${Buffer.from(bytes.subarray(offset, end)).toString("hex")}`;
      }
      return end;
    }
    default:
      throw new Error(`unknown descriptor field type ${field.type}`);
  }
}

function readFields(layout, bytes, offset, layouts, out, num) {
  let position = offset;
  for (const field of layout.fields) {
    position = readField(field, bytes, position, layouts, out, num);
  }
  return position;
}

function directionTable(layouts) {
  const byValue = new Map();
  for (const entry of Object.values(layouts.record_types)) {
    byValue.set(entry.value, entry.directions);
  }
  return byValue;
}

function validateDirectionCheck(layout, layouts, num) {
  if (!layout.direction_check) return;
  const allowed = directionTable(layouts).get(Number(num.record_type));
  if (allowed === undefined) {
    throw new BinaryStageError("enum", "record type is not a known record type");
  }
  if (!allowed.includes(Number(num.direction))) {
    throw new BinaryStageError(
      "direction",
      `record type 0x${Number(num.record_type).toString(16).padStart(4, "0")} is not permitted in direction 0x${Number(num.direction).toString(16).padStart(2, "0")}`,
    );
  }
}

// ---------- validateBinary ----------

export function validateBinary(layoutId, bytes, layouts) {
  const layout = layouts && layouts.layouts ? layouts.layouts[layoutId] : undefined;
  if (layout === undefined) {
    return {
      accepted: false,
      error: `stage=framing: unknown layout id: ${layoutId}`,
      fields: null,
    };
  }
  if (!(bytes instanceof Uint8Array)) {
    return {
      accepted: false,
      error: "stage=framing: bytes must be a Uint8Array",
      fields: null,
    };
  }
  try {
    const out = {};
    const num = {};
    if (layout.container === "frame") {
      readFrameEnvelope(layout, bytes, layouts, out, num);
    } else {
      const end = readFields(layout, bytes, 0, layouts, out, num);
      if (end !== bytes.length) {
        throw new BinaryStageError(
          "trailing",
          `${bytes.length - end} trailing byte(s) after the declared layout`,
        );
      }
      if (layout.container === "record") {
        validateDirectionCheck(layout, layouts, num);
      }
      if (layoutId === "phone_policy_state") {
        const policy = Number(num.policy);
        const flags = Number(num.flags);
        const interactive = (flags & 0x01) !== 0;
        const unlocked = (flags & 0x02) !== 0;
        const eligible = (flags & 0x04) !== 0;
        const expected = policy === 0 ? interactive && unlocked : policy === 1 ? interactive : true;
        if (eligible !== expected) {
          throw new BinaryStageError("enum", "phone_policy_state eligible flag does not match the observed policy");
        }
      }
      if (layout.total_max_bytes !== undefined && bytes.length > layout.total_max_bytes) {
        throw new BinaryStageError(
          "length",
          `layout ${layoutId} exceeds its ${layout.total_max_bytes}-byte bound`,
        );
      }
    }
    return { accepted: true, error: null, fields: out };
  } catch (error) {
    if (error instanceof BinaryStageError) {
      return { accepted: false, error: error.message, fields: null };
    }
    throw error;
  }
}

function readFrameEnvelope(layout, bytes, layouts, out, num) {
  const frame = layouts.constants.frame;
  if (bytes.length < frame.header_bytes) {
    throw new BinaryStageError(
      "framing",
      `frame shorter than its ${frame.header_bytes}-byte header`,
    );
  }
  if (bytes.length > frame.total_max_bytes) {
    throw new BinaryStageError(
      "framing",
      `frame is ${bytes.length} bytes, above the ${frame.total_max_bytes}-byte maximum`,
    );
  }
  const type = readU8(bytes, 0);
  if (type !== layout.frame_type) {
    throw new BinaryStageError(
      "framing",
      `frame type 0x${type.toString(16).padStart(2, "0")} is not 0x${layout.frame_type.toString(16).padStart(2, "0")} (${layout.anchor ? layout.anchor.split("#")[1] : "frame"})`,
    );
  }
  const payloadLen = readU16(bytes, 1);
  const available = bytes.length - frame.header_bytes;
  if (payloadLen > available) {
    throw new BinaryStageError(
      "length",
      `frame payload truncated: payload_len ${payloadLen} exceeds the ${available} present byte(s)`,
    );
  }
  if (payloadLen < available) {
    throw new BinaryStageError(
      "trailing",
      `${available - payloadLen} byte(s) after the declared payload length`,
    );
  }
  const payloadEnd = frame.header_bytes + payloadLen;
  const end = readFields(layout, bytes, frame.header_bytes, layouts, out, num);
  if (end !== payloadEnd) {
    throw new BinaryStageError(
      "trailing",
      `${payloadEnd - end} unused byte(s) inside the frame payload`,
    );
  }
}

// ---------- fragment reassembly (transport layer; OTP2-GAT-010/011/012) ----------

export function validateFragmented(transactions, mtu, layouts) {
  const results = [];
  let accepted = true;
  let error = null;
  const fr = layouts.fragmentation;
  if (!Number.isInteger(mtu) || mtu < fr.mtu_min) {
    return {
      accepted: false,
      error: `stage=fragment_header: negotiated MTU ${mtu} below the ${fr.mtu_min}-byte minimum (connection refused at setup)`,
      transactions: [
        {
          transaction_id: null,
          completed: false,
          reassembled: null,
          stage: "fragment_header",
          ignored: false,
        },
      ],
    };
  }
  const budget = mtu - fr.att_header_bytes - fr.header_bytes;
  const openTransactions = new Map();
  for (const tx of transactions) {
    if (openTransactions.size >= fr.reassembly.incomplete_per_peer_max) {
      results.push({
        transaction_id: tx.transaction_id,
        completed: false,
        reassembled: null,
        stage: "fragment_caps",
        ignored: false,
      });
      accepted = false;
      error ??=
        `stage=fragment_caps: reassembly cap of ${fr.reassembly.incomplete_per_peer_max} incomplete transactions per peer exceeded before transaction ${tx.transaction_id}`;
      continue;
    }
    const result = reassembleTransaction(tx, budget, fr, openTransactions);
    results.push({
      transaction_id: tx.transaction_id,
      completed: result.done,
      reassembled: result.bytes === null ? null : result.bytes,
      stage: result.stage === null ? null : result.stage.stage,
      ignored: result.ignored,
    });
    if (result.stage) {
      accepted = false;
      error ??=
        `stage=${result.stage.stage}: transaction ${tx.transaction_id}: ${result.stage.message}`;
    }
  }
  return { accepted, error, transactions: results };
}

function reassembleTransaction(tx, budget, fr, openTransactions) {
  const tid = tx.transaction_id;
  if (
    !Number.isInteger(tid) ||
    tid < fr.transaction_id_min ||
    tid > fr.transaction_id_max
  ) {
    return {
      done: false,
      bytes: null,
      stage: new BinaryStageError(
        "fragment_header",
        `transaction_id ${tid} outside ${fr.transaction_id_min}..${fr.transaction_id_max}`,
      ),
      ignored: false,
    };
  }
  let declared = null;
  let buffer = null;
  let sum = 0;
  let nextIndex = 0;
  let registered = false;
  let completedBytes = null;
  try {
    for (let i = 0; i < tx.fragments.length; i++) {
      if (completedBytes !== null) {
        throw new BinaryStageError(
          "fragment_final",
          `fragment ${i} arrives after the final fragment (transaction discarded)`,
        );
      }
      const fragment = tx.fragments[i];
      const bytes = typeof fragment === "string" ? decodeHex(fragment) : fragment;
      if (!(bytes instanceof Uint8Array)) {
        throw new BinaryStageError("fragment_header", `fragment ${i} is not bytes`);
      }
      if (bytes.length < fr.header_bytes) {
        throw new BinaryStageError(
          "fragment_header",
          `fragment ${i} is shorter than the ${fr.header_bytes}-byte fragment header`,
        );
      }
      const header = bytes[0];
      const isFinal = (header & (1 << fr.final_flag_bit)) !== 0;
      const index = header & (0xff >> (8 - fr.index_bit_width));
      const tidByte = readU8(bytes, 1);
      if (tidByte === 0) {
        throw new BinaryStageError(
          "fragment_header",
          `fragment ${i}: transaction_id 0 is invalid`,
        );
      }
      if (declared === null) {
        if (index !== 0) {
          throw new BinaryStageError(
            "fragment_index",
            `first fragment has index ${index}; transactions start at 0 without gaps`,
          );
        }
        declared = readU16(bytes, 2);
        if (declared < fr.total_min_bytes || declared > fr.total_max_bytes) {
          throw new BinaryStageError(
            "fragment_header",
            `fragment 0: total_record_length ${declared} outside ${fr.total_min_bytes}..${fr.total_max_bytes}`,
          );
        }
        if (openTransactions.has(tidByte)) {
          return { done: false, bytes: null, stage: null, ignored: true };
        }
        const openBytes = [...openTransactions.values()].reduce((acc, n) => acc + n, 0);
        if (openBytes + declared > fr.reassembly.bytes_per_peer_max) {
          throw new BinaryStageError(
            "fragment_caps",
            `byte cap of ${fr.reassembly.bytes_per_peer_max} reassembly bytes per peer exceeded`,
          );
        }
        buffer = new Uint8Array(declared); // allocation strictly after all bounds checks
        openTransactions.set(tidByte, declared);
        registered = true;
      } else {
        if (index !== nextIndex) {
          throw new BinaryStageError(
            "fragment_index",
            `fragment ${i} index ${index} != expected ${nextIndex} (gap or duplicate)`,
          );
        }
        const reserved = readU16(bytes, 2);
        if (reserved !== 0) {
          throw new BinaryStageError(
            "fragment_header",
            `fragment ${index}: reserved bytes must be 0x0000, got 0x${reserved.toString(16).padStart(4, "0")}`,
          );
        }
      }
      const payload = bytes.subarray(fr.header_bytes);
      const remaining = declared - sum;
      const expected = Math.min(budget, remaining);
      if (payload.length !== expected) {
        throw new BinaryStageError(
          "fragment_total",
          `fragment ${index} payload ${payload.length} != expected ${expected} (size rules)`,
        );
      }
      buffer.set(payload, sum);
      sum += payload.length;
      nextIndex = index + 1;
      if (isFinal) {
        if (sum !== declared) {
          throw new BinaryStageError(
            "fragment_total",
            `reassembled ${sum} byte(s) != declared total_record_length ${declared} at completion`,
          );
        }
        // do not return yet: the walk verifies no fragment arrives after the
        // final one; completion is reported after the loop
        completedBytes = buffer;
        openTransactions.delete(tidByte);
        continue;
      }
      if (nextIndex > 127) {
        throw new BinaryStageError(
          "fragment_index",
          `fragment index exhausted (more than ${fr.index_max} fragments)`,
        );
      }
    }
    // Natural end without a final fragment: the partial transaction stays open
    // (transport-only incomplete state) and keeps counting toward the caps.
    if (completedBytes !== null) {
      openTransactions.delete(tid);
      return { done: true, bytes: completedBytes, stage: null, ignored: false };
    }
    return { done: false, bytes: null, stage: null, ignored: false };
  } catch (e) {
    if (!(e instanceof BinaryStageError)) throw e;
    if (registered) openTransactions.delete(tid); // discarded: buffers freed
    return { done: false, bytes: null, stage: e, ignored: false };
  }
}
