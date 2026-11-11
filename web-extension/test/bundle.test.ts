import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { buildExtensionManifest } from '../src/manifest';

function walk(dir: string, base = ''): string[] {
    return readdirSync(dir).flatMap((entry) => {
        const path = join(dir, entry);
        const rel = base ? `${base}/${entry}` : entry;
        return statSync(path).isDirectory() ? walk(path, rel) : [rel];
    });
}

/** UTF-16LE decoded as text so probe strings cannot hide behind the wider encoding. */
function utf16leText(bytes: Uint8Array): string {
    return Buffer.from(bytes).toString('utf16le');
}

const DIST = join(__dirname, '..', 'dist');
const GUARD = join(__dirname, '..', '..', 'build', 'web-extension-guard');

describe('dist/ contents (post-build artifact)', () => {
    it('contains exactly the expected file set', () => {
        // Run after `npm run build`. The exact list is the contract:
        // any change (new chunk, new asset, new manifest field) breaks this test
        // and forces a conscious review. Hash-named files (chunks/-[hash].js,
        // assets/[name]-[hash][extname]) are part of the asserted list because
        // under the committed lockfile they are deterministic.
        const files = walk(DIST).sort();
        expect(files).toEqual([
            'background.js',
            'chunks/clipboard-DE1lG-x_.js',
            'chunks/modulepreload-polyfill-DaKOjhqt.js',
            'chunks/ownership-BOcJ26Iv.js',
            'chunks/probe-BoZGVkp6.js',
            'chunks/repository-FtdCOb-V.js',
            'chunks/storage-DOMCF8BZ.js',
            'connector.html',
            'connector.js',
            'content.js',
            'manifest.json',
            'offscreen.html',
            'offscreen.js',
            'options.html',
            'options.js',
            'popup.html',
            'popup.js',
            'trust.js',
        ]);
    });

    it('dist/manifest.json matches the canonical generator exactly', () => {
        const emitted = JSON.parse(readFileSync(join(DIST, 'manifest.json'), 'utf8'));
        expect(emitted).toEqual(JSON.parse(JSON.stringify(buildExtensionManifest())));
    });

    it('ships the production connector without the test-only browser harness', () => {
        const files = walk(DIST);
        expect(files.filter((file) => /harness|fixture|browser-connector/.test(file))).toEqual([]);

        const connector = readFileSync(join(DIST, 'connector.js'), 'utf8');
        expect(connector).toContain('connectNative');
        expect(connector).not.toContain('__velesConnectorHarness');
        expect(connector).not.toContain('SimulatedNativePort');
    });
});

// OTP-09 D5 release guard: the guard build is the production-channel artifact
// (`VELES_GUARD_BUILD=1`, output `build/web-extension-guard/`). It must contain
// no debug hook global, synthetic probe marker or debug readout test id. The
// `dist/` assertions above stay on the ordinary development build.
describe('guard bundle excludes the debug probe', () => {
    const FORBIDDEN = ['velesDebug', 'Veles synthetic', 'veles-debug-readout'];

    it('is a production-channel build with the expected inventory shape', () => {
        expect(statSync(GUARD).isDirectory()).toBe(true);
        const manifest = JSON.parse(readFileSync(join(GUARD, 'manifest.json'), 'utf8'));
        expect(manifest.manifest_version).toBe(3);
        expect(walk(GUARD)).toContain('background.js');
    });

    it('contains none of the debug hook or synthetic probe strings', () => {
        const files = walk(GUARD);
        const hits: string[] = [];
        for (const file of files) {
            const bytes = readFileSync(join(GUARD, file));
            const utf8 = bytes.toString('utf8');
            const utf16 = utf16leText(bytes);
            for (const needle of FORBIDDEN) {
                if (utf8.includes(needle) || utf16.includes(needle))
                    hits.push(`${file}: ${needle}`);
            }
        }
        expect(hits).toEqual([]);
    });
});
