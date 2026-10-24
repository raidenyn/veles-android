// Runs both production boundary tests and (with a separate package path) RFC
// fixed-scalar conformance. Never import this into the extension product.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const packageDir = process.argv[2] ?? 'web-extension/rust-wasm/pkg';
const conformance = process.argv.includes('--conformance');
const entropyFailure = process.argv.includes('--entropy-failure');
const clockTests = process.argv.includes('--clock-tests');
let clock = 0;
if (entropyFailure) Object.defineProperty(globalThis, 'crypto', { value: { getRandomValues() { throw new Error('test entropy failure'); } }, configurable: true });
if (clockTests) Object.defineProperty(globalThis, 'performance', { value: { now: () => clock }, configurable: true });
const mod = await import(pathToFileURL(resolve(packageDir, 'veles_crypto.js')));
const wasm = await mod.default({ module_or_path: await readFile(resolve(packageDir, 'veles_crypto_bg.wasm')) });
const call = (input) => Buffer.from(mod.pairing_command(Uint8Array.from(input)));
const hex = (v) => Buffer.from(v.slice(2), 'hex');
const fixtures = JSON.parse(await readFile(new URL('../../../protocol/v1/fixtures/crypto.json', import.meta.url), 'utf8')).fixtures;
const base = fixtures.find((f) => f.id === 'veles-pairing-code-000000');
const ctx = hex(base.expected.outputs.ctx);
const op = (n, h, body = []) => [1, n, ...h, ...body];
const start = (role, code = '000000', context = ctx) => call([1, 1, role, ...Buffer.from(code), ...context]);
const empty = Buffer.from([0]);
const failed = Buffer.from([1]);

if (process.argv.includes('--memory-boundary')) {
  const before = wasm.memory.buffer.byteLength;
  assert.deepEqual(call(new Uint8Array(8 * 1024 * 1024)), failed);
  assert.ok(wasm.memory.buffer.byteLength - before <= 2 * 65536, 'oversized input must not be copied into WASM');
  console.log('WASM pre-copy input bound: PASS');
  process.exit(0);
}

if (process.argv.includes('--response-erasure')) {
  const offsets = [];
  const OriginalArray = Uint8Array;
  const originalSet = Uint8Array.prototype.set;
  const originalSlice = Uint8Array.prototype.slice;
  globalThis.Uint8Array = new Proxy(OriginalArray, {
    construct(target, args) {
      const source = args[0];
      if (source?.buffer === wasm.memory.buffer && source.length === 17) offsets.push(source.byteOffset);
      return Reflect.construct(target, args);
    },
  });
  Uint8Array.prototype.set = function (source, offset) {
    if (source.buffer === wasm.memory.buffer && source.length === 17) offsets.push(source.byteOffset);
    return originalSet.call(this, source, offset);
  };
  Uint8Array.prototype.slice = function (...args) {
    if (this.buffer === wasm.memory.buffer && this.length === 17) offsets.push(this.byteOffset);
    return originalSlice.apply(this, args);
  };
  try { exchange(start(1), start(2)); }
  finally { Uint8Array.prototype.set = originalSet; Uint8Array.prototype.slice = originalSlice; globalThis.Uint8Array = OriginalArray; }
  assert.equal(offsets.length, 2, 'observe both real Ke response allocations');
  for (const offset of offsets) {
    // dlmalloc may reuse the first two u32 words as free-list metadata after
    // drop. The remaining nine bytes must have been wiped before deallocation.
    assert.ok(new Uint8Array(wasm.memory.buffer, offset + 8, 9).every((b) => b === 0), 'Rust-owned Ke response tail must be erased');
  }
  console.log('WASM Rust-owned response erasure: PASS');
  process.exit(0);
}

if (entropyFailure) {
  assert.deepEqual(start(1), Buffer.from([2]));
  console.log('WASM entropy failure: PASS');
  process.exit(0);
}
if (clockTests) {
  const a = start(1); const b = start(2);
  clock = 299999;
  assert.equal(call(op(2, a.subarray(1, 9), b.subarray(9)))[0], 0);
  clock = 300000;
  assert.deepEqual(call(op(4, a.subarray(1, 9))), failed);
  assert.deepEqual(call(op(5, b.subarray(1, 9))), failed);
  console.log('WASM monotonic expiry boundary: PASS');
  process.exit(0);
}

