// Verifies scripts/manifests.mjs emits platform-specific subdirectories.
//
// Both platforms share the same `name` field, so the manifests must be
// written into per-platform subdirectories to avoid the second write
// overwriting the first.

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, rmSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const BRIDGE_DIR = resolve(__dirname, '..');
const OUT_DIR = resolve(BRIDGE_DIR, '..', 'build', 'native-bridge', 'manifests');

function clean() {
    rmSync(OUT_DIR, { recursive: true, force: true });
}

describe('manifests script output tree', () => {
    beforeAll(clean);
    afterAll(clean);

    it('writes a manifest into a platform-specific subdirectory for each platform', () => {
        const stdout = execFileSync('node', [resolve(BRIDGE_DIR, 'scripts/manifests.mjs')], {
            cwd: BRIDGE_DIR,
            encoding: 'utf8',
        });

        const printed = stdout.trim().split(/\r?\n/);
        expect(printed).toHaveLength(2);

        for (const platform of ['windows', 'macos']) {
            const outPath = resolve(OUT_DIR, platform, 'app.veles.native_bridge.json');
            expect(existsSync(outPath)).toBe(true);
            expect(printed).toContain(outPath);

            const parsed = JSON.parse(readFileSync(outPath, 'utf8'));
            expect(parsed.name).toBe('app.veles.native_bridge');
            expect(parsed.type).toBe('stdio');
        }
    });

    it('keeps both platform manifests separate (no overwrite)', () => {
        execFileSync('node', [resolve(BRIDGE_DIR, 'scripts/manifests.mjs')], {
            cwd: BRIDGE_DIR,
            encoding: 'utf8',
        });

        const windowsPath = resolve(OUT_DIR, 'windows', 'app.veles.native_bridge.json');
        const macosPath = resolve(OUT_DIR, 'macos', 'app.veles.native_bridge.json');

        expect(existsSync(windowsPath)).toBe(true);
        expect(existsSync(macosPath)).toBe(true);

        const windows = JSON.parse(readFileSync(windowsPath, 'utf8'));
        const macos = JSON.parse(readFileSync(macosPath, 'utf8'));

        // Both keep the shared name but live in distinct subdirectories.
        expect(windows.name).toBe('app.veles.native_bridge');
        expect(macos.name).toBe('app.veles.native_bridge');

        // Platform-specific path field must differ (sanity check that
        // the two files are not byte-identical duplicates).
        expect(windows.path).not.toBe(macos.path);
    });
});
