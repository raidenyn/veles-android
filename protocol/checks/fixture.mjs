// Test-only fixture loader and schema validation for the Veles protocol
// contract (OTP-02 Plan Task 2). Never imported by product code.
//
// loadFixtures(root) expects `root` to be the protocol/v1 directory containing
// manifest.json, limits.json, binary-layouts.json, fixture.schema.json and a
// fixtures/ subdirectory of fixture files.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import Ajv2020 from "ajv/dist/2020.js";
import { decodeHex } from "./binary.mjs";

export { decodeHex, validateBinary, validateFragmented } from "./binary.mjs";

export const U64_MAX_STRING = "18446744073709551615";
const U64_MAX = 18446744073709551615n;

const ajv = new Ajv2020({ strict: true, allErrors: true });
// Content-addressed: schema TEXT never changes meaning between roots, so this
// cache is safe to share across loadFixtures calls.
const validatorBySchemaText = new Map();
// Per-layout typed field maps are derived from a root's binary-layouts.json:
// two roots may declare the same layoutId differently, so this cache MUST be
// scoped per loadFixtures call (created fresh from `layouts`), never at module
// level keyed by layoutId alone.
const keySchemasByLayouts = new WeakMap();

export { parseJsonStrict };

// ---------- strict JSON parsing (raw-text duplicate-key detection) ----------
// V8 collapses duplicate keys before any JSON.parse reviver runs, so detection
// MUST inspect the original JSON text (Plan: "JSON duplicate detection needs
// raw text; schema alone insufficient").

class DuplicateKeyError extends Error {
  constructor(key) {
    super(`duplicate JSON key "${key}"`);
    this.key = key;
  }
}
class ScanAbort extends Error {}

const WS = " \t\n\r";

function skipWs(text, i) {
  while (i < text.length && WS.includes(text[i])) i++;
  return i;
}

