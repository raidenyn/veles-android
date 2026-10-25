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
