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
// The extension ID is a placeholder until 1a's published extension provides
// the real value. Until then, every consumer reads this constant.

export const EXTENSION_ID_PLACEHOLDER = 'velesotpplaceholderextension0001';

const HOST_NAME = 'app.veles.native_bridge';
const HOST_DESCRIPTION = 'Veles Native Messaging host';

export function buildHostManifest(platform) {
    const allowedOrigins = [`chrome-extension://${EXTENSION_ID_PLACEHOLDER}/`];
    switch (platform) {
        case 'windows':
            return {
                name: HOST_NAME,
                description: HOST_DESCRIPTION,
                path: 'veles-native-bridge.exe',
                type: 'stdio',
                allowed_origins: allowedOrigins,
            };
        case 'macos':
            return {
                name: HOST_NAME,
                description: HOST_DESCRIPTION,
                path: 'Veles Native Bridge.app/Contents/MacOS/Veles Native Bridge',
                type: 'stdio',
                allowed_origins: allowedOrigins,
            };
        default:
            throw new Error(`Unknown native-bridge platform: ${platform}`);
    }
}
