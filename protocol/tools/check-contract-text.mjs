#!/usr/bin/env node
// Test-only documentation-contract text checks for OTP-02 Task 1.
// Derives every expectation from the documents themselves (no handwritten
// label-length tables). Exits 0 when all assertions pass, 1 otherwise.
// Not imported by product code; superseded by Plan Task 2 executable checks.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { buildEvidenceInventory } from "../checks/evidence.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const docPath = join(root, "docs", "protocol", "veles-v1.md");
const manifestPath = join(root, "protocol", "v1", "manifest.json");
const limitsPath = join(root, "protocol", "v1", "limits.json");

const doc = readFileSync(docPath, "utf8");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const limits = JSON.parse(readFileSync(limitsPath, "utf8"));

const failures = [];
const checked = { labelClaims: 0, layoutBlocks: 0, anchors: 0, ids: 0 };
function fail(msg) {
  failures.push(msg);
}
function assert(cond, msg) {
  if (!cond) fail(msg);
}

// ---------- 1. ASCII label length claims (derived from the doc) ----------
// Every line quoting a "Veles ... v1" label (double quotes or backticks)
// whose same line carries a length annotation MUST have that annotation
// equal to the label's exact ASCII byte length. Claims are parsed from the
// document; nothing is handwritten here.
const labelLine = /(?:"(Veles [A-Za-z0-9 -]+v1|ConfirmationKeys)"|`(Veles [A-Za-z0-9 -]+v1|ConfirmationKeys)`)/;
const lines = doc.split("\n");
for (const line of lines) {
  const lm = line.match(labelLine);
  if (!lm) continue;
  const label = (lm[1] ?? lm[2]);
  const rest = line.slice(lm.index + lm[0].length);
  let claimed = null;
  let m2;
  if ((m2 = rest.match(/#\s*(\d+)\s*ASCII bytes?/)) ||
      (m2 = rest.match(/(\d+)\s*ASCII bytes?/)) ||
      (m2 = rest.match(/(\d+)\s*bytes?\s*\)/)) ||
      (m2 = rest.match(/\((\d+)\)/))) {
    claimed = Number(m2[1]);
  }
  if (claimed === null) continue;
  const actual = Buffer.byteLength(label, "ascii");
  checked.labelClaims += 1;
  assert(actual === claimed, `label length: "${label}" claims ${claimed}, actual ${actual}`);
}
assert(checked.labelClaims >= 20, `expected many label claims, found ${checked.labelClaims}`);

// Inline "label = N" claims for the recovery labels.
for (const m of doc.matchAll(/`(Veles recovery (?:client|phone) activation v1)` = (\d+)/g)) {
  const actual = Buffer.byteLength(m[1], "ascii");
  assert(actual === Number(m[2]), `inline length: ${m[1]} = ${m[2]}, actual ${actual}`);
  checked.labelClaims += 1;
}

// ---------- 2. Layout blocks: field sums and payload formulas ----------
// Collect fenced code blocks.
const blocks = [];
let start = -1;
for (let i = 0; i < lines.length; i++) {
  if (lines[i].trim().startsWith("```")) {
    if (start === -1) start = i;
    else {
      blocks.push({ begin: start, end: i, text: lines.slice(start + 1, i).join("\n") });
      start = -1;
    }
  }
}

function fieldWidth(lineText) {
  // "name   N bytes" → {w: N}; "name   u16be" → {w: 2}; "name   u8" → {w: 1};
  // "name   u16be, then X" → {w: 2, variable: true}
  const m = lineText.match(/^\s*([a-zA-Z_0-9]+)\s+(?:(\d+) bytes\b|(u16be|u8)\b)/);
  if (!m) return null;
  const name = m[1];
  if (m[2]) return { name, w: Number(m[2]) };
  const w = m[3] === "u16be" ? 2 : 1;
  const variable = /u16be,\s*then\b/.test(lineText);
  return { name, w, variable };
}
// "pA" (mixed case) etc. handled inside fieldWidth below.

function fixedSum(block) {
  let sum = 0;
  let labelMax = null;
  for (const line of block.text.split("\n")) {
    const f = fieldWidth(line);
    if (!f) continue;
    if (f.variable) {
      sum += f.w;
      const lm = line.match(/0\.\.(\d+) bytes/);
      if (lm) labelMax = Number(lm[1]);
    } else {
      sum += f.w;
    }
  }
  return { sum, labelMax };
}

function checkBlock(anchorField, formula, expect) {
  const block = blocks.find((b) =>
    b.text.split("\n").some((l) => l.trim().startsWith(`${anchorField}`)) &&
    b.text.split("\n").filter((l) => fieldWidth(l)).length >= 3,
  );
  assert(block, `layout block for ${anchorField} not found`);
  if (!block) return;
  const { sum, labelMax } = fixedSum(block);
  checked.layoutBlocks += 1;
  if (expect.type === "fixed") {
    assert(sum === expect.fixed, `${anchorField}: computed fixed sum ${sum} != ${expect.fixed}`);
    const after = lines.slice(block.end + 1, block.end + 6).join("\n");
    const fm = after.match(new RegExp(formula));
    assert(fm && Number(fm[1]) === expect.fixed,
      `${anchorField}: doc formula "${formula}" not matched to ${expect.fixed}`);
  } else if (expect.type === "bounded") {
    assert(labelMax === expect.labelMax, `${anchorField}: label max parsed ${labelMax} != ${expect.labelMax}`);
    assert(sum + expect.labelMax === expect.maxBody,
      `${anchorField}: computed max body ${sum + expect.labelMax} != ${expect.maxBody}`);
    const after = lines.slice(block.end + 1, block.end + 8).join("\n");
    const mm = after.match(/Maximum body size:[^\n]*= (\d+)/s);
    assert(mm && Number(mm[1]) === expect.maxBody,
      `${anchorField}: doc maximum-body value ${mm && mm[1]} != ${expect.maxBody}`);
    const mn = after.match(/Minimum body size:\s*(\d+)/s);
    assert(mn && Number(mn[1]) === expect.minBody,
      `${anchorField}: doc minimum-body value ${mn && mn[1]} != ${expect.minBody}`);
  }
}

checkBlock("client_nonce", /payload_length\s*=\s*(\d+) \+ caps_len/, { type: "fixed", fixed: 20 });
checkBlock("pairing_id", /payload_length\s*=\s*(\d+) \+ caps_len/, { type: "fixed", fixed: 101 });
checkBlock("ephemeral_pubkey", /payload_length\s*=\s*(\d+) \+ caps_len/, { type: "fixed", fixed: 102 });
checkBlock("installation_id", /body size/, {
  type: "bounded", labelMax: 32, maxBody: 164, minBody: 132,
});

// ---------- 3. Anchor / ID closure ----------
const anchors = [...doc.matchAll(/<a id="(sec-[^"]+)"/g)].map((m) => m[1]);
const anchorSet = new Set(anchors);
checked.anchors = anchors.length;
assert(new Set(anchors).size === anchors.length, "duplicate <a id> anchors in veles-v1.md");

// Every sec-* reference in the doc body resolves to an anchor.
const refs = new Set([...doc.matchAll(/(?:\(|see |in |per |; |— )sec-[a-z0-9-]+/g)].map((m) =>
  m[0].replace(/[^a-z0-9-]/g, "").replace(/^/, "").replace(/^[^s]*/, "").replace(/^.*?(sec-[a-z0-9-]+)$/, "$1"),
));
for (const r of refs) {
  if (r === "sec-...") continue; // documented format example
  assert(anchorSet.has(r), `unresolved anchor reference: ${r}`);
}

// Every OTP2-* ID used in the doc exists in the manifest (registry-only
// families documented in doc section 13 are excluded from the used set).
const idPat = /OTP2-(?:CON|SEC|ENC|CRY|SID|STA|REC|REV|GAT|WAT|BRD|FIX|PRD|SCP|EVD)-[0-9A-Za-z]+b?\b/g;
const docIds = new Set([...doc.matchAll(idPat)].map((m) => m[0]));
const manifestIds = new Set(manifest.entries.map((e) => e.id));
checked.ids = docIds.size;
for (const id of docIds) {
  assert(manifestIds.has(id), `requirement ID used in doc but missing in manifest: ${id}`);
}

// Manifest entry integrity: shape, anchors, statuses, uniqueness, and the
// closed evidence loop: evidence_ids/status must EXACTLY equal the inventory
// derived from the committed fixtures and named checks (I4: populated,
// honest, drift-detecting — checked BEFORE the manifest is regenerated).
const statusEnum = new Set(manifest.status_values);
const seenIds = new Set();
for (const e of manifest.entries) {
  assert(!seenIds.has(e.id), `duplicate manifest id ${e.id}`);
  seenIds.add(e.id);
  assert(
    typeof e.id === "string" &&
      Array.isArray(e.evidence_ids) &&
      Object.keys(e).sort().join(",") === "document_anchor,evidence_ids,id,status",
    `manifest entry ${e.id}: wrong field set`,
  );
  assert(
    e.document_anchor.startsWith("veles-v1.md#") &&
      anchorSet.has(e.document_anchor.split("#")[1]),
    `manifest entry ${e.id}: unresolvable anchor`,
  );
  assert(statusEnum.has(e.status), `manifest entry ${e.id}: unknown status ${e.status}`);
}
assert(manifest.entries.length === 156, `manifest must cover 156 requirements, got ${manifest.entries.length}`);

// Limits integrity: known names, integer values except the documented u64
// decimal-string, resolvable anchors, unique names.
const seenLimits = new Set();
for (const l of limits.limits) {
  assert(!seenLimits.has(l.name), `duplicate limit name ${l.name}`);
  seenLimits.add(l.name);
  const anchorOk = l.source_anchor.startsWith("veles-v1.md#") &&
    anchorSet.has(l.source_anchor.split("#")[1]);
  assert(anchorOk, `limit ${l.name}: unresolvable source_anchor`);
  if (l.name === "sequence_max") {
    assert(l.value === "18446744073709551615", `sequence_max must be the canonical u64 decimal string`);
  } else {
    assert(Number.isInteger(l.value), `limit ${l.name}: value must be an integer`);
  }
}
assert(limits.limits.some((l) => l.name === "sequence_max"), "sequence_max limit missing");

// ---------- summary ----------
try {
  const inventory = buildEvidenceInventory(join(root, "protocol", "v1"));
  assert(inventory.entries.length === manifest.entries.length, "evidence inventory does not cover every requirement");
  const derived = new Map(inventory.entries.map((e) => [e.id, e]));
  for (const e of manifest.entries) {
    const d = derived.get(e.id);
    assert(d, `inventory lost requirement ${e.id}`);
    const expectIds = d.evidence_ids;
    assert(
      JSON.stringify(e.evidence_ids) === JSON.stringify(expectIds),
      `manifest evidence_ids for ${e.id} are stale or dishonest: manifest=${JSON.stringify(e.evidence_ids)} vs derived=${JSON.stringify(expectIds)}`,
    );
    assert(
      e.status === d.status,
      `manifest status for ${e.id} is inconsistent with its evidence: ${e.status} vs derived ${d.status}`,
    );
    assert(
      e.status !== "accepted" && e.status !== "reviewed",
      `manifest entry ${e.id}: review/acceptance claims require the recorded evidence`,
    );
  }
} catch (error) {
  fail(`evidence inventory: ${error.message}`);
}
if (failures.length > 0) {
  for (const f of failures) console.error(`FAIL: ${f}`);
  console.error(`${failures.length} failure(s)`);
  process.exit(1);
}
console.log(
  `check-contract-text: PASS (${checked.labelClaims} label claims, ` +
    `${checked.layoutBlocks} layout blocks, ${checked.anchors} anchors, ` +
    `${checked.ids} requirement IDs, ${manifest.entries.length} manifest entries, ` +
    `${limits.limits.length} limits)`,
);