function scanString(text, start) {
  let decoded = "";
  let i = start + 1;
  for (;;) {
    if (i >= text.length) throw new ScanAbort();
    const ch = text[i];
    if (ch === "\\") {
      const next = text[i + 1];
      if (next === "u") {
        const hex = text.slice(i + 2, i + 6);
        if (!/^[0-9a-fA-F]{4}$/.test(hex)) throw new ScanAbort();
        decoded += String.fromCharCode(parseInt(hex, 16));
        i += 6;
      } else if (next !== undefined && 'trnbf"\\/'.includes(next)) {
        decoded += { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" }[next];
        i += 2;
      } else {
        throw new ScanAbort();
      }
    } else if (ch === '"') {
      return [decoded, i + 1];
    } else {
      decoded += ch;
      i += 1;
    }
  }
}

function scanValue(text, i) {
  i = skipWs(text, i);
  const ch = text[i];
  if (ch === '"') return scanString(text, i)[1];
  if (ch === "{") return scanObject(text, i);
  if (ch === "[") return scanArray(text, i);
  if (ch === "-" || (ch >= "0" && ch <= "9")) {
    const start = i;
    while (i < text.length && /[0-9.eE+-]/.test(text[i])) i++;
    if (i === start) throw new ScanAbort();
    return i;
  }
  for (const literal of ["true", "false", "null"]) {
    if (text.startsWith(literal, i)) return i + literal.length;
  }
  throw new ScanAbort();
}

function scanObject(text, start) {
  const keys = new Set();
  let i = skipWs(text, start + 1);
  if (text[i] === "}") return i + 1;
  for (;;) {
    i = skipWs(text, i);
    if (text[i] !== '"') throw new ScanAbort();
    const [key, afterKey] = scanString(text, i);
    if (keys.has(key)) throw new DuplicateKeyError(key);
    keys.add(key);
    i = skipWs(text, afterKey);
    if (text[i] !== ":") throw new ScanAbort();
    i = skipWs(text, i + 1);
    i = scanValue(text, i);
    i = skipWs(text, i);
    if (text[i] === ",") {
      i += 1;
      continue;
    }
    if (text[i] === "}") return i + 1;
    throw new ScanAbort();
  }
}

function scanArray(text, start) {
  let i = skipWs(text, start + 1);
  if (text[i] === "]") return i + 1;
  for (;;) {
    i = scanValue(text, i);
    i = skipWs(text, i);
    if (text[i] === ",") {
      i += 1;
      continue;
    }
    if (text[i] === "]") return i + 1;
    throw new ScanAbort();
  }
}

function scanJsonDuplicates(text, where) {
  try {
    let i = skipWs(text, 0);
    i = scanValue(text, i);
    i = skipWs(text, i);
    if (i !== text.length) throw new ScanAbort(); // trailing bytes after the JSON document
  } catch (e) {
    if (e instanceof DuplicateKeyError) throw e;
    throw new ScanAbort();
  }
}

function parseJsonStrict(text, where) {
  try {
    scanJsonDuplicates(text, where);
  } catch (e) {
    if (e instanceof DuplicateKeyError) {
      throw new Error(
        `stage=schema: duplicate JSON key "${e.key}" in ${where} (raw text inspected before parsing)`,
      );
    }
    // scanner aborted: ordinary JSON.parse produces the authoritative error
  }
  return JSON.parse(text);
}

function schemaValidator(schemaText, schema) {
  if (!validatorBySchemaText.has(schemaText)) {
    validatorBySchemaText.set(schemaText, ajv.compile(schema));
  }
  return validatorBySchemaText.get(schemaText);
}

// ---------- per-layout typed field maps (derived from binary-layouts.json) ----------

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function keySchemaForLayout(layoutId, layouts, cache) {
  const cached = cache.get(layoutId);
  if (cached) return cached;
  const layout = layouts.layouts[layoutId];
  if (!layout) throw new Error(`stage=unknown_layout: unknown layout id: ${layoutId}`);
  const map = buildKeyMap(layout.fields, layouts, layoutId);
  cache.set(layoutId, map);
  return map;
}

function keySchemasFor(layouts) {
  let cache = keySchemasByLayouts.get(layouts);
  if (!cache) {
    cache = new Map();
    keySchemasByLayouts.set(layouts, cache);
  }
  return cache;
}

function buildKeyMap(fields, layouts, layoutId) {
  const map = new Map();
  const errors = [];
  const claim = (name, cls) => {
    const existing = map.get(name);
    if (existing !== undefined && existing !== cls) {
      errors.push(`field "${name}" declared twice with different classes`);
      return;
    }
    map.set(name, cls);
  };
  const nested = (layoutRef) => {
    const itemLayout = layouts.layouts[layoutRef];
    if (!itemLayout) {
      errors.push(`layout ${layoutId}: unknown layout reference ${layoutRef}`);
      return null;
    }
    return buildKeyMap(itemLayout.fields, layouts, layoutRef);
  };
  for (const field of fields) {
    if (
      field.type === "bytes" ||
      field.type === "bytes_dynamic" ||
      field.type === "point" ||
      field.type === "signature" ||
      field.type === "literal"
    ) {
      if (!field.opaque) claim(field.name, "bytes");
    } else if (field.enum !== undefined) {
      claim(field.name, "hex_scalar");
    } else if (
      field.type === "u8" ||
      field.type === "u16" ||
      field.type === "u32" ||
      field.type === "u64"
    ) {
      claim(field.name, "decimal");
    } else if (field.type === "currency") {
      claim(field.name, "text");
    } else if (field.type === "length_prefixed_u16") {
      claim(`${field.name}_len`, "decimal");
      if (field.value_type === "utf8") claim(field.name, "text");
      else if (field.value_type === "caps") claim(field.name, "hex_scalar");
      else errors.push(`layout ${layoutId}: unknown value_type ${field.value_type}`);
    } else if (field.type === "count_prefixed_u8") {
      claim(`${field.name}_count`, "decimal");
      const itemMap = nested(field.item_layout);
      if (itemMap) claim(field.name, { objects: itemMap });
    } else if (field.type === "inline_layout") {
      const itemMap = nested(field.layout);
      if (itemMap) claim(field.name, { objects: itemMap });
    } else {
      errors.push(`layout ${layoutId}: unknown field type ${field.type}`);
    }
  }
  if (errors.length > 0) {
    throw new Error(`stage=schema: binary layout ${layoutId}: ${errors.join("; ")}`);
  }
  return map;
}

function checkFieldTree(node, map, errors, path) {
  if (!isPlainObject(node)) {
    errors.push(`fields at ${path} must be an object`);
    return;
  }
  for (const [name, value] of Object.entries(node)) {
    const cls = map.get(name);
    const at = path === "" ? name : `${path}.${name}`;
    if (cls === undefined) {
      errors.push(`field "${at}" is not part of this layout's decoded fields`);
      continue;
    }
    if (isPlainObject(cls) && cls.objects) {
      if (Array.isArray(value)) {
        value.forEach((item, i) => {
          if (!isPlainObject(item)) {
            errors.push(`field "${at}[${i}]" must be an object`);
          } else {
            checkFieldTree(item, cls.objects, errors, `${at}[${i}]`);
          }
        });
      } else if (isPlainObject(value)) {
        checkFieldTree(value, cls.objects, errors, at);
      } else {
        errors.push(`field "${at}" must decode to object value(s)`);
      }
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        checkScalarValue(item, cls, errors, `${at}[]`);
      }
      continue;
    }
    checkScalarValue(value, cls, errors, at);
  }
}

