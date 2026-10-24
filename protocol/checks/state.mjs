// Test-only state/authorization interpreter for the Veles protocol contract
// (OTP-02 Plan Task 4). Never imported by product code (OTP2-FIX-006).
//
// loadStates(root) parses and validates protocol/v1/states.json against
// protocol/v1/limits.json. checkProtocolTrace(trace, states) walks a trace
// fixture: every step is applied to the declared state-machine instances via
// descriptor transitions; the fixture's declared final state, per-step states
// and required effects are re-derived independently and only accepted on an
// exact match (a fixture can never make the checker "trust" its declared
// outcomes). Validation failures are explicit no-mutation results: rejected
// traces report {accepted:false, error, final_state:null, effects:[]}.
//
// Simulated protocol outcomes in trace steps (tag_ok, hmac_ok, sig_ok,
// act_mac_ok, peer_state, ...) are explicit SIMULATED inputs (OTP2-FIX-004),
// not cryptographic conformance evidence; real crypto vectors are Plan Task 5.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parseJsonStrict } from "./fixture.mjs";

export const U64_MAX_STRING = "18446744073709551615";
const U64_MAX = 18446744073709551615n;

const ROLES = new Set(["phone", "client"]);
const DIRECTION_BYTES = {
  phone: { rx: "0x01", tx: "0x02" },
  client: { rx: "0x02", tx: "0x01" },
};

const GUARD_KEYS = new Set([
  "simulate",
  "attempts_below",
  "attempts_exhausted",
  "rolling_attempts_below",
  "rolling_attempts_exhausted",
  "fresh_pa_below",
  "fresh_pa_connection_exhausted",
  "fresh_pa_rolling_exhausted",
  "window_age_below",
  "window_age_at_least",
  "elapsed_at_least",
  "record_state_in",
  "class_allows",
  "class_in",
  "class_is",
  "type_is",
  "type_in_enrollment_allowlist",
  "enrollment_channel_attempt",
  "prepare_verified",
  "sequence_is_next",
  "sequence_mismatched",
  "pull_response_allowed",
  "pull_outstanding_below",
  "peer_reported",
  "push_outstanding_below",
  "push_outstanding_at_least",
  "push_retransmits_below",
  "push_retransmits_exhausted",
  "tx_rate_below",
  "tx_rate_exceeded",
]);

const LIMIT_KEYS = ["limit", "limit_s", "per_id", "max", "window_s", "per_connection", "timeout"];

class StageError extends Error {
  constructor(stage, message) {
    super(`stage=${stage}: ${message}`);
    this.stage = stage;
  }
}

function fail(stage, message) {
  throw new StageError(stage, message);
}

function invalid(message) {
  return { accepted: false, error: message, final_state: null, effects: [] };
}

// ---------- descriptor loading ----------

function machineKind(name) {
  if (name === "session") return "session";
  if (name.startsWith("durable_")) return "durable";
  if (name.endsWith("_pairing")) return "pairing";
  return "plain";
}

const KIND_RANK = { session: 0, pairing: 1, durable: 2, plain: 3 };
function kindRank(inst) {
  return KIND_RANK[inst.kind] ?? 99;
}

export function loadStates(root) {
  const read = (name) => parseJsonStrict(readFileSync(join(root, name), "utf8"), name);
  const descriptor = read("states.json");
  const limits = read("limits.json");
  validateDescriptor(descriptor, limits);
  return { descriptor, limits };
}

function requireType(name, value) {
  if (typeof value !== "string") fail("descriptor", `${name} must be a string`);
  return value;
}

function validateDescriptor(d, limits) {
  if (d.version !== 1 || d.schema !== "veles.protocol.states") {
    fail("descriptor", "states.json must declare version 1 and schema veles.protocol.states");
  }
  const limitNames = new Set(limits?.limits?.map((l) => l.name) ?? []);
  const machineNames = new Set(Object.keys(d.machines ?? {}));
  if (machineNames.size === 0) fail("descriptor", "no machines declared");

  const checkLimits = (node, seen = new Set(), key = null) => {
    if (node === null || typeof node !== "object" || seen.has(node)) return;
    seen.add(node);
    if (key === "tx_rate_below" || key === "tx_rate_exceeded") {
      // The heartbeat-abuse threshold is doc-derived literal text
      // ("more than 4x in a rolling minute"), not a limits.json entry; the
      // suite pins the literals against the normative sentence.
      for (const v of [node.max, node.window_s]) {
        if (!Number.isInteger(v) || v <= 0) fail("descriptor", "tx rate guards must carry positive integer max and window_s");
      }
      return;
    }
    for (const [childKey, value] of Object.entries(node)) {
      if (LIMIT_KEYS.includes(childKey)) {
        if (typeof value !== "string" || !limitNames.has(value)) {
          fail("descriptor", `guard limit reference ${JSON.stringify(value)} is not defined in limits.json`);
        }
      } else {
        checkLimits(value, seen, childKey);
      }
    }
  };

  const typeNames = new Set(Object.keys(d.record_types ?? {}));
  const knownType = (t) => typeof t === "string" && /^0x[0-9a-f]{4}$/.test(t) && typeNames.has(t);

  if (d.session_classes === undefined || d.session_classes === null) {
    fail("descriptor", "session_classes missing");
  }

  const knownClasses = new Set(["enrollment", "activation_recovery", "full_pending_peer", "full"]);
  for (const [cls, list] of Object.entries(d.session_classes)) {
    if (!knownClasses.has(cls)) fail("descriptor", `unknown session class ${cls}`);
    if (!Array.isArray(list)) fail("descriptor", `session class ${cls} must map to a record list`);
    for (const t of list) if (!knownType(t)) fail("descriptor", `class ${cls} references unknown record type ${t}`);
  }

  for (const [t, dirs] of Object.entries(d.record_type_directions ?? {})) {
    if (t === "description") continue;
    if (!knownType(t)) fail("descriptor", `record_type_directions key ${t} is not a known record type`);
    for (const dir of dirs) {
      if (dir !== "0x01" && dir !== "0x02") fail("descriptor", `bad direction byte ${dir} for ${t}`);
    }
  }
  for (const list of [d.framework_types ?? [], d.connection_types ?? [], d.enrollment_types?.types ?? []]) {
    for (const t of list) if (!knownType(t)) fail("descriptor", `type inventory references unknown type ${t}`);
  }
  for (const [cls, list] of Object.entries(d.session_classes)) {
    void cls;
    void list;
  }
  for (const t of Object.keys(d.record_effects ?? {})) {
    if (t === "description") continue;
    if (!knownType(t)) fail("descriptor", `record_effects key ${t} is not a known record type`);
  }
  for (const t of Object.keys(d.record_flow ?? {})) {
    if (t === "description") continue;
    if (!knownType(t)) fail("descriptor", `record_flow key ${t} is not a known record type`);
  }
  for (const [name, direction] of Object.entries(d.frame_directions ?? {})) {
    if (name === "description") continue;
    if (!Array.isArray(direction)) fail("descriptor", "frame_directions values must be arrays");
    for (const b of direction) {
      if (b !== "0x01" && b !== "0x02") fail("descriptor", `bad frame direction ${b}`);
    }
  }
  for (const [role, machine] of Object.entries(d.record_state_machines ?? {})) {
    if (!ROLES.has(role)) fail("descriptor", `record_state_machines role ${role} invalid`);
    if (!machineNames.has(machine)) fail("descriptor", `record_state_machines names unknown machine ${machine}`);
  }

  for (const [name, machine] of Object.entries(d.machines)) {
    const kind = machineKind(name);
    void kind;
    const stateList = machine.states;
    if (!Array.isArray(stateList) || stateList.length === 0) {
      fail("descriptor", `machine ${name}: states missing`);
    }
    const stateSet = new Set(stateList);
    if (!stateSet.has(machine.initial)) {
      fail("descriptor", `machine ${name}: initial state ${machine.initial} not declared`);
    }
    if (!Array.isArray(machine.trans)) fail("descriptor", `machine ${name}: trans missing`);
    for (const t of machine.trans) {
      if (!stateSet.has(t.from)) fail("descriptor", `machine ${name}: transition from unknown state ${t.from}`);
      if (!stateSet.has(t.to)) fail("descriptor", `machine ${name}: transition to unknown state ${t.to}`);
      if (typeof t.event !== "string" || t.event.length === 0) {
        fail("descriptor", `machine ${name}: transition event invalid`);
      }
      if (!Array.isArray(t.effects)) fail("descriptor", `machine ${name}: transition effects missing`);
      for (const e of t.effects) {
        if (typeof e !== "string") fail("descriptor", `machine ${name}: effect must be a string`);
      }
      if (t.guard !== undefined) fail("descriptor", `machine ${name}: use "guards" (object) not "guard"`);
      if (t.guards !== undefined) {
        for (const key of Object.keys(t.guards)) {
          if (!GUARD_KEYS.has(key)) fail("descriptor", `machine ${name}: unknown guard ${key}`);
        }
        checkLimits(t.guards);
        for (const cls of t.guards.class_in ?? []) {
          if (!knownClasses.has(cls)) fail("descriptor", `machine ${name}: unknown class in class_in: ${cls}`);
        }
      }
      if (t.roles !== undefined) {
        for (const r of t.roles) {
          if (r !== "C" && r !== "P") fail("descriptor", `machine ${name}: bad role tag ${r}`);
        }
      }
    }
  }

  const persisted = d.public_persisted_fields ?? {};
  if (!Array.isArray(persisted.fields)) fail("descriptor", "public_persisted_fields.fields missing");
}

// ---------- small parsing helpers ----------

function parseSequence(value) {
  if (typeof value !== "string") return null;
  if (!/^(0|[1-9][0-9]{0,19})$/.test(value)) fail("bounds", `sequence number ${JSON.stringify(value)} is not a canonical unsigned decimal string`);
  const n = BigInt(value);
  if (n > U64_MAX) fail("bounds", `sequence number ${value} exceeds the u64 maximum ${U64_MAX_STRING}`);
  return n;
}

function parseRecordType(value) {
  if (typeof value !== "string" || !/^0x[0-9a-f]{4}$/.test(value)) {
    fail("bounds", `record type ${JSON.stringify(value)} is not a 4-digit lowercase hex scalar`);
  }
  return value;
}

function limitMs(states, nameOrNumber) {
  // Numeric guards are written in SECONDS (field names *_window_s/limit_s);
  // raw numbers are scaled like named limits.
  if (typeof nameOrNumber === "number") return nameOrNumber * 1000;
  const entry = states.limits.limits.find((l) => l.name === nameOrNumber);
  if (!entry || typeof entry.value !== "number") fail("descriptor", `limits.json has no numeric limit ${JSON.stringify(nameOrNumber)}`);
  return entry.value * 1000;
}

function limitValue(states, name) {
  const entry = states.limits.limits.find((l) => l.name === name);
  if (!entry || typeof entry.value !== "number") fail("descriptor", `limits.json has no numeric limit ${JSON.stringify(name)}`);
  return entry.value;
}

// ---------- trace validation ----------

function validateTraceShape(trace) {
  if (!trace || typeof trace !== "object" || Array.isArray(trace)) fail("trace", "trace must be an object");
  if (!ROLES.has(trace.role)) fail("trace", `trace role ${JSON.stringify(trace.role)} must be "phone" or "client"`);
  if (!Array.isArray(trace.steps) || trace.steps.length === 0) fail("trace", "trace must carry a non-empty steps array");
  trace.steps.forEach((step, i) => {
    if (!step || typeof step !== "object" || Array.isArray(step)) fail("trace", `step ${i} must be an object`);
    if (!Number.isInteger(step.t_ms) || step.t_ms < 0) fail("trace", `step ${i}: t_ms must be a non-negative integer`);
    if (typeof step.event !== "string" || step.event.length === 0 || step.event.length > 64) {
      fail("trace", `step ${i}: event must be a 1..64 character string`);
    }
    for (const key of ["peer", "pairing_id", "connection"]) {
      if (step[key] !== undefined && (typeof step[key] !== "string" || step[key].length === 0)) {
        fail("trace", `step ${i}: ${key} must be a non-empty string`);
      }
    }
    if (step.simulate !== undefined) {
      if (!step.simulate || typeof step.simulate !== "object" || Array.isArray(step.simulate)) {
        fail("trace", `step ${i}: simulate must be an object`);
      }
      for (const [k, v] of Object.entries(step.simulate)) {
        if (!["string", "number", "boolean"].includes(typeof v)) {
          fail("trace", `step ${i}: simulate.${k} must be a string/number/boolean`);
        }
      }
    }
    if (step.expect !== undefined) {
      if (!step.expect || typeof step.expect !== "object" || Array.isArray(step.expect)) {
        fail("trace", `step ${i}: expect must be an object`);
      }
      for (const v of Object.values(step.expect)) {
        if (typeof v !== "string") fail("trace", `step ${i}: expect values must be strings`);
      }
    }
  });
}

// ---------- instances ----------

