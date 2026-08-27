import { describe, it, expect } from 'vitest';
import { buildExtensionManifest } from '../src/manifest';
import pkg from '../package.json';

describe('extension manifest', () => {
    it('is a valid MV3 manifest at the locked-down baseline', () => {
        const m = buildExtensionManifest();
        expect(m.manifest_version).toBe(3);
        expect(m.name).toBe('Veles OTP');
        expect(m.version).toBe(pkg.version);
        expect(m.permissions).toEqual([]);
        expect(m).not.toHaveProperty('host_permissions');
        expect(m.background?.service_worker).toBe('background.js');
        expect(m.content_scripts?.[0]?.js).toEqual(['content.js']);
        expect(m.action?.default_popup).toBe('popup.html');
        expect(m.options_ui?.page).toBe('options.html');
    });

    it('uses a restrictive CSP without wasm-unsafe-eval (allowed in 1b)', () => {
        const csp = buildExtensionManifest().content_security_policy;
        expect(csp).toEqual({ extension_pages: "script-src 'self'; object-src 'self'" });
    });
});

describe('entry-point modules', () => {
    it('background.ts, content.ts, options.ts and popup.ts compile and export', async () => {
        await expect(import('../src/background')).resolves.toBeDefined();
        await expect(import('../src/content')).resolves.toBeDefined();
        await expect(import('../src/options')).resolves.toBeDefined();
        await expect(import('../src/popup')).resolves.toBeDefined();
    });
});
