import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const SCRIPT = join(import.meta.dirname, 'check-mac-comparisons.mjs');

// Temporary source roots so each case controls exactly which files the guard
// sees. The repository's own sources are never mutated.
async function withTree(run) {
    const root = await mkdtemp(join(tmpdir(), 'veles-mac-comparisons-'));
    try {
        await run(root);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
}

function runScript(cwd, ...args) {
    return spawnSync(process.execPath, [SCRIPT, ...args], { cwd, encoding: 'utf8' });
}

test('a violation injected into a temporary Kotlin root fails', async () => {
    await withTree(async (root) => {
        const sources = join(root, 'kotlin');
        await mkdir(sources, { recursive: true });
        await writeFile(join(sources, 'MacCheck.kt'), 'fun check(mac: ByteArray, expectedMac: ByteArray) {\n    if (mac == expectedMac) return\n}\n');

        const result = runScript(root, '--scan', sources);

        assert.equal(result.status, 1, `expected exit 1, got ${result.status}\nstderr: ${result.stderr}`);
        assert.match(result.stderr, /MacCheck\.kt:2:/);
        assert.match(result.stderr, /direct MAC comparison/);
    });
});

test('a violation injected into a temporary TypeScript root fails', async () => {
    await withTree(async (root) => {
        const sources = join(root, 'typescript');
        await mkdir(sources, { recursive: true });
        await writeFile(join(sources, 'mac-check.ts'), 'export function check(mac: string, expectedMac: string): boolean {\n    return mac === expectedMac;\n}\n');

        const result = runScript(root, '--scan', sources);

        assert.equal(result.status, 1, `expected exit 1, got ${result.status}\nstderr: ${result.stderr}`);
        assert.match(result.stderr, /mac-check\.ts:2:/);
        assert.match(result.stderr, /direct MAC comparison/);
    });
});

test('scan of a missing directory exits 2', async () => {
    await withTree(async (root) => {
        const missing = join(root, 'does-not-exist');
        const result = runScript(root, '--scan', missing);

        assert.equal(result.status, 2, `expected exit 2, got ${result.status}\nstderr: ${result.stderr}`);
        assert.ok(
            result.stderr.includes(`no such directory ${missing}`),
            `expected a no-such-directory diagnostic, got stderr: ${result.stderr}`,
        );
    });
});

test('empty scan set exits 2', async () => {
    await withTree(async (root) => {
        // Neither default root exists, and no --scan was given.
        const result = runScript(root);

        assert.equal(result.status, 2, `expected exit 2, got ${result.status}\nstderr: ${result.stderr}`);
        assert.match(result.stderr, /nothing to scan|no director/i);
    });
});

test('default roots scan android/app/src/main and web-extension/src', async () => {
    await withTree(async (root) => {
        const kotlin = join(root, 'android', 'app', 'src', 'main', 'kotlin');
        const typescript = join(root, 'web-extension', 'src', 'session');
        await mkdir(kotlin, { recursive: true });
        await mkdir(typescript, { recursive: true });
        await writeFile(join(kotlin, 'MacCheck.kt'), 'fun check(mac: ByteArray, expectedMac: ByteArray) {\n    if (mac != expectedMac) return\n}\n');
        await writeFile(join(typescript, 'mac-check.ts'), 'export function check(mac: string, expectedMac: string): boolean {\n    return mac !== expectedMac;\n}\n');

        const result = runScript(root);

        assert.equal(result.status, 1, `expected exit 1, got ${result.status}\nstderr: ${result.stderr}`);
        assert.match(result.stderr, /MacCheck\.kt:2:/);
        assert.match(result.stderr, /mac-check\.ts:2:/);
    });
});

test('--root keeps its meaning as base for default roots', async () => {
    await withTree(async (root) => {
        const base = join(root, 'checkout');
        const kotlin = join(base, 'android', 'app', 'src', 'main', 'kotlin');
        await mkdir(kotlin, { recursive: true });
        await writeFile(join(kotlin, 'MacCheck.kt'), 'fun check(mac: ByteArray, expectedMac: ByteArray) {\n    if (mac == expectedMac) return\n}\n');
        // web-extension/src is deliberately absent: default roots under --root
        // are still skipped silently, unlike an explicit --scan directory.

        const result = runScript(root, '--root', base);

        assert.equal(result.status, 1, `expected exit 1, got ${result.status}\nstderr: ${result.stderr}`);
        assert.match(result.stderr, /MacCheck\.kt:2:/);
        assert.doesNotMatch(result.stderr, /no such directory/);
    });
});
