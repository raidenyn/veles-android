import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

test('isolated debug APK keeps the .otp23 suffix and gets BuildConfig.OTP23_POC=true', () => {
    const script = readFileSync(new URL('./isolated-device.gradle', import.meta.url), 'utf8');
    assert.match(script, /\.applicationIdSuffix = '\.otp23'/);
    assert.match(script, /\.buildConfigField\('boolean', 'OTP23_POC', 'true'\)/);
    // The opt-in flag must stay out of release builds.
    assert.doesNotMatch(script, /release/);
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