class Instance {
  constructor(machine, peer, state) {
    this.machine = machine;
    this.name = machine.name;
    this.peer = peer;
    this.kind = machineKind(machine.name);
    this.state = state;
    this.lastActivityT = null;
    if (this.kind === "pairing") {
      this.pairingCounter = 0;
      this.pairingId = null;
      this.windowCreatedT = null;
    this.attempts = []; // {t, id}
    this.attemptsPerId = new Map();
    this.paEmissions = []; // {t, connection}
    this.paPerConnection = new Map();
  }
  if (this.kind === "durable") {
    this.pendingCreatedT = null;
  }
  if (this.kind === "session") {
    this.class = null;
    this.nextRx = 1n; // reconnect_confirm consumed sequence 0 in each direction
    this.nextTx = 1n;
    this.outstandingPull = 0;
    this.pushOutstanding = 0;
    this.pushSentT = null;
    this.pushRetransmits = 0;
    this.prepareVerified = false; // OTP2-STA-010a/010b step 0: this session's verified prepare
    this.peerReported = null; // peer_state byte the peer's reconnect_confirm reported (OTP2-SID-001)
    this.txTimes = new Map(); // record_type hex -> [t, ...] for tx-rate guards
  }
}

  key() {
    return `${this.name}:${this.peer}`;
  }
}

function instantiateMachines(trace, descriptor) {
  const declared = trace.machines ?? [
    { machine: `${trace.role}_pairing` },
    { machine: trace.role === "phone" ? "durable_phone_record" : "durable_client_record" },
  ];
  const instances = [];
  const seen = new Set();
  for (const entry of declared) {
    if (!entry || typeof entry !== "object") fail("trace", "machines entries must be objects");
    const name = entry.machine;
    const machine = descriptor.machines[name];
    if (!machine) fail("trace", `unknown machine ${JSON.stringify(name)}`);
    const peer = entry.peer ?? "main";
    const key = `${name}:${peer}`;
    if (seen.has(key)) fail("trace", `machine instance ${key} declared twice`);
    seen.add(key);
    instances.push(new Instance({ name, ...machine }, peer, machine.initial));
  }
  return instances;
}

function applyPersistedState(trace, instances, descriptor) {
  const persisted = trace.persisted_state ?? {};
  for (const [key, value] of Object.entries(persisted)) {
    if (typeof value !== "string") fail("persisted_state", `persisted state value for ${key} must be a string`);
    const isOverride = /^[a-z_]+:[a-z0-9_-]+$/.test(key);
    if (isOverride) {
      const machineName = key.slice(0, key.indexOf(":"));
      const machine = descriptor.machines[machineName];
      const peer = key.slice(key.indexOf(":") + 1);
      if (!machine) fail("persisted_state", `persisted state override names unknown machine ${machineName}`);
      const inst = instances.find((i) => i.name === machineName && i.peer === peer);
      if (!inst) fail("persisted_state", `persisted state override for ${key} has no declared instance`);
      if (!machine.states.includes(value)) {
        fail("persisted_state", `persisted state ${key}=${value} is not a state of ${machineName}`);
      }
      inst.state = value;
      continue;
    }
    const publicFields = descriptor.public_persisted_fields?.fields ?? [];
    const publicPrefixes = descriptor.public_persisted_fields?.prefixes ?? [];
    const publicAllowed = publicFields.includes(key) || publicPrefixes.some((p) => key.startsWith(p));
    if (!publicAllowed) {
      fail(
        "persisted_state",
        `persisted state key ${JSON.stringify(key)} is not a public durable field (OTP2-CRY-019g: only public values are persisted; enrollment/activation secrets are never persisted)`,
      );
    }
    if (key.startsWith("created_monotonic_ms:")) {
      const target = key.slice("created_monotonic_ms:".length);
      const inst = instances.find((i) => i.key() === target);
      if (!inst) fail("persisted_state", `created_monotonic_ms override for ${target} has no declared instance`);
      const n = Number(value);
      if (!Number.isInteger(n) || n < 0) fail("persisted_state", `created_monotonic_ms value for ${target} must be a non-negative integer string`);
      inst.pendingCreatedT = n;
    }
  }
}

// ---------- guard evaluation ----------

function guardContext(step) {
  return {
    t: step.t_ms,
    simulate: step.simulate ?? {},
    pairingId: step.pairing_id ?? null,
    connection: step.connection ?? "default",
  };
}

