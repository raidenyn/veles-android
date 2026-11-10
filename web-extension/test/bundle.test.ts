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

const DIST = join(__dirname, '..', 'dist');

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
            'chunks/probe-D1NtVc0m.js',
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
