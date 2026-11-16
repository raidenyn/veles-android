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
// The schema below pins the committed journey contract: the exact file shape,
// journey set, action sequences, parameter names and expectation kinds, so an
// incomplete or out-of-contract journey cannot pass. Negative cases clone the
// real file and break one rule at a time.
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

// The committed contract: per journey, the ordered steps with the exact
// parameter names and expectation names each step carries.
const EXPECTED = {
  enrollment: [
    { action: "enroll", params: [], expect: ["session_class"] },
    { action: "reconnect", params: [], expect: ["session_class", "key_fresh"] },
  ],
  "protected-records": [
    { action: "seed", params: ["lifetimes"], expect: ["event_count"] },
    { action: "pull", params: [], expect: ["event_count"] },
    { action: "advance_clock", params: ["seconds"], expect: [] },
    { action: "pull", params: ["same_ids_as_previous_pull"], expect: ["event_count", "remaining_lifetime_range"] },
    { action: "schedule_push", params: ["delay_seconds"], expect: [] },
    { action: "advance_clock", params: ["seconds"], expect: [] },
    { action: "push", params: [], expect: ["event_count"] },
    { action: "ack", params: [], expect: [] },
    { action: "revoke", params: [], expect: [] },
    { action: "reconnect_expect_rejected", params: [], expect: ["rejected"] },
  ],
};

const SESSION_CLASSES = new Set(["enrollment", "full"]);

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNonNegativeInteger(value) {
  return Number.isInteger(value) && value >= 0;
}

function isLifetimeRange(value) {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    value.every(isNonNegativeInteger) &&
    value[0] <= value[1]
  );
}

// Parameter names are closed: an action may carry only these names, with these values.
const PARAMETERS = {
  lifetimes: (value) => Array.isArray(value) && value.length > 0 && value.every((entry) => Number.isInteger(entry) && entry > 0),
  seconds: isNonNegativeInteger,
  delay_seconds: isNonNegativeInteger,
  same_ids_as_previous_pull: (value) => value === true,
};

const EXPECTATIONS = {
  session_class: (value) => SESSION_CLASSES.has(value),
  event_count: isNonNegativeInteger,
  remaining_lifetime_range: isLifetimeRange,
  key_fresh: (value) => typeof value === "boolean",
  rejected: (value) => typeof value === "boolean",
};

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

function expectKeys(value, names, where, failures) {
  const actual = Object.keys(value).sort();
  const expected = [...names].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    failures.push(`${where}: expected keys [${expected}], found [${actual}]`);
  }
}

