// Disposable acceptance connector: owns exactly one Native Messaging port and correlates requests.
// It forwards opaque, synthetic records only. It never parses, stores or logs record contents:
// the timeline carries event types, codes and byte LENGTHS, never payload bytes.
"use strict";

const ENVELOPE_VERSION = 1;
const state = {
    port: null,
    hostName: null,
    apiVersion: null,
    counter: 0,
    pending: new Map(),
    events: [],
    completions: [],
    disconnect: null,
};

function wall() {
    return new Date().toISOString();
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

// RFC OTP2-BRD-020: the connector, not the host, synthesizes failures when the port dies.
function synthesizePortClosed(type, request_id) {
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

function redact(message) {
    // Event view without any record contents.
    const view = { ...message };
    if (typeof view.record_base64 === "string") {
        view.record_length = view.record_base64.length;
        delete view.record_base64;
    }
    return view;
}

function onMessage(message) {
    const entry = { at: wall(), message: redact(message) };
    if (typeof message.request_id === "string" && state.pending.has(message.request_id)) {
        const { resolve, timer } = state.pending.get(message.request_id);
        clearTimeout(timer);
        state.pending.delete(message.request_id);
        state.completions.push(entry);
        resolve(redact(message));
    } else {
        state.events.push({ ...entry, orphan_correlated: typeof message.request_id === "string" });
    }
}

function onDisconnect() {
    const error = chrome.runtime.lastError ? chrome.runtime.lastError.message : null;
    state.disconnect = { at: wall(), message: error };
    state.port = null;
    for (const [request_id, { resolve, timer, type }] of state.pending) {
        clearTimeout(timer);
        resolve(synthesizePortClosed(type, request_id));
    }
    state.pending.clear();
    setStatus(`port closed: ${error ?? "no error"}`);
}

function connect(hostName) {
    if (state.port) throw new Error("port already open");
    state.hostName = hostName;
    state.disconnect = null;
    state.apiVersion = null;
    state.port = chrome.runtime.connectNative(hostName);
    state.port.onMessage.addListener(onMessage);
    state.port.onDisconnect.addListener(onDisconnect);
    setStatus(`port open to ${hostName}`);
    return { opened_at: wall() };
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

window.velesAcceptance = {
    connect,
    hello,
    request,
    startScan: (service_uuid) => request("start_scan", { service_uuid }),
    stopScan: (scan_handle) => request("stop_scan", { scan_handle }),
    connectDevice: (target) => request("connect", target),
    disconnectDevice: (connection_handle) => request("disconnect", { connection_handle }),
    // Synthetic bytes only; the caller supplies the array.
    send: (connection_handle, bytes) => request("send", { connection_handle, record_base64: toBase64(bytes) }),
    shutdown: () => request("shutdown"),
    // The literal port.disconnect() lifecycle trigger.
    closePort: () => {
        if (state.port) state.port.disconnect();
        state.port = null;
        state.disconnect = { at: wall(), message: "closed by connector" };
    },
    drainEvents: () => state.events.splice(0, state.events.length),
    // Host-delivered terminal events only, in arrival order. Connector-synthesized errors (port_closed, timeout, post_failed) resolve their
    // promise directly and are never added here, so an entry in this log is evidence that the HOST emitted it, and `seq` orders host events.
    completionLog: () => state.completions.map((entry, seq) => ({
        seq, at: entry.at, type: entry.message.type, request_id: entry.message.request_id ?? null,
        connection_handle: entry.message.connection_handle ?? null, code: entry.message.code ?? null, stage: entry.message.stage ?? null,
    })),
    snapshot: () => ({
        hostName: state.hostName,
        portOpen: state.port !== null,
        apiVersion: state.apiVersion,
        disconnect: state.disconnect,
        pending: [...state.pending.keys()],
        eventCount: state.events.length,
        completionCount: state.completions.length,
    }),
};
setStatus("ready");
