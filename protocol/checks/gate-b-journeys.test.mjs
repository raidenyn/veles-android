// Gate B (OTP-12) — client-agnostic journey file schema and vocabulary suite.
// protocol/conformance/gate-b-journeys.json describes two protocol-level
// journeys (enrollment, protected-records) that the scripted non-desktop client
// runs against the real phone engine. Because the same journeys must hold for
// any client, no key, action name, parameter name or string value may carry
// desktop vocabulary: a token such as tab, port, native, host, handle,
// extension, offscreen or badge would tie the journeys to the browser
// extension. The single permitted platform reference is the top-level property
// trust_independent_of_platform_hint.
//
// The existing conformance files (otp-09/otp-10-scenarios.json) keep their own
// shape and runners; this suite reads only the journey file.
//
//   node --test protocol/checks/gate-b-journeys.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const conformanceDir = join(dirname(fileURLToPath(import.meta.url)), "..", "conformance");
const JOURNEYS_PATH = join(conformanceDir, "gate-b-journeys.json");

const HINT_PROPERTY = "trust_independent_of_platform_hint";
const JOURNEY_NAMES = ["enrollment", "protected-records"];
const ACTIONS = new Set([
  "enroll",
  "reconnect",
  "seed",
  "pull",
  "schedule_push",
  "push",
  "ack",
  "advance_clock",
  "revoke",
  "reconnect_expect_rejected",
]);
const FORBIDDEN = new Set(["tab", "port", "native", "host", "handle", "extension", "offscreen", "badge"]);

// Lowercase, then split on non-alphanumerics and camelCase boundaries, so
// transport_mtu yields [transport, mtu] and deviceHandle yields [device, handle].
function tokenize(text) {
  return text
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

// Visit every key and string value with its JSON path ("$.journeys...[0].params.x").
function walk(value, visit, path = "$") {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => walk(entry, visit, `${path}[${index}]`));
  } else if (value !== null && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      const childPath = `${path}.${key}`;
      visit(childPath, "key", key);
      walk(child, visit, childPath);
    }
  } else if (typeof value === "string") {
    visit(path, "value", value);
  }
}

function vocabularyFailures(document) {
  const failures = [];
  walk(document, (path, kind, text) => {
    for (const token of tokenize(text)) {
      if (FORBIDDEN.has(token)) {
        failures.push(`${path}: ${kind} "${text}" contains forbidden token "${token}"`);
      } else if (token === "hint" && !(kind === "key" && path === `$.${HINT_PROPERTY}`)) {
        failures.push(`${path}: "hint" may only be the top-level ${HINT_PROPERTY} property`);
      }
    }
  });
  return failures;
}

function schemaFailures(document) {
  if (document === null || typeof document !== "object" || Array.isArray(document)) {
    return ["$: journey file must be a JSON object"];
  }
  const failures = [];
  if (document[HINT_PROPERTY] !== true) failures.push(`$.${HINT_PROPERTY} must be true`);
  const journeys = document.journeys;
  if (journeys === null || typeof journeys !== "object" || Array.isArray(journeys)) {
    return [...failures, "$.journeys must be an object of journey arrays"];
  }
  for (const name of JOURNEY_NAMES) {
    const steps = journeys[name];
    if (!Array.isArray(steps) || steps.length === 0) {
      failures.push(`$.journeys.${name} must be a non-empty array of steps`);
      continue;
    }
    steps.forEach((step, index) => {
      const where = `$.journeys.${name}[${index}]`;
      if (step === null || typeof step !== "object" || Array.isArray(step)) {
        failures.push(`${where}: step must be an object`);
        return;
      }
      if (!ACTIONS.has(step.action)) {
        failures.push(`${where}.action "${step.action}" is not a known action`);
      }
      for (const field of ["params", "expect"]) {
        const value = step[field];
        if (value === null || typeof value !== "object" || Array.isArray(value)) {
          failures.push(`${where}.${field}: must be an object`);
        }
      }
    });
  }
  return failures;
}

function load() {
  return JSON.parse(readFileSync(JOURNEYS_PATH, "utf8"));
}

function mutated(change) {
  const document = load();
  change(document);
  return document;
}

test("file_has_both_journeys", () => {
  const document = load();
  assert.deepEqual(schemaFailures(document), []);
  assert.deepEqual(vocabularyFailures(document), []);
  for (const name of JOURNEY_NAMES) {
    assert.ok(Array.isArray(document.journeys[name]), `journey ${name} must be an array`);
    assert.ok(document.journeys[name].length > 0, `journey ${name} must not be empty`);
  }
});

test("unknown_action_fails", () => {
  assert.deepEqual(schemaFailures(load()), []);
  const document = mutated((value) => {
    value.journeys.enrollment.push({ action: "teleport", params: {}, expect: {} });
  });
  const failures = schemaFailures(document);
  assert.ok(
    failures.some((failure) => failure.includes('"teleport"') && failure.includes("not a known action")),
    failures.join("\n"),
  );
});

test("forbidden_token_in_any_key_or_value_fails", () => {
  for (const token of FORBIDDEN) {
    const inKey = mutated((value) => {
      value.journeys.enrollment[0].params[`${token}_id`] = "opaque";
    });
    assert.ok(
      vocabularyFailures(inKey).some((failure) => failure.includes(`"${token}"`)),
      `a parameter named ${token}_id must fail`,
    );

    const inValue = mutated((value) => {
      value.journeys.enrollment[0].params.label = `${token}-value`;
    });
    assert.ok(
      vocabularyFailures(inValue).some((failure) => failure.includes(`"${token}"`)),
      `a value of "${token}-value" must fail`,
    );
  }

  // tab_id is the canonical example: token "tab" is forbidden.
  const tabId = mutated((value) => {
    value.journeys.enrollment[0].params.tab_id = "opaque";
  });
  assert.equal(vocabularyFailures(tabId).length, 1);

  // camelCase boundaries are token boundaries too.
  const camelCase = mutated((value) => {
    value.journeys.enrollment[0].params.label = "deviceHandle";
  });
  assert.ok(vocabularyFailures(camelCase).some((failure) => failure.includes('"handle"')));

  assert.deepEqual(vocabularyFailures(load()), []);
});

test("transport_mtu_passes", () => {
  const document = mutated((value) => {
    value.journeys.enrollment[0].params.transport_mtu = 1024;
    value.journeys.enrollment[0].params.support_note = "transport and support are allowed";
  });
  assert.deepEqual(vocabularyFailures(document), []);
  assert.deepEqual(schemaFailures(document), []);
});

test("hint_only_as_the_named_property", () => {
  // The named top-level property is the one permitted "hint".
  assert.deepEqual(vocabularyFailures(load()), []);

  for (const [label, change] of [
    ["a nested parameter", (value) => { value.journeys.enrollment[0].params.platform_hint = "none"; }],
    ["a camelCase expectation", (value) => { value.journeys.enrollment[0].expect.platformHint = "none"; }],
    ["a string value", (value) => { value.journeys.enrollment[0].params.label = "hint"; }],
    ["a nested copy of the property name", (value) => { value.journeys.enrollment[0].params[HINT_PROPERTY] = true; }],
  ]) {
    const document = mutated(change);
    assert.ok(
      vocabularyFailures(document).some((failure) => failure.includes('"hint"')),
      `${label} must fail`,
    );
  }
});
