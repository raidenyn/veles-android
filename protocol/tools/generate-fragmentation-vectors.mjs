#!/usr/bin/env node
// Deterministic fragmentation vector generator for profile revision 2 (OTP-23, test-only).
//
// The MTU-185 vectors of revision 1 are committed fixtures and stay byte-for-byte
// untouched, except `frag-reserved-nonzero` and `frag-live-transaction-duplicate-ignored`,
// which are amended (see `amendments()`).
//
// Everything else managed here is the `frag-r2-*` family: attribute-budget boundaries,
// the 512-byte clamp (ATT MTU 515..517), intermediates and malformed-frame negatives.
// Frames and verdicts come from the independent reference model below (a literal
// transcription of OTP2-GAT-010), never from `validateFragmented`; the contract test
// then requires the checker, the Kotlin receiver and the Rust receiver to agree.
//
// Usage:
//   node protocol/tools/generate-fragmentation-vectors.mjs            # write candidate to build/protocol/
//   node protocol/tools/generate-fragmentation-vectors.mjs --check    # fail if committed file drifts
//   node protocol/tools/generate-fragmentation-vectors.mjs --write    # reviewed: update committed file

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const COMMITTED = join(root, "protocol", "v1", "fixtures", "fragmentation.json");
const CANDIDATE = join(root, "build", "protocol", "fragmentation-candidate.json");

const ATT_OVERHEAD = 3;
const HEADER = 4;
const ATTRIBUTE_MAX = 512;

/** Reference budget: the single formula of OTP2-GAT-010 (profile revision 2). */
export function payloadBudget(mtu) {
  return Math.min(mtu - ATT_OVERHEAD, ATTRIBUTE_MAX) - HEADER;
}

/** Deterministic record bytes: never all-equal, so chunk-order mistakes are visible. */
export function sampleRecord(length, seed) {
  const bytes = new Uint8Array(length);
  for (let i = 0; i < length; i++) bytes[i] = (i * 131 + seed * 17 + (i >> 8) * 29 + 7) & 0xff;
  return bytes;
}

const hex = (bytes) => Buffer.from(bytes).toString("hex");

function frame({ index, final, tid, total = 0, payload }) {
  const out = new Uint8Array(HEADER + payload.length);
  out[0] = (final ? 0x80 : 0) | index;
  out[1] = tid;
  out[2] = total >> 8;
  out[3] = total & 0xff;
  out.set(payload, HEADER);
  return out;
}

/** Canonical sender, or the tolerated form with an empty trailing final fragment. */
export function fragmentRecord(record, mtu, tid, { emptyFinal = false } = {}) {
  const budget = payloadBudget(mtu);
  const frames = [];
  let offset = 0;
  while (offset < record.length) {
    const payload = record.subarray(offset, Math.min(offset + budget, record.length));
    offset += payload.length;
    const last = offset === record.length;
    frames.push(
      frame({
        index: frames.length,
        final: last && !emptyFinal,
        tid,
        total: frames.length === 0 ? record.length : 0,
        payload,
      }),
    );
  }
  if (emptyFinal) {
    frames.push(frame({ index: frames.length, final: true, tid, payload: new Uint8Array(0) }));
  }
  return frames.map(hex);
}

const REQS_ACCEPT = ["OTP2-GAT-010", "OTP2-GAT-003", "OTP2-GAT-013"];
const REQS_REJECT = ["OTP2-GAT-010", "OTP2-GAT-011", "OTP2-FIX-004"];

function accept(id, mtu, tid, record, options) {
  return {
    id,
    fixture_version: 1,
    category: "fragmentation",
    requirement_ids: REQS_ACCEPT,
    input: { mtu, transactions: [{ transaction_id: tid, fragments: fragmentRecord(record, mtu, tid, options) }] },
    expected: { accepted: true, transactions: [{ completed: true, reassembled: hex(record) }] },
  };
}

