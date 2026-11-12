import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';

// The WASM package is generated, git-ignored output of `./gradlew rustWasm`.
const PACKAGE_BYTES = new URL('../../rust-wasm/pkg/veles_crypto_bg.wasm', import.meta.url);

interface Fixture {
    id: string;
    expected: { outputs: { ctx: string } };
}

/** The frozen public pairing context from the shared OTP-02 fixture. */
function pairingContext(): Uint8Array {
    const document = JSON.parse(
        readFileSync(new URL('../../../protocol/v1/fixtures/crypto.json', import.meta.url), 'utf8'),
    ) as { fixtures: Fixture[] };
    const fixture = document.fixtures.find((entry) => entry.id === 'veles-pairing-code-000000');
    if (fixture === undefined) throw new Error('missing veles-pairing-code-000000 fixture');
    return Uint8Array.from(
        (fixture.expected.outputs.ctx.slice(2).match(/../g) ?? []).map((byte) =>
            Number.parseInt(byte, 16),
        ),
    );
}

describe('pairing core', () => {
    it('loads_wasm_and_round_trips_start_close', async () => {
        const { initPairingCore, PairingExecution, openHandleCount } =
            await import('../../src/pairing/core');
        await initPairingCore(readFileSync(PACKAGE_BYTES));

        expect(openHandleCount()).toBe(0);
        const { execution, point } = PairingExecution.start('000042', pairingContext());
        expect(point).toHaveLength(65);
        expect(point[0]).toBe(4);
        expect(openHandleCount()).toBe(1);

        execution.close();
        execution.close();
        expect(openHandleCount()).toBe(0);
    });

    it('missing_package_gives_clear_error', async () => {
        // A fresh module instance so the successful initialization above cannot
        // satisfy this loader.
        vi.resetModules();
        const { initPairingCore } = await import('../../src/pairing/core');
        await expect(
            initPairingCore(new URL('./missing-package/veles_crypto_bg.wasm', import.meta.url)),
        ).rejects.toThrow(/rustWasm/);
    });
});