function evaluateGuards(guards, inst, ctx, states, trace, descriptor, reasons) {
  const sim = ctx.simulate;

  const simOK = () => {
    for (const [k, v] of Object.entries(guards.simulate ?? {})) {
      if (!(k in sim) || sim[k] !== v) {
        if (reasons) reasons.add("simulate");
        return false;
      }
    }
    return true;
  };
  if (guards.simulate !== undefined && !simOK()) return false;

  const attemptsFor = () => inst.attemptsPerId.get(ctx.pairingId ?? inst.pairingId ?? "unknown") ?? 0;
  const rollingAttempts = (windowMs) => inst.attempts.filter((a) => ctx.t - a.t < windowMs).length;

  if (guards.attempts_below !== undefined && !(attemptsFor() < limitValue(states, guards.attempts_below))) return false;
  if (guards.attempts_exhausted !== undefined && !(attemptsFor() >= limitValue(states, guards.attempts_exhausted))) return false;
  if (guards.rolling_attempts_below !== undefined) {
    const g = guards.rolling_attempts_below;
    if (!(rollingAttempts(limitMs(states, g.window_s)) < limitValue(states, g.max))) return false;
  }
  if (guards.rolling_attempts_exhausted !== undefined) {
    const g = guards.rolling_attempts_exhausted;
    if (!(rollingAttempts(limitMs(states, g.window_s)) >= limitValue(states, g.max))) return false;
  }

  const paFor = () => inst.paPerConnection.get(ctx.connection) ?? 0;
  const paRolling = (windowMs) => inst.paEmissions.filter((a) => ctx.t - a.t < windowMs).length;

  if (guards.fresh_pa_below !== undefined) {
    const g = guards.fresh_pa_below;
    if (!(paFor() < limitValue(states, g.per_connection))) return false;
    if (!(paRolling(limitMs(states, g.window_s)) < limitValue(states, g.max))) return false;
  }
  if (guards.fresh_pa_connection_exhausted !== undefined && !(paFor() >= limitValue(states, guards.fresh_pa_connection_exhausted))) return false;
  if (guards.fresh_pa_rolling_exhausted !== undefined) {
    const g = guards.fresh_pa_rolling_exhausted;
    if (!(paRolling(limitMs(states, g.window_s)) >= limitValue(states, g.max))) return false;
  }

  if (guards.window_age_below !== undefined) {
    const g = guards.window_age_below;
    if (inst.windowCreatedT === null || inst.windowCreatedT === undefined) {
      if (reasons) reasons.add("window_age_below");
      return false;
    }
    if (!(ctx.t - inst.windowCreatedT < limitMs(states, g.limit_s))) {
      if (reasons) reasons.add("window_age_below");
      return false;
    }
  }

  if (guards.window_age_at_least !== undefined) {
    const g = guards.window_age_at_least;
    if (inst.windowCreatedT === null || inst.windowCreatedT === undefined) {
      if (reasons) reasons.add("window_age_at_least");
      return false;
    }
    if (!(ctx.t - inst.windowCreatedT >= limitMs(states, g.limit_s))) {
      if (reasons) reasons.add("window_age_at_least");
      return false;
    }
  }

  if (guards.elapsed_at_least !== undefined) {
    const g = guards.elapsed_at_least;
    const sinceT =
      g.since === "window_created" ? inst.windowCreatedT
      : g.since === "last_activity" ? inst.lastActivityT
      : g.since === "pending_created" ? inst.pendingCreatedT
      : g.since === "push_sent" ? inst.pushSentT
      : null;
    if (sinceT === null || sinceT === undefined || !(ctx.t - sinceT >= limitMs(states, g.limit_s))) {
      if (reasons) reasons.add("elapsed_at_least");
      return false;
    }
  }

  if (guards.record_state_in !== undefined) {
    const recordMachine = descriptor.record_state_machines?.[trace.role];
    const recordInst = recordMachine
      ? inst.parent?.find((i) => i.name === recordMachine && i.peer === inst.peer)
      : null;
    if (!recordInst) return false;
    if (!guards.record_state_in.includes(recordInst.state)) return false;
  }

  if (
    guards.class_allows !== undefined ||
    guards.class_in !== undefined ||
    guards.class_is !== undefined
  ) {
    // The session machine reads its own class; other machines read the class
    // fixed on the session instance of the same peer (OTP2-SID-001).
    const sessionInst =
      inst.kind === "session" ? inst : (inst.parent ?? []).find((i) => i.kind === "session" && i.peer === inst.peer);
    const resolvedClass = sessionInst?.class ?? null;
    if (!resolvedClass) {
      if (guards.class_allows === true) return false;
    } else {
      const origin = DIRECTION_BYTES[trace.role][inst.pendingOrientation === "tx" ? "tx" : "rx"];
      const type = recordTypeOf(ctx.simulate);
      const allowed =
        type !== null &&
        ((descriptor.framework_types ?? []).includes(type) ||
          (descriptor.connection_types ?? []).includes(type) ||
          ((descriptor.session_classes?.[resolvedClass] ?? []).includes(type) &&
            (descriptor.record_type_directions?.[type] ?? []).includes(origin)));
      if (guards.class_allows !== undefined && guards.class_allows !== allowed) return false;
      if (guards.class_in !== undefined && !guards.class_in.includes(resolvedClass)) return false;
      if (guards.class_is !== undefined && guards.class_is !== resolvedClass) return false;
    }
  }

  if (guards.type_is !== undefined) {
    const type = recordTypeOf(sim);
    if (type === null || type !== guards.type_is) return false;
  }

  if (guards.enrollment_channel_attempt !== undefined) {
    // True only when the endpoint's pairing machine is mid-enrollment on the
    // original enrollment connection (OTP2-CRY-020a); session-machine traces
    // never satisfy it, so recovery-session records cannot erase pending
    // trust material.
    const pairInst = (inst.parent ?? []).find((i) => i.kind === "pairing" && i.peer === inst.peer);
    const statesList = Array.isArray(guards.enrollment_channel_attempt)
      ? guards.enrollment_channel_attempt
      : [];
    const inChannel = !!pairInst && statesList.includes(pairInst.state);
    if (!inChannel) return false;
  }

  if (guards.type_in_enrollment_allowlist !== undefined) {
    const type = recordTypeOf(sim);
    if (type === null) return false;
    const inList = (descriptor.enrollment_types?.types ?? []).includes(type);
    if (inList !== guards.type_in_enrollment_allowlist) return false;
  }

  if (guards.prepare_verified !== undefined) {
    // OTP2-STA-010a/010b: a commit may be sent only after this session's
    // enrollment_prepare MAC verified (session-kind instances carry the flag;
    // other instances have no flag and can never satisfy it).
    if (inst.prepareVerified !== guards.prepare_verified) return false;
  }

  if (guards.peer_reported !== undefined) {
    // OTP2-SID-001/STA-010b step 0: the exact peer-reported byte observed on
    // this session's reconnect_confirm (e.g. the phone prepares only for a
    // client that reported below-active).
    if ((inst.peerReported ?? null) !== guards.peer_reported) return false;
  }

  if (guards.sequence_is_next !== undefined || guards.sequence_mismatched !== undefined) {
    const seq = sim.sequence === undefined ? null : parseSequence(sim.sequence);
    const next = inst.pendingOrientation === "tx" ? inst.nextTx : inst.nextRx;
    if (guards.sequence_is_next !== undefined) {
      if (seq === null || seq !== next) return false;
    }
    if (guards.sequence_mismatched !== undefined) {
      if (seq === null || seq === next) return false;
    }
  }

  if (guards.pull_response_allowed !== undefined && !(inst.outstandingPull > 0)) return false;
  if (guards.pull_outstanding_below !== undefined && !(inst.outstandingPull < limitValue(states, guards.pull_outstanding_below))) return false;
  if (guards.push_outstanding_below !== undefined && !(inst.pushOutstanding < limitValue(states, guards.push_outstanding_below))) return false;
  if (guards.push_outstanding_at_least !== undefined && !(inst.pushOutstanding > 0)) return false;
  if (guards.push_retransmits_below !== undefined && !(inst.pushRetransmits < limitValue(states, guards.push_retransmits_below))) return false;
  if (guards.push_retransmits_exhausted !== undefined && !(inst.pushRetransmits >= limitValue(states, guards.push_retransmits_exhausted))) return false;

  if (guards.tx_rate_below !== undefined || guards.tx_rate_exceeded !== undefined) {
    const type = recordTypeOf(sim);
    const prior = type === null ? [] : (inst.txTimes.get(type) ?? []);
    const windowMs = limitMs(
      states,
      guards.tx_rate_below?.window_s ?? guards.tx_rate_exceeded?.window_s,
    );
    const rolling = prior.filter((t0) => ctx.t - t0 < windowMs).length;
    const maxKey = guards.tx_rate_below?.max ?? guards.tx_rate_exceeded?.max;
    if (guards.tx_rate_below !== undefined && !(rolling < maxKey)) return false;
    if (guards.tx_rate_exceeded !== undefined && !(rolling >= maxKey)) return false;
  }

  return true;
}

function recordTypeOf(sim) {
  if (sim.record_type === undefined) return null;
  return parseRecordType(sim.record_type);
}

// ---------- transition application ----------

