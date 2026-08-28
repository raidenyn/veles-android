// OTP-01 sub-project 1c — macOS packaged manifest must carry a concrete
// absolute host path (Chrome requires absolute paths on macOS; `~` is not
// expanded by Chrome). These tests pin the install-path contract that the
// packaging script relies on: a supplied installDir must be absolute and
// must not contain a literal `~`, otherwise buildHostManifest throws so a
// broken manifest can never be packaged.
//
// The template form (no installDir) is still emitted by the bridgeManifests
// task for development registration and is covered by manifest-guard.test.ts.
import { describe, expect, it } from 'vitest';
import { buildHostManifest, VELES_EXTENSION_ID } from '../src/manifest';

const EXPECTED_NAME = 'app.veles.native_bridge';
// The .app bundle name comes from the Tauri productName, but the binary inside
// Contents/MacOS/ is the Cargo [[bin]] name (veles-native-bridge), NOT the
// productName. The manifest must point at the actual executable.
const APP_BUNDLE = 'Veles Native Bridge.app';
const EXEC_NAME = 'veles-native-bridge';
const APP_RELATIVE = `${APP_BUNDLE}/Contents/MacOS/${EXEC_NAME}`;

describe('macOS packaged manifest install path (absolute, no tilde)', () => {
    it('buildHostManifest("macos", "/Applications/Veles/NativeBridge") yields a concrete absolute path', () => {
        const m = buildHostManifest('macos', '/Applications/Veles/NativeBridge');
        expect(m).toEqual({
            name: EXPECTED_NAME,
            description: 'Veles Native Messaging host',
            path: `/Applications/Veles/NativeBridge/${APP_RELATIVE}`,
            type: 'stdio',
            allowed_origins: [`chrome-extension://${VELES_EXTENSION_ID}/`],
        });
    });

    it('rejects an installDir containing a literal ~ (Chrome does not expand it)', () => {
        expect(() =>
            buildHostManifest('macos', '~/Library/Application Support/Veles/NativeBridge'),
        ).toThrow(/absolute|~/i);
    });

    it('rejects a relative installDir', () => {
        expect(() => buildHostManifest('macos', 'relative/path')).toThrow(/absolute/i);
    });

    it('rejects an empty-string installDir (falls back would be a template, not absolute)', () => {
        // A packaged manifest must be absolute, so an empty installDir is a
        // programmer error in the packaging path. The no-arg template form
        // remains available for bridgeManifests via buildHostManifest("macos").
        expect(() => buildHostManifest('macos', '')).toThrow(/absolute|install/i);
    });

    it('Windows manifest path stays relative (Chrome permits relative on Windows)', () => {
        const m = buildHostManifest('windows');
        expect(m.path).toBe('veles-native-bridge.exe');
        expect(m.path).not.toMatch(/^\//);
    });
});
