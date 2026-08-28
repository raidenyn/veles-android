// OTP-01 sub-project 1c — Chrome Native Messaging host manifest emitter.
//
// Emits the host manifest JSON for each supported platform into
// build/native-bridge/manifests/<platform>/. The manifest contents are
// validated by test/manifest-guard.test.ts; this script is a thin
// file-writing wrapper.
//
// Output:
//   build/native-bridge/manifests/windows/app.veles.native_bridge.json
//   build/native-bridge/manifests/macos/app.veles.native_bridge.json
//
// Both platforms share the same `name` field, so each manifest is written
// into a platform-specific subdirectory to avoid the second write
// overwriting the first.

import { mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildHostManifest } from '../src/manifest.mjs';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const BRIDGE_DIR = resolve(__dirname, '..');
const OUT_DIR = resolve(BRIDGE_DIR, '..', 'build', 'native-bridge', 'manifests');

function main() {
    mkdirSync(OUT_DIR, { recursive: true });

    for (const platform of ['windows', 'macos']) {
        const manifest = buildHostManifest(platform);
        const filename = `${manifest.name}.json`;
        const platformDir = join(OUT_DIR, platform);
        mkdirSync(platformDir, { recursive: true });
        const outPath = join(platformDir, filename);
        const json = JSON.stringify(manifest, null, 2) + '\n';
        writeFileSync(outPath, json);
        console.log(outPath);
    }
}

main();
