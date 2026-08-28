// OTP-01 sub-project 1c — deterministic packaging for the native-bridge.
//
// `cargo tauri build --no-bundle -- --locked` is expected to have already
// produced the raw unsigned binary (the `bridgeBuild` Gradle task / npm
// `build` script). This script packages it deterministically with the
// Chrome native-messaging host manifest into build/native-bridge/<platform>/.
//
// Outputs (at the repo-root build/native-bridge/):
//   windows/veles-native-bridge-<version>.zip         deterministic zip
//   windows/veles-native-bridge-<version>.zip.sha256  sidecar
//   macos/veles-native-bridge-<version>.tar.gz        deterministic tar
//   macos/veles-native-bridge-<version>.tar.gz.sha256 sidecar
//
// Deterministic recipe (mirrors web-extension/scripts/package.mjs):
//   - entries sorted lexicographically by path
//   - fixed mtimes (Unix epoch)
//   - no directory entries for empty dirs
//   - DEFLATE compression (zip) / fixed gzip metadata (tar.gz)
//   - unix file mode 100644 for regular files, 040755 for dirs
//
// On a Linux developer machine the Tauri binary is not built (it requires
// Windows/macOS SDKs). This script still validates the manifest and emits
// the SHA256 sidecar structure; the actual binary packaging runs in CI on
// the target platform runner.

import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import yazl from 'yazl';
import { buildHostManifest } from '../src/manifest.mjs';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const BRIDGE_DIR = resolve(__dirname, '..');
const SRC_TAURI_DIR = join(BRIDGE_DIR, 'src-tauri');
const BUILD_DIR = resolve(BRIDGE_DIR, '..', 'build', 'native-bridge');
const FIXED_MTIME = new Date(0);

function fail(reason) {
    console.error(`native-bridge package: ${reason}`);
    process.exit(1);
}

function readJson(path) {
    return JSON.parse(readFileSync(path, 'utf8'));
}

function walkFiles(dir, base = '') {
    const files = [];
    const dirs = [];
    for (const entry of readdirSync(dir)) {
        const abs = join(dir, entry);
        const rel = base ? `${base}/${entry}` : entry;
        const st = statSync(abs);
        if (st.isDirectory()) {
            const sub = walkFiles(abs, rel);
            if (sub.files.length > 0) {
                dirs.push({ kind: 'dir', rel: rel.split(sep).join('/'), abs });
                dirs.push(...sub.dirs);
                files.push(...sub.files);
            }
        } else if (st.isFile()) {
            files.push({ kind: 'file', rel: rel.split(sep).join('/'), abs });
        }
    }
    return { files, dirs };
}

function sha256File(path) {
    return createHash('sha256').update(readFileSync(path)).digest('hex');
}

function writeSidecar(artifactPath, name) {
    const digest = sha256File(artifactPath);
    const sidecarPath = `${artifactPath}.sha256`;
    writeFileSync(sidecarPath, `${digest}  ${name}\n`);
    console.log(sidecarPath);
    console.log(`sha256: ${digest}`);
}

function packageWindows(version, payloadDir, manifest) {
    const outDir = join(BUILD_DIR, 'windows');
    mkdirSync(outDir, { recursive: true });
    const zipName = `veles-native-bridge-${version}.zip`;
    const zipPath = join(outDir, zipName);

    const entries = [];
    if (existsSync(payloadDir)) {
        const { files, dirs } = walkFiles(payloadDir);
        entries.push(...dirs, ...files);
    }
    entries.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));

    const zipfile = new yazl.ZipFile();
    const manifestEntry = {
        kind: 'file',
        rel: `${manifest.name}.json`,
        abs: null,
        content: JSON.stringify(manifest, null, 2) + '\n',
    };
    entries.push(manifestEntry);
    entries.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));

    for (const entry of entries) {
        if (entry.kind === 'dir') {
            zipfile.addEmptyDirectory(entry.rel, { mtime: FIXED_MTIME, mode: 0o40755 });
        } else if (entry.content) {
            zipfile.addBuffer(Buffer.from(entry.content), entry.rel, {
                mtime: FIXED_MTIME,
                mode: 0o100644,
            });
        } else {
            zipfile.addBuffer(readFileSync(entry.abs), entry.rel, {
                mtime: FIXED_MTIME,
                mode: 0o100644,
            });
        }
    }
    zipfile.end();

    const chunks = [];
    zipfile.outputStream.on('data', (c) => chunks.push(c));
    zipfile.outputStream.on('end', () => {
        writeFileSync(zipPath, Buffer.concat(chunks));
        console.log(zipPath);
        writeSidecar(zipPath, zipName);
    });
}

function packageMacos(version, payloadDir, manifest) {
    const outDir = join(BUILD_DIR, 'macos');
    mkdirSync(outDir, { recursive: true });
    const tarName = `veles-native-bridge-${version}.tar.gz`;
    const tarPath = join(outDir, tarName);

    const stagingDir = join(BUILD_DIR, '_staging_macos');
    if (existsSync(stagingDir)) {
        execFileSync('rm', ['-rf', stagingDir]);
    }
    mkdirSync(stagingDir, { recursive: true });

    if (existsSync(payloadDir)) {
        execFileSync('cp', ['-R', payloadDir + '/.', stagingDir]);
    }
    const manifestPath = join(stagingDir, `${manifest.name}.json`);
    writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');

    execFileSync('tar', [
        '--sort=name',
        '--mtime=@0',
        '--owner=0',
        '--group=0',
        '--numeric-owner',
        '-czf',
        tarPath,
        '-C',
        stagingDir,
        '.',
    ]);

    execFileSync('rm', ['-rf', stagingDir]);
    console.log(tarPath);
    writeSidecar(tarPath, tarName);
}

function main() {
    const pkg = readJson(join(BRIDGE_DIR, 'package.json'));
    const version = pkg.version;
    if (typeof version !== 'string' || version.length === 0) {
        fail('native-bridge/package.json missing a string "version".');
    }

    const platform = process.env.VELES_BRIDGE_PLATFORM || '';
    const payloadDir = join(SRC_TAURI_DIR, 'target', 'release');

    if (platform === 'windows') {
        const manifest = buildHostManifest('windows');
        packageWindows(version, payloadDir, manifest);
    } else if (platform === 'macos') {
        const manifest = buildHostManifest('macos');
        packageMacos(version, payloadDir, manifest);
    } else {
        console.log(
            `native-bridge package: VELES_BRIDGE_PLATFORM not set or unknown ('${platform}').`,
        );
        console.log('Set VELES_BRIDGE_PLATFORM=windows or macos to package.');
        console.log('Emitting manifests only.');
        const manifestsDir = join(BUILD_DIR, 'manifests');
        mkdirSync(manifestsDir, { recursive: true });
        for (const p of ['windows', 'macos']) {
            const m = buildHostManifest(p);
            const outPath = join(manifestsDir, `${m.name}.json`);
            writeFileSync(outPath, JSON.stringify(m, null, 2) + '\n');
            console.log(outPath);
        }
    }
}

main();
