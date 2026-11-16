// Gate B (OTP-12) — the evidence matrix may cite only journeys the journey file defines.
// docs/gate-b/matrix.json links its rows to protocol/conformance/gate-b-journeys.json by id,
// and the docs-side validator reads that file at test time. A matrix-only edit does not run
// the protocol node's suites, so this check is the independent guard that a cited journey
// exists: a renamed or removed journey stops the matrix from passing unnoticed.
//
//   node --test protocol/checks/gate-b-matrix-journeys.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const checksDir = dirname(fileURLToPath(import.meta.url));
const root = join(checksDir, "..", "..");
const MATRIX_PATH = join(root, "docs", "gate-b", "matrix.json");
const JOURNEYS_PATH = join(root, "protocol", "conformance", "gate-b-journeys.json");

function load(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function journeyIds() {
  return Object.keys(load(JOURNEYS_PATH).journeys);
}

// Structural failures first, then one failure per citation that names a journey the journey
// file does not define. The matrix itself is never modified.
function citationFailures(matrix, defined) {
  const failures = [];
  if (matrix === null || typeof matrix !== "object" || Array.isArray(matrix)) {
    return ["matrix: must be a JSON object"];
  }
  if (!Array.isArray(matrix.rows)) {
    return ["matrix.rows: must be an array"];
  }
  const cited = new Set();
  matrix.rows.forEach((row, index) => {
    const where = row !== null && typeof row === "object" && typeof row.id === "string" ? row.id : `matrix.rows[${index}]`;
    if (row === null || typeof row !== "object" || Array.isArray(row)) {
      failures.push(`${where}: row must be an object`);
      return;
    }
    if (!Array.isArray(row.journeys)) {
      failures.push(`${where}: journeys must be an array of journey ids`);
      return;
    }
    for (const journey of row.journeys) {
      if (typeof journey !== "string") {
        failures.push(`${where}: journey id ${JSON.stringify(journey)} is not a string`);
        continue;
      }
      cited.add(journey);
      if (!defined.has(journey)) {
        failures.push(`${where}: cites journey id ${JSON.stringify(journey)} that the journey file does not define`);
      }
    }
  });
  for (const journey of defined) {
    if (!cited.has(journey)) {
      failures.push(`matrix: defines no row that cites journey ${JSON.stringify(journey)}`);
    }
  }
  return failures;
}

function mutated(change) {
  const copy = structuredClone(load(MATRIX_PATH));
  change(copy);
  return copy;
}

test("committed_matrix_cites_only_defined_journeys", () => {
  const matrix = load(MATRIX_PATH);
  const defined = new Set(journeyIds());
  assert.ok(matrix.rows.length > 0, "the matrix must have rows");
  assert.deepEqual(citationFailures(matrix, defined), []);
  assert.deepEqual([...defined].sort(), ["enrollment", "protected-records"]);
});

test("unknown_journey_id_fails", () => {
  const defined = new Set(journeyIds());
  const document = mutated((value) => {
    value.rows[0].journeys = ["teleport"];
  });
  const failures = citationFailures(document, defined);
  assert.ok(
    failures.some((failure) => failure.includes('"teleport"') && failure.includes("does not define")),
    failures.join("\n"),
  );
});

test("missing_or_malformed_journey_list_fails", () => {
  const defined = new Set(journeyIds());
  for (const [label, change, reason] of [
    ["a removed journeys list", (value) => { delete value.rows[0].journeys; }, "journeys must be an array"],
    ["a non-string journey id", (value) => { value.rows[0].journeys = [7]; }, "is not a string"],
    ["a malformed row", (value) => { value.rows[0] = null; }, "row must be an object"],
    ["a removed rows array", (value) => { delete value.rows; }, "matrix.rows: must be an array"],
  ]) {
    const failures = citationFailures(mutated(change), defined);
    assert.ok(failures.length > 0, `${label} must fail`);
    assert.ok(failures.some((failure) => failure.includes(reason)), `${label}: ${failures.join("\n")}`);
  }
});

test("a_journey_no_row_cites_fails", () => {
  const defined = new Set([...journeyIds(), "future-journey"]);
  const failures = citationFailures(load(MATRIX_PATH), defined);
  assert.ok(
    failures.some((failure) => failure.includes('"future-journey"')),
    failures.join("\n"),
  );
});
