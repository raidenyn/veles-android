import { execFileSync } from 'node:child_process';

const UNTAGGED_VERSION = '0.0.1';
const DESCRIBE_PATTERN = /^web-extension-v(\d+(?:\.\d+){0,3})(?:-(\d+-g[0-9a-f]+))?$/;

export interface ExtensionVersion {
    version: string;
    versionName?: string;
}

/** Chrome rules: 1-4 dot-separated integers, each 0..65535, no leading zeros, not all zero. */
export function isChromeVersion(v: string): boolean {
    const parts = v.split('.');
    if (parts.length < 1 || parts.length > 4) return false;
    if (!parts.every((p) => /^(0|[1-9][0-9]*)$/.test(p) && Number(p) <= 65535)) return false;
    return parts.some((p) => p !== '0');
}

function checked(v: string, source: string): string {
    if (!isChromeVersion(v)) {
        throw new Error(`${source} is not a valid Chrome extension version: ${JSON.stringify(v)}`);
    }
    return v;
}

export function resolveExtensionVersion(
    env: Record<string, string | undefined>,
    describe: () => string | undefined,
): ExtensionVersion {
    const injected = env.VELES_EXTENSION_VERSION;
    if (injected !== undefined) {
        return { version: checked(injected, 'VELES_EXTENSION_VERSION') };
    }
    const described = describe();
    if (described === undefined) return { version: UNTAGGED_VERSION };
    const match = DESCRIBE_PATTERN.exec(described);
    if (!match) {
        throw new Error(`unrecognized git describe output: ${JSON.stringify(described)}`);
    }
    const version = checked(match[1], 'component tag');
    return match[2] === undefined
        ? { version }
        : { version, versionName: `${version}-${match[2]}` };
}

/** `git describe` for the nearest web-extension-v* tag; undefined when none or git fails. */
export function gitDescribe(): string | undefined {
    try {
        const out = execFileSync('git', ['describe', '--tags', '--match', 'web-extension-v*'], {
            encoding: 'utf8',
            stdio: ['ignore', 'pipe', 'ignore'],
        }).trim();
        return out === '' ? undefined : out;
    } catch {
        return undefined;
    }
}

/** Version for the current build; an empty injected value counts as unset. */
export function currentExtensionVersion(): ExtensionVersion {
    return resolveExtensionVersion(
        { VELES_EXTENSION_VERSION: process.env.VELES_EXTENSION_VERSION || undefined },
        gitDescribe,
    );
}
