import pkg from '../package.json';

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
