/* global document, window, chrome, Event */
// Packaged-browser evidence for OTP-07.
//
// This loads the built extension in Chromium. One page exercises the real
// production bundle with no native host registered, proving the asynchronous
// missing-host guidance from Chrome's runtime. The other pages are ephemeral
// copies of the built connector HTML whose production bundle runs unchanged
// except for a test-only `connectNative` patch, so the real HTML/UI, controller,
// protocol and state code execute against a simulated Native Port.
//
// This is NOT physical Bluetooth evidence. See docs/otp-07-connector.md for the
// separate Windows/macOS smoke checklist, which remains NOT RUN.
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium } from 'playwright-core';
import { build } from 'vite';
import { execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const root = resolve(import.meta.dirname, '..');
const dist = join(root, 'dist');
const SCAN = 'a000000000000001';
const DEVICE_A = 'b000000000000001';
const DEVICE_B = 'b000000000000002';
const DEVICE_C = 'b000000000000003';
const CONNECTION_A = 'c000000000000001';
const CONNECTION_B = 'c000000000000002';
const CONNECTION_C = 'c000000000000003';
const VELES_SERVICE_UUID = '8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51';

for (const required of ['connector.html', 'connector.js', 'manifest.json']) {
    if (!existsSync(join(dist, required))) {
        console.error(
            `Missing dist/${required}. Run "npm run build" before "npm run test:connector-browser".`,
        );
        process.exit(1);
    }
}
if (existsSync(join(dist, 'browser-harness.js'))) {
    console.error('dist/ unexpectedly contains the test-only browser harness.');
    process.exit(1);
}

const temporary = await mkdtemp(join(tmpdir(), 'veles-connector-browser-'));
const extension = join(temporary, 'extension');
let context;
let remoteBrowser;
let remoteExtensionSession;
let loadedExtensionId;
const remote = process.env.VELES_BROWSER_CDP;
const syncRemote = () =>
    execFileSync('homepc', ['--host', 'mac', 'sync', extension, 'otp08-browser-fixture'], {
        stdio: 'inherit',
    });

async function nextPost(page) {
    return page.evaluate(() => window.__velesConnectorHarness.nextPosted());
}

async function emit(page, event) {
    return page.evaluate((value) => window.__velesConnectorHarness.emit(value), event);
}

async function posted(page) {
    return page.evaluate(() => window.__velesConnectorHarness.posted());
}

async function waitForText(page, selector, text) {
    await page.waitForFunction(
        ({ selector: target, text: expected }) =>
            (document.querySelector(target)?.textContent ?? '').includes(expected),
        { selector, text },
        { timeout: 15_000 },
    );
}

function readyFor(requestId, apiVersion = 1) {
    return {
        envelope_version: 1,
        api_version: apiVersion,
        type: 'ready',
        request_id: requestId,
        bridge_version: 'veles-bridge-1.0.0',
        platform: 'windows',
        capabilities: ['veles_record_v1'],
    };
}

function deviceFound(handle, label) {
    return {
        envelope_version: 1,
        api_version: 1,
        type: 'device_found',
        scan_handle: SCAN,
        device_handle: handle,
        label,
    };
}

function connected(requestId, connectionHandle) {
    return {
        envelope_version: 1,
        api_version: 1,
        type: 'connected',
        request_id: requestId,
        connection_handle: connectionHandle,
    };
}

function message(connectionHandle, recordBase64) {
    return {
        envelope_version: 1,
        api_version: 1,
        type: 'message',
        connection_handle: connectionHandle,
        record_base64: recordBase64,
    };
}

async function waitForHarness(page) {
    await page.waitForFunction(() => (window.__velesConnectorHarness?.portCount() ?? 0) > 0, null, {
        timeout: 15_000,
    });
}

async function connectViaUi(page, deviceRow, deviceHandle, connectionHandle, expectedRows) {
    await page.click(`#device-list li:nth-child(${deviceRow}) button`);
    const request = await nextPost(page);
    assert.equal(request.type, 'connect');
    assert.equal(request.device_handle, deviceHandle);
    assert.equal(request.api_version, 1);
    await emit(page, connected(request.request_id, connectionHandle));
    await page.waitForFunction(
        (expected) => document.querySelectorAll('#connection-list li').length === expected,
        expectedRows,
        { timeout: 15_000 },
    );
    assert.equal(
        await page.evaluate(
            (expected) =>
                document.querySelector(`#connection-list button[data-handle="${expected}"]`) !==
                null,
            connectionHandle,
        ),
        true,
    );
}

try {
    await cp(dist, extension, { recursive: true });
    await build({
        configFile: false,
        logLevel: 'error',
        build: {
            outDir: extension,
            emptyOutDir: false,
            minify: false,
            lib: {
                entry: join(root, 'test/connector/browser-harness.ts'),
                formats: ['es'],
                fileName: () => 'browser-harness.js',
            },
            rollupOptions: { output: { inlineDynamicImports: true } },
        },
    });

    const connectorHtml = await readFile(join(extension, 'connector.html'), 'utf8');
    const productionScript = '<script type="module" crossorigin src="/connector.js"></script>';
    assert(
        connectorHtml.includes(productionScript),
        'built connector.html is missing the production connector module',
    );
    // Leave the packaged connector.html untouched for the real-runtime test.
    // The harness page is an ephemeral copy with the test-only module inserted
    // before the unchanged production entry.
    const harnessHtml = connectorHtml.replace(
        productionScript,
        `<script type="module" crossorigin src="/browser-harness.js"></script>\n        ${productionScript}`,
    );

    const executablePath =
        process.env.VELES_TEST_CHROMIUM ??
        (existsSync(chromium.executablePath()) ? chromium.executablePath() : undefined);
    if (executablePath === undefined && !remote) {
        throw new Error(
            'Chromium is unavailable. Set VELES_TEST_CHROMIUM or install Playwright Chromium.',
        );
    }

    if (remote) {
        syncRemote();
        remoteBrowser = await chromium.connectOverCDP(remote);
        remoteExtensionSession = await remoteBrowser.newBrowserCDPSession();
        ({ id: loadedExtensionId } = await remoteExtensionSession.send('Extensions.loadUnpacked', {
            path: '/Users/raiden/homepc/work/otp08-browser-fixture',
        }));
        context = remoteBrowser.contexts()[0];
    } else
        context = await chromium.launchPersistentContext(join(temporary, 'profile'), {
            executablePath,
            headless: true,
            args: [
                `--disable-extensions-except=${extension}`,
                `--load-extension=${extension}`,
                '--no-sandbox',
            ],
        });
    const worker =
        context
            .serviceWorkers()
            .find((item) => !loadedExtensionId || new URL(item.url()).host === loadedExtensionId) ??
        (await context.waitForEvent('serviceworker', {
            predicate: (item) =>
                !loadedExtensionId || new URL(item.url()).host === loadedExtensionId,
        }));
    const extensionId = loadedExtensionId ?? new URL(worker.url()).host;

    // 1. Real production bundle, no host registered, real Chrome runtime.
    const production = await context.newPage();
    await production.goto(`chrome-extension://${extensionId}/connector.html`);
    await production.waitForFunction(
        () => {
            const guidance = document.querySelector('#host-guidance');
            return guidance !== null && !guidance.hidden;
        },
        null,
        { timeout: 20_000 },
    );
    const missingHost = await production.evaluate(() => ({
        status: document.querySelector('#status')?.textContent ?? '',
        hostStatus: document.querySelector('#host-status')?.textContent ?? '',
        guidance: document.querySelector('#host-guidance')?.textContent ?? '',
        searchDisabled: document.querySelector('#search-button')?.disabled,
        retryDisabled: document.querySelector('#reconnect-button')?.disabled,
    }));
    assert.match(missingHost.status, /Native bridge not installed/i);
    assert.match(missingHost.hostStatus, /not installed/i);
    assert.match(missingHost.guidance, /Install the compatible Veles Native Bridge/);
    assert.equal(missingHost.searchDisabled, true);
    assert.equal(missingHost.retryDisabled, false);
    await production.close();

    // Keep the genuine connector path: lifecycle authorization intentionally
    // rejects other extension pages. Modify only this ephemeral test copy.
    await writeFile(join(extension, 'connector.html'), harnessHtml);
    if (remote) syncRemote();

    // 2. Real built HTML/UI with a simulated Native Port in the test copy.
    const page = await context.newPage();
    await page.goto(`chrome-extension://${extensionId}/connector.html`);
    await waitForHarness(page);

    const hello = await nextPost(page);
    assert.equal(hello.type, 'hello');
    assert.deepEqual(hello.api_versions, [1]);
    await emit(page, readyFor(hello.request_id));
    await waitForText(page, '#status', 'Connector ready.');
    await waitForText(page, '#host-status', 'API 1');

    await page.click('#search-button');
    const scan = await nextPost(page);
    assert.equal(scan.type, 'start_scan');
    assert.equal(scan.service_uuid, VELES_SERVICE_UUID);
    await emit(page, {
        envelope_version: 1,
        api_version: 1,
        type: 'scan_state',
        request_id: scan.request_id,
        scan_handle: SCAN,
        status: 'started',
    });
    await emit(page, deviceFound(DEVICE_A, 'Phone A'));
    await emit(page, deviceFound(DEVICE_B, 'Phone B'));
    await page.waitForFunction(
        () => document.querySelectorAll('#device-list li').length === 2,
        null,
        { timeout: 10_000 },
    );
    assert.match(await page.locator('#device-list').textContent(), /Phone A[\s\S]*Phone B/);

    // Task 4 residual: a focused row must survive an unrelated list rebuild.
    await page.focus('#device-list li:nth-child(1) button');
    const focusedHandle = await page.evaluate(() => document.activeElement?.dataset.handle ?? null);
    assert.equal(focusedHandle, DEVICE_A);
    await emit(page, deviceFound(DEVICE_C, 'Phone C'));
    await page.waitForFunction(
        () => document.querySelectorAll('#device-list li').length === 3,
        null,
        { timeout: 10_000 },
    );
    assert.equal(
        await page.evaluate(() => document.activeElement?.dataset.handle ?? null),
        DEVICE_A,
        'focus should survive an unrelated device-list rebuild',
    );

    await connectViaUi(page, 1, DEVICE_A, CONNECTION_A, 1);
    await connectViaUi(page, 2, DEVICE_B, CONNECTION_B, 2);
    assert.match(await page.locator('#connection-list').textContent(), /Phone A/);
    assert.match(await page.locator('#connection-list').textContent(), /Phone B/);
    assert.deepEqual(
        await page.$$eval('#connection-list button[data-action="disconnect"]', (buttons) =>
            buttons.map((button) => button.dataset.handle),
        ),
        [CONNECTION_A, CONNECTION_B],
    );

    // Task 4 residual: when the initiating row becomes disabled/removed after
    // success, focus moves to a meaningful surviving control.
    await page.focus('#device-list li:nth-child(3) button');
    assert.equal(
        await page.evaluate(() => document.activeElement?.dataset.handle ?? null),
        DEVICE_C,
    );
    await connectViaUi(page, 3, DEVICE_C, CONNECTION_C, 3);
    const fallbackFocus = await page.evaluate(() => ({
        id: document.activeElement?.id ?? null,
        disabled: document.activeElement?.getAttribute('disabled') !== null,
    }));
    assert.ok(
        fallbackFocus.id === 'search-button' || fallbackFocus.id === 'reconnect-button',
        `focus should fall back to an enabled connector control, got ${String(fallbackFocus.id)}`,
    );
    assert.equal(fallbackFocus.disabled, false);

    // Disconnect routing is checked in the real DOM too: row C must post C's
    // handle and leave A/B connected.
    await page.click('#connection-list li:nth-child(3) button');
    const disconnectRequest = await nextPost(page);
    assert.equal(disconnectRequest.type, 'disconnect');
    assert.equal(disconnectRequest.connection_handle, CONNECTION_C);
    await emit(page, {
        envelope_version: 1,
        api_version: 1,
        type: 'disconnected',
        request_id: disconnectRequest.request_id,
        connection_handle: CONNECTION_C,
    });
    await page.waitForFunction(
        () => document.querySelectorAll('#connection-list li').length === 2,
        null,
        { timeout: 10_000 },
    );
    assert.deepEqual(
        await page.$$eval('#connection-list button[data-action="disconnect"]', (buttons) =>
            buttons.map((button) => button.dataset.handle),
        ),
        [CONNECTION_A, CONNECTION_B],
    );

    // Whole-record boundary through the real packaged controller/UI: a 4096-byte
    // record is accepted and diagnosed, while 4097 bytes fails closed visibly.
    const exact = Buffer.alloc(4096, 0x33).toString('base64');
    await page.locator('#diagnostics').evaluate((element) => {
        element.open = true;
    });
    await page.click('#synthetic-copy-arm');
    await emit(page, message(CONNECTION_A, exact));
    await waitForText(page, '#diagnostics-list', 'bytes=4096');
    await waitForText(page, '#synthetic-copy-status', 'Synthetic code copied.');
    assert.equal(
        await page.evaluate(
            async () =>
                (await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] })).length,
        ),
        0,
    );
    const oversized = Buffer.alloc(4097, 0x44).toString('base64');
    await emit(page, message(CONNECTION_A, oversized));
    await waitForText(page, '#error-banner', 'unexpected or correlated-invalid');
    assert.equal(
        await page.evaluate(() => document.querySelector('#search-button')?.disabled),
        true,
    );

    // 3. API overlap rejection is visible and performs no BLE post.
    await page.reload();
    await waitForHarness(page);
    const incompatibleHello = await nextPost(page);
    assert.deepEqual(incompatibleHello.api_versions, [1]);
    await emit(page, readyFor(incompatibleHello.request_id, 2));
    await waitForText(page, '#status', 'Update required');
    await waitForText(page, '#host-guidance', 'Update the Veles Native Bridge');
    assert.equal(
        await page.evaluate(() => document.querySelector('#search-button')?.disabled),
        true,
    );
    assert.equal((await posted(page)).filter((entry) => entry.type === 'start_scan').length, 0);

    // 4. Extension-wide ownership across restored/duplicated documents. The
    // owner holds the real Web Lock and owns the only simulated Native Port;
    // the duplicate stays visibly secondary and creates none.
    await page.close();
    const ownerPage = await context.newPage();
    await ownerPage.goto(`chrome-extension://${extensionId}/connector.html`);
    await waitForHarness(ownerPage);
    const ownerHello = await nextPost(ownerPage);
    assert.equal(ownerHello.type, 'hello');
    await emit(ownerPage, readyFor(ownerHello.request_id));
    await waitForText(ownerPage, '#status', 'Connector ready.');
    assert.equal(await ownerPage.evaluate(() => window.__velesConnectorHarness.portCount()), 1);

    const duplicate = await context.newPage();
    await duplicate.goto(`chrome-extension://${extensionId}/connector.html`);
    await waitForText(duplicate, '#status', 'already open in another tab');
    assert.match(
        await duplicate.evaluate(() => document.querySelector('#host-guidance')?.textContent ?? ''),
        /Only one connector tab/,
    );
    assert.equal(
        await duplicate.evaluate(() => window.__velesConnectorHarness?.portCount() ?? 0),
        0,
        'a secondary connector document must not create a Native Port',
    );
    assert.equal(
        await ownerPage.evaluate(() => window.__velesConnectorHarness.portCount()),
        1,
        'the owner must keep the only active port while a duplicate exists',
    );
    await duplicate.close();

    // Owner closure releases ownership; a replacement document becomes the
    // sole owner and the only new port.
    await ownerPage.close();
    let replacementPage = await context.newPage();
    await replacementPage.goto(`chrome-extension://${extensionId}/connector.html`);
    await waitForHarness(replacementPage);
    const replacementHello = await nextPost(replacementPage);
    assert.equal(replacementHello.type, 'hello');
    assert.equal(
        await replacementPage.evaluate(() => window.__velesConnectorHarness.portCount()),
        1,
    );

    // OTP-08: real browser freeze/discard transitions, real service worker and
    // real offscreen clipboard; transport alone is simulated in this fixture.
    await emit(replacementPage, readyFor(replacementHello.request_id));
    await waitForText(replacementPage, '#status', 'Connector ready.');
    let tabId = await replacementPage.evaluate(async () => (await chrome.tabs.getCurrent()).id);
    let generationKey = `veles.lifecycle.${tabId}`;
    const oldGeneration = await worker.evaluate(
        async (key) => (await chrome.storage.session.get(key))[key],
        generationKey,
    );
    assert.equal(typeof oldGeneration.token, 'string');
    const ordinary = await context.newPage();
    await ordinary.goto('about:blank');
    await ordinary.bringToFront();
    const lifecycleSession = await context.newCDPSession(replacementPage);
    await lifecycleSession.send('Page.setWebLifecycleState', { state: 'frozen' });
    const observedFreeze = await worker.evaluate(
        async (id) => (await chrome.tabs.get(id)).frozen,
        tabId,
    );
    if (!observedFreeze) {
        console.log(
            'BLOCKED: CDP freeze returned success but tabs.get reports frozen=false for this extension page. Following freeze/resume checks use explicit DOM event injection, not physical freeze acceptance.',
        );
        await replacementPage.evaluate(() => document.dispatchEvent(new Event('freeze')));
    }
    const waitForRevocation = async () => {
        for (let i = 0; i < 100; i++) {
            const state = await worker.evaluate(
                async ({ key, tabId }) => ({
                    generation: (await chrome.storage.session.get(key))[key],
                    badge: await chrome.action.getBadgeText({ tabId }).catch(() => ''),
                    exists: await chrome.tabs.get(tabId).then(
                        () => true,
                        () => false,
                    ),
                }),
                { key: generationKey, tabId },
            );
            if ((state.generation == null || !state.exists) && state.badge === '') return;
            await delay(50);
        }
        const observed = await worker.evaluate(
            async ({ key, tabId }) => ({
                hasGeneration: (await chrome.storage.session.get(key))[key] != null,
                badge: await chrome.action.getBadgeText({ tabId }).catch(() => ''),
                tab: await chrome.tabs.get(tabId).catch(() => ({})),
            }),
            { key: generationKey, tabId },
        );
        throw new Error(
            `worker did not revoke generation: ${JSON.stringify({ hasGeneration: observed.hasGeneration, badge: observed.badge, frozen: observed.tab.frozen, discarded: observed.tab.discarded })}`,
        );
    };
    await waitForRevocation();
    const resumedLoad = replacementPage.waitForEvent('load');
    if (observedFreeze)
        await lifecycleSession.send('Page.setWebLifecycleState', { state: 'active' });
    else await replacementPage.evaluate(() => document.dispatchEvent(new Event('resume')));
    await resumedLoad;
    await waitForHarness(replacementPage);
    const resumedHello = await nextPost(replacementPage);
    assert.equal(resumedHello.type, 'hello');
    // Request IDs are process-local and may restart at c1; the authority token
    // below, not a request-ID string, proves the new document generation.
    const resumedGeneration = await worker.evaluate(
        async (key) => (await chrome.storage.session.get(key))[key],
        generationKey,
    );
    assert.notEqual(resumedGeneration.token, oldGeneration.token);
    await emit(replacementPage, readyFor(resumedHello.request_id));
    await waitForText(replacementPage, '#status', 'Connector ready.');

    await ordinary.bringToFront();
    const discardResult = await worker.evaluate(async (id) => {
        try {
            const result = await chrome.tabs.discard(id);
            return { discarded: result?.discarded, frozen: result?.frozen };
        } catch (error) {
            return { error: String(error) };
        }
    }, tabId);
    console.log(
        `Discard observation: ${JSON.stringify(discardResult)}, prior target closed=${replacementPage.isClosed()}`,
    );
    const observedDiscard = discardResult.discarded === true;
    if (!observedDiscard) {
        console.log(
            'BLOCKED: Chrome did not discard the extension page. Worker transition handling remains covered by injected API regressions; following reload is not discard acceptance.',
        );
        await replacementPage.evaluate(() => document.dispatchEvent(new Event('freeze')));
    }
    await waitForRevocation();
    if (observedDiscard && replacementPage.isClosed()) {
        const newTarget = context.waitForEvent('page');
        const restored = await worker.evaluate(async (id) => {
            try {
                await chrome.tabs.update(id, { active: true });
                return true;
            } catch {
                return false;
            }
        }, tabId);
        if (restored) replacementPage = await newTarget;
        else {
            void newTarget.catch(() => undefined);
            console.log(
                'BLOCKED: discarded target/tab was removed rather than resumable; reopening a connector proves new-document recovery only.',
            );
            replacementPage = await context.newPage();
            await replacementPage.goto(`chrome-extension://${extensionId}/connector.html`);
        }
    } else {
        const discardedLoad = replacementPage.waitForEvent('load');
        if (observedDiscard) await replacementPage.bringToFront();
        else await replacementPage.reload();
        await discardedLoad;
    }
    await waitForHarness(replacementPage);
    const discardedHello = await nextPost(replacementPage);
    assert.equal(discardedHello.type, 'hello');
    tabId = await replacementPage.evaluate(async () => (await chrome.tabs.getCurrent()).id);
    generationKey = `veles.lifecycle.${tabId}`;
    const discardedGeneration = await worker.evaluate(
        async (key) => (await chrome.storage.session.get(key))[key],
        generationKey,
    );
    assert.notEqual(discardedGeneration.token, resumedGeneration.token);
    await replacementPage.close();
    await waitForRevocation();
    await ordinary.close();

    console.log(
        'Packaged browser: missing-host UX, API overlap, simulated two-phone routing/bounds, real offscreen synthetic copy/cleanup, singleton ownership, lifecycle fresh-generation recovery and removal invalidation passed. See explicit BLOCKED lines for unobserved browser transitions. Not physical BLE or strict background acceptance.',
    );
} finally {
    if (remoteExtensionSession && loadedExtensionId)
        await remoteExtensionSession
            .send('Extensions.uninstall', { id: loadedExtensionId })
            .catch(() => undefined);
    if (remoteBrowser) await remoteBrowser.close();
    else await context?.close();
    await rm(temporary, { recursive: true, force: true });
}
