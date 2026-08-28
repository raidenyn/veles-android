export const VELES_EXTENSION_ID: string;
export type HostPlatform = 'windows' | 'macos';
export interface HostManifest {
    name: string;
    description: string;
    path: string;
    type: 'stdio';
    allowed_origins: string[];
}
// macOS: omit installDir to emit the {{INSTALL_DIR}} installer template
// (used by the bridgeManifests task for development registration), or pass a
// concrete absolute install root (no '~') to emit a manifest Chrome can launch
// as-is (used by the packaging step). A non-absolute or '~'-containing
// installDir throws. Windows ignores installDir (path is always relative).
export function buildHostManifest(platform: HostPlatform, installDir?: string): HostManifest;
