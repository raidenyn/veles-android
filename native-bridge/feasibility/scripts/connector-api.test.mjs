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