function exchange(a, b, expected) {
  assert.equal(a.length, 74); assert.equal(b.length, 74);
  assert.equal(a[0], 0); assert.equal(b[0], 0);
  if (expected) {
    assert.deepEqual(a.subarray(9), hex(expected.p_a));
    assert.deepEqual(b.subarray(9), hex(expected.p_b));
  }
  const ca = call(op(2, a.subarray(1, 9), b.subarray(9)));
  assert.equal(ca[0], 0); assert.equal(ca.length, 33);
  assert.deepEqual(call(op(2, b.subarray(1, 9), a.subarray(9))), empty);
  const cb = call(op(3, b.subarray(1, 9), ca.subarray(1)));
  assert.equal(cb[0], 0); assert.equal(cb.length, 33);
  assert.deepEqual(call(op(3, a.subarray(1, 9), cb.subarray(1))), empty);
  if (expected) {
    assert.deepEqual(ca.subarray(1), hex(expected.c_a));
    assert.deepEqual(cb.subarray(1), hex(expected.c_b));
  }
  const ka = call(op(4, a.subarray(1, 9)));
  const kb = call(op(4, b.subarray(1, 9)));
  assert.equal(ka[0], 0); assert.equal(ka.length, 17);
  assert.deepEqual(ka, kb);
  if (expected) assert.deepEqual(ka.subarray(1), hex(expected.ke));
  assert.deepEqual(call(op(4, a.subarray(1, 9))), failed);
}

exchange(start(1), start(2));
const first = start(1); const second = start(1);
assert.notDeepEqual(first.subarray(9), second.subarray(9));
for (const p of [first, second]) assert.deepEqual(call(op(5, p.subarray(1, 9))), empty);
for (const size of [0, 1, 4106]) assert.deepEqual(call(new Uint8Array(size)), failed);
for (const fixture of fixtures.filter((f) => f.input?.kind === 'spake2_pairing' && f.expected.rejected && f.input.deviation?.value?.startsWith('0x'))) {
  if (!['point', 'point_format', 'shared_secret'].includes(fixture.expected.stage)) continue;
  const a = start(1);
  assert.deepEqual(call(op(2, a.subarray(1, 9), hex(fixture.input.deviation.value))), failed, fixture.id);
  assert.deepEqual(call(op(4, a.subarray(1, 9))), failed);
  console.log(`WASM negative: ${fixture.id} PASS`);
}
const wrongA = start(1); const wrongB = start(2, '123456');
const wrongCa = call(op(2, wrongA.subarray(1, 9), wrongB.subarray(9)));
call(op(2, wrongB.subarray(1, 9), wrongA.subarray(9)));
assert.deepEqual(call(op(3, wrongB.subarray(1, 9), wrongCa.subarray(1))), failed);
call(op(5, wrongA.subarray(1, 9)));

// Confirmation binding rejects reflected roles and byte-different public AAD.
for (const mismatch of ['role', 'context']) {
  const otherCtx = Buffer.from(ctx); otherCtx[60] ^= 1;
  const a = start(1); const b = start(mismatch === 'role' ? 1 : 2, '000000', mismatch === 'context' ? otherCtx : ctx);
  const ca = call(op(2, a.subarray(1, 9), b.subarray(9)));
  call(op(2, b.subarray(1, 9), a.subarray(9)));
  assert.deepEqual(call(op(3, b.subarray(1, 9), ca.subarray(1))), failed);
  call(op(5, a.subarray(1, 9)));
}

if (conformance) {
  for (const f of fixtures.filter((f) => ['rfc9382_anchor', 'spake2_pairing'].includes(f.input.kind) && f.expected.outputs)) {
    const i = f.input; const o = f.expected.outputs;
    const isRfc = i.kind === 'rfc9382_anchor';
    const ids = isRfc ? [i.id_a, i.id_b] : ['Veles Android SPAKE2 party A v1', 'Veles client SPAKE2 party B v1'];
    const aad = isRfc ? Buffer.alloc(0) : hex(o.ctx);
    const make = (role, scalar) => call(isRfc
      ? [1, 127, role, ...hex(i.w), ...hex(scalar), ids[0].length, ids[1].length, ...Buffer.from(ids.join('')), ...aad]
      : [1, 128, role, ...hex(scalar), ...Buffer.from(i.code), ...aad]);
    exchange(make(1, i.x), make(2, i.y), o);
    console.log(`WASM vector: ${f.id} PASS`);
  }
} else {
  const testStart = [1, 127, 1, ...new Uint8Array(32), ...new Uint8Array(31), 1, 0, 0];
  assert.deepEqual(call(testStart), failed);
}
console.log(`WASM pairing boundary (${conformance ? 'conformance' : 'production'}) PASS`);