function applyTransition(inst, t, step, ctx, states, trace, descriptor, effects) {
  const effectsList = [...t.effects];
  // OTP2-SID-001 bookkeeping: the peer's reported peer_state byte is observed
  // from ANY received reconnect_confirm on a session instance (independent of
  // the matched row's declared effects, so class-fix rows and later send-time
  // guards agree).
  if (
    inst.kind === "session" &&
    (step.event === "reconnect_confirm" ||
      (step.event === "protected_record" && recordTypeOf(ctx.simulate) === "0x0001"))
  ) {
    inst.peerReported = typeof ctx.simulate.peer_state === "string" ? ctx.simulate.peer_state : null;
  }
  const isSessionRecord =
    inst.kind === "session" &&
    (step.event === "protected_record" || step.event === "send_protected_record") &&
    (t.effects.includes("record_accepted") || t.effects.includes("record_sent"));

  for (const effect of t.effects) {
    switch (effect) {
      case "generate_pairing_id_and_code":
        inst.pairingCounter += 1;
        inst.pairingId = `pid${inst.pairingCounter}`;
        inst.windowCreatedT = ctx.t;
        break;
      case "consume_attempt": {
        const id = ctx.pairingId ?? inst.pairingId ?? "unknown";
        inst.attempts.push({ t: ctx.t, id });
        inst.attemptsPerId.set(id, (inst.attemptsPerId.get(id) ?? 0) + 1);
        break;
      }
      case "emit_pairing_offer": {
        inst.paEmissions.push({ t: ctx.t, connection: ctx.connection });
        inst.paPerConnection.set(ctx.connection, (inst.paPerConnection.get(ctx.connection) ?? 0) + 1);
        break;
      }
      case "persist_committed_waiting_ack":
        break;
      case "reset_session_slot": {
        inst.class = null;
        inst.nextRx = 1n;
        inst.nextTx = 1n;
        inst.outstandingPull = 0;
        inst.pushOutstanding = 0;
        inst.pushSentT = null;
        inst.pushRetransmits = 0;
        inst.prepareVerified = false;
        inst.peerReported = null;
        inst.txTimes = new Map();
        break;
      }
      case "advance_rx_sequence": {
        const seq = parseSequence(ctx.simulate.sequence);
        inst.nextRx = seq + 1n;
        break;
      }
      case "advance_tx_sequence": {
        const seq = parseSequence(ctx.simulate.sequence);
        inst.nextTx = seq + 1n;
        break;
      }
      case "set_class_full":
      case "set_class_activation_recovery":
      case "set_class_full_pending_peer":
      case "set_class_enrollment":
        inst.class = effect.slice("set_class_".length);
        break;
      case "prepare_verified":
        // OTP2-STA-010a/010b step 0: the enrollment_prepare MAC of the
        // CURRENT session verified — the commit gate is open for this session
        // only (cleared with its slot on reset/teardown).
        if (inst.kind === "session") inst.prepareVerified = true;
        break;
      case "peer_state_reported_0x01":
      case "peer_state_reported_0x02":
        // OTP2-SID-001: bookkeeping of the peer's reported byte on class-fix
        // rows; the byte itself is also stored implicitly below (a fixture
        // may rely on either encoding).
        if (inst.kind === "session") {
          inst.peerReported = effect.endsWith("0x01") ? "0x01" : "0x02";
        }
        break;
      case "test_probe_counter_seek": {
        if (ctx.simulate.rx_next !== undefined) inst.nextRx = parseSequence(ctx.simulate.rx_next);
        if (ctx.simulate.tx_next !== undefined) inst.nextTx = parseSequence(ctx.simulate.tx_next);
        break;
      }
      case "push_retransmitted":
        inst.pushRetransmits += 1;
        inst.pushSentT = ctx.t;
        break;
      case "push_queue_dropped":
        inst.pushOutstanding = 0;
        inst.pushRetransmits = 0;
        inst.pushSentT = null;
        break;
      default:
        break;
    }
    effects.push(effect);
  }

  // Accepted session records: descriptor-declared per-type effect, flow
  // bookkeeping (pull/push outstanding), and event-ack settlement semantics.
  if (isSessionRecord) {
    const type = recordTypeOf(ctx.simulate);
    if (type !== null) {
      const mapped = descriptor.record_effects?.[type];
      if (mapped) {
        // Effect mapping is either a plain string or an object with an
        // `always` effect plus flag-conditional extras (OTP2-REC-031: the
        // canonical response_to_pull flag adds the blocked-response/settle
        // effects only when the flag is set).
        for (const name of typeof mapped === "string" ? [mapped] : [mapped.always]) {
          if (name && !effects.includes(name)) effects.push(name);
        }
        if (mapped.on_flag && typeof mapped.on_flag === "object") {
          if (ctx.simulate?.[mapped.on_flag.flag] === true) {
            for (const name of mapped.on_flag.effects ?? []) {
              if (!effects.includes(name)) effects.push(name);
            }
          }
        }
      }
      const flow = descriptor.record_flow?.[type];
      if (flow) {
        if (flow.inc) {
          if (flow.inc === "outstanding_pull") inst.outstandingPull += 1;
          if (flow.inc === "outstanding_push") {
            inst.pushOutstanding += 1;
            inst.pushSentT = ctx.t;
          }
        }
        if (flow.dec) {
          if (flow.dec === "outstanding_pull") inst.outstandingPull = Math.max(0, inst.outstandingPull - 1);
          if (flow.dec === "outstanding_push") inst.pushOutstanding = Math.max(0, inst.pushOutstanding - 1);
        }
        // Conditional settlement (OTP2-REC-031): a descriptor flow may dec a
        // counter only when a declared simulate flag is boolean true
        // (e.g. phone_policy_state's canonical response_to_pull flag); the
        // dec never underflows (stale reports keep bookkeeping intact).
        if (flow.dec_if_flag) {
          const { flag, counter } = flow.dec_if_flag;
          const on = ctx.simulate?.[flag] === true;
          if (on && counter === "outstanding_pull") {
            inst.outstandingPull = Math.max(0, inst.outstandingPull - 1);
          } else if (on && counter === "outstanding_push") {
            inst.pushOutstanding = Math.max(0, inst.pushOutstanding - 1);
          }
        }
        if (flow.event_known_settles && step.event === "protected_record" && ctx.simulate.event_known !== false) {
          inst.pushOutstanding = 0;
          inst.pushRetransmits = 0; // settled push ends its retransmission cycle
        }
      }
      // Tx-rate bookkeeping (heartbeat threshold pinning): record accepted tx
      // times per record type.
      if (t.effects.includes("record_sent")) {
        const times = inst.txTimes.get(type) ?? [];
        times.push(ctx.t);
        inst.txTimes.set(type, times);
      }
      if (type === "0x0204" && ctx.simulate.event_known !== undefined && typeof ctx.simulate.event_known !== "boolean") {
        fail("bounds", "simulate.event_known must be a boolean");
      }
    }
  }

  if (inst.kind === "durable") {
    if (t.to === "pending" && inst.pendingCreatedT === null) inst.pendingCreatedT = ctx.t;
  }
  inst.lastActivityT = ctx.t;
  inst.state = t.to;
  void states;
  void trace;
  void effectsList;
}

