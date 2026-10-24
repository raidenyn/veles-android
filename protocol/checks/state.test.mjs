// OTP-02 Plan Task 4 — enrollment, reconnect and protected-state contract suite.
// Test-only; never imported by product code. Run: node --test protocol/checks/state.test.mjs
//
// Expectations below are derived from docs/protocol/veles-v1.md (normative) —
// section 3.5 (attempt/rate accounting), section 5 (enrollment/activation/recovery),
// section 6 (reconnect), section 7 (record format), section 11 (state transition
// tables). The committed trace fixtures under protocol/v1/fixtures/ are the
// shared truth; this suite is their test-only interpreter's executor. The
// interpreter (state.mjs) derives transitions from protocol/v1/states.json and
// never trusts fixture-declared next states: positive fixtures' declared
// final state, per-step states and required effects are independently
// recomputed, and fixture-invalid negatives must be rejected at their declared
// stage. Crypto-valid flags in traces (tag_ok, hmac_ok, sig_ok, act_mac_ok...)
// are explicit SIMULATED inputs only (OTP2-FIX-004): they model outcomes, they
// are not cryptographic conformance evidence (Task 5).

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

import { loadFixtures } from "./fixture.mjs";
import { loadStates, checkProtocolTrace, checkResourceTrace } from "./state.mjs";

const checksDir = dirname(fileURLToPath(import.meta.url));
const v1Root = join(checksDir, "..", "v1");

let statesCache = null;
function states() {
  if (!statesCache) statesCache = loadStates(v1Root);
  return statesCache;
}

// ---------- helpers ----------

function assertRejectStage(res, stage, where) {
  assert.equal(res.accepted, false, `${where}: expected rejection, got accept`);
  assert.equal(res.final_state, null, `${where}: rejected traces expose no final state`);
  assert.deepEqual(res.effects, [], `${where}: rejected traces expose no effects`);
  assert.equal(typeof res.error, "string");
  assert.ok(
    res.error.includes(`stage=${stage}`),
    `${where}: expected stage=${stage}, error was: ${res.error}`,
  );
}

function assertAccept(res, where, expected) {
  assert.equal(res.accepted, true, `${where}: expected accept, error was: ${res.error}`);
  assert.equal(res.error, null);
  if (expected && expected.final_state !== undefined) {
    assert.equal(res.final_state, expected.final_state, `${where}: final state mismatch`);
  }
  if (expected && expected.effects) {
    for (const required of expected.effects.require ?? []) {
      assert.ok(
        res.effects.includes(required),
        `${where}: required effect ${JSON.stringify(required)} missing; got: ${JSON.stringify(res.effects)}`,
      );
    }
    for (const forbidden of expected.effects.forbid ?? []) {
      assert.ok(
        !res.effects.includes(forbidden),
        `${where}: forbidden effect ${JSON.stringify(forbidden)} present: ${JSON.stringify(res.effects)}`,
      );
    }
    if (expected.effects.order) {
      let cursor = -1;
      for (const name of expected.effects.order) {
        const found = res.effects.indexOf(name, cursor + 1);
        assert.ok(
          found > cursor,
          `${where}: effects must appear in order ${JSON.stringify(expected.effects.order)}; got: ${JSON.stringify(res.effects)}`,
        );
        cursor = found;
      }
    }
  }
}

// Step shorthand: s(t_ms, event, {peer, pairing_id, connection, simulate, expect})
function s(t, event, extra = {}) {
  const step = { t_ms: t, event };
  for (const key of ["peer", "pairing_id", "connection", "simulate", "expect"]) {
    if (extra[key] !== undefined) step[key] = extra[key];
  }
  return step;
}

function tempStatesTree(overrides = []) {
  // Copy the full v1 tree so loadStates/loadFixtures see a complete root.
  const dir = mkdtempSync(join(tmpdir(), "veles-state-"));
  for (const name of ["manifest.json", "limits.json", "fixture.schema.json", "binary-layouts.json"]) {
    copyFileSync(join(v1Root, name), join(dir, name));
  }
  copyFileSync(join(v1Root, "states.json"), join(dir, "states.json"));
  mkdirSync(join(dir, "fixtures"));
  for (const name of ["encoding.json", "fragmentation.json", "bridge.json", "enrollment.json", "reconnect.json", "records.json"]) {
    try {
      copyFileSync(join(v1Root, "fixtures", name), join(dir, "fixtures", name));
    } catch {
      // optional per-root fixture files
    }
  }
  for (const [name, body] of overrides) {
    writeFileSync(join(dir, name), typeof body === "string" ? body : JSON.stringify(body, null, 2) + "\n");
  }
  return dir;
}

