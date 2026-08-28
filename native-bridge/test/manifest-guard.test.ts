import { describe, it, expect } from 'vitest';
import { buildHostManifest, EXTENSION_ID_PLACEHOLDER } from '../src/manifest';

// OTP-01 sub-project 1c — Chrome Native Messaging host manifest guard.
// The host manifest is registered with Chrome so the extension can launch
// the bridge via chrome.runtime.connectNative. Exact-match assertions so a
// wrong extension ID, a wrong path, or a weak type is caught at `npm test`.
describe('native-messaging host manifest (exact match)', () => {
    it('EXTENSION_ID_PLACEHOLDER is a 32-char lowercase hex string', () => {
        expect(EXTENSION_ID_PLACEHOLDER).toMatch(/^[a-z0-9]{32}$/);
    });

    it('buildHostManifest("windows") produces the exact Windows manifest', () => {
        expect(buildHostManifest('windows')).toEqual({
            name: 'app.veles.native_bridge',
            description: 'Veles Native Messaging host',
            path: 'veles-native-bridge.exe',
            type: 'stdio',
            allowed_origins: [`chrome-extension://${EXTENSION_ID_PLACEHOLDER}/`],
        });
    });

    it('buildHostManifest("macos") produces the exact macOS manifest', () => {
        expect(buildHostManifest('macos')).toEqual({
            name: 'app.veles.native_bridge',
            description: 'Veles Native Messaging host',
            path: 'Veles Native Bridge.app/Contents/MacOS/Veles Native Bridge',
            type: 'stdio',
            allowed_origins: [`chrome-extension://${EXTENSION_ID_PLACEHOLDER}/`],
        });
    });

    it('buildHostManifest rejects unknown platforms', () => {
        expect(() => buildHostManifest('linux' as 'windows')).toThrow(/platform/);
    });

    it('manifest name uses underscore form of the bundle identifier', () => {
        expect(buildHostManifest('windows').name).toBe('app.veles.native_bridge');
        expect(buildHostManifest('macos').name).toBe('app.veles.native_bridge');
    });
});