// ---------- main entry point ----------

export function checkProtocolTrace(trace, states) {
  try {
    return runTrace(trace, states);
  } catch (e) {
    if (e instanceof StageError) return invalid(e.message);
    return invalid(`stage=trace: ${e?.message ?? String(e)}`);
  }
}

function runTrace(trace, states) {
  const descriptor = states.descriptor;
  validateTraceShape(trace);
  const instances = instantiateMachines(trace, descriptor);
  applyPersistedState(trace, instances, descriptor);

  const byPeer = new Map();
  for (const inst of instances) {
    (byPeer.get(inst.peer) ?? byPeer.set(inst.peer, []).get(inst.peer)).push(inst);
  }
  for (const inst of instances) inst.parent = instances;

  const effects = [];
  let prevT = null;

  for (let i = 0; i < trace.steps.length; i++) {
    const step = trace.steps[i];
    if (prevT !== null && step.t_ms < prevT) {
      fail("time", `step ${i}: t_ms ${step.t_ms} precedes the previous step's ${prevT} (monotonic trace times)`);
    }
    prevT = Math.max(prevT ?? step.t_ms, step.t_ms);

    const peer = step.peer ?? "main";
    const targets = byPeer.get(peer);
    if (!targets || targets.length === 0) {
      fail("trace", `step ${i}: event targets peer ${JSON.stringify(peer)} which has no declared instance`);
    }
    // Deterministic application order: session state (which reads the durable
    // record state to fix the session class) is evaluated before the durable
    // record machines update, so a class decision and a same-step record
    // upgrade can never leak into each other.
    const ordered = [...targets].sort(
      (a, b) => kindRank(a) - kindRank(b) || a.peer.localeCompare(b.peer) || a.name.localeCompare(b.name),
    );

    // Bound checks for simulated record bodies (stage=bounds).
    if ((step.event === "protected_record" || step.event === "send_protected_record") && step.simulate?.record_type !== undefined) {
      const type = parseRecordType(step.simulate.record_type);
      if (type === "0x0202" && step.simulate.event_count !== undefined) {
        const n = step.simulate.event_count;
        if (typeof n !== "number" || !Number.isInteger(n) || n < 0 || n > 5) {
          fail("bounds", `pull_events event_count must be an integer 0..5 (otp_events_global), got ${JSON.stringify(n)}`);
        }
      }
      if (type === "0x0204" && step.simulate.event_known !== undefined && typeof step.simulate.event_known !== "boolean") {
        fail("bounds", "simulate.event_known must be a boolean");
      }
    }

    for (const inst of ordered) {
      applyStepToInstance(inst, step, i, states, trace, descriptor, effects);
    }

    // Cross-check declared per-step states against the recomputation.
    if (step.expect !== undefined) {
      for (const [key, declared] of Object.entries(step.expect)) {
        const inst = instances.find((x) => x.key() === key);
        if (!inst) fail("trace", `step ${i}: expect names undeclared instance ${key}`);
        if (inst.state !== declared) {
          fail(
            "expectation",
            `step ${i}: declared state ${key}=${declared} contradicts the descriptor-derived state ${inst.state}`,
          );
        }
      }
    }
  }

  const finalInstances = [...instances].sort(
    (a, b) => (a.peer === b.peer ? a.name.localeCompare(b.name) : a.peer.localeCompare(b.peer)),
  );
  const finalState = finalInstances.map((i) => `${i.key()}=${i.state}`).join(";");
  return { accepted: true, error: null, final_state: finalState, effects };
}

function applyStepToInstance(inst, step, stepIndex, states, trace, descriptor, effects) {
  const ctx = guardContext(step);

  // Role/context mismatch: a frame that can never arrive at this endpoint's
  // inbound direction is rejected, never ignored. Pairing machines apply their
  // declared generic pre-auth policy and, while a pairing execution is in
  // flight, disconnect the current connection (ephemeral state erased, window
  // and attempt budget persist); the session machine tears the (possibly
  // authenticated) session down (OTP2-ENC-004).
  const frameDirs = descriptor.frame_directions?.[step.event];
  if (frameDirs !== undefined && (inst.kind === "pairing" || inst.kind === "session")) {
    const inbound = DIRECTION_BYTES[trace.role].rx;
    if (!frameDirs.includes(inbound)) {
      const policy = descriptor.machines[inst.name]?.on_direction_mismatch;
      inst.lastActivityT = step.t_ms;
      if (policy) {
        for (const e of policy.effects) effects.push(e);
        if (policy.to) {
          const inFlight = (policy.flight_states ?? []).includes(inst.state);
          if (inst.kind === "session" || inFlight) inst.state = policy.to;
        }
      }
      return;
    }
  }

  // Record events must declare their type once it matters for authorization.
  if (step.event === "protected_record" || step.event === "send_protected_record") {
    const type = recordTypeOf(step.simulate ?? {});
    if (inst.kind === "session" && type === null) {
      fail("trace", `step ${stepIndex}: record event on the session machine must declare simulate.record_type`);
    }
    if (inst.kind === "pairing" && type === null && needsType(step)) {
      fail("trace", `step ${stepIndex}: enrollment-channel record event must declare simulate.record_type`);
    }
  }

  // A dead session slot (closed or terminated) ignores process/timer events
  // (pending_expiry, revoke, ...) — the slot itself is gone — but still
  // surfaces protocol messages, handshake frames and self-actions, so
  // post-teardown records and mis-modeled actions reject loudly.
  const DEAD_SLOT_IGNORED = new Set([
    "pending_expiry", "window_expired", "process_load", "inactivity_timeout",
    "idle_timeout", "cancel_pairing", "connection_lost", "revoke", "close_connection",
  ]);
  if (inst.kind === "session" && (inst.state === "closed" || inst.state === "terminated")) {
    if (DEAD_SLOT_IGNORED.has(step.event)) return;
  }

  const candidates = descriptor.machines[inst.name].trans.filter(
    (t) => t.from === inst.state && t.event === step.event,
  );
  if (candidates.length === 0) {
    if (inst.kind === "durable") return; // passive state container: events it does not model leave it untouched
    fail(
      "transition",
      `step ${stepIndex}: no protocol transition for event ${JSON.stringify(step.event)} from state ${inst.name}:${inst.peer}=${inst.state} (OTP2-ENC-004: never silent continuation)`,
    );
  }

  let matched = null;
  let allTimeOnly = candidates.length > 0;
  const TIME_GUARDS = new Set(["elapsed_at_least", "window_age_below"]);
  for (const t of candidates) {
    if (t.roles !== undefined && !t.roles.includes(trace.role === "phone" ? "P" : "C")) {
      allTimeOnly = false;
      continue;
    }
    inst.pendingOrientation =
      step.event === "send_protected_record" || step.event === "push_retransmit" ? "tx" : "rx";
    const reasons = new Set();
    if (evaluateGuards(t.guards ?? {}, inst, ctx, states, trace, descriptor, reasons)) {
      matched = t;
      break;
    }
    if (!(reasons.size > 0 && [...reasons].every((k) => TIME_GUARDS.has(k)))) {
      allTimeOnly = false;
    }
  }
  if (!matched) {
    if (inst.kind === "durable") {
      // Passive state container: candidate transitions exist but none matched —
      // the durable record is simply untouched by this event. Cross-checks come
      // from declared final states and per-step expects.
      return;
    }
    if (candidates.length > 0 && allTimeOnly) {
      fail(
        "time",
        `step ${stepIndex}: declared timer event ${JSON.stringify(step.event)} at ${inst.name}:${inst.peer}=${inst.state} fires before its monotonic bound (t_ms=${step.t_ms})`,
      );
    }
    fail(
      "transition",
      `step ${stepIndex}: every candidate transition for ${JSON.stringify(step.event)} at ${inst.name}:${inst.peer}=${inst.state} failed its guards`,
    );
  }

  applyTransition(inst, matched, step, ctx, states, trace, descriptor, effects);
}