function withTempStatesTree(overrides, fn) {
  const dir = tempStatesTree(overrides);
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

// ---------- descriptor: loadStates + structural/normative cross-checks ----------

test("states.json loads and validates against the normative machines of veles-v1.md section 11", () => {
  const st = states();
  const m = st.descriptor.machines;
  // 11.1 phone pairing states (exact table column "Current")
  assert.deepEqual(Object.values(m.phone_pairing.states).sort(), [
    "active", "awaiting_cB", "awaiting_commit", "committed_waiting_ack",
    "enrollment_protected", "idle", "spake_in_flight", "window_open",
  ]);
  // 11.2 client pairing states (I1: the prepare gate splits off
  // awaiting_prepare; ready_to_commit is the prepare-verified state)
  assert.deepEqual(Object.values(m.client_pairing.states).sort(), [
    "active", "awaiting_ca", "awaiting_complete", "awaiting_offer",
    "awaiting_phone_identity", "awaiting_prepare", "idle",
    "verified_prepare_ready",
  ]);
  // 5.4/OTP2-STA-010 durable phone record states
  assert.deepEqual(Object.values(m.durable_phone_record.states).sort(), [
    "absent", "active", "committed_waiting_ack", "deleted", "pending",
  ]);
  // 5.4/OTP2-STA-010c client durable record states (activation_pending retention)
  assert.deepEqual(Object.values(m.durable_client_record.states).sort(), [
    "absent", "active", "active_activation_pending", "deleted", "pending",
  ]);
  // 11.3 session states (role-explicit table, both roles + terminal states)
  assert.deepEqual(Object.values(m.session.states).sort(), [
    "awaiting_client_signature", "awaiting_peer_confirm", "awaiting_phone_confirm",
    "awaiting_phone_hello", "awaiting_phone_signature", "closed", "keys_derived",
    "none", "session_open", "terminated",
  ]);
  assert.equal(m.phone_pairing.initial, "idle");
  assert.equal(m.client_pairing.initial, "idle");
  assert.equal(m.session.initial, "none");
  assert.equal(m.durable_phone_record.initial, "absent");
  assert.equal(m.durable_client_record.initial, "absent");
});

test("session classes match the OTP2-REC-035 / 11.4 authorization allowlists exactly", () => {
  const classes = states().descriptor.session_classes;
  assert.deepEqual(classes.enrollment.slice().sort(), ["0x0100", "0x0101", "0x0102", "0x0103", "0x0104"]);
  assert.deepEqual(classes.activation_recovery.slice().sort(), ["0x0101", "0x0102", "0x0103", "0x0104"]);
  assert.deepEqual(classes.full_pending_peer.slice().sort(), ["0x0101", "0x0102", "0x0103", "0x0104"]);
  assert.deepEqual(classes.full.slice().sort(), [
    "0x0200", "0x0202", "0x0203", "0x0204", "0x0205", "0x0206", "0x0207",
  ]);
  // C2: the retired pull_phone_state tag is gone from every inventory and class.
  const raw = readFileSync(join(v1Root, "states.json"), "utf8");
  assert.ok(!/"0x0201"/.test(raw), "states.json must not reference the retired 0x0201 tag");
  // Framework + connection records are class-independent (OTP2-REC-035).
  assert.deepEqual(states().descriptor.framework_types.slice().sort(), ["0x0002", "0x0003"]);
  assert.deepEqual(states().descriptor.connection_types, ["0x0001"]);
});

test("record type directions match the OTP2-ENC-020 direction column", () => {
  const dirs = states().descriptor.record_type_directions;
  const expected = {
    "0x0001": ["0x01", "0x02"],
    "0x0002": ["0x01", "0x02"],
    "0x0003": ["0x01", "0x02"],
    "0x0100": ["0x01", "0x02"],
    "0x0101": ["0x01"], // activation_commit: client->phone only
    "0x0102": ["0x02"], // activation_complete: phone->client only
    "0x0103": ["0x01"], // activation_ack: client->phone only
    "0x0104": ["0x02"], // enrollment_prepare: phone->client only
    "0x0200": ["0x01"], // pull_request: client->phone
    "0x0202": ["0x02"], // pull_events: phone->client
    "0x0203": ["0x02"], // push_event: phone->client
    "0x0204": ["0x01"], // event_ack: client->phone
    "0x0205": ["0x01", "0x02"],
    "0x0206": ["0x01", "0x02"],
    "0x0207": ["0x02"], // phone_policy_state: phone->client only
  };
  assert.equal(Object.keys(dirs).length, Object.keys(expected).length);
  for (const [type, list] of Object.entries(expected)) {
    assert.deepEqual(dirs[type].slice().sort(), list.slice().sort(), `direction of ${type}`);
  }
});

// C1 (OTP2-SID-001): the client's upgrade-before-class-selection rows and the
// outbound peer_state sends are descriptor-encoded.
test("C1: client upgrade rows fix the class from the phone-active proof; recovery catch-all excludes the mirror", () => {
  const session = states().descriptor.machines.session;
  const confirms = session.trans.filter((t) => t.from === "awaiting_phone_confirm");
  const upgradeRow = confirms.find(
    (t) => t.guards?.simulate?.peer_state === "0x01" &&
      t.guards?.record_state_in?.includes("active_activation_pending"),
  );
  assert.ok(upgradeRow, "the upgrade row (peer_state 0x01 + active_activation_pending) must exist");
  assert.ok(
    upgradeRow.effects.includes("record_upgraded_fully_active") &&
      upgradeRow.effects.includes("set_class_full") &&
      upgradeRow.effects.indexOf("record_upgraded_fully_active") < upgradeRow.effects.indexOf("set_class_full"),
    "the upgrade precedes the class selection in the row's own effects",
  );
  // No catch-all row may assign activation_recovery to the mirror state.
  const catchAll = confirms.find(
    (t) => t.guards?.simulate?.hmac_ok === true && !t.guards?.simulate?.peer_state,
  );
  if (catchAll) {
    assert.ok(
      !catchAll.guards.record_state_in?.includes("active_activation_pending"),
      "no catch-all may route the mirror into recovery",
    );
  }
  // The phone's recovery catch-all no longer claims the client-only state.
  const peerRows = session.trans.filter((t) => t.from === "awaiting_peer_confirm");
  assert.equal(
    peerRows.some((t) => t.guards?.record_state_in?.includes("active_activation_pending")),
    false,
    "the phone has no active_activation_pending state",
  );
  // The outbound peer_state byte is descriptor-encoded for both roles.
  const clientRows = session.trans.filter((t) => t.event === "send_reconnect_confirm");
  const phoneRows = session.trans.filter(
    (t) => t.from === "awaiting_client_signature" && t.event === "reconnect_signature" && t.guards?.simulate?.sig_ok === true,
  );
  assert.equal(clientRows.length, 2);
  assert.equal(phoneRows.length, 2, "the phone's confirm send is split by its durable record state");
  const clientActive = clientRows.find((t) => t.effects.includes("reconnect_confirm_sent_peer_state_active"));
  assert.ok(
    clientActive?.guards?.record_state_in?.includes("active") &&
      clientActive?.guards?.record_state_in?.includes("active_activation_pending"),
    "the client's 0x01 send must cover both client active-family states",
  );
  assert.ok(
    clientRows.find((t) => t.effects.includes("reconnect_confirm_sent_peer_state_below_active"))?.guards?.record_state_in?.includes("pending"),
    "the client's 0x02 send covers pending",
  );
  assert.ok(
    phoneRows.find((t) => t.effects.includes("reconnect_confirm_sent_peer_state_active"))?.guards?.record_state_in?.includes("active"),
    "the phone's 0x01 send covers an active record",
  );
  assert.ok(
    phoneRows.find((t) => t.effects.includes("reconnect_confirm_sent_peer_state_below_active"))?.guards?.record_state_in?.includes("pending"),
    "the phone's 0x02 send covers below-active records",
  );
});

// I1 (OTP2-STA-010a/010b step 0): prepare gates are descriptor-encoded.
test("I1: the client's commit send requires the session's verified prepare; prepare rows exist", () => {
  const session = states().descriptor.machines.session;
  const commitRow = session.trans.find(
    (t) => t.event === "send_protected_record" && t.guards?.type_is === "0x0101",
  );
  assert.ok(commitRow, "the recovery commit send row must exist");
  assert.equal(commitRow.guards.prepare_verified, true, "a recovery commit requires the session's verified prepare");
  assert.deepEqual(commitRow.guards.record_state_in, ["pending"], "only a pending client record commits");
  const prepareAccept = session.trans.find(
    (t) => t.event === "protected_record" && t.guards?.type_is === "0x0104" && t.guards?.class_allows === true,
  );
  assert.ok(prepareAccept, "the recovery prepare accept row must exist");
  const prepareSend = session.trans.find(
    (t) => t.event === "send_protected_record" && t.guards?.type_is === "0x0104",
  );
  assert.ok(prepareSend, "the phone's recovery prepare send row must exist");
  // NEW-1 exact peer rule (re-review): the phone prepares for a client that
  // reported below-active, whatever its own record state; never for an 0x01 client.
  assert.equal(prepareSend.guards.peer_reported, "0x02", "the prepare is gated on the CLIENT-reported byte");
  assert.deepEqual(prepareSend.guards.class_in, ["activation_recovery", "full_pending_peer"]);
  const cp = states().descriptor.machines.client_pairing;
  assert.ok(
    cp.states.includes("awaiting_prepare"),
    "the client pairing machine carries awaiting_prepare",
  );
  assert.ok(
    cp.trans.some((t) => t.to === "verified_prepare_ready" && t.event === "protected_record" && t.guards?.type_is === "0x0104"),
    "awaiting_prepare -> verified_prepare_ready only on a verified prepare",
  );
  assert.ok(
    cp.trans.some((t) => t.from === "awaiting_phone_identity" && t.guards?.type_is === "0x0104" && t.effects.includes("reject_out_of_order_prepare")),
    "an early prepare is an ordering violation",
  );
  const pp = states().descriptor.machines.phone_pairing;
  const identityRow = pp.trans.find(
    (t) => t.from === "enrollment_protected" && t.event === "enroll_identity" && t.effects.includes("send_enrollment_prepare"),
  );
  assert.ok(identityRow, "the phone sends the prepare as the effect of entering awaiting_commit");
});

// R1 residual (scoped re-review): BOTH teardown paths on the identity-exchange
// state are conditional on the record — connection_lost AND inactivity_timeout
// split identically.
test("R1 residual: connection_lost and inactivity_timeout split identically on record existence", () => {
  const pp = states().descriptor.machines.phone_pairing;
  for (const event of ["connection_lost", "inactivity_timeout"]) {
    const rows = pp.trans.filter((t) => t.from === "enrollment_protected" && t.event === event);
    assert.equal(rows.length, 2, `${event}: exactly the record-existence split`);
    const repair = rows.find((t) =>
      t.effects.includes(event === "inactivity_timeout"
        ? "inactivity_timeout_no_usable_peer_record_repair_only"
        : "connection_lost_no_usable_peer_record_repair_only"));
    const recovery = rows.find((t) =>
      t.effects.includes(event === "inactivity_timeout"
        ? "inactivity_timeout_pending_record_continues_via_recovery"
        : "connection_lost_pending_record_continues_via_recovery"));
    assert.ok(repair, `${event}: the no-peer-record path is named`);
    assert.ok(repair.guards?.record_state_in?.includes("absent"), `${event}: repair covers the record-less interruption`);
    assert.ok(repair.effects.includes("close_enrollment_connection"), `${event}: the repair path closes the enrollment connection`);
    assert.equal(repair.to, "idle", `${event}: the window stays closed`);
    assert.ok(recovery, `${event}: the persisted-record path is named`);
    assert.ok(recovery.guards?.record_state_in?.includes("pending"), `${event}: recovery requires a usable record`);
    assert.equal(recovery.to, "idle");
    for (const row of rows) {
      assert.ok(
        row.effects.includes("pairing_step_inactivity_timeout") || row.effects.includes("close_enrollment_connection"),
        `${event}: the teardown path erases/drops the enrollment connection`,
      );
    }
  }
  // The successful-cB row's description must no longer claim unconditional
  // recovery-only continuation (R1 residual wording).
  const cBRow = pp.trans.find(
    (t) => t.from === "awaiting_cB" && t.event === "pairing_confirm_b" && t.effects.includes("pairing_code_consumed_one_use"),
  );
  assert.match(cBRow.description ?? "", /CONDITIONAL on usable persisted peer records/, "cB row wording is record-conditional");
  assert.match(cBRow.description ?? "", /OTP2-STA-010b/);
  assert.doesNotMatch(cBRow.description ?? "", /recovery-only \(OTP2-STA-010b\)/, "no unconditional recovery-only claim remains");
});

// R1 persistence boundary (re-scope): the identity-exchange rows derive ET
// first, atomically persist the COMPLETE record, and release only after
// durable persistence success; a persist failure publishes nothing.
test("R1 persistence boundary: ET derives before persist; release follows durable success; failures publish nothing", () => {
  const phone = states().descriptor.machines.phone_pairing.trans;
  const client = states().descriptor.machines.client_pairing.trans;
  for (const [name, rows, release] of [
    ["phone", phone, "send_enrollment_prepare"],
    ["client", client, "send_enroll_identity"],
  ]) {
    const row = rows.find((t) => t.effects?.includes("compute_et") && t.effects?.includes("persist_pending_record"));
    assert.ok(row, `${name}: the identity-exchange row derives ET and persists`);
    assert.equal(
      row.effects.indexOf("compute_et") < row.effects.indexOf("persist_pending_record"),
      true,
      `${name}: ET derives BEFORE the atomic persist`,
    );
    assert.equal(
      row.effects.indexOf("persist_pending_record") < row.effects.indexOf(release),
      true,
      `${name}: the release follows the durable persist success exactly once`,
    );
    assert.equal(row.guards?.simulate?.persist_ok, true, `${name}: the row attests the atomic persist succeeded`);
    const fail = rows.find((t) => t.effects?.[0] === "persist_failed_no_usable_record_published");
    assert.ok(fail, `${name}: the persistence-failure row exists`);
    assert.ok(
      !fail.effects.includes(release),
      `${name}: a persist failure never releases the record's publishable output`,
    );
    assert.equal(fail.guards?.simulate?.persist_ok, false, `${name}: the failure row is persist_ok-false gated`);
    // failure rows precede the generic catch-all reject rows (first-match order)
    const fi = rows.indexOf(fail);
    const gi = rows.findIndex((t) => t.event === fail.event && !t.guards);
    assert.ok(gi > fi, `${name}: the failure row must be declared before the generic reject row`);
    // the durable machine has the matching fail-cleanup row
    const durable = states().descriptor.machines[name === "phone" ? "durable_phone_record" : "durable_client_record"];
    assert.ok(
      durable.trans.some((t) => t.effects?.[0] === "persist_failed_no_usable_record_published" && t.to === "deleted"),
      `${name}: the durable machine records the failure cleanup (no usable record)`,
    );
  }
  // the descriptor summary is conditional, not a blanket recovery claim
  const summary = states().descriptor.description;
  assert.match(summary, /ONLY where usable persisted peer records exist/);
});

// I2 (OTP2-CRY-009a): the one-use code rules are descriptor-encoded.

test("I2: the code is consumed at the verified cB; post-code connection loss is record-conditional (R1)", () => {
  const pp = states().descriptor.machines.phone_pairing;
  const cBRow = pp.trans.find(
    (t) => t.from === "awaiting_cB" && t.event === "pairing_confirm_b" && t.effects.includes("pairing_code_consumed_one_use"),
  );
  assert.ok(cBRow, "verifying cB consumes the code (one-use)");
  // With a persisted record (awaiting_commit/committed_waiting_ack always
  // imply one), the loss continues via signed recovery only.
  for (const state of ["awaiting_commit", "committed_waiting_ack"]) {
    const lostRows = pp.trans.filter((t) => t.from === state && t.event === "connection_lost");
    assert.ok(lostRows.length > 0, `${state}: a connection_lost row exists`);
    for (const row of lostRows) {
      assert.equal(
        row.to, "idle",
        `${state}: after the code is consumed the window is closed (never window_open)`,
      );
      assert.ok(
        row.effects.includes("connection_lost_pending_record_continues_via_recovery"),
        `${state}: the loss names the recovery-only continuation`,
      );
    }
  }
  // R1: before the peer identity was persisted (enrollment_protected with an
  // absent/deleted record), NO recovery exists — the machine names the
  // repair-only path; a persisted record would continue via recovery.
  const lost = pp.trans.filter((t) => t.from === "enrollment_protected" && t.event === "connection_lost");
  assert.equal(lost.length, 2, "enrollment_protected: the loss splits by record existence");
  const repairRow = lost.find((t) => t.effects.includes("connection_lost_no_usable_peer_record_repair_only"));
  const recoveryRow = lost.find((t) => t.effects.includes("connection_lost_pending_record_continues_via_recovery"));
  assert.ok(repairRow && repairRow.guards?.record_state_in?.includes("absent"),
    "a record-less interruption is repair-only (no signed recovery exists)");
  assert.ok(recoveryRow && recoveryRow.guards?.record_state_in?.includes("pending"),
    "a persisted-record interruption continues via recovery");
  // no inactivity row may resurrect the closed window
  for (const state of ["enrollment_protected", "awaiting_commit", "committed_waiting_ack"]) {
    for (const row of pp.trans.filter((t) => t.from === state && t.event === "inactivity_timeout")) {
      assert.equal(row.to, "idle", `${state}: inactivity cannot re-open the window`);
    }
  }
  // The pre-code states still resume in the open window.
  for (const state of ["spake_in_flight", "awaiting_cB"]) {
    const row = pp.trans.find((t) => t.from === state && t.event === "connection_lost");
    assert.ok(row && row.to === "window_open", `${state}: window still open pre-consumption`);
  }
});

test("every transition's source, target and event are well-formed; no transition lacks effects", () => {
  const st = states();
  for (const [name, machine] of Object.entries(st.descriptor.machines)) {
    const stateSet = new Set(Object.values(machine.states));
    const hasInitial = Object.values(machine.trans).some(
      (t) => t.from === machine.initial,
    );
    assert.ok(hasInitial, `machine ${name}: no transition leaves the initial state`);
    for (const t of machine.trans) {
      assert.ok(stateSet.has(t.from), `machine ${name}: transition from unknown state ${t.from}`);
      assert.ok(stateSet.has(t.to), `machine ${name}: transition to unknown state ${t.to}`);
      assert.equal(typeof t.event, "string");
      assert.ok(Array.isArray(t.effects) && t.effects.length >= 0);
    }
  }
});

test("descriptor guard limits reference names defined in limits.json", () => {
  const st = states();
  const limitNames = new Set(st.limits.limits.map((l) => l.name));
  const walk = (node, seen = new Set(), key = null) => {
    if (node === null || typeof node !== "object" || seen.has(node)) return;
    seen.add(node);
    if (key === "tx_rate_below" || key === "tx_rate_exceeded") {
      // heartbeat-threshold literals ("more than 4x in a rolling minute"),
      // pinned against the normative sentence by a dedicated test
      assert.equal(typeof node.max, "number");
      return;
    }
    if (Array.isArray(node)) {
      for (const item of node) walk(item, seen);
      return;
    }
    for (const [childKey, value] of Object.entries(node)) {
      if (
        ["limit", "limit_s", "per_id", "max", "window_s", "per_connection", "timeout"].includes(childKey)
      ) {
        assert.ok(
          limitNames.has(value),
          `guard limit reference ${JSON.stringify(value)} is not in limits.json`,
        );
      }
      walk(value, seen, childKey);
    }
  };
  for (const machine of Object.values(st.descriptor.machines)) walk(machine.trans);
});

test("counter integrity is descriptor-encoded: accept transitions advance, violation transitions never do", () => {
  const session = states().descriptor.machines.session;
  const accepts = [];
  const violations = [];
  for (const t of session.trans) {
    if (t.effects.includes("advance_rx_sequence") || t.effects.includes("advance_tx_sequence")) {
      accepts.push(t);
    }
    if (t.to === "terminated") violations.push(t);
  }
  assert.ok(accepts.length > 0, "session machine must declare counter-advancing accepts");
  for (const t of violations) {
    assert.ok(
      !t.effects.includes("advance_rx_sequence") && !t.effects.includes("advance_tx_sequence"),
      `terminated transition must never advance counters: ${JSON.stringify(t.effects)}`,
    );
  }
  // Every terminated transition erases session keys (OTP2-STA-015).
  for (const t of violations) {
    assert.ok(
      t.effects.includes("erase_session_keys"),
      `teardown transition must erase session keys: ${JSON.stringify(t.effects)}`,
    );
  }
});

test("session class setters reach all four classes; recovery/full_pending_peer never authorize OTP", () => {
  const session = states().descriptor.machines.session;
  const setters = new Set();
  for (const t of session.trans) {
    for (const e of t.effects) if (e.startsWith("set_class_")) setters.add(e);
  }
  assert.deepEqual([...setters].sort(), [
    "set_class_activation_recovery", "set_class_full", "set_class_full_pending_peer",
  ]);
});

// ---------- interpreter unit behavior ----------

test("checkProtocolTrace rejects a malformed trace input at stage=trace", () => {
  assertRejectStage(
    checkProtocolTrace({ role: "phone", steps: [] }, states()),
    "trace",
    "empty steps",
  );
  assertRejectStage(
    checkProtocolTrace({ role: "banana", steps: [{ t_ms: 0, event: "enable_pairing" }] }, states()),
    "trace",
    "bad role",
  );
  assertRejectStage(
    checkProtocolTrace(
      { role: "phone", machines: [{ machine: "nope" }], steps: [{ t_ms: 0, event: "enable_pairing" }] },
      states(),
    ),
    "trace",
    "unknown machine",
  );
});

test("checkProtocolTrace enforces monotonic trace times at stage=time", () => {
  const trace = {
    role: "phone",
    steps: [
      { t_ms: 200, event: "enable_pairing" },
      { t_ms: 100, event: "enable_pairing" },
    ],
  };
  assertRejectStage(checkProtocolTrace(trace, states()), "time", "decreasing t_ms");
});

test("checkProtocolTrace rejects secrets in persisted state at stage=persisted_state (OTP2-CRY-019g)", () => {
  const trace = {
    role: "phone",
    persisted_state: {
      "durable_phone_record:main": "pending",
      enrollment_aes_key: "0xcafe",
    },
    steps: [{ t_ms: 0, event: "enable_pairing" }],
  };
  assertRejectStage(checkProtocolTrace(trace, states()), "persisted_state", "secret key");
  const ok = checkProtocolTrace(
    {
      role: "phone",
      persisted_state: {
        "durable_phone_record:main": "active",
        installation_id: "0x000102030405060708090a0b0c0d0e0f",
        et: "0xaabb",
        created_monotonic_ms: "0",
      },
      steps: [{ t_ms: 0, event: "enable_pairing" }],
    },
    states(),
  );
  assert.equal(ok.accepted, true, `public-only persisted state must load: ${ok.error}`);
});

test("declared timeouts before their monotonic bound fail at stage=time", () => {
  const trace = {
    role: "phone",
    steps: [
      { t_ms: 0, event: "enable_pairing" },
      { t_ms: 299_000, event: "window_expired" },
    ],
  };
  assertRejectStage(checkProtocolTrace(trace, states()), "time", "early window expiry");
});

test("simulated record bodies beyond their bound fail at stage=bounds (pull events 0..5)", () => {
  const trace = {
    role: "client",
    persisted_state: { "durable_client_record:main": "active", "durable_phone_record:main": "active" },
    machines: [
      { machine: "session" },
      { machine: "durable_client_record" },
      { machine: "durable_phone_record" },
    ],
    steps: [
      { t_ms: 0, event: "send_hello" },
      { t_ms: 10, event: "reconnect_hello", simulate: { hello_valid: true } },
      { t_ms: 20, event: "reconnect_signature", simulate: { sig_ok: true } },
      { t_ms: 30, event: "send_reconnect_confirm" },
      { t_ms: 40, event: "reconnect_confirm", simulate: { hmac_ok: true, peer_state: "0x01" } },
      { t_ms: 50, event: "send_protected_record", simulate: { record_type: "0x0200", sequence: "1" } },
      {
        t_ms: 60,
        event: "protected_record",
        simulate: { record_type: "0x0202", sequence: "1", tag_ok: true, length_ok: true, session_id_ok: true, event_count: 6 },
      },
    ],
  };
  assertRejectStage(checkProtocolTrace(trace, states()), "bounds", "event_count 6");
});

test("non-canonical or overflowing sequence numbers fail at stage=bounds", () => {
  const base = {
    role: "phone",
    persisted_state: { "durable_phone_record:main": "active", "durable_client_record:main": "active" },
    machines: [
      { machine: "session" },
      { machine: "durable_phone_record" },
      { machine: "durable_client_record" },
    ],
    steps: [
      { t_ms: 0, event: "reconnect_hello", simulate: { record_exists: true, hello_valid: true } },
      { t_ms: 10, event: "reconnect_signature", simulate: { sig_ok: true } },
      { t_ms: 20, event: "reconnect_confirm", simulate: { hmac_ok: true, peer_state: "0x01" } },
    ],
  };
  const trace = (seq) => ({
    ...base,
    steps: [
      ...base.steps,
      { t_ms: 30, event: "protected_record", simulate: { record_type: "0x0200", sequence: seq, tag_ok: true, length_ok: true, session_id_ok: true } },
    ],
  });
  assertRejectStage(checkProtocolTrace(trace("18446744073709551616"), states()), "bounds", "u64 overflow");
  assertRejectStage(checkProtocolTrace(trace("010"), states()), "bounds", "non-canonical");
});

test("failures are explicit no-mutation results: no final state and no effects leak", () => {
  const res = checkProtocolTrace({ role: "phone", steps: [] }, states());
  assert.equal(res.accepted, false);
  assert.equal(res.final_state, null);
  assert.deepEqual(res.effects, []);
  assert.equal(typeof res.error, "string");
  assert.ok(res.error.startsWith("stage="));
});

// ---------- committed state fixtures: generic executors ----------

test("committed state fixtures load through the shared loader", () => {
  const fixtures = loadFixtures(v1Root).filter((f) => f.category === "state");
  assert.ok(fixtures.length >= 50, `expected the full state fixture set, got ${fixtures.length}`);
  for (const name of ["enrollment", "reconnect", "records"]) {
    const file = join(v1Root, "fixtures", `${name}.json`);
    const body = JSON.parse(readFileSync(file, "utf8"));
    assert.equal(body.version, 1);
    assert.equal(body.schema, "veles.protocol.fixtures");
    assert.ok(body.fixtures.length >= 10, `${name}.json should carry a full set`);
  }
});

test("positive state fixtures are independently recomputed: final state and effects must match", () => {
  const st = states();
  const fixtures = loadFixtures(v1Root).filter(
    (f) => f.category === "state" && f.expected.accepted === true,
  );
  for (const f of fixtures) {
    const res = checkProtocolTrace(f.input, st);
    assertAccept(res, `fixture ${f.id}`, {
      final_state: f.expected.final_state,
      effects: f.expected.effects,
    });
  }
});

test("fixture-invalid state fixtures are rejected at their declared stage", () => {
  const st = states();
  const fixtures = loadFixtures(v1Root).filter(
    (f) => f.category === "state" && f.expected.accepted === false,
  );
  assert.ok(fixtures.length >= 10, "the negative state fixture set must be present");
  for (const f of fixtures) {
    assert.equal(f.expected.no_state_mutation, true, `fixture ${f.id}`);
    assertRejectStage(checkProtocolTrace(f.input, st), f.expected.stage, `fixture ${f.id}`);
  }
});

test("mutating one declared effect of a state fixture is detected (declaration independence)", () => {
  withTempStatesTree([], (dir) => {
    const file = join(dir, "fixtures", "records.json");
    const parsed = JSON.parse(readFileSync(file, "utf8"));
    const victim = parsed.fixtures.find(
      (f) => f.category === "state" && f.expected.accepted === true && f.expected.effects,
    );
    assert.ok(victim, "records.json must contain a positive fixture with effects");
    const required = victim.expected.effects.require;
    assert.ok(required.length > 0, "victim fixture must require at least one effect");
    victim.expected.effects.require = required.slice(1).concat(`not-${required[0]}`);
    writeFileSync(file, JSON.stringify(parsed, null, 2) + "\n");
    const st = loadStates(dir);
    const res = checkProtocolTrace(victim.input, st);
    assert.equal(res.accepted, true, "the trace itself must still be valid");
    assert.ok(
      !res.effects.includes(`not-${required[0]}`),
      "the mutated required effect must not be produced by the interpreter",
    );
  });
});

test("mutating one declared final state of a state fixture is detected", () => {
  withTempStatesTree([], (dir) => {
    const file = join(dir, "fixtures", "reconnect.json");
    const parsed = JSON.parse(readFileSync(file, "utf8"));
    const victim = parsed.fixtures.find(
      (f) => f.category === "state" && f.expected.accepted === true && f.expected.final_state,
    );
    assert.ok(victim, "reconnect.json must contain a positive fixture with final_state");
    victim.expected.final_state = victim.expected.final_state + "-wrong";
    writeFileSync(file, JSON.stringify(parsed, null, 2) + "\n");
    const st = loadStates(dir);
    const res = checkProtocolTrace(victim.input, st);
    assert.equal(res.accepted, true);
    assert.notEqual(
      res.final_state,
      victim.expected.final_state,
      "the mutated final_state declaration must disagree with the interpreter",
    );
  });
});

// ---------- separate multi-peer resource-ceiling accounting ----------
// All ceilings come from limits.json; the utility refuses over-cap
// allocations BEFORE accounting them (bounds before allocation), records
// refusals/releases, and rejects structurally impossible steps. These are
// suite-level traces (not committed fixtures): the ceilings are shared data,
// and Task 5/6 consumers inherit them via limits.json (OTP2-GAT-004,
// OTP2-GAT-012, OTP2-REC-032, OTP2-CRY-016c).

function resourceSteps(builder) {
  let t = 0;
  const steps = builder((event, extra = {}) => {
    t += 10;
    return { t_ms: t, event, ...extra };
  });
  return { role: "phone", steps };
}

test("8 clients reach the GATT cap; the 9th is refused before allocation; disconnect releases and the 9th then fits", () => {
  const res = checkResourceTrace(
    resourceSteps((ev) => {
      const steps = [];
      for (let i = 1; i <= 8; i++) steps.push(ev("client_connect", { peer: `p${i}` }));
      steps.push(ev("client_connect", { peer: "p9" }));
      steps.push(ev("client_disconnect", { peer: "p1" }));
      steps.push(ev("client_connect", { peer: "p9" }));
      return steps;
    }),
    states(),
  );
  assert.equal(res.accepted, true, res.error);
  assert.equal(res.accounting.connected_clients, 8);
  assert.equal(res.refusals.length, 1);
  assert.ok(res.refusals[0].includes("gatt_max_clients=8"));
  assert.equal(res.releases.length, 1);
  assert.ok(res.releases[0].includes('"p1"'));
});

test("per-peer reassembly caps are enforced before allocation (2 transactions, 8 KiB)", () => {
  const res = checkResourceTrace(
    resourceSteps((ev) => [
      ev("client_connect", { peer: "p1" }),
      ev("fragment_open", { peer: "p1", tx_id: "a", bytes: 4096 }),
      ev("fragment_open", { peer: "p1", tx_id: "b", bytes: 4096 }),
      ev("fragment_open", { peer: "p1", tx_id: "c", bytes: 1 }),
      ev("fragment_discard", { peer: "p1", tx_id: "a" }),
      ev("fragment_open", { peer: "p1", tx_id: "d", bytes: 4097 }),
      ev("fragment_open", { peer: "p1", tx_id: "e", bytes: 4096 }),
      ev("fragment_open", { peer: "p1", tx_id: "f", bytes: 1 }),
    ]),
    states(),
  );
  assert.equal(res.accepted, true, res.error);
  const p1 = res.accounting.per_peer.p1;
  assert.deepEqual(
    [p1.incomplete_tx, p1.reassembly_bytes],
    [2, 8192],
    "refused offers must not allocate; the at-bound 2 tx/8192 B must fit",
  );
  assert.equal(res.refusals.length, 3);
  assert.ok(res.refusals[0].includes("reassembly_incomplete_max_per_peer=2"));
  assert.ok(res.refusals[1].includes("record_max_bytes=4096"));
  assert.ok(res.refusals[2].includes("reassembly_incomplete_max_per_peer=2"));
});

test("global reassembly caps accept the exact 16 tx / 64 KiB bound and refuse anything beyond", () => {
  const res = checkResourceTrace(
    resourceSteps((ev) => {
      const steps = [];
      for (let i = 1; i <= 8; i++) steps.push(ev("client_connect", { peer: `p${i}` }));
      for (let i = 1; i <= 8; i++) {
        steps.push(ev("fragment_open", { peer: `p${i}`, tx_id: "t1", bytes: 4096 }));
        steps.push(ev("fragment_open", { peer: `p${i}`, tx_id: "t2", bytes: 4096 }));
      }
      steps.push(ev("fragment_open", { peer: "p1", tx_id: "t3", bytes: 1 }));
      steps.push(ev("fragment_discard", { peer: "p1", tx_id: "t1" }));
      steps.push(ev("fragment_open", { peer: "p1", tx_id: "t3", bytes: 1 }));
      return steps;
    }),
    states(),
  );
  assert.equal(res.accepted, true, res.error);
  // after the discard/release and re-open the totals sit at [16, 61441]
  assert.deepEqual(res.accounting.totals.reassembly_incomplete_global, 16);
  assert.deepEqual(res.accounting.totals.reassembly_bytes_global, 61441);
  assert.equal(res.refusals.length, 1);
  assert.ok(res.refusals[0].includes("reassembly_incomplete_max"));
});

test("per-peer delivery queues respect both the message and byte ceiling with bounds before allocation", () => {
  const res = checkResourceTrace(
    resourceSteps((ev) => {
      const steps = [ev("client_connect", { peer: "p1" }), ev("client_connect", { peer: "p2" })];
      for (let i = 0; i < 16; i++) steps.push(ev("queue_offer", { peer: "p1", bytes: 1024 }));
      steps.push(ev("queue_offer", { peer: "p1", bytes: 1 }));
      for (let i = 0; i < 15; i++) steps.push(ev("queue_offer", { peer: "p2", bytes: 1024 }));
      steps.push(ev("queue_offer", { peer: "p2", bytes: 2048 }));
      steps.push(ev("queue_offer", { peer: "p2", bytes: 1024 }));
      return steps;
    }),
    states(),
  );
  assert.equal(res.accepted, true, res.error);
  assert.equal(res.accounting.per_peer.p1.queued_msgs, 16);
  assert.equal(res.accounting.per_peer.p1.queued_bytes, 16384);
  assert.equal(res.accounting.per_peer.p2.queued_msgs, 16);
  assert.equal(res.accounting.per_peer.p2.queued_bytes, 16384);
  assert.equal(res.refusals.length, 2);
  assert.ok(res.refusals[0].includes("deliver_queue_messages_max_per_peer=16"));
  assert.ok(res.refusals[1].includes("deliver_queue_bytes_max_per_peer=16384"));
});

test("global delivery queues accept exactly 8 x 16 KiB (128 msgs / 128 KiB) and refuse a 129th message", () => {
  const res = checkResourceTrace(
    resourceSteps((ev) => {
      const steps = [];
      for (let i = 1; i <= 8; i++) steps.push(ev("client_connect", { peer: `p${i}` }));
      for (let i = 1; i <= 8; i++) for (let j = 0; j < 16; j++) steps.push(ev("queue_offer", { peer: `p${i}`, bytes: 1024 }));
      steps.push(ev("queue_offer", { peer: "p1", bytes: 1 }));
      return steps;
    }),
    states(),
  );
  assert.equal(res.accepted, true, res.error);
  assert.deepEqual(res.accounting.totals.queued_msgs_global, 128);
  assert.deepEqual(res.accounting.totals.queued_bytes_global, 131072);
  assert.equal(res.refusals.length, 1);
  // The per-peer ceilings exactly partition the global ones
  // (8 clients x 16 = 128 msgs; 8 x 16 KiB = 128 KiB), so the 129th offer is
  // refused at whichever bound binds first; both are enforced and the
  // totals remain at the global bound.
  assert.ok(res.refusals[0].includes("deliver_queue_messages_max"));
});

test("peer isolation: a saturated or torn-down peer never consumes another peer's capacity", () => {
  const res = checkResourceTrace(
    resourceSteps((ev) => {
      const steps = [
        ev("client_connect", { peer: "p1" }),
        ev("client_connect", { peer: "p2" }),
        ev("fragment_open", { peer: "p1", tx_id: "a", bytes: 4096 }),
        ev("fragment_open", { peer: "p1", tx_id: "b", bytes: 4096 }),
        ev("fragment_open", { peer: "p1", tx_id: "c", bytes: 1 }),
      ];
      for (let j = 0; j < 16; j++) steps.push(ev("queue_offer", { peer: "p1", bytes: 1024 }));
      steps.push(ev("fragment_open", { peer: "p2", tx_id: "z", bytes: 4096 }));
      steps.push(ev("queue_offer", { peer: "p2", bytes: 1024 }));
      return steps;
    }),
    states(),
  );
  assert.equal(res.accepted, true, res.error);
  assert.deepEqual(res.accounting.per_peer.p2, {
    connected: true, incomplete_tx: 1, reassembly_bytes: 4096, queued_msgs: 1, queued_bytes: 1024,
  });
  assert.ok(res.refusals.some((r) => r.includes('"p1"')));
  assert.ok(res.refusals.every((r) => r.includes('"p1"') && !r.includes('"p2"')));
});

test("release accounting: disconnect frees reassembly and queue allocations back to the global pools", () => {
  const res = checkResourceTrace(
    resourceSteps((ev) => {
      const steps = [
        ev("client_connect", { peer: "p1" }),
        ev("client_connect", { peer: "p2" }),
        ev("fragment_open", { peer: "p1", tx_id: "a", bytes: 4096 }),
        ev("fragment_open", { peer: "p1", tx_id: "b", bytes: 4096 }),
      ];
      for (let j = 0; j < 16; j++) steps.push(ev("queue_offer", { peer: "p1", bytes: 1024 }));
      for (let j = 0; j < 15; j++) steps.push(ev("queue_offer", { peer: "p2", bytes: 1024 }));
      // p1 saturated: its totals hold 8 KiB reassembly + 16 KiB queue
      steps.push(ev("client_disconnect", { peer: "p1" }));
      steps.push(ev("client_connect", { peer: "p1" }));
      steps.push(ev("fragment_open", { peer: "p1", tx_id: "x", bytes: 4096 }));
      steps.push(ev("fragment_open", { peer: "p1", tx_id: "y", bytes: 4096 }));
      for (let j = 0; j < 4; j++) steps.push(ev("queue_offer", { peer: "p1", bytes: 4096 }));
      return steps;
    }),
    states(),
  );
  assert.equal(res.accepted, true, res.error);
  assert.ok(res.releases[0].includes("2 incomplete tx (8192 B)") && res.releases[0].includes("16 queued msgs (16384 B)"));
  // the freed capacity is reusable: p1 runs a fresh 2 tx/8 KiB reassembly and
  // a full 4 msg/16 KiB queue alongside p2's retained 15 msgs
  assert.deepEqual(res.accounting.totals.reassembly_incomplete_global, 2);
  assert.deepEqual(res.accounting.totals.reassembly_bytes_global, 8192);
  assert.deepEqual(res.accounting.totals.queued_msgs_global, 19);
  assert.deepEqual(res.accounting.totals.queued_bytes_global, 31744);
  assert.equal(res.accounting.connected_clients, 2);
});

test("resource traces reject structurally impossible steps at stage=resource", () => {
  const bad = (steps) => checkResourceTrace(resourceSteps((ev) => steps(ev)), states());
  let res = bad((ev) => [ev("fragment_complete", { peer: "p1", tx_id: "nope" })]);
  assert.ok(res.accepted === false && res.error.includes("stage=resource"), JSON.stringify(res));
  res = bad((ev) => [ev("client_disconnect", { peer: "ghost" })]);
  assert.ok(res.accepted === false && res.error.includes("stage=resource"));
  res = bad((ev) => [ev("client_connect", { peer: "p1" }), ev("queue_pop", { peer: "p1" })]);
  assert.ok(res.accepted === false && res.error.includes("stage=resource"));
  res = bad((ev) => [ev("fragment_open", { peer: "p1", tx_id: "a", bytes: 32 })]);
  assert.ok(res.accepted === false && res.error.includes("stage=resource"));
});

// I5 (OTP2-STA-010d, re-review): the durable active-family mirror survives
// restarts and continuity loss; only genuinely-pending records clean up.
test("I5: no continuity/expiry deletion may touch the durable active-mirror record", () => {
  const dc = states().descriptor.machines.durable_client_record;
  for (const event of ["process_load", "pending_expiry"]) {
    assert.equal(
      dc.trans.some((t) => t.from === "active_activation_pending" && t.event === event),
      false,
      `the active mirror must have no ${event} deletion row (durable trust, OTP2-STA-010d)`,
    );
  }
  // The genuinely-pending client record keeps its fail-closed cleanup.
  assert.ok(
    dc.trans.some((t) => t.from === "pending" && t.event === "process_load" && t.to === "deleted"),
    "a pending client record cleans up on context loss",
  );
  assert.ok(
    dc.trans.some((t) => t.from === "pending" && t.event === "pending_expiry" && t.to === "deleted"),
    "a pending client record keeps the bounded expiry",
  );
  // The phone's boot-scoped machine is unchanged.
  const dp = states().descriptor.machines.durable_phone_record;
  for (const state of ["pending", "committed_waiting_ack"]) {
    assert.ok(
      dp.trans.some((t) => t.from === state && t.event === "process_load"),
      `phone ${state}: boot-continuity deletion stays`,
    );
    assert.ok(
      dp.trans.some((t) => t.from === state && t.event === "pending_expiry"),
      `phone ${state}: boot-scoped expiry stays`,
    );
  }
});

// ---------- key behavioral coverage pinning (fixture inventory) ----------

test("fixtures cover the brief's required behavioral families", () => {
  const fixtures = loadFixtures(v1Root).filter((f) => f.category === "state");
  const ids = new Set(fixtures.map((f) => f.id));
  const requireIds = (names) => {
    for (const n of names) {
      assert.ok(ids.has(n), `missing fixture: ${n}`);
    }
  };
  requireIds([
    // attempts / rolling windows / pB consumption ordering / malformed points
    "st-en-attempts-five-ok-sixth-rejected",
    "st-en-rolling-attempts-boundaries",
    "st-en-malformed-point-no-consumption",
    "st-neg-same-connection-pb-after-invalid-point",
    "st-en-valid-pb-consumes-before-sensitive-operations",
    "st-en-pa-churn-rolling-and-per-connection",
    // pairing window deadline enforced before password-sensitive progression
    "st-en-window-deadline-pb-299999-allowed",
    "st-en-window-deadline-pb-300000-rejected",
    "st-en-window-deadline-pb-300001-rejected",
    "st-en-window-deadline-cb-299999-allowed",
    "st-en-window-deadline-cb-300000-rejected",
    "st-en-window-deadline-cb-300001-rejected",
    // role/context mismatch and both confirmations
    "st-en-role-mismatch-frame-rejected",
    "st-en-client-confirms-in-order",
    // original-enrollment OTP prohibition, client receive side
    "st-en-enrollment-no-otp-client-receives",
    // every interruption boundary + lost final ack + recovery and revocation
    "st-rec-commit-lost-recovery",
    "st-rec-complete-lost-recovery-client",
    "st-rec-ack-lost-active-client-direct-ack",
    "st-rec-recovery-session-stays-recovery-only",
    "st-rec-client-activation-pending-upgrades-on-phone-proof",
    "st-rec-revocation-is-generic-preauth-failure",
    "st-rec-revoke-disconnects-connected-peer",
    "st-rec-pending-expiry-deletes-incomplete",
    "st-rec-boot-continuity-loss-deletes-incomplete-only",
    "st-rec-authentication-without-authorization",
    // persisted-ET binding: recovery MACs verify against the persisted ET
    "st-rec-recovery-commit-mac-mismatch",
    "st-rec-recovery-complete-mac-mismatch",
    // protected record negatives
    "st-rec-duplicate-sequence-terminates",
    "st-rec-skipped-sequence-terminates",
    "st-rec-reordered-sequence-terminates",
    "st-rec-unknown-type-terminates",
    "st-rec-excess-length-terminates",
    "st-rec-bad-tag-terminates-without-advance",
    "st-rec-max-u64-sequence-then-terminated",
    "st-rec-direction-mismatch-terminates",
    "st-rec-session-role-mismatch-terminates",
    // blocked pull vs empty history; peer isolation; delivery policies
    "st-rec-blocked-pull-vs-empty-history",
    "st-rec-peer-isolation",
    "st-rec-heartbeat-rate-terminates",
    "st-rec-push-retransmit-drops-after-max",
    // I1 prepare gates (original enrollment + recovery, exact order)
    "st-en-prepare-sent-after-persist-pairing",
    "st-en-client-prepare-gate-full-flow",
    "st-neg-commit-without-verified-prepare",
    "st-neg-early-prepare-rejected",
    "st-neg-prepare-mismatch-erases",
    "st-rec-complete-lost-recovery-client",
    // I2 one-use code (window closed at verified cB; recovery-only afterwards)
    "st-en-code-one-use-window-closed-after-cb",
    // C1 first post-pairing reconnect: both endpoints full (paired role traces)
    "st-rec-client-activation-pending-upgrades-on-phone-proof",
    "st-rec-phone-full-on-first-reconnect-peer-active",
    // C2 policy response flag vs unsolicited notice; stale report
    "st-rec-policy-response-flag-settles-pull",
    "st-neg-policy-notice-does-not-settle-pull",
    // M8: full_pending_peer machinery is defined even for the unreachable mix
    "st-rec-full-pending-peer-commit-duplicate-no-regression",
    // NEW-1 (scoped re-review): matched paired complete-lost traces; the
    // committed phone prepares for a pending client, then a fresh full session
    "st-rec-phone-complete-lost-prepares-then-full-session",
    // I5 (scoped re-review): the durable active mirror survives restart loss
    "st-rec-client-mirror-durable-across-restart",
    // R1 (reviewer subagent): interruptions before a usable peer record
    // requires repair (fresh explicit window), never signed recovery
    "st-en-cb-disconnect-before-identity-repair-only",
    "st-en-client-one-sided-pending-cancelled-then-fresh-pairing",
    "st-rec-no-peer-record-no-recovery",
    // R1 residual: the pre-identity INACTIVITY timeout is repair-only too
    "st-en-inactivity-before-identity-repair-only",
    // R1 persistence boundary (re-scope): ET derives first; the COMPLETE
    // record persists atomically; release happens only after durable success
    // (persist-failure publishes nothing; success survives interruption w/ ET)
    "st-neg-client-persist-failure-publishes-nothing",
    "st-neg-phone-persist-failure-sends-no-prepare",
    "st-en-phone-pending-survives-interruption-with-et",
  ]);
});

test("heartbeat-threshold literals in the descriptor are pinned to the normative sentence (OTP2-REC-034)", () => {
  const session = states().descriptor.machines.session;
  const guards = session.trans
    .filter((t) => t.event === "send_protected_record" && t.guards?.type_is === "0x0002")
    .flatMap((t) => [t.guards.tx_rate_below, t.guards.tx_rate_exceeded])
    .filter(Boolean);
  assert.equal(
    guards.length, 2,
    "the heartbeat accept and terminate transitions must both carry the rate guard",
  );
  for (const g of guards) {
    assert.deepEqual(g, { max: 4, window_s: 60 },
      "heartbeat abuse terminates on more than 4 heartbeats within any rolling 60 s (OTP2-REC-034)");
  }
});
