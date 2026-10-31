import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

test('isolated init script only adds the .otp23 suffix to the debug build type', () => {
    const script = readFileSync(new URL('./isolated-device.gradle', import.meta.url), 'utf8');
    const code = script.replace(/^\s*\/\/.*$/gm, '');
    assert.match(code, /getByName\('debug'\)\.applicationIdSuffix = '\.otp23'/);
    // No wire behavior or feature flag may hang off the isolated package any more.
    assert.doesNotMatch(code, /buildConfigField|OTP23_POC/);
    // The opt-in script must stay out of release builds.
    assert.doesNotMatch(code, /release/);
});

test('app no longer defines or reads a cap-only OTP23_POC flag', () => {
    const root = new URL('../../../', import.meta.url);
    for (const file of ['app/build.gradle.kts', 'app/src/main/java/me/nagaev/veles/sharing/SharingGattServer.kt', 'app/src/main/java/me/nagaev/veles/sharing/GattTransport.kt']) {
        assert.doesNotMatch(readFileSync(new URL(file, root), 'utf8'), /OTP23_POC/, file);
    }
});

test('physical driver only calls methods exported by the real connector', () => {
    const window = {};
    vm.runInNewContext(readFileSync(new URL('../extension/connector.js', import.meta.url), 'utf8'), {
        window,
        document: { getElementById: () => null },
    });
    const script = readFileSync(new URL('./physical_validate.py', import.meta.url), 'utf8');
    const calls = [...script.matchAll(/otp23\.(\w+)\(/g)].map((match) => match[1]);
    assert.ok(calls.length > 5);
    for (const method of new Set(calls)) assert.equal(typeof window.otp23[method], 'function', `missing ${method}`);
});