function reject(id, mtu, tid, fragments, stage) {
  return {
    id,
    fixture_version: 1,
    category: "fragmentation",
    requirement_ids: REQS_REJECT,
    input: { mtu, transactions: [{ transaction_id: tid, fragments }] },
    expected: { accepted: false, transactions: [{ completed: false, stage, no_state_mutation: true }] },
  };
}

const pad = (n, byte) => new Uint8Array(n).fill(byte);

export function buildManagedFixtures() {
  const out = [];
  let tid = 129;
  const nextTid = () => ++tid;

  // Accepted: every budget regime. 185 floor, 247 mid, 514 (last unclamped attribute 511),
  // 515/516/517 (clamped to 512), each around the one-fragment and two-fragment edges.
  for (const mtu of [185, 247, 514, 515, 516, 517]) {
    const p = payloadBudget(mtu);
    const sizes = new Set(
      mtu === 185
        ? [p - 1, p, p + 1, 2 * p, 4096]
        : [1, p - 1, p, p + 1, 2 * p - 1, 2 * p, 2 * p + 1, 4096, ...(mtu === 517 ? [4095] : [])],
    );
    for (const size of [...sizes].sort((x, y) => x - y)) {
      out.push(accept(`frag-r2-mtu${mtu}-len${size}`, mtu, nextTid(), sampleRecord(size, mtu + size)));
    }
  }
  // Non-canonical but tolerated: exact multiple closed by an EMPTY final fragment.
  out.push(accept("frag-r2-mtu517-len1016-empty-final", 517, nextTid(), sampleRecord(1016, 3), { emptyFinal: true }));
  out.push(accept("frag-r2-mtu516-len508-empty-final", 516, nextTid(), sampleRecord(508, 4), { emptyFinal: true }));

  // Rejected: the legacy (revision 1) frames at MTU 517 and other over-budget attribute values.
  const legacy = [];
  const record4096 = sampleRecord(4096, 9);
  for (let i = 0, offset = 0; offset < record4096.length; i++) {
    const payload = record4096.subarray(offset, Math.min(offset + 510, record4096.length));
    offset += payload.length;
    legacy.push(
      hex(frame({ index: i, final: offset === record4096.length, tid: 111, total: i === 0 ? 4096 : 0, payload })),
    );
  }
  out.push(reject("frag-r2-mtu517-legacy-r1-frames-rejected", 517, 111, legacy, "fragment_total"));
  out.push(
    reject(
      "frag-r2-mtu517-nonfinal-513-attribute",
      517,
      112,
      [hex(frame({ index: 0, final: false, tid: 112, total: 1100, payload: pad(509, 0x11) }))],
      "fragment_total",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu517-final-513-attribute",
      517,
      113,
      [hex(frame({ index: 0, final: true, tid: 113, total: 509, payload: pad(509, 0x12) }))],
      "fragment_total",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu514-512-attribute-over-budget",
      514,
      114,
      [hex(frame({ index: 0, final: false, tid: 114, total: 1100, payload: pad(508, 0x13) }))],
      "fragment_total",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu517-nonfinal-short-507",
      517,
      115,
      [
        hex(frame({ index: 0, final: false, tid: 115, total: 1016, payload: pad(507, 0x14) })),
        hex(frame({ index: 1, final: true, tid: 115, payload: pad(509, 0x14) })),
      ],
      "fragment_total",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu517-nonfinal-exceeds-remaining",
      517,
      116,
      [hex(frame({ index: 0, final: false, tid: 116, total: 507, payload: pad(508, 0x15) }))],
      "fragment_total",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu517-final-short-of-total",
      517,
      117,
      [
        hex(frame({ index: 0, final: false, tid: 117, total: 1000, payload: pad(508, 0x16) })),
        hex(frame({ index: 1, final: true, tid: 117, payload: pad(491, 0x16) })),
      ],
      "fragment_total",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu517-empty-final-after-partial",
      517,
      118,
      [
        hex(frame({ index: 0, final: false, tid: 118, total: 1000, payload: pad(508, 0x17) })),
        hex(frame({ index: 1, final: true, tid: 118, payload: new Uint8Array(0) })),
      ],
      "fragment_total",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu517-total-4097",
      517,
      119,
      [hex(frame({ index: 0, final: false, tid: 119, total: 4097, payload: pad(508, 0x18) }))],
      "fragment_header",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu517-total-0",
      517,
      120,
      [hex(frame({ index: 0, final: true, tid: 120, total: 0, payload: new Uint8Array(0) }))],
      "fragment_header",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu517-index-gap",
      517,
      121,
      [
        hex(frame({ index: 0, final: false, tid: 121, total: 1017, payload: pad(508, 0x19) })),
        hex(frame({ index: 2, final: true, tid: 121, payload: pad(509, 0x19) })),
      ],
      "fragment_index",
    ),
  );
  out.push(
    reject(
      "frag-r2-mtu517-full-final-then-extra",
      517,
      122,
      [
        hex(frame({ index: 0, final: true, tid: 122, total: 508, payload: pad(508, 0x1a) })),
        hex(frame({ index: 1, final: true, tid: 122, payload: new Uint8Array(0) })),
      ],
      "fragment_final",
    ),
  );
  return out;
}