function checkScalarValue(value, cls, errors, at) {
  switch (cls) {
    case "decimal": {
      if (
        typeof value !== "string" ||
        !/^[0-9]+$/.test(value) ||
        (value.length > 1 && value[0] === "0")
      ) {
        errors.push(
          `field "${at}" must be a canonical unsigned decimal string, got ${JSON.stringify(value)}`,
        );
        return;
      }
      if (BigInt(value) > U64_MAX) {
        errors.push(
          `field "${at}": ${value} exceeds the u64 maximum ${U64_MAX_STRING} (rejected)`,
        );
      }
      break;
    }
    case "hex_scalar":
      if (typeof value !== "string" || !/^0x[0-9a-f]{2}$|^0x[0-9a-f]{4}$/.test(value)) {
        errors.push(
          `field "${at}" must be a 0x-prefixed 2- or 4-digit lowercase hex scalar, got ${JSON.stringify(value)}`,
        );
      }
      break;
    case "bytes":
      if (typeof value !== "string" || !/^0x([0-9a-f]{2})+$/.test(value)) {
        errors.push(
          `field "${at}" must be a 0x-prefixed lowercase even-length hex string, got ${JSON.stringify(value)}`,
        );
      }
      break;
    case "text":
      if (typeof value !== "string") {
        errors.push(`field "${at}" must be a string, got ${JSON.stringify(value)}`);
      }
      break;
    default:
      errors.push(`field "${at}": unexpected value class ${JSON.stringify(cls)}`);
  }
}

// ---------- loadFixtures ----------

export function loadFixtures(root) {
  const readJson = (name) =>
    parseJsonStrict(readFileSync(join(root, name), "utf8"), name);
  const manifest = readJson("manifest.json");
  const requirementIds = new Set(manifest.entries.map((e) => e.id));
  const schemaText = readFileSync(join(root, "fixture.schema.json"), "utf8");
  const schema = parseJsonStrict(schemaText, "fixture.schema.json");
  const validate = schemaValidator(schemaText, schema);
  const layouts = readJson("binary-layouts.json");

  const fixturesDir = join(root, "fixtures");
  let names;
  try {
    names = readdirSync(fixturesDir).filter((n) => n.endsWith(".json")).sort();
  } catch {
    throw new Error(`stage=schema: no fixtures directory at ${fixturesDir}`);
  }
  if (names.length === 0) {
    throw new Error(`stage=schema: no fixture files found in ${fixturesDir}`);
  }

  const fixtures = [];
  const ids = new Map();
  const keySchemaCache = keySchemasFor(layouts);
  for (const name of names) {
    const envelope = parseJsonStrict(readFileSync(join(fixturesDir, name), "utf8"), name);
    if (!validate(envelope)) {
      throw new Error(
        `stage=schema: fixture file ${name}: ${ajv.errorsText(validate.errors)}`,
      );
    }
    for (const fixture of envelope.fixtures) {
      checkFixture(fixture, name, requirementIds, layouts, keySchemaCache);
      if (ids.has(fixture.id)) {
        throw new Error(
          `stage=duplicate_id: fixture id "${fixture.id}" in ${name} duplicates ${ids.get(fixture.id)}`,
        );
      }
      ids.set(fixture.id, name);
      fixtures.push(fixture);
    }
  }
  return fixtures;
}

