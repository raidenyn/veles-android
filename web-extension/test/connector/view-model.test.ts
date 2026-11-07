import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { ConnectorFailure } from '../../src/connector/errors';
import type { WireEvent } from '../../src/connector/protocol';
import type { ConnectorSnapshot } from '../../src/connector/state';
import { buildViewModel, guidanceFor, type ReadySummary } from '../../src/connector/view-model';

const READY: ReadySummary = {
    apiVersion: 1,
    bridgeVersion: 'veles-bridge-1.0.0',
    platform: 'windows',
    capabilities: ['veles_record_v1'],
};

const MAC_READY: ReadySummary = { ...READY, platform: 'macos' };

function snapshot(overrides: Partial<ConnectorSnapshot> = {}): ConnectorSnapshot {
    return {
        scans: [],
        devices: [],
        connections: [],
        diagnostics: [],
        ...overrides,
    };
}

function localFailure(
    code: ConnectorFailure['code'],
    stage = code,
    recoverability: ConnectorFailure['recoverability'] = 'fatal',
): ConnectorFailure {
    return { type: 'error', code, stage, recoverability, synthesized: true };
}

type WireError = Extract<WireEvent, { type: 'error' }>;

function wireFailure(
    code: WireError['code'],
    stage: string,
    recoverability: WireError['recoverability'] = 'retryable',
): WireError {
    return {
        type: 'error',
        envelope_version: 1,
        api_version: 1,
        code,
        stage,
        recoverability,
    };
}

describe('startup and host guidance', () => {
    it('shows install guidance and no BLE controls for a missing host', () => {
        const view = buildViewModel(snapshot(), {
            phase: 'failed',
            failure: localFailure('missing_host'),
        });
        expect(view.guidance).toMatchObject({
            kind: 'missing_host',
            actionLabel: 'Retry',
            bleControlsEnabled: false,
        });
        expect(view.guidance?.message).toContain('Veles Native Bridge package');
        expect(view.controls.search.enabled).toBe(false);
        expect(view.controls.ble).toBe(false);
        expect(view.controls.reconnect).toMatchObject({ enabled: true, label: 'Retry' });
        expect(view.devices).toEqual([]);
    });

    it('shows host/extension update guidance and disabled BLE controls for an incompatible API', () => {
        const view = buildViewModel(snapshot(), {
            phase: 'failed',
            failure: localFailure('incompatible_api'),
        });
        expect(view.guidance).toMatchObject({
            kind: 'incompatible_api',
            actionLabel: 'Retry',
            bleControlsEnabled: false,
        });
        expect(view.guidance?.message).toMatch(/update/i);
        expect(view.controls.search.enabled).toBe(false);
        expect(view.controls.stopScan.enabled).toBe(false);
        expect(view.controls.ble).toBe(false);
    });

    it('shows the selected bridge version and API only after ready', () => {
        const view = buildViewModel(snapshot(), {
            phase: 'ready',
            ready: READY,
        });
        expect(view.host).toMatchObject({
            state: 'ready',
            platform: 'windows',
            bridgeVersion: 'veles-bridge-1.0.0',
            apiVersion: 1,
        });
        expect(view.statusText).toBe('Connector ready.');
        expect(view.controls.search.enabled).toBe(true);
        expect(view.error).toBeNull();
    });
});