/**
 * Revision-1 fixtures amended so each pins only the rule it names. Both used a short
 * NON-final first fragment, which the exact size rule (and the Kotlin/Rust receivers)
 * rejects for an unrelated reason, so they never exercised the reserved-byte rule or
 * the live-duplicate rule they claim.
 */
function amendments() {
  const live = hex(frame({ index: 0, final: false, tid: 51, total: 200, payload: pad(178, 0xab) }));
  return new Map([
    [
      "frag-reserved-nonzero",
      reject(
        "frag-reserved-nonzero",
        185,
        27,
        [hex(frame({ index: 0, final: false, tid: 27, total: 180, payload: pad(178, 0xab) })), "811b0001abab"],
        "fragment_header",
      ),
    ],
    [
      "frag-live-transaction-duplicate-ignored",
      {
        id: "frag-live-transaction-duplicate-ignored",
        fixture_version: 1,
        category: "fragmentation",
        requirement_ids: ["OTP2-GAT-011", "OTP2-FIX-004"],
        input: {
          mtu: 185,
          transactions: [
            { transaction_id: 51, fragments: [live] },
            { transaction_id: 51, fragments: [live] },
          ],
        },
        expected: {
          accepted: true,
          transactions: [
            { completed: false, incomplete: true, no_state_mutation: true },
            { completed: false, ignored: true, no_state_mutation: true },
          ],
        },
      },
    ],
  ]);
}

export function mergeFixtures(committed) {
  const managed = new Map(buildManagedFixtures().map((f) => [f.id, f]));
  const amended = amendments();
  const fixtures = committed.fixtures
    .filter((f) => !managed.has(f.id))
    .map((f) => {
      const replacement = amended.get(f.id);
      return replacement ? { ...replacement, requirement_ids: f.requirement_ids } : f;
    });
  return { ...committed, fixtures: [...fixtures, ...managed.values()] };
}

function main() {
  const mode = process.argv[2] ?? "candidate";
  const committedText = readFileSync(COMMITTED, "utf8");
  const text = JSON.stringify(mergeFixtures(JSON.parse(committedText)), null, 2) + "\n";
  if (mode === "--check") {
    if (text !== committedText) {
      console.error("fragmentation.json drifts from generate-fragmentation-vectors.mjs; review and run with --write");
      process.exit(1);
    }
    console.log("fragmentation-vectors: committed file matches the generator");
  } else if (mode === "--write") {
    writeFileSync(COMMITTED, text);
    console.log("fragmentation-vectors: wrote protocol/v1/fixtures/fragmentation.json");
  } else {
    mkdirSync(dirname(CANDIDATE), { recursive: true });
    writeFileSync(CANDIDATE, text);
    console.log(`fragmentation-vectors: candidate at ${CANDIDATE}`);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) main();
