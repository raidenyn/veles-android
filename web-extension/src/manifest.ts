import pkg from '../package.json';

// Canonical Chrome extension ID placeholder shared with the native-bridge
// host manifest generator (native-bridge/src/manifest.mjs). Chrome extension
// IDs are 32 lowercase characters from `a` through `p`. Both projects must
// use this exact value so the host authorizes this extension. Until the
// published extension provides the real ID, every consumer reads this
// constant.
export const VELES_EXTENSION_ID = 'abcdefghijklmnopabcdefghijklmnop';

export function buildExtensionManifest(): chrome.runtime.ManifestV3 {
    return {
        manifest_version: 3,
        name: 'Veles OTP',
        version: pkg.version,
        description:
            'Delivers one-time passcodes from the Veles Android app to this browser over an authenticated local channel.',
        permissions: [],
        action: { default_popup: 'popup.html' },
        options_ui: { page: 'options.html' },
        background: { service_worker: 'background.js' },
        content_scripts: [
            {
                matches: ['https://*/*'],
                js: ['content.js'],
                run_at: 'document_idle',
            },
        ],
        content_security_policy: {
            extension_pages: "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'",
        },
    };
}
