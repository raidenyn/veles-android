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
// The .app bundle directory is named after the Tauri productName
// ("Veles Native Bridge"), but the executable inside Contents/MacOS/ is the
// Cargo [[bin]] name ("veles-native-bridge" — see src-tauri/Cargo.toml), NOT
// the productName. The manifest's `path` must point at the real binary Chrome
// will launch; the productName does not exist on disk under Contents/MacOS/.
const MACOS_APP_BUNDLE = 'Veles Native Bridge.app';
const MACOS_BIN_NAME = 'veles-native-bridge';
const MACOS_APP_RELATIVE = `${MACOS_APP_BUNDLE}/Contents/MacOS/${MACOS_BIN_NAME}`;
const INSTALL_DIR_PLACEHOLDER = '{{INSTALL_DIR}}';

// A macOS packaged manifest must carry a concrete absolute path: Chrome
// requires absolute paths on macOS and does not expand `~`. The packaging
// step therefore resolves a concrete install root (VELES_BRIDGE_INSTALL_ROOT)
// and passes it here. A `~`-containing or relative installDir is rejected so a
// manifest that could never launch the host can never be packaged.
//
// The no-argument form (installDir omitted) keeps the {{INSTALL_DIR}} installer
// template: that is emitted only by the bridgeManifests task for development
// registration, where an installer substitutes the final path at install time.
function assertAbsoluteInstallDir(installDir) {
    if (typeof installDir !== 'string' || installDir.length === 0) {
        throw new Error(
            'native-bridge macOS manifest requires a concrete install directory ' +
                '(received none). Pass the resolved install root, not a placeholder.',
        );
    }
    if (installDir.includes('~')) {
        throw new Error(
            `native-bridge macOS manifest install directory must be absolute; Chrome ` +
                `does not expand '~' (received: ${installDir}).`,
        );
    }
    // A path is absolute if it starts with '/' (POSIX). Windows-style drive
    // paths are not relevant for the macOS branch.
    if (!installDir.startsWith('/')) {
        throw new Error(
            `native-bridge macOS manifest install directory must be absolute ` +
                `(received: ${installDir}).`,
        );
    }
}

export function buildHostManifest(platform, installDir) {
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
            // Two forms:
            //   - Template (installDir === undefined): the bridgeManifests task
            //     emits {{INSTALL_DIR}} for development registration; an
            //     installer substitutes the final absolute path at install time.
            //   - Concrete (installDir provided): the packaging step resolves a
            //     concrete absolute install root and validates it is absolute and
            //     free of '~', producing a manifest Chrome can launch as-is.
            if (installDir === undefined) {
                return {
                    name: HOST_NAME,
                    description: HOST_DESCRIPTION,
                    path: `${INSTALL_DIR_PLACEHOLDER}/${MACOS_APP_RELATIVE}`,
                    type: 'stdio',
                    allowed_origins: allowedOrigins,
                };
            }
            assertAbsoluteInstallDir(installDir);
            return {
                name: HOST_NAME,
                description: HOST_DESCRIPTION,
                path: `${installDir}/${MACOS_APP_RELATIVE}`,
                type: 'stdio',
                allowed_origins: allowedOrigins,
            };
        }
        default:
            throw new Error(`Unknown native-bridge platform: ${platform}`);
    }
}
