import { describe, it, expect } from 'vitest';
import { buildExtensionManifest } from '../src/manifest';

// Strict MV3 baseline guard — the TypeScript-native equivalent of the former
// Kotlin `validateExtensionManifest` Gradle task. Exact-match (not substring)
// so weakening CSP with extra directives, adding host_permissions, or widening
// permissions is caught at `npm test` time.
describe('manifest baseline guard (exact match)', () => {
    it('manifest_version is exactly 3', () => {
        expect(buildExtensionManifest().manifest_version).toBe(3);
    });

    it('requests only storage and nativeMessaging', () => {
        const m = buildExtensionManifest();
        expect(m).toHaveProperty('permissions');
        expect(m.permissions).toEqual(['storage', 'nativeMessaging']);
    });

    it('does not request tabs, activeTab, or host permissions', () => {
        const m = buildExtensionManifest();
        expect(m).not.toHaveProperty('host_permissions');
        expect(m.permissions).not.toContain('tabs');
        expect(m.permissions).not.toContain('activeTab');
    });

    it('has no web_accessible_resources', () => {
        expect(buildExtensionManifest()).not.toHaveProperty('web_accessible_resources');
    });

    it('content_security_policy is exactly the locked-down map', () => {
        expect(buildExtensionManifest().content_security_policy).toEqual({
            extension_pages: "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'",
        });
    });

    it('action has only the title so action.onClicked can fire', () => {
        expect(buildExtensionManifest().action).toEqual({ default_title: 'Veles OTP' });
        expect(buildExtensionManifest().action).not.toHaveProperty('default_popup');
    });

    it('options_ui.page is exactly options.html', () => {
        expect(buildExtensionManifest().options_ui).toEqual({ page: 'options.html' });
    });
});
