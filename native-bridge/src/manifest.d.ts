export const VELES_EXTENSION_ID: string;
export type HostPlatform = 'windows' | 'macos';
export interface HostManifest {
    name: string;
    description: string;
    path: string;
    type: 'stdio';
    allowed_origins: string[];
}
export function buildHostManifest(platform: HostPlatform, installDir?: string): HostManifest;
