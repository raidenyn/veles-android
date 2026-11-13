import { readFileSync } from 'node:fs';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { ConnectorApp, type ConnectorView } from '../../src/connector';
import {
    mountPairingPanel,
    PAIRING_PANEL_TEST_ID,
    type PairingPanelDeps,
    type PairingPanelStatus,
} from '../../src/debug/pairing-panel';
import { initPairingCore } from '../../src/pairing/core';
import type { ConnectorViewModel } from '../../src/connector/view-model';
import {
    HANDLE_CONNECTION,
    HANDLE_DEVICE,
    connectedEvent,
    disconnectedEvent,
    readyEvent,
    sentEvent,
} from '../connector/fixtures';
import {
    createTestController,
    createTestTrust,
    sessionEngineDeps,
} from '../connector/session-harness';

const PACKAGE_BYTES = new URL('../../rust-wasm/pkg/veles_crypto_bg.wasm', import.meta.url);

beforeAll(async () => {
    await initPairingCore(readFileSync(PACKAGE_BYTES));
});

/** Minimal DOM surface: the panel only creates and appends elements. */
class FakeElement {
    readonly dataset: Record<string, string> = {};
    readonly attributes: Record<string, string> = {};
    readonly children: FakeElement[] = [];
    readonly listeners = new Map<string, (() => void)[]>();
    textContent = '';
    hidden = false;
    disabled = false;
    value = '';
    type = '';
    maxLength = 0;

    constructor(
        readonly tagName: string,
        readonly ownerDocument: FakeDocument,
    ) {}

    append(...nodes: FakeElement[]): void {
        this.children.push(...nodes);
    }

    replaceChildren(...nodes: FakeElement[]): void {
        this.children.splice(0, this.children.length, ...nodes);
    }

    setAttribute(name: string, value: string): void {
        this.attributes[name] = value;
    }

    addEventListener(type: string, listener: () => void): void {
        const existing = this.listeners.get(type) ?? [];
        existing.push(listener);
        this.listeners.set(type, existing);
    }

    click(): void {
        for (const listener of this.listeners.get('click') ?? []) listener();
    }

    all(): FakeElement[] {
        return [this, ...this.children.flatMap((child) => child.all())];
    }

    byTestId(testid: string): FakeElement | undefined {
        return this.all().find((element) => element.dataset.testid === testid);
    }

    byLabel(label: string): FakeElement | undefined {
        return this.all().find((element) => element.attributes['aria-label'] === label);
    }

    button(text: string): FakeElement | undefined {
        return this.all().find(
            (element) => element.tagName === 'button' && element.textContent === text,
        );
    }
}

class FakeDocument {
    readonly body = new FakeElement('body', this);

    createElement(tagName: string): FakeElement {
        return new FakeElement(tagName, this);
    }
}

async function flush(): Promise<void> {
    for (let index = 0; index < 20; index += 1) await Promise.resolve();
}

function sectionText(section: FakeElement): string {
    return section
        .all()
        .map((element) => element.textContent)
        .join(' ');
}

function mountPanel(
    options: {
        devices?: { handle: string; label: string }[];
        peers?: { installationId: string; label: string; state: string }[];
        start?: (deviceHandle: string, code: string) => Promise<boolean>;
        cancel?: () => Promise<boolean>;
    } = {},
) {
    const document = new FakeDocument();
    const devices = vi.fn(() => options.devices ?? [{ handle: 'device-1', label: 'Pixel' }]);
    const status = vi.fn<() => PairingPanelStatus>(() => ({ phase: 'idle', finished: false }));
    const peers = vi.fn(async () => options.peers ?? []);
    const revoke = vi.fn(async () => undefined);
    const start = vi.fn(options.start ?? (async () => true));
    const cancel = vi.fn(options.cancel ?? (async () => true));
    const deps: PairingPanelDeps = {
        document: document as unknown as Document,
        devices,
        status,
        trust: { peers, revoke },
        start,
        cancel,
        onChange: () => () => undefined,
        // No polling in unit tests: status is refreshed after every action.
        setInterval: () => 0,
    };
    mountPairingPanel(document.body as unknown as HTMLElement, deps);
    const section = document.body.byTestId(PAIRING_PANEL_TEST_ID);
    if (section === undefined) throw new Error('panel is not mounted');
    return { document, section, devices, status, peers, revoke, start, cancel };
}

