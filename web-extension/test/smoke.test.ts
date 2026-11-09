import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { buildExtensionManifest } from '../src/manifest';
import { gitDescribe, resolveExtensionVersion } from '../src/version';

describe('extension manifest', () => {
    it('is a valid MV3 manifest with the connector permissions', () => {
        const m = buildExtensionManifest();
        expect(m.manifest_version).toBe(3);
        expect(m.name).toBe('Veles OTP');
        expect(m.version).toBe(
            resolveExtensionVersion(
                { VELES_EXTENSION_VERSION: process.env.VELES_EXTENSION_VERSION || undefined },
                gitDescribe,
            ).version,
        );
        expect(m.permissions).toEqual([
            'storage',
            'nativeMessaging',
            'offscreen',
            'clipboardWrite',
        ]);
        expect(m).not.toHaveProperty('host_permissions');
        expect(m).not.toHaveProperty('web_accessible_resources');
        expect(m.background?.service_worker).toBe('background.js');
        expect(m.content_scripts?.[0]?.js).toEqual(['content.js']);
        expect(m.action).toEqual({ default_title: 'Veles OTP' });
        expect(m.options_ui?.page).toBe('options.html');
    });

    it('uses a restrictive CSP permitting only wasm-unsafe-eval (1b)', () => {
        const csp = buildExtensionManifest().content_security_policy;
        expect(csp).toEqual({
            extension_pages: "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'",
        });
    });
});

describe('entry-point modules', () => {
    it('background.ts, content.ts, options.ts, popup.ts and connector.ts compile and export', async () => {
        await expect(import('../src/background')).resolves.toBeDefined();
        await expect(import('../src/content')).resolves.toBeDefined();
        await expect(import('../src/options')).resolves.toBeDefined();
        await expect(import('../src/popup')).resolves.toBeDefined();
        await expect(import('../src/connector')).resolves.toBeDefined();
    });
});

describe('root connector page', () => {
    it('contains the required static element ids and module entry', () => {
        const html = readFileSync(join(__dirname, '..', 'connector.html'), 'utf8');
        for (const id of [
            'status',
            'host-guidance',
            'search-button',
            'scan-status',
            'device-list',
            'connection-list',
            'error-banner',
            'reconnect-button',
            'shutdown-button',
            'diagnostics',
        ]) {
            expect(html).toContain(`id="${id}"`);
        }
        expect(html).toContain('./src/connector.ts');
    });
});
