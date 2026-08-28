import { describe, it, expect } from 'vitest';
import { buildExtensionManifest } from '../src/manifest';

// Strict MV3 baseline guard — the TypeScript-native equivalent of the former
// Kotlin `validateExtensionManifest` Gradle task. Exact-match (not substring)
// so weakening CSP with extra directives, adding host_permissions, or
// non-empty permissions is caught at `npm test` time.
describe('manifest baseline guard (exact match)', () => {
    it('manifest_version is exactly 3', () => {
        expect(buildExtensionManifest().manifest_version).toBe(3);
    });

    it('permissions is present and exactly []', () => {
        const m = buildExtensionManifest();
        expect(m).toHaveProperty('permissions');
        expect(m.permissions).toEqual([]);
    });

    it('host_permissions is absent', () => {
        expect(buildExtensionManifest()).not.toHaveProperty('host_permissions');
    });

    it('content_security_policy is exactly the locked-down map', () => {
        expect(buildExtensionManifest().content_security_policy).toEqual({
            extension_pages: "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'",
        });
    });

    it('action.default_popup is exactly popup.html', () => {
        expect(buildExtensionManifest().action).toEqual({ default_popup: 'popup.html' });
    });

    it('options_ui.page is exactly options.html', () => {
        expect(buildExtensionManifest().options_ui).toEqual({ page: 'options.html' });
    });
});
