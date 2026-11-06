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
        // frozen tab transitions require Chrome 132 (offscreen requires 109).
        minimum_chrome_version: '132',
        name: 'Veles OTP',
        version: pkg.version,
        description:
            'Delivers one-time passcodes from the Veles Android app to this browser over an authenticated local channel.',
        permissions: ['storage', 'nativeMessaging', 'offscreen', 'clipboardWrite'],
        // No default_popup: chrome.action.onClicked must fire so the service
        // worker can focus or create the single connector tab.
        action: { default_title: 'Veles OTP' },
        options_ui: { page: 'options.html' },
        background: { service_worker: 'background.js', type: 'module' },
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