function needsType(step) {
  // On the enrollment channel only authorization-relevant record events need a
  // type; other named frame events do not.
  return (
    step.event === "protected_record" ||
    step.event === "send_protected_record"
  );
}
// ---------- checkResourceTrace: multi-peer resource-ceiling accounting ----------
// Test-only trace utility for the phone-side GATT/record resource ceilings of
// veles-v1.md section 9 (OTP2-GAT-004, OTP2-GAT-012, OTP2-REC-032) plus the
// record-size bound (OTP2-CRY-016c). All numeric ceilings come from
// limits.json; allocation is bounded BEFORE buffers are accounted: an offer
// that would exceed a ceiling is refused (the NEWEST offer; existing
// allocations and other peers are never disturbed) and recorded in
// `refusals`. Release accounting: completion, discard and disconnect free
// their allocations back to the peer and global pools. A structurally
// impossible step (completing an unknown/already-freed transaction,
// disconnecting an unknown peer, draining an empty queue) rejects the trace
// with stage=resource. This checker does NOT model fragment framing itself
// (protocol/v1/binary-layouts.json + contract.test.mjs cover the wire format).

const RESOURCE_EVENTS = new Set([
  "client_connect",
  "client_disconnect",
  "fragment_open",
  "fragment_complete",
  "fragment_discard",
  "queue_offer",
  "queue_pop",
]);

export function checkResourceTrace(trace, states) {
  try {
    return runResourceTrace(trace, states);
  } catch (e) {
    if (e instanceof StageError) return { accepted: false, error: e.message, accounting: null, refusals: null };
    return { accepted: false, error: `stage=resource: ${e?.message ?? String(e)}`, accounting: null, refusals: null };
  }
}

function resourceLimit(states, name) {
  return limitValue(states, name);
}

function resourceFail(message) {
  fail("resource", message);
}

