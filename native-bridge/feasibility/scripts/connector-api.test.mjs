import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

test('physical driver only calls methods exported by the real connector', () => {
    const window = {};
    vm.runInNewContext(readFileSync(new URL('../extension/connector.js', import.meta.url), 'utf8'), {
        window,
        document: { getElementById: () => null },
    });
    const script = readFileSync(new URL('./physical_validate.py', import.meta.url), 'utf8');
    const calls = [...script.matchAll(/otp23\.(\w+)\(/g)].map((match) => match[1]);
    assert.ok(calls.length > 5);
    for (const method of calls) assert.equal(typeof window.otp23[method], 'function', `missing ${method}`);
});

test('connector records visibility/focus history through listeners only', () => {
    const listeners = {};
    const document = {
        visibilityState: 'hidden',
        getElementById: () => null,
        hasFocus: () => false,
        addEventListener: (name, fn) => { listeners[`document:${name}`] = fn; },
    };
    const window = { addEventListener: (name, fn) => { listeners[`window:${name}`] = fn; } };
    const source = readFileSync(new URL('../extension/connector.js', import.meta.url), 'utf8');
    vm.runInNewContext(source, { window, document, performance: { now: () => 5 } });
    const before = window.otp23.visibilityHistory();
    assert.equal(before.initial.visibility, 'hidden');
    assert.equal(before.initial.focused, false);
    assert.equal(before.events.length, 0);
    assert.equal(before.truncated, false);
    document.visibilityState = 'visible';
    listeners['document:visibilitychange']();
    listeners['window:focus']();
    const after = window.otp23.visibilityHistory();
    assert.equal(after.events.map((e) => `${e.seq}:${e.kind}`).join(), '0:visibilitychange,1:focus');
    assert.equal(after.events[0].visibility, 'visible');
    assert.ok(!/setInterval|AudioContext|WebSocket|RTCPeerConnection/.test(source));
});

test('native macOS access denial displays exact retry guidance and successful scan clears it', async () => {
    const panel = { hidden: true };
    const text = { textContent: '' };
    let receive;
    let platform = 'macos';
    let scanReply = { type: 'error', code: 'scan_failed', stage: 'access_denied', recoverability: 'retryable' };
    const port = {
        disconnect: () => {},
        onMessage: { addListener: (fn) => { receive = fn; } },
        onDisconnect: { addListener: () => {} },
        postMessage: (request) => receive({ request_id: request.request_id,
            ...(request.type === 'hello' ? { type: 'ready', api_version: 1, platform } : scanReply) }),
    };
    const window = {};
    vm.runInNewContext(readFileSync(new URL('../extension/connector.js', import.meta.url), 'utf8'), {
        window,
        document: { getElementById: (id) => ({ 'permission-guidance': panel, 'permission-guidance-text': text })[id] ?? null },
        chrome: { runtime: { connectNative: () => port } },
        performance: { now: () => 0 },
        setTimeout: () => 1,
        clearTimeout: () => {},
    });
    window.otp23.connect('test-host');
    await window.otp23.hello();
    const denied = await window.otp23.startScan('synthetic-service');
    assert.equal(denied.stage, 'access_denied');
    assert.equal(panel.hidden, false);
    assert.equal(text.textContent, 'Bluetooth access was denied. Open System Settings -> Privacy & Security -> Bluetooth, enable VelesOtp23PoC, return to this connector, and retry the scan. Keep this extension loaded; no reinstall or permission reset is needed.');
    // Reconnecting to the same platform retains recovery instructions until the retry succeeds.
    window.otp23.closePort();
    window.otp23.connect('test-host');
    await window.otp23.hello();
    assert.equal(panel.hidden, false);
    scanReply = { type: 'scan_state', status: 'started', scan_handle: 'scan' };
    await window.otp23.startScan('synthetic-service');
    assert.equal(panel.hidden, true);
    assert.equal(text.textContent, '');
    // Show another Mac denial, then switch platform WITHOUT an intervening successful scan.
    scanReply = { type: 'error', code: 'scan_failed', stage: 'access_denied' };
    await window.otp23.startScan('synthetic-service');
    assert.equal(panel.hidden, false);
    window.otp23.closePort();
    platform = 'windows';
    window.otp23.connect('test-host');
    await window.otp23.hello();
    assert.equal(panel.hidden, true);
    assert.equal(text.textContent, '');
    scanReply = { type: 'error', code: 'scan_failed', stage: 'access_denied' };
    await window.otp23.startScan('synthetic-service');
    assert.equal(panel.hidden, true);
    assert.equal(text.textContent, '');
});
