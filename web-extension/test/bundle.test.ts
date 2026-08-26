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
        expect(files).toEqual(['background.js', 'content.js', 'manifest.json']);
    });

    it('dist/manifest.json matches the canonical generator exactly', () => {
        const emitted = JSON.parse(readFileSync(join(DIST, 'manifest.json'), 'utf8'));
        expect(emitted).toEqual(JSON.parse(JSON.stringify(buildExtensionManifest())));
    });
});
