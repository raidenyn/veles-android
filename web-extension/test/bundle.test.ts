import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { basename, join } from 'node:path';
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
        //
        // The WASM asset name changes with every Rust build, so it is matched
        // by pattern instead of an exact hashed name.
        const files = walk(DIST).sort();
        const wasm = files.filter((file) => /^assets\/veles_crypto_bg-[^/]+\.wasm$/.test(file));
        expect(wasm).toHaveLength(1);
        // The connector module chunk embeds the hashed WASM asset name, so its own hash
        // changes with every Rust build; match it by pattern like the WASM asset.
        const connectorChunks = files.filter((file) => /^chunks\/connector-[^/]+\.js$/.test(file));
        expect(connectorChunks).toHaveLength(1);
        expect(
            files.filter((file) => !wasm.includes(file) && !connectorChunks.includes(file)),
        ).toEqual([
            'background.js',
            'chunks/clipboard-DE1lG-x_.js',
            'chunks/modulepreload-polyfill-DaKOjhqt.js',
            'chunks/ownership-BOcJ26Iv.js',
            'chunks/pairing-panel-BWIaYGNA.js',
            'chunks/probe-BXD-9UJ6.js',
            'chunks/repository-wKCuwgFE.js',
            'chunks/storage-DOMCF8BZ.js',
            'connector.html',
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

    it('bundles the WASM core for the connector tab only', () => {
        const files = walk(DIST);
        const wasm = files.filter((file) => /^assets\/veles_crypto_bg-[^/]+\.wasm$/.test(file));
        expect(wasm).toHaveLength(1);
        const asset = basename(wasm[0]);
        const connectorChunks = files.filter((file) => /^chunks\/connector-[^/]+\.js$/.test(file));

        // The connector tab's module chunk references the emitted asset; it is the only
        // script that may do so.
        const referencing = files
            .filter((file) => file.endsWith('.js'))
            .filter((file) => readFileSync(join(DIST, file), 'utf8').includes(asset));
        expect(referencing).toEqual(connectorChunks);

        // The MV3 service worker (background.js) and its chunks never load it.
        const background = readFileSync(join(DIST, 'background.js'), 'utf8');
        expect(background).not.toContain(asset);
        expect(background).not.toContain('veles_crypto');
    });

    it('dist/manifest.json matches the canonical generator exactly', () => {
        const emitted = JSON.parse(readFileSync(join(DIST, 'manifest.json'), 'utf8'));
        expect(emitted).toEqual(JSON.parse(JSON.stringify(buildExtensionManifest())));
    });

    it('ships the production connector without the test-only browser harness', () => {
        const files = walk(DIST);
        expect(files.filter((file) => /harness|fixture|browser-connector/.test(file))).toEqual([]);

        const connectorChunks = files.filter((file) => /^chunks\/connector-[^/]+\.js$/.test(file));
        expect(connectorChunks).toHaveLength(1);
        const connector = readFileSync(join(DIST, connectorChunks[0]), 'utf8');
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
    const FORBIDDEN = [
        'velesDebug',
        'Veles synthetic',
        'veles-debug-readout',
        // OTP-10 pairing panel surface (D5). The panel chunk is a dynamic import behind
        // the build-time `__VELES_CHANNEL__` guard, so the production build folds it
        // away entirely; these strings prove a regression would be caught.
        'veles-pairing-debug',
        'Veles pairing debug',
        'Pairing code',
        'Start pairing',
        'Cancel pairing',
        'Clear incomplete record',
        'Phone to pair',
    ];

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

    it('panel_absent_from_production_bundle', () => {
        const files = walk(GUARD);
        // A tree-shaken dynamic import leaves no chunk behind.
        expect(files.filter((file) => /pairing-panel/.test(file))).toEqual([]);
        const leaks: string[] = [];
        for (const file of files) {
            const bytes = readFileSync(join(GUARD, file));
            const utf8 = bytes.toString('utf8');
            const utf16 = utf16leText(bytes);
            for (const needle of FORBIDDEN) {
                if (utf8.includes(needle) || utf16.includes(needle))
                    leaks.push(`${file}: ${needle}`);
            }
        }
        expect(leaks).toEqual([]);
    });
});
