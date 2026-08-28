import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import init, { reverse_bytes } from '../../../web-extension/rust-wasm/pkg/veles_crypto.js';

const wasmUrl = new URL(
    '../../../web-extension/rust-wasm/pkg/veles_crypto_bg.wasm',
    import.meta.url,
);
await init({ module_or_path: await readFile(wasmUrl) });

for (const [input, expected] of [
    [[], []],
    [[0x00, 0x80, 0xff, 0x2a], [0x2a, 0xff, 0x80, 0x00]],
]) {
    assert.deepEqual(Array.from(reverse_bytes(Uint8Array.from(input))), expected);
}
