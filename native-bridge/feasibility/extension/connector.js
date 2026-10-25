// Disposable connector: owns exactly one Native Messaging port and correlates requests.
// It forwards opaque records only; it never parses, stores or logs record contents.
"use strict";

const ENVELOPE_VERSION = 1;
const state = {
    port: null,
    hostName: null,
    apiVersion: null,
    counter: 0,
    pending: new Map(), // request_id -> { resolve, timer, type }
    events: [], // unsolicited host events
    completions: [], // every correlated reply, in arrival order
    disconnect: null, // { at, message } once the port closed
    opened: null,
};

function now() {
    return Math.round(performance.now());
}

function setStatus(text) {
    const node = document.getElementById("status");
    if (node) node.textContent = text;
}

function toBase64(bytes) {
    let binary = "";
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary);
}

function synthesizePortClosed(type, request_id) {
    // RFC OTP2-BRD-020: the connector, not the host, synthesizes failures when the port dies.
    return {
        envelope_version: ENVELOPE_VERSION,
        type: "error",
        request_id,
        code: "internal",
        recoverability: "fatal",
        stage: "port_closed",
        synthesized: true,
        request_type: type,
    };
}

function onMessage(message) {
    const entry = { at: now(), message };
    if (typeof message.request_id === "string" && state.pending.has(message.request_id)) {
        const { resolve, timer } = state.pending.get(message.request_id);
        clearTimeout(timer);
        state.pending.delete(message.request_id);
        state.completions.push(entry);
        resolve(message);
    } else if (typeof message.request_id === "string") {
        // Correlated message nobody is waiting for: a duplicate terminal event is a defect.
        state.events.push({ at: now(), message, orphan_correlated: true });
    } else {
        state.events.push(entry);
    }
}

function onDisconnect() {
    const message = chrome.runtime.lastError ? chrome.runtime.lastError.message : null;
    state.disconnect = { at: now(), message };
    state.port = null;
    for (const [request_id, { resolve, timer, type }] of state.pending) {
        clearTimeout(timer);
        resolve(synthesizePortClosed(type, request_id));
    }
    state.pending.clear();
    setStatus(`port closed: ${message ?? "no error"}`);
}

function connect(hostName) {
    if (state.port) throw new Error("port already open");
    state.hostName = hostName;
    state.disconnect = null;
    state.apiVersion = null;
    state.opened = now();
    state.port = chrome.runtime.connectNative(hostName);
    state.port.onMessage.addListener(onMessage);
    state.port.onDisconnect.addListener(onDisconnect);
    setStatus(`port open to ${hostName}`);
    return { opened_at: state.opened };
}

function request(type, fields = {}, timeoutMs = 40000) {
    const request_id = `r${(++state.counter).toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const body = { envelope_version: ENVELOPE_VERSION, type, request_id, ...fields };
    if (state.apiVersion !== null && type !== "hello") body.api_version = state.apiVersion;
    return new Promise((resolve) => {
        if (!state.port) {
            resolve(synthesizePortClosed(type, request_id));
            return;
        }
        const timer = setTimeout(() => {
            state.pending.delete(request_id);
            resolve({ type: "error", request_id, code: "timeout", recoverability: "retryable", stage: "connector_timeout", synthesized: true });
        }, timeoutMs);
        state.pending.set(request_id, { resolve, timer, type });
        try {
            state.port.postMessage(body);
        } catch (error) {
            clearTimeout(timer);
            state.pending.delete(request_id);
            resolve({ type: "error", request_id, code: "internal", recoverability: "fatal", stage: "post_failed", synthesized: true, detail: String(error) });
        }
    });
}

async function hello() {
    const reply = await request("hello", { api_versions: [1] });
    if (reply.type === "ready") state.apiVersion = reply.api_version;
    return reply;
}

window.otp23 = {
    connect,
    hello,
    request,
    startScan: (service_uuid) => request("start_scan", { service_uuid }),
    stopScan: (scan_handle) => request("stop_scan", { scan_handle }),
    connectDevice: (target) => request("connect", target),
    disconnectDevice: (connection_handle) => request("disconnect", { connection_handle }),
    send: (connection_handle, bytes) =>
        request("send", { connection_handle, record_base64: toBase64(bytes) }),
    shutdown: () => request("shutdown"),
    closePort: () => {
        if (state.port) state.port.disconnect();
        state.port = null;
        state.disconnect = { at: now(), message: "closed by connector" };
    },
    drainEvents: () => state.events.splice(0, state.events.length),
    snapshot: () => ({
        hostName: state.hostName,
        portOpen: state.port !== null,
        apiVersion: state.apiVersion,
        disconnect: state.disconnect,
        pending: [...state.pending.keys()],
        eventCount: state.events.length,
        completionCount: state.completions.length,
        visibility: document.visibilityState,
        hasFocus: document.hasFocus(),
        now: now(),
    }),
    // Unsolicited-event timeline without any record contents.
    timeline: () =>
        state.events.map(({ at, message, orphan_correlated }) => ({
            at,
            type: message.type,
            status: message.status,
            code: message.code,
            stage: message.stage,
            scan_handle: message.scan_handle,
            orphan_correlated: !!orphan_correlated,
        })),
};
setStatus("ready");