describe('macOS permission guidance', () => {
    it('retains permission guidance on the same Mac across a later generic failure', () => {
        const denied = guidanceFor(wireFailure('scan_failed', 'access_denied'), {
            platform: 'macos',
        });
        expect(denied).toMatchObject({ kind: 'macos_permission', actionLabel: 'Retry' });
        expect(denied?.message).toContain('System Settings');

        const sticky = buildViewModel(snapshot(), {
            phase: 'ready',
            ready: MAC_READY,
            failure: wireFailure('connect_failed', 'phone_unavailable'),
            macPermissionDenied: true,
        });
        expect(sticky.guidance?.kind).toBe('macos_permission');
        expect(sticky.controls.search.enabled).toBe(true);
        expect(sticky.controls.ble).toBe(true);
    });

    it('clears permission guidance on a non-Mac platform and after success', () => {
        const windowsDenial = guidanceFor(wireFailure('scan_failed', 'access_denied'), {
            platform: 'windows',
        });
        expect(windowsDenial?.kind).not.toBe('macos_permission');

        const windows = buildViewModel(snapshot(), {
            phase: 'ready',
            ready: READY,
            failure: wireFailure('scan_failed', 'access_denied'),
            macPermissionDenied: true,
        });
        expect(windows.guidance?.kind).not.toBe('macos_permission');

        const recovered = buildViewModel(snapshot(), {
            phase: 'ready',
            ready: MAC_READY,
            failure: null,
            macPermissionDenied: false,
        });
        expect(recovered.guidance).toBeNull();
    });

    it('does not invent a production app label in the permission guidance', () => {
        const denial = guidanceFor(wireFailure('scan_failed', 'access_denied'), {
            platform: 'macos',
        });
        expect(denial?.message).toContain('Veles Native Bridge');
        expect(denial?.message).not.toMatch(/signed|installer|\.app/i);
    });
});

describe('search, devices and connections', () => {
    it('renders scanning status and searchable device rows', () => {
        const view = buildViewModel(
            snapshot({
                scans: [
                    {
                        handle: 'a000000000000001',
                        status: 'started',
                        generation: 1,
                        deviceCount: 1,
                        startedAt: 1,
                        expiresAt: 2,
                        locallyExpired: false,
                    },
                ],
                devices: [
                    {
                        handle: 'b000000000000001',
                        label: 'Pixel test phone',
                        platformHint: 'winrt:opaque',
                        advertisementBytes: 0,
                        scanHandle: 'a000000000000001',
                        generation: 1,
                    },
                ],
            }),
            { phase: 'ready', ready: READY },
        );
        expect(view.scanStatusText).toContain('Searching');
        expect(view.devices).toHaveLength(1);
        expect(view.devices[0]).toMatchObject({
            label: 'Pixel test phone',
            canConnect: true,
            hintLabel: 'Unverified hint: winrt:opaque',
        });
        expect(view.controls.search.enabled).toBe(false);
        expect(view.controls.stopScan).toMatchObject({ enabled: true, visible: true });
    });

    it('renders a connected row with a provisional hint and disconnect control', () => {
        const view = buildViewModel(
            snapshot({
                connections: [
                    {
                        handle: 'c000000000000001',
                        deviceHandle: 'b000000000000001',
                        platformHint: 'winrt:refreshed',
                        status: 'connected',
                        generation: 1,
                        queuedOperations: 0,
                        queuedBytes: 0,
                        sendInFlight: false,
                    },
                ],
                devices: [
                    {
                        handle: 'b000000000000001',
                        label: 'Pixel test phone',
                        advertisementBytes: 0,
                        scanHandle: 'a000000000000001',
                        generation: 1,
                    },
                ],
            }),
            { phase: 'ready', ready: READY },
        );
        expect(view.connections[0]).toMatchObject({
            label: 'Pixel test phone',
            status: 'connected',
            canDisconnect: true,
            hintLabel: 'Unverified hint: winrt:refreshed',
        });
    });
});

describe('timeouts and replay guidance', () => {
    it('shows an explicit timeout error and no automatic replay control', () => {
        const view = buildViewModel(snapshot(), {
            phase: 'ready',
            ready: READY,
            failure: localFailure('timeout', 'timeout', 'retryable'),
        });
        expect(view.error).toMatchObject({ code: 'timeout', recoverable: true });
        expect(view.guidance).toMatchObject({
            kind: 'timeout',
            noAutomaticReplay: true,
        });
        expect(view.guidance?.message).toContain('not replayed');
        expect(view.guidance?.actionLabel).not.toMatch(/send|replay/i);
    });

    it('shows an explicit ambiguous-send error without replay', () => {
        const view = buildViewModel(snapshot(), {
            phase: 'ready',
            ready: READY,
            failure: wireFailure('send_failed', 'ambiguous_send', 'fatal'),
        });
        expect(view.error?.message).toContain('not replayed');
        expect(view.guidance?.kind).toBe('ambiguous_send');
        expect(view.guidance?.noAutomaticReplay).toBe(true);
    });
});

