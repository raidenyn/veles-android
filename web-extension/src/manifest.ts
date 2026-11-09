import { currentExtensionVersion } from './version';

// Development-only Chrome extension ID placeholder shared with the native-bridge
// host manifest generator (native-bridge/src/manifest.mjs) and the host's
// compile-time default allowlist. Chrome extension IDs are 32 lowercase
// characters from `a` through `p`. This value is NOT a Web Store item ID: it is
// rejected by every release path (see `resolveReleaseIdentity`) and is the
// explicit default only for unreleased development builds.
export const DEVELOPMENT_EXTENSION_ID = 'abcdefghijklmnopabcdefghijklmnop';

// Backwards-compatible alias; still the development placeholder, never a release ID.
export const VELES_EXTENSION_ID = DEVELOPMENT_EXTENSION_ID;

const PRODUCTION_HOST_NAME = 'me.nagaev.veles.bridge';
const DEVELOPMENT_HOST_NAME = 'me.nagaev.veles.bridge.dev';
const EXTENSION_ID_PATTERN = /^[a-p]{32}$/;

export interface ReleaseIdentity {
    channel: 'production' | 'development';
    extensionId: string;
    /** The one literal `chrome-extension://<id>/` origin the host must allow. */
    origin: string;
    /** True for the unconfigured default and for any development-channel config. */
    developmentOnly: boolean;
}

export interface ResolveReleaseIdentityOptions {
    /** Release builds set this: only a validated production identity is accepted. */
    requireProduction?: boolean;
}

function identity(channel: ReleaseIdentity['channel'], extensionId: string): ReleaseIdentity {
    return {
        channel,
        extensionId,
        origin: `chrome-extension://${extensionId}/`,
        developmentOnly: channel !== 'production',
    };
}

/**
 * Validate the release identity the extension build consumes.
 *
 * `config` is the approved release configuration (the same `channel`,
 * `extension_id` and `host_name` fields the host packaging validates in the
 * installer's `ReleaseDescriptor`). With no config the explicit development
 * default is returned; a release build passes `requireProduction` and then
 * refuses the default, the placeholder, wildcards, lists, origin-shaped values,
 * a development/fixture channel and any host name other than the production one.
 */
export function resolveReleaseIdentity(
    config: unknown,
    options: ResolveReleaseIdentityOptions = {},
): ReleaseIdentity {
    if (config === undefined) {
        if (options.requireProduction === true) {
            throw new Error('release identity: a production release config is required');
        }
        return identity('development', DEVELOPMENT_EXTENSION_ID);
    }
    if (typeof config !== 'object' || config === null || Array.isArray(config)) {
        throw new Error('release identity: config must be an object');
    }
    const {
        channel,
        extension_id: extensionId,
        host_name: hostName,
    } = config as Record<string, unknown>;
    if (channel !== 'production' && channel !== 'development') {
        throw new Error('release identity: channel must be production or development');
    }
    if (options.requireProduction === true && channel !== 'production') {
        throw new Error('release identity: a production channel is required');
    }
    if (typeof extensionId !== 'string' || !EXTENSION_ID_PATTERN.test(extensionId)) {
        throw new Error('release identity: extension_id must be one 32-character a-p ID');
    }
    if (extensionId === DEVELOPMENT_EXTENSION_ID) {
        throw new Error('release identity: the development placeholder is not a release ID');
    }
    const expectedHost = channel === 'production' ? PRODUCTION_HOST_NAME : DEVELOPMENT_HOST_NAME;
    if (hostName !== undefined && hostName !== expectedHost) {
        throw new Error('release identity: host_name does not match the channel');
    }
    return identity(channel, extensionId);
}

export function buildExtensionManifest(): chrome.runtime.ManifestV3 {
    const { version, versionName } = currentExtensionVersion();
    return {
        manifest_version: 3,
        // frozen tab transitions require Chrome 132 (offscreen requires 109).
        minimum_chrome_version: '132',
        name: 'Veles OTP',
        version,
        ...(versionName === undefined ? {} : { version_name: versionName }),
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