describe('Chrome pairing debug panel', () => {
    it('chrome_clear_uses_revoke', async () => {
        const fixture = mountPanel({
            peers: [
                {
                    installationId: 'ffeeddccbbaa99887766554433221100',
                    label: 'Pixel',
                    state: 'pending',
                },
            ],
        });
        await flush();

        fixture.revoke.mockImplementationOnce(async () => {
            fixture.peers.mockResolvedValue([]);
        });
        const clear = fixture.section.button('Clear incomplete record');
        if (clear === undefined) throw new Error('clear control is missing');
        clear.click();
        await flush();

        expect(fixture.revoke).toHaveBeenCalledTimes(1);
        expect(fixture.revoke).toHaveBeenCalledWith('ffeeddccbbaa99887766554433221100');
        expect(sectionText(fixture.section)).not.toContain('ffeeddccbbaa99887766554433221100');
    });

    it('start passes the code to the machine and never renders it', async () => {
        const fixture = mountPanel();
        const code = fixture.section.byLabel('Pairing code');
        const device = fixture.section.byLabel('Phone to pair');
        if (code === undefined || device === undefined) throw new Error('panel inputs are missing');
        code.value = '123456';
        device.value = 'device-1';

        const start = fixture.section.button('Start pairing');
        if (start === undefined) throw new Error('start control is missing');
        start.click();
        await flush();

        expect(fixture.start).toHaveBeenCalledWith('device-1', '123456');
        expect(
            fixture.section
                .all()
                .map((element) => element.textContent)
                .join(' '),
        ).not.toContain('123456');
        expect(code.value).toBe('');
    });

    it('shows state and the fingerprint but not the code', async () => {
        const fixture = mountPanel();
        fixture.status.mockReturnValue({
            phase: 'awaiting_prepare',
            fingerprint: '1122 3344 5566 7788',
            finished: false,
        });
        fixture.devices.mockReturnValue([]);
        const code = fixture.section.byLabel('Pairing code');
        const device = fixture.section.byLabel('Phone to pair');
        if (code === undefined || device === undefined) throw new Error('panel inputs are missing');
        code.value = '123456';
        device.value = 'device-1';
        fixture.section.button('Start pairing')?.click();
        await flush();

        expect(sectionText(fixture.section)).toContain('awaiting_prepare');
        expect(sectionText(fixture.section)).toContain('1122 3344 5566 7788');
        expect(sectionText(fixture.section)).not.toContain('123456');
    });

    it('cancel reports a cleanup failure without leaking the code', async () => {
        const fixture = mountPanel({ cancel: async () => false });
        fixture.section.button('Cancel pairing')?.click();
        await flush();

        expect(sectionText(fixture.section)).toContain('cancel cleanup failed');
        expect(sectionText(fixture.section)).not.toMatch(/\b\d{6}\b/);
    });
});

class RecordingView implements ConnectorView {
    readonly models: ConnectorViewModel[] = [];

    render(viewModel: ConnectorViewModel): void {
        this.models.push(viewModel);
    }

    onAction(): () => void {
        return () => undefined;
    }
}

describe('pairing code containment', () => {
    it('code_never_in_diagnostic_ring_or_view_model', async () => {
        const CODE = '123456';
        const context = createTestController();
        const trust = await createTestTrust(context.clock);
        const view = new RecordingView();
        const app = new ConnectorApp({
            controller: context.controller,
            view,
            setTimeout: context.clock.setTimeout,
            clearTimeout: context.clock.clearTimeout,
            session: {
                createRepository(verifier) {
                    trust.bindVerifier(verifier);
                    return trust.repository;
                },
                createEngineDeps(handle, peer, localInstallationId) {
                    return sessionEngineDeps(
                        trust,
                        context.clock,
                        localInstallationId,
                    )(handle, peer);
                },
            },
        });
        const started = app.start();
        const hello = await context.currentPort().nextPosted();
        await context.currentPort().emit(readyEvent({ request_id: hello.request_id }));
        await started;

        const pairing = app.startPairing(HANDLE_DEVICE, CODE);
        const connect = await context.currentPort().nextPosted();
        expect(connect.type).toBe('connect');
        await context.currentPort().emit(
            connectedEvent({
                request_id: connect.request_id,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        const send = await context.currentPort().nextPosted();
        expect(send.type).toBe('send');
        await context.currentPort().emit(
            sentEvent({
                request_id: send.request_id,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect(await pairing).toBe(true);

        const snapshot = JSON.stringify({
            models: view.models,
            diagnostics: context.controller.snapshot().diagnostics,
            posted: context.currentPort().posted,
            status: app.pairingState,
        });
        expect(snapshot).not.toContain(CODE);

        const cancelling = app.cancelPairing();
        const disconnect = await context.currentPort().nextPosted();
        expect(disconnect.type).toBe('disconnect');
        await context.currentPort().emit(
            disconnectedEvent({
                request_id: disconnect.request_id,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect(await cancelling).toBe(true);
        app.dispose();
    });
});
