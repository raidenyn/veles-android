import pkg from '../package.json';

export function buildExtensionManifest(): chrome.runtime.ManifestV3 {
    return {
        manifest_version: 3,
        name: 'Veles OTP',
        version: pkg.version,
        description:
            'Delivers one-time passcodes from the Veles Android app to this browser over an authenticated local channel.',
        permissions: [],
        background: { service_worker: 'background.js' },
        content_scripts: [
            {
                matches: ['https://*/*'],
                js: ['content.js'],
                run_at: 'document_idle',
            },
        ],
        content_security_policy: {
            extension_pages: "script-src 'self'; object-src 'self'",
        },
    };
}