describe('connector.html static contract', () => {
    it('contains the required accessible/static elements and a responsive viewport', () => {
        const html = readFileSync(join(__dirname, '..', '..', 'connector.html'), 'utf8');
        for (const id of [
            'status',
            'host-guidance',
            'search-button',
            'stop-scan-button',
            'scan-status',
            'device-list',
            'connection-list',
            'error-banner',
            'reconnect-button',
            'shutdown-button',
            'diagnostics',
            'diagnostics-list',
        ]) {
            expect(html).toContain(`id="${id}"`);
        }
        expect(html).toContain('name="viewport"');
        expect(html).toContain('aria-live');
        expect(html).toContain('--veles-on-accent');
        expect(html).toContain('color: var(--veles-on-accent)');
        expect(html).toContain('./src/connector.ts');
        // Dynamic rendering must never depend on an inline event handler.
        expect(html).not.toMatch(/onclick=/i);
    });
});

// OTP-25 Task 12: the installer guidance keeps the two failure kinds apart, is
// user-invoked (Retry only), and never offers or implies a trust reset or an
// automatic replay.
describe('installGuidancePreservesTrustAndRetry', () => {
    function deepFreeze<T>(value: T): T {
        if (typeof value === 'object' && value !== null && !Object.isFrozen(value)) {
            Object.freeze(value);
            for (const child of Object.values(value)) deepFreeze(child);
        }
        return value;
    }

    const forbidden =
        /reset|forget|re-?pair|clear|remove (?:the )?(?:trust|pair)|replay(?:ed)? (?:the|your)|auto(?:matic(?:ally)?)? (?:install|update)/i;

    it('keeps missing-host and incompatible-API guidance distinct and retry-only', () => {
        const missing = buildViewModel(deepFreeze(snapshot()), {
            phase: 'failed',
            failure: localFailure('missing_host'),
        });
        const incompatible = buildViewModel(deepFreeze(snapshot()), {
            phase: 'failed',
            failure: localFailure('incompatible_api'),
        });

        expect(missing.guidance?.kind).toBe('missing_host');
        expect(incompatible.guidance?.kind).toBe('incompatible_api');
        expect(missing.guidance?.title).not.toBe(incompatible.guidance?.title);
        expect(missing.guidance?.message).toContain('Install the compatible Veles Native Bridge');
        expect(missing.guidance?.message).not.toMatch(/update/i);
        expect(incompatible.guidance?.message).toContain('Update the Veles Native Bridge');
        expect(incompatible.guidance?.message).not.toMatch(/install the compatible/i);

        for (const view of [missing, incompatible]) {
            const guidance = view.guidance;
            expect(guidance).toMatchObject({
                actionLabel: 'Retry',
                canRetry: true,
                bleControlsEnabled: false,
                noAutomaticReplay: false,
            });
            // Trust survives installation: say so, and never offer to undo it.
            expect(guidance?.message).toMatch(/paired phones? (?:stay|remain)s? trusted/i);
            expect(guidance?.message).not.toMatch(forbidden);
            // Links are user-invoked: the page never fabricates or auto-opens a URL.
            expect(guidance?.message).not.toMatch(/https?:\/\//i);
            // Retry is the single recovery control; BLE and search stay off.
            expect(view.controls.reconnect).toMatchObject({ enabled: true, label: 'Retry' });
            expect(view.controls.ble).toBe(false);
            expect(view.controls.search.enabled).toBe(false);
            expect(view.controls.stopScan.enabled).toBe(false);
            expect(view.devices).toEqual([]);
            expect(view.connections).toEqual([]);
        }
    });
});
