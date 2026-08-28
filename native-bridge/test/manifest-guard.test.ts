import { describe, it, expect } from 'vitest';
import { buildHostManifest, VELES_EXTENSION_ID } from '../src/manifest';

// OTP-01 sub-project 1c — Chrome Native Messaging host manifest guard.
// The host manifest is registered with Chrome so the extension can launch
// the bridge via chrome.runtime.connectNative. Exact-match assertions so a
// wrong extension ID, a wrong path, or a weak type is caught at `npm test`.
describe('native-messaging host manifest (exact match)', () => {
    it('VELES_EXTENSION_ID is a 32-char lowercase [a-p] string', () => {
        expect(VELES_EXTENSION_ID).toMatch(/^[a-p]{32}$/);
    });

    it('buildHostManifest("windows") produces the exact Windows manifest', () => {
        expect(buildHostManifest('windows')).toEqual({
            name: 'app.veles.native_bridge',
            description: 'Veles Native Messaging host',
            path: 'veles-native-bridge.exe',
            type: 'stdio',
            allowed_origins: [`chrome-extension://${VELES_EXTENSION_ID}/`],
        });
    });

    it('buildHostManifest("macos") emits an installer template by default', () => {
        // Without an install directory the path is an installer template
        // containing {{INSTALL_DIR}}; Chrome requires an absolute path on
        // macOS, so this manifest cannot launch the host until substituted.
        expect(buildHostManifest('macos')).toEqual({
            name: 'app.veles.native_bridge',
            description: 'Veles Native Messaging host',
            path: '{{INSTALL_DIR}}/Veles Native Bridge.app/Contents/MacOS/veles-native-bridge',
            type: 'stdio',
            allowed_origins: [`chrome-extension://${VELES_EXTENSION_ID}/`],
        });
    });

    it('buildHostManifest("macos", installDir) produces an absolute path', () => {
        expect(buildHostManifest('macos', '/Applications/Veles')).toEqual({
            name: 'app.veles.native_bridge',
            description: 'Veles Native Messaging host',
            // The .app's Contents/MacOS/ binary is the Cargo [[bin]] name
            // ("veles-native-bridge"), NOT the Tauri productName. The manifest
            // must point at the real executable Chrome will launch.
            path: '/Applications/Veles/Veles Native Bridge.app/Contents/MacOS/veles-native-bridge',
            type: 'stdio',
            allowed_origins: [`chrome-extension://${VELES_EXTENSION_ID}/`],
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