function checkFixture(fixture, name, requirementIds, layouts, keySchemaCache) {
  const errors = [];
  for (const requirementId of fixture.requirement_ids) {
    if (!requirementIds.has(requirementId)) {
      throw new Error(
        `stage=unknown_requirement: fixture "${fixture.id}" (${name}) references unknown requirement id ${requirementId}`,
      );
    }
  }

  if (fixture.category === "encoding") {
    let layoutKeyMap = null;
    try {
      layoutKeyMap = keySchemaForLayout(fixture.input.layout, layouts, keySchemaCache);
    } catch (e) {
      throw new Error(
        `stage=unknown_layout: fixture "${fixture.id}" (${name}): ${e.message}`,
      );
    }
    try {
      decodeHex(fixture.input.bytes);
    } catch (e) {
      throw new Error(`stage=hex: fixture "${fixture.id}" (${name}): ${e.message}`);
    }
    if (fixture.expected.accepted === true) {
      if (fixture.expected.fields === undefined) {
        errors.push("accepted encoding fixture must carry exact fields");
      } else {
        checkFieldTree(fixture.expected.fields, layoutKeyMap, errors, "expected.fields");
      }
    } else if (fixture.expected.accepted === false) {
      if (fixture.expected.stage === undefined) {
        errors.push("rejected encoding fixture must name its validation stage");
      }
      if (fixture.expected.no_state_mutation !== true) {
        errors.push("rejected encoding fixture must carry no_state_mutation: true");
      }
    } else {
      errors.push("encoding expected.accepted must be a boolean");
    }
  } else if (fixture.category === "fragmentation") {
    for (const tx of fixture.input.transactions) {
      for (const fragment of tx.fragments) {
        try {
          decodeHex(fragment);
        } catch {
          errors.push(`transaction ${tx.transaction_id}: malformed fragment hex`);
        }
      }
    }
    if (fixture.expected.accepted === false) {
      for (const outcome of fixture.expected.transactions) {
        if (
          outcome.completed === false &&
          outcome.stage !== undefined &&
          outcome.no_state_mutation !== true
        ) {
          errors.push("rejected fragment outcomes must carry no_state_mutation: true");
        }
      }
    }
  } else if (fixture.category === "bridge") {
    // Shape/typing is enforced by fixture.schema.json's bridge input/expected
    // branches; semantic interpretation (validateEnvelope/validateEvent/
    // selectApi/checkBridgeTrace) happens in checks/bridge.test.mjs so the
    // loader stays a schema gate and never becomes a second bridge validator.
    const input = fixture.input;
    if (input.kind === "envelope" && input.direction === "request" && input.phase === undefined) {
      errors.push("request envelope fixtures must carry phase");
    }
    if (input.kind === "trace" && input.steps.length === 0) {
      errors.push("trace fixtures must carry at least one step");
    }
  } else if (fixture.category === "state") {
    // Shape/typing is enforced by fixture.schema.json's state input/expected
    // branches; semantic interpretation (loadStates/checkProtocolTrace) happens
    // in checks/state.test.mjs so the loader stays a schema gate and never
    // becomes a second state validator (OTP2-FIX-006).
    const input = fixture.input;
    if (input.kind !== "trace" || input.steps.length === 0) {
      errors.push("state fixtures must carry a non-empty state trace");
    }
    if (fixture.expected.accepted === true) {
      if (fixture.expected.final_state === undefined) {
        errors.push("accepted state fixtures must declare their expected final state");
      }
      if (
        fixture.expected.effects !== undefined &&
        (fixture.expected.effects.require?.length ?? 0) +
          (fixture.expected.effects.forbid?.length ?? 0) +
          (fixture.expected.effects.order?.length ?? 0) ===
          0
      ) {
        errors.push("state effect expectations must require, forbid, or order at least one effect");
      }
    } else if (fixture.expected.accepted === false) {
      if (fixture.expected.no_state_mutation !== true) {
        errors.push("rejected state fixtures must carry no_state_mutation: true");
      }
    } else {
      errors.push("state expected.accepted must be a boolean");
    }
  } else if (fixture.category === "crypto") {
    // Shape/typing is enforced by fixture.schema.json's crypto input/expected
    // branches; semantic verification (derive-and-compare, rejection proofs,
    // independent oracle/OpenSSL report coverage) happens in checks/crypto.mjs
    // so the loader stays a schema gate and never becomes a crypto verifier
    // (OTP2-FIX-006).
    const input = fixture.input;
    if (fixture.expected.rejected === true) {
      if (input.deviation?.negates === undefined) {
        errors.push("rejected crypto fixtures must reference their positive base (deviation.negates)");
      }
      if (
        fixture.expected.proof === "confirmation_mismatch" &&
        !(fixture.expected.distinguishes?.length > 0)
      ) {
        errors.push("confirmation-mismatch crypto fixtures must list distinguishing outputs");
      }
    } else {
      if (!input.kind || input.deviation) {
        errors.push("positive crypto fixtures must carry kind and no deviation");
      }
      if (Object.keys(fixture.expected.outputs ?? {}).length === 0) {
        errors.push("verified crypto fixtures must carry outputs");
      }
    }
  } else {
    errors.push(`unknown category ${fixture.category}`);
  }

  if (errors.length > 0) {
    throw new Error(`stage=schema: fixture "${fixture.id}" (${name}): ${errors.join("; ")}`);
  }
}