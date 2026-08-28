// OTP-01 sub-project 1c — Chrome Native Messaging host manifest emitter.
//
// Emits the host manifest JSON for each supported platform into
// build/native-bridge/manifests/. The manifest contents are validated by
// test/manifest-guard.test.ts; this script is a thin file-writing wrapper.
//
// Output:
//   build/native-bridge/manifests/com.veles.native_bridge.json (windows)
//   build/native-bridge/manifests/app.veles.native_bridge.json (macos)
//
// Chrome's native-messaging convention installs the manifest at a
// platform-specific system path under the name "<host_name>.json".

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
        const outPath = join(OUT_DIR, filename);
        const json = JSON.stringify(manifest, null, 2) + '\n';
        writeFileSync(outPath, json);
        console.log(outPath);
    }
}

main();
