import { describe, it, expect } from 'vitest';
import {
    DEVELOPMENT_EXTENSION_ID,
    VELES_EXTENSION_ID,
    buildExtensionManifest,
    resolveReleaseIdentity,
} from '../src/manifest';

// Strict MV3 baseline guard — the TypeScript-native equivalent of the former
// Kotlin `validateExtensionManifest` Gradle task. Exact-match (not substring)
// so weakening CSP with extra directives, adding host_permissions, or widening
// permissions is caught at `npm test` time.
describe('manifest baseline guard (exact match)', () => {
    it('manifest_version is exactly 3', () => {
        expect(buildExtensionManifest().manifest_version).toBe(3);
    });

    it('requests only connector storage/transport and offscreen clipboard permissions', () => {
        const m = buildExtensionManifest();
        expect(m).toHaveProperty('permissions');
        expect(m.permissions).toEqual([
            'storage',
            'nativeMessaging',
            'offscreen',
            'clipboardWrite',
        ]);
        expect(m.minimum_chrome_version).toBe('132');
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

// OTP-25 Task 12: the production extension ID comes from the same approved
// release config the host packaging uses. A release build never falls back to
// the development placeholder, and a development config can never be mistaken
// for a release.
describe('release identity (production vs development)', () => {
    const PRODUCTION_ID = 'bbbbbbbbbbbbbbbbcccccccccccccccc';
    const production = {
        channel: 'production',
        extension_id: PRODUCTION_ID,
        host_name: 'me.nagaev.veles.bridge',
    };

    it('releaseIdentityRejectsPlaceholder', () => {
        // The repository placeholder is development-only and never a release identity.
        expect(DEVELOPMENT_EXTENSION_ID).toBe('abcdefghijklmnopabcdefghijklmnop');
        expect(VELES_EXTENSION_ID).toBe(DEVELOPMENT_EXTENSION_ID);

        const rejected: [string, unknown][] = [
            ['placeholder', { ...production, extension_id: DEVELOPMENT_EXTENSION_ID }],
            ['wildcard', { ...production, extension_id: '*' }],
            ['empty', { ...production, extension_id: '' }],
            ['missing', { channel: 'production', host_name: production.host_name }],
            ['non-string', { ...production, extension_id: 7 }],
            ['comma list', { ...production, extension_id: `${PRODUCTION_ID},${PRODUCTION_ID}` }],
            [
                'origin form',
                { ...production, extension_id: `chrome-extension://${PRODUCTION_ID}/` },
            ],
            ['uppercase', { ...production, extension_id: PRODUCTION_ID.toUpperCase() }],
            ['too short', { ...production, extension_id: PRODUCTION_ID.slice(1) }],
            ['out of alphabet', { ...production, extension_id: 'q'.repeat(32) }],
            ['development host name', { ...production, host_name: 'me.nagaev.veles.bridge.dev' }],
            ['unrelated host name', { ...production, host_name: 'com.example.host' }],
            ['development channel', { ...production, channel: 'development' }],
            ['fixture channel', { ...production, channel: 'fixture' }],
            ['unknown channel', { ...production, channel: 'staging' }],
            ['null config', null],
            ['array config', []],
        ];
        for (const [label, config] of rejected) {
            expect(
                () => resolveReleaseIdentity(config, { requireProduction: true }),
                `release identity must reject: ${label}`,
            ).toThrow();
        }
        // Without an explicit production requirement a production-labelled config is still
        // validated; only the unlabelled default may carry the placeholder.
        expect(() => resolveReleaseIdentity({ ...production, extension_id: '*' })).toThrow();
        expect(() =>
            resolveReleaseIdentity({ ...production, extension_id: DEVELOPMENT_EXTENSION_ID }),
        ).toThrow();

        expect(resolveReleaseIdentity(production, { requireProduction: true })).toEqual({
            channel: 'production',
            extensionId: PRODUCTION_ID,
            origin: `chrome-extension://${PRODUCTION_ID}/`,
            developmentOnly: false,
        });
    });

    it('keeps the unconfigured default explicitly development-only', () => {
        const identity = resolveReleaseIdentity(undefined);
        expect(identity).toEqual({
            channel: 'development',
            extensionId: DEVELOPMENT_EXTENSION_ID,
            origin: `chrome-extension://${DEVELOPMENT_EXTENSION_ID}/`,
            developmentOnly: true,
        });
        // A release build must refuse the default instead of shipping it.
        expect(() => resolveReleaseIdentity(undefined, { requireProduction: true })).toThrow();
    });

    it('accepts an explicit development config only on the development channel', () => {
        const dev = {
            channel: 'development',
            extension_id: 'hgfedcbahgfedcbahgfedcbahgfedcba',
            host_name: 'me.nagaev.veles.bridge.dev',
        };
        expect(resolveReleaseIdentity(dev)).toMatchObject({
            channel: 'development',
            developmentOnly: true,
        });
        expect(() => resolveReleaseIdentity(dev, { requireProduction: true })).toThrow();
        // A development channel may not claim the production host.
        expect(() =>
            resolveReleaseIdentity({ ...dev, host_name: 'me.nagaev.veles.bridge' }),
        ).toThrow();
    });

    it('does not let the identity change the strict manifest baseline', () => {
        // Injecting the identity must not widen permissions, CSP or host access.
        expect(JSON.stringify(buildExtensionManifest())).not.toContain('chrome-extension://');
        expect(buildExtensionManifest()).not.toHaveProperty('key');
        expect(buildExtensionManifest()).not.toHaveProperty('externally_connectable');
    });
});
