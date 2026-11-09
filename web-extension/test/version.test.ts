import { describe, expect, it } from 'vitest';
import { isChromeVersion, resolveExtensionVersion } from '../src/version';

describe('resolveExtensionVersion', () => {
    it('prefers the injected value', () => {
        expect(
            resolveExtensionVersion({ VELES_EXTENSION_VERSION: '0.4.0' }, () => undefined),
        ).toEqual({ version: '0.4.0' });
    });

    it('derives version and version_name from a post-tag describe', () => {
        expect(resolveExtensionVersion({}, () => 'web-extension-v0.2.0-3-gabc1234')).toEqual({
            version: '0.2.0',
            versionName: '0.2.0-3-gabc1234',
        });
    });

    it('uses an exact tag as the plain version', () => {
        expect(resolveExtensionVersion({}, () => 'web-extension-v0.2.0')).toEqual({
            version: '0.2.0',
        });
    });

    it('falls back to 0.0.1 when untagged', () => {
        expect(resolveExtensionVersion({}, () => undefined)).toEqual({ version: '0.0.1' });
    });

    it('rejects values that are not valid Chrome versions', () => {
        for (const bad of ['0.0.0', '01.2.3', '1.2.x', '1.65536.0', '1.2.3.4.5', '']) {
            expect(() =>
                resolveExtensionVersion({ VELES_EXTENSION_VERSION: bad }, () => undefined),
            ).toThrow();
        }
    });
});

describe('isChromeVersion', () => {
    it('accepts the Chrome maximum', () => {
        expect(isChromeVersion('65535.0.1')).toBe(true);
    });
});