function schemaFailures(document) {
  if (!isPlainObject(document)) return ["$: journey file must be a JSON object"];
  const failures = [];
  expectKeys(document, [HINT_PROPERTY, "journeys"], "$", failures);
  if (document[HINT_PROPERTY] !== true) {
    failures.push(`$.${HINT_PROPERTY} must be true`);
  }
  const journeys = document.journeys;
  if (!isPlainObject(journeys)) {
    return [...failures, "$.journeys must be an object of exactly the two journeys"];
  }
  expectKeys(journeys, JOURNEY_NAMES, "$.journeys", failures);

  // An extra journey already fails the key set above; still name any unknown
  // action it hides, so the enum is enforced over the whole file.
  for (const [name, steps] of Object.entries(journeys)) {
    if (JOURNEY_NAMES.includes(name) || !Array.isArray(steps)) continue;
    steps.forEach((step, index) => {
      if (!isPlainObject(step) || !ACTIONS.has(step.action)) {
        failures.push(`$.journeys.${name}[${index}].action ${JSON.stringify(step?.action)} is not a known action`);
      }
    });
  }

  for (const name of JOURNEY_NAMES) {
    const steps = journeys[name];
    const plan = EXPECTED[name];
    if (!Array.isArray(steps)) {
      failures.push(`$.journeys.${name} must be an array of steps`);
      continue;
    }
    if (steps.length !== plan.length) {
      failures.push(`$.journeys.${name} must have exactly ${plan.length} steps, found ${steps.length}`);
      continue;
    }
    steps.forEach((step, index) => {
      const where = `$.journeys.${name}[${index}]`;
      const expected = plan[index];
      if (!isPlainObject(step)) {
        failures.push(`${where}: step must be an object`);
        return;
      }
      expectKeys(step, ["action", "params", "expect"], where, failures);
      if (!ACTIONS.has(step.action)) {
        failures.push(`${where}.action "${step.action}" is not a known action`);
      } else if (step.action !== expected.action) {
        failures.push(`${where}.action: expected "${expected.action}", found "${step.action}"`);
      }

      if (!isPlainObject(step.params)) {
        failures.push(`${where}.params must be an object`);
      } else {
        expectKeys(step.params, expected.params, `${where}.params`, failures);
        for (const [key, value] of Object.entries(step.params)) {
          const check = PARAMETERS[key];
          if (check !== undefined && !check(value)) {
            failures.push(`${where}.params.${key}: invalid value ${JSON.stringify(value)}`);
          }
        }
      }

      if (!isPlainObject(step.expect)) {
        failures.push(`${where}.expect must be an object`);
      } else {
        expectKeys(step.expect, expected.expect, `${where}.expect`, failures);
        for (const [key, value] of Object.entries(step.expect)) {
          const check = EXPECTATIONS[key];
          if (check !== undefined && !check(value)) {
            failures.push(`${where}.expect.${key}: invalid value ${JSON.stringify(value)}`);
          }
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
  assert.deepEqual(Object.keys(document.journeys), JOURNEY_NAMES);
  for (const name of JOURNEY_NAMES) {
    assert.ok(document.journeys[name].length > 0, `journey ${name} must not be empty`);
  }

  // The journey facts the spec and design require, pinned independently of the
  // generic schema above.
  const [enroll, reconnect] = document.journeys.enrollment;
  assert.equal(enroll.expect.session_class, "enrollment");
  assert.equal(reconnect.expect.session_class, "full");
  assert.equal(reconnect.expect.key_fresh, true);

  // seed, pull, advance_clock, pull, schedule_push, advance_clock, push, ack, revoke, reconnect_expect_rejected
  const [, firstPull, , secondPull, , , push, , , rejected] = document.journeys["protected-records"];
  assert.equal(firstPull.expect.event_count, 5);
  assert.equal(secondPull.expect.event_count, 3);
  assert.deepEqual(secondPull.expect.remaining_lifetime_range, [150, 450]);
  assert.equal(push.expect.event_count, 1);
  assert.equal(rejected.expect.rejected, true);
});

test("unknown_action_fails", () => {
  assert.deepEqual(schemaFailures(load()), []);
  const document = mutated((value) => {
    value.journeys.enrollment[1].action = "teleport";
  });
  const failures = schemaFailures(document);
  assert.ok(failures.some((failure) => failure.includes('"teleport"') && failure.includes("not a known action")), failures.join("\n"));
});

test("incomplete_or_out_of_contract_journeys_fail", () => {
  const protectedSteps = "protected-records";
  for (const [label, change, reason] of [
    ["a one-step journey", (value) => { value.journeys.enrollment = [value.journeys.enrollment[0]]; }, "must have exactly 2 steps"],
    ["an extra journey", (value) => { value.journeys.extra = [{ action: "enroll", params: {}, expect: {} }]; }, "$.journeys: expected keys"],
    ["an unknown action inside an extra journey", (value) => { value.journeys.extra = [{ action: "teleport", params: {}, expect: {} }]; }, "not a known action"],
    ["an unknown expectation name", (value) => { value.journeys.enrollment[0].expect.mystery = 1; }, "mystery"],
    ["an invalid expectation type", (value) => { value.journeys[protectedSteps][1].expect.event_count = "5"; }, "invalid value"],
    ["an inverted remaining-lifetime range", (value) => { value.journeys[protectedSteps][3].expect.remaining_lifetime_range = [450, 150]; }, "remaining_lifetime_range: invalid value"],
    ["a missing required expectation", (value) => { delete value.journeys.enrollment[1].expect.key_fresh; }, "key_fresh"],
    ["an unknown parameter name", (value) => { value.journeys[protectedSteps][0].params.mystery = 1; }, "params: expected keys"],
    ["a missing step", (value) => { value.journeys[protectedSteps].pop(); }, "exactly 10 steps"],
  ]) {
    const failures = schemaFailures(mutated(change));
    assert.ok(failures.length > 0, `${label} must fail the schema`);
    assert.ok(failures.some((failure) => failure.includes(reason)), `${label}: ${failures.join("\n")}`);
  }
  assert.deepEqual(schemaFailures(load()), []);
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
    value.journeys.enrollment[0].params.support = "transport and support are allowed";
  });
  assert.deepEqual(vocabularyFailures(document), []);
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