function runResourceTrace(trace, states) {
  if (!trace || typeof trace !== "object" || Array.isArray(trace)) resourceFail("trace must be an object");
  if (!Array.isArray(trace.steps) || trace.steps.length === 0) resourceFail("trace must carry a non-empty steps array");
  const peers = new Map(); // peer -> {incomplete: Map(tx_id -> bytes), queue: [bytes], queueBytes, connected}
  let globalIncomplete = 0;
  let globalBytes = 0;
  let globalQueueMsgs = 0;
  let globalQueueBytes = 0;
  const refusals = [];
  const releases = [];
  let prevT = null;

  const peerState = (peer) => {
    if (typeof peer !== "string" || peer.length === 0) resourceFail("peer must be a non-empty string");
    let p = peers.get(peer);
    if (!p) {
      p = { incomplete: new Map(), queue: [], queueBytes: 0, connected: false };
      peers.set(peer, p);
    }
    return p;
  };

  for (let i = 0; i < trace.steps.length; i++) {
    const step = trace.steps[i];
    if (!step || typeof step !== "object") resourceFail(`step ${i} must be an object`);
    if (prevT !== null && (typeof step.t_ms !== "number" || step.t_ms < prevT)) {
      resourceFail(`step ${i}: t_ms must be a non-decreasing number (monotonic resource trace)`);
    }
    prevT = step.t_ms ?? 0;
    if (!RESOURCE_EVENTS.has(step.event)) resourceFail(`step ${i}: unknown resource event ${JSON.stringify(step.event)}`);
    const peer = step.peer;
    const capClient = resourceLimit(states, "gatt_max_clients");
    const capIncPeer = resourceLimit(states, "reassembly_incomplete_max_per_peer");
    const capBytesPeer = resourceLimit(states, "reassembly_bytes_max_per_peer");
    const capIncGlobal = resourceLimit(states, "reassembly_incomplete_max_global");
    const capBytesGlobal = resourceLimit(states, "reassembly_bytes_max_global");
    const capQueueMsgsPeer = resourceLimit(states, "deliver_queue_messages_max_per_peer");
    const capQueueBytesPeer = resourceLimit(states, "deliver_queue_bytes_max_per_peer");
    const capQueueMsgsGlobal = resourceLimit(states, "deliver_queue_messages_max_global");
    const capQueueBytesGlobal = resourceLimit(states, "deliver_queue_bytes_max_global");
    const capRecordBytes = resourceLimit(states, "record_max_bytes");

    switch (step.event) {
      case "client_connect": {
        const connected = [...peers.values()].filter((p) => p.connected).length;
        if (connected >= capClient) {
          refusals.push(`step ${i}: client_connect ${JSON.stringify(peer)} refused: ${connected} clients already connected (gatt_max_clients=${capClient}), no allocation`);
          break;
        }
        peerState(peer).connected = true;
        break;
      }
      case "client_disconnect": {
        const p = peers.get(peer);
        if (!p || !p.connected) resourceFail(`step ${i}: disconnect for unknown or unconnected peer ${JSON.stringify(peer)}`);
        for (const bytes of p.incomplete.values()) {
          globalIncomplete -= 1;
          globalBytes -= bytes;
        }
        globalQueueMsgs -= p.queue.length;
        globalQueueBytes -= p.queueBytes;
        releases.push(
          `step ${i}: disconnect ${JSON.stringify(peer)} released ${p.incomplete.size} incomplete tx (${[...p.incomplete.values()].reduce((a, b) => a + b, 0)} B) and ${p.queue.length} queued msgs (${p.queueBytes} B)`,
        );
        peers.delete(peer);
        break;
      }
      case "fragment_open": {
        const p = peerState(peer);
        if (!Number.isInteger(step.bytes) || step.bytes <= 0) resourceFail(`step ${i}: fragment_open bytes must be a positive integer`);
        if (step.bytes > capRecordBytes) {
          refusals.push(`step ${i}: fragment_open ${JSON.stringify(peer)} tx ${JSON.stringify(step.tx_id)} refused: ${step.bytes} B exceeds record_max_bytes=${capRecordBytes}, no allocation`);
          break;
        }
        if (step.bytes > capBytesPeer) {
          refusals.push(`step ${i}: fragment_open ${JSON.stringify(peer)} tx ${JSON.stringify(step.tx_id)} refused: ${step.bytes} B exceeds reassembly_bytes_max_per_peer=${capBytesPeer}, no allocation`);
          break;
        }
        if (!p.connected) resourceFail(`step ${i}: fragment_open for unconnected peer ${JSON.stringify(peer)}`);
        const peerBytes = [...p.incomplete.values()].reduce((a, b) => a + b, 0);
        if (p.incomplete.size >= capIncPeer) {
          refusals.push(`step ${i}: fragment_open ${JSON.stringify(peer)} refused: ${p.incomplete.size} incomplete tx already (reassembly_incomplete_max_per_peer=${capIncPeer}), no allocation`);
          break;
        }
        if (peerBytes + step.bytes > capBytesPeer) {
          refusals.push(`step ${i}: fragment_open ${JSON.stringify(peer)} refused: ${peerBytes}+${step.bytes} B would exceed reassembly_bytes_max_per_peer=${capBytesPeer}, no allocation`);
          break;
        }
        if (globalIncomplete >= capIncGlobal) {
          refusals.push(`step ${i}: fragment_open ${JSON.stringify(peer)} refused: ${globalIncomplete} incomplete tx globally (reassembly_incomplete_max_global=${capIncGlobal}), no allocation`);
          break;
        }
        if (globalBytes + step.bytes > capBytesGlobal) {
          refusals.push(`step ${i}: fragment_open ${JSON.stringify(peer)} refused: ${globalBytes}+${step.bytes} B would exceed reassembly_bytes_max_global=${capBytesGlobal}, no allocation`);
          break;
        }
        p.incomplete.set(String(step.tx_id), step.bytes);
        globalIncomplete += 1;
        globalBytes += step.bytes;
        break;
      }
      case "fragment_complete":
      case "fragment_discard": {
        const p = peers.get(peer);
        const key = String(step.tx_id);
        if (!p || !p.incomplete.has(key)) resourceFail(`step ${i}: ${step.event} for unknown or already-freed tx ${JSON.stringify(step.tx_id)} of peer ${JSON.stringify(peer)}`);
        const bytes = p.incomplete.get(key);
        p.incomplete.delete(key);
        globalIncomplete -= 1;
        globalBytes -= bytes;
        releases.push(`step ${i}: ${step.event} ${JSON.stringify(peer)} tx ${JSON.stringify(step.tx_id)} freed ${bytes} B`);
        break;
      }
      case "queue_offer": {
        const p = peerState(peer);
        if (!Number.isInteger(step.bytes) || step.bytes <= 0) resourceFail(`step ${i}: queue_offer bytes must be a positive integer`);
        if (!p.connected) resourceFail(`step ${i}: queue_offer for unconnected peer ${JSON.stringify(peer)}`);
        if (p.queue.length >= capQueueMsgsPeer) {
          refusals.push(`step ${i}: queue_offer ${JSON.stringify(peer)} refused: ${p.queue.length} msgs queued (deliver_queue_messages_max_per_peer=${capQueueMsgsPeer}), newest not enqueued`);
          break;
        }
        if (p.queueBytes + step.bytes > capQueueBytesPeer) {
          refusals.push(`step ${i}: queue_offer ${JSON.stringify(peer)} refused: ${p.queueBytes}+${step.bytes} B would exceed deliver_queue_bytes_max_per_peer=${capQueueBytesPeer}, newest not enqueued`);
          break;
        }
        if (globalQueueMsgs >= capQueueMsgsGlobal) {
          refusals.push(`step ${i}: queue_offer ${JSON.stringify(peer)} refused: ${globalQueueMsgs} msgs queued globally (deliver_queue_messages_max_global=${capQueueMsgsGlobal}), newest not enqueued`);
          break;
        }
        if (globalQueueBytes + step.bytes > capQueueBytesGlobal) {
          refusals.push(`step ${i}: queue_offer ${JSON.stringify(peer)} refused: ${globalQueueBytes}+${step.bytes} B would exceed deliver_queue_bytes_max_global=${capQueueBytesGlobal}, newest not enqueued`);
          break;
        }
        p.queue.push(step.bytes);
        p.queueBytes += step.bytes;
        globalQueueMsgs += 1;
        globalQueueBytes += step.bytes;
        break;
      }
      case "queue_pop": {
        const p = peers.get(peer);
        if (!p || !p.connected) resourceFail(`step ${i}: queue_pop for unknown or unconnected peer ${JSON.stringify(peer)}`);
        if (p.queue.length === 0) resourceFail(`step ${i}: queue_pop for empty queue of ${JSON.stringify(peer)}`);
        const bytes = p.queue.shift();
        p.queueBytes -= bytes;
        globalQueueMsgs -= 1;
        globalQueueBytes -= bytes;
        releases.push(`step ${i}: queue_pop ${JSON.stringify(peer)} drained ${bytes} B`);
        break;
      }
      default:
        resourceFail(`step ${i}: unhandled resource event ${JSON.stringify(step.event)}`);
    }
  }

  const perPeer = {};
  for (const [peer, p] of peers) {
    perPeer[peer] = {
      connected: p.connected,
      incomplete_tx: p.incomplete.size,
      reassembly_bytes: [...p.incomplete.values()].reduce((a, b) => a + b, 0),
      queued_msgs: p.queue.length,
      queued_bytes: p.queueBytes,
    };
  }
  return {
    accepted: true,
    error: null,
    refusals,
    releases,
    accounting: {
      connected_clients: [...peers.values()].filter((p) => p.connected).length,
      totals: {
        reassembly_incomplete_global: globalIncomplete,
        reassembly_bytes_global: globalBytes,
        queued_msgs_global: globalQueueMsgs,
        queued_bytes_global: globalQueueBytes,
      },
      per_peer: perPeer,
    },
  };
}
