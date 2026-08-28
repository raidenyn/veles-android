// OTP-01 sub-project 1c — Chrome Native Messaging host manifest generator.
//
// The host manifest is a JSON file Chrome reads to discover how to launch the
// native bridge. Chrome's native-messaging spec requires:
//   - name:    lowercased identifier with dots/underscores, matching the file
//              name the manifest is installed as
//   - type:    "stdio"
//   - path:    absolute path to the executable, OR a relative name when the
//              manifest lives next to the binary (packaging-time)
//   - allowed_origins: chrome-extension://<extension-id>/ entries
//
// Chrome extension IDs are 32 lowercase characters from `a` through `p` (the
// base-16 alphabet used by Chrome's ID encoding). This canonical placeholder
// is shared verbatim with the web-extension manifest generator
// (web-extension/src/manifest.ts) so both projects authorize the same
// extension. Until 1a's published extension provides the real value, every
// consumer reads this constant.

export const VELES_EXTENSION_ID = 'abcdefghijklmnopabcdefghijklmnop';

const HOST_NAME = 'app.veles.native_bridge';
const HOST_DESCRIPTION = 'Veles Native Messaging host';
const MACOS_APP_RELATIVE = 'Veles Native Bridge.app/Contents/MacOS/Veles Native Bridge';
const INSTALL_DIR_PLACEHOLDER = '{{INSTALL_DIR}}';

export function buildHostManifest(platform, installDir = '') {
    const allowedOrigins = [`chrome-extension://${VELES_EXTENSION_ID}/`];
    switch (platform) {
        case 'windows':
            // Chrome permits a relative path on Windows when the manifest lives
            // next to the binary; keep it relative at packaging time.
            return {
                name: HOST_NAME,
                description: HOST_DESCRIPTION,
                path: 'veles-native-bridge.exe',
                type: 'stdio',
                allowed_origins: allowedOrigins,
            };
        case 'macos': {
            // Chrome requires an absolute path on macOS. Until an installer
            // substitutes the final install directory, emit an installer
            // template containing {{INSTALL_DIR}} so it is obvious the manifest
            // cannot launch the host as-is.
            const dir = installDir.length > 0 ? installDir : INSTALL_DIR_PLACEHOLDER;
            return {
                name: HOST_NAME,
                description: HOST_DESCRIPTION,
                path: `${dir}/${MACOS_APP_RELATIVE}`,
                type: 'stdio',
                allowed_origins: allowedOrigins,
            };
        }
        default:
            throw new Error(`Unknown native-bridge platform: ${platform}`);
    }
}
