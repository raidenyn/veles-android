/* global chrome, document */
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, cp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { createServer } from 'node:http';
import { chromium } from 'playwright-core';
import { build } from 'vite';

const root = resolve(import.meta.dirname, '..');
const temporary = await mkdtemp(join(tmpdir(), 'veles-browser-'));
const extension = join(temporary, 'extension');
const profile = join(temporary, 'profile');
let context;
const server = createServer((_req, res) =>
    res.end('<html><title>Isolation fixture</title><body>Fixture</body></html>'),
);
try {
    await cp(join(root, 'dist'), extension, { recursive: true });
    await build({
        configFile: false,
        logLevel: 'error',
        build: {
            outDir: extension,
            emptyOutDir: false,
            minify: false,
            lib: {
                entry: join(root, 'test/browser-fixture.ts'),
                formats: ['es'],
                fileName: () => 'fixture.js',
            },
            rollupOptions: {
                external: (id) => id.endsWith('/src/trust/repository'),
                output: { paths: () => './trust.js' },
            },
        },
    });
    const manifest = JSON.parse(await readFile(join(extension, 'manifest.json'), 'utf8'));
    // Only the isolated test copy points its content script at the local HTTP fixture.
    manifest.content_scripts[0].matches = ['http://127.0.0.1/*'];
    await writeFile(join(extension, 'manifest.json'), JSON.stringify(manifest));
    await writeFile(
        join(extension, 'content.js'),
        `
        (async () => {
            let localDenied = false;
            try { await chrome.storage.local.get('veles.trust.v1'); } catch { localDenied = true; }
            // Content scripts use the page's IndexedDB origin, not the extension's.
            const databases = await indexedDB.databases();
            document.documentElement.dataset.isolation = JSON.stringify({localDenied,
                identityVisible: databases.some(db => db.name === 'veles-installation-v1')});
        })();
    `,
    );
    const launch = () =>
        chromium.launchPersistentContext(profile, {
            channel: 'chromium',
            executablePath: process.env.VELES_TEST_CHROMIUM,
            headless: true,
            args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
        });
    context = await launch();
    const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
    const id = new URL(worker.url()).host;
    let page = await context.newPage();
    const open = () => page.goto(`chrome-extension://${id}/options.html`);
    const call = (op, ...args) =>
        page.evaluate(
            async ({ op, args }) => {
                const fixture = await import(chrome.runtime.getURL('fixture.js'));
                return fixture[op](...args);
            },
            { op, args },
        );
    const logs = [];
    const observe = (page) => page.on('console', (message) => logs.push(message.text()));
    observe(page);
    await open();
    const identity = await call('identity');
    assert.match(identity.installationId, /^[0-9a-f]{32}$/);
    assert.equal(await call('exportPrivate'), false);
    const mirror = await call('addPending');
    await call('complete', mirror);
    const active = await call('addPending');
    await call('complete', active);
    await call('activate', active);
    await call('addPending');
    const observer = await context.newPage();
    await observer.goto(`chrome-extension://${id}/options.html`);
    assert.equal(
        await observer.evaluate(async () => {
            const fixture = await import(chrome.runtime.getURL('fixture.js'));
            return (await fixture.peers()).length;
        }),
        3,
        'a second live connector must not erase the owner pending record',
    );
    await observer.close();
    await page.reload();
    assert.equal((await call('peers')).length, 2, 'context loss deletes pending only');
    await context.close();
    context = await launch();
    page = await context.newPage();
    // Test the restart boundary before ANY extension page/repository call can
    // reapply the access restriction in this browser session.
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const untrusted = await context.newPage();
    await untrusted.goto(`http://127.0.0.1:${server.address().port}/`);
    await untrusted.waitForFunction(() => document.documentElement.dataset.isolation);
    assert.deepEqual(
        await untrusted.evaluate(() => JSON.parse(document.documentElement.dataset.isolation)),
        { localDenied: true, identityVisible: false },
    );
    observe(page);
    await open();
    assert.deepEqual(await call('identity'), identity, 'identity survives actual browser restart');
    assert.equal(await call('exportPrivate'), false);
    assert.deepEqual(
        (await call('peers')).map((p) => p.state),
        ['active_activation_pending', 'active'],
    );
    await call('corrupt');
    await assert.rejects(call('peers'), /re-pair/);
    await call('reset');
    await call('identity');
    await call('loseKey');
    await assert.rejects(call('identity'), /re-pair/);
    assert(
        !logs.some(
            (line) => line.includes(identity.installationId) || line.includes(identity.publicKey),
        ),
    );
    await context.close();
    context = undefined;
    // A removed browser profile has neither identity nor trust on a subsequent install.
    await rm(profile, { recursive: true, force: true });
    context = await launch();
    page = await context.newPage();
    await open();
    assert.notEqual((await call('identity')).installationId, identity.installationId);
    assert.deepEqual(await call('peers'), []);
    console.log(
        'Browser trust: persistence, non-exportability, pending/mirror lifecycle, content isolation, corruption, key loss, profile removal and safe logs passed.',
    );
} finally {
    await context?.close();
    server.close();
    await rm(temporary, { recursive: true, force: true });
}
