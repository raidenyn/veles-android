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
// The host platform to package is supplied via the VELES_BRIDGE_PLATFORM env
// var (set by the `bridgePackage` Gradle task). This script only packages the
// already-built payload for that platform — it never emits manifests as a
// fallback (that is the `bridgeManifests` task's job). A missing or
// unsupported platform, or a missing payload, is a hard error.

import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import yazl from 'yazl';
import { buildHostManifest } from '../src/manifest.mjs';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const BRIDGE_DIR = resolve(__dirname, '..');
const SRC_TAURI_DIR = join(BRIDGE_DIR, 'src-tauri');
const RELEASE_DIR = join(SRC_TAURI_DIR, 'target', 'release');
const BUILD_DIR = resolve(BRIDGE_DIR, '..', 'build', 'native-bridge');
const FIXED_MTIME = new Date(0);

const SUPPORTED_PLATFORMS = ['windows', 'macos'];

// Tauri productName (from src-tauri/tauri.conf.json) drives the emitted .app
// bundle name on macOS. Keep this in sync with that file.
const MACOS_APP_BUNDLE = 'Veles Native Bridge.app';
const WINDOWS_BINARY = 'veles-native-bridge.exe';

function fail(reason) {
    console.error(`native-bridge package: ${reason}`);
    process.exit(1);
}

function readJson(path) {
    return JSON.parse(readFileSync(path, 'utf8'));
}

// Walk a directory tree, collecting non-empty directory entries and regular
// files. Paths are returned with forward slashes regardless of host OS. This
// is used to enumerate the macOS .app bundle payload only — the entire
// `target/release` tree is never archived.
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

function sortByRel(entries) {
    entries.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));
}

function packageWindows(version, manifest) {
    const binaryPath = join(RELEASE_DIR, WINDOWS_BINARY);
    if (!existsSync(binaryPath)) {
        fail(
            `Windows payload not found. Expected the built binary at:\n  ${binaryPath}\n` +
                `Run \`cargo tauri build --no-bundle -- --locked\` (bridgeBuild) first.`,
        );
    }

    const outDir = join(BUILD_DIR, 'windows');
    mkdirSync(outDir, { recursive: true });
    const zipName = `veles-native-bridge-${version}.zip`;
    const zipPath = join(outDir, zipName);

    // Allow-list: only the binary plus the host manifest JSON.
    const entries = [
        { kind: 'file', rel: WINDOWS_BINARY, abs: binaryPath },
        {
            kind: 'file',
            rel: `${manifest.name}.json`,
            abs: null,
            content: JSON.stringify(manifest, null, 2) + '\n',
        },
    ];
    sortByRel(entries);

    const zipfile = new yazl.ZipFile();
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

// Build a deterministic USTAR tar archive entirely in pure JS so the macOS
// packaging step does not depend on GNU tar flags (the macOS runner ships
// bsdtar, which rejects --sort=name / --mtime=@0). Every entry is normalized:
//   - sorted lexicographically by path
//   - mtime fixed at Unix epoch (0)
//   - uid/gid fixed at 0, uname/gname empty
//   - mode 0644 for regular files, 0755 for directories
// The archive is terminated by two 512-byte zero blocks (end-of-archive).
function createTar(entries) {
    const blocks = [];
    for (const entry of entries) {
        const isDir = entry.kind === 'dir';
        const data = isDir ? Buffer.alloc(0) : readFileSync(entry.abs);
        const header = Buffer.alloc(512, 0);

        const name = Buffer.from(entry.rel, 'utf8');
        if (name.length > 100) {
            throw new Error(`tar entry name too long (>100 bytes): ${entry.rel}`);
        }
        name.copy(header, 0);

        // mode: 8 bytes, octal, NUL-terminated.
        header.write(isDir ? '0000755\0' : '0000644\0', 100, 'ascii');
        // uid / gid: 8 bytes each, octal, NUL-terminated.
        header.write('0000000\0', 108, 'ascii');
        header.write('0000000\0', 116, 'ascii');
        // size: 12 bytes, octal, NUL-terminated.
        header.write(data.length.toString(8).padStart(11, '0') + '\0', 124, 'ascii');
        // mtime: 12 bytes, octal, NUL-terminated — fixed at epoch 0.
        header.write('00000000000\0', 136, 'ascii');
        // typeflag: '0' regular file, '5' directory.
        header.write(isDir ? '5' : '0', 156, 'ascii');
        // magic "ustar\0" + version "00".
        header.write('ustar\0', 257, 'ascii');
        header.write('00', 263, 'ascii');
        // uname/gname left zeroed (offset 265 / 297).
        // devmajor/devminor/prefix left zeroed (329 / 337 / 345).

        // checksum: sum of all header bytes with the 8-byte checksum field
        // (offset 148..156) treated as ASCII spaces (0x20). The field is then
        // written as 6 octal digits, a NUL, and a space.
        let checksum = 0;
        for (let i = 0; i < 512; i++) {
            checksum += i >= 148 && i < 156 ? 0x20 : header[i];
        }
        header.write(checksum.toString(8).padStart(6, '0') + '\0 ', 148, 'ascii');

        blocks.push(header);
        if (!isDir) {
            // File data padded to a 512-byte boundary with zero bytes.
            const pad = (512 - (data.length % 512)) % 512;
            if (pad === 0) {
                blocks.push(data);
            } else {
                blocks.push(data, Buffer.alloc(pad, 0));
            }
        }
    }
    // End-of-archive: two 512-byte zero blocks.
    blocks.push(Buffer.alloc(512, 0), Buffer.alloc(512, 0));
    return Buffer.concat(blocks);
}

function packageMacos(version, manifest) {
    const bundlePath = join(RELEASE_DIR, MACOS_APP_BUNDLE);
    if (!existsSync(bundlePath) || !statSync(bundlePath).isDirectory()) {
        fail(
            `macOS payload not found. Expected the built .app bundle at:\n  ${bundlePath}\n` +
                `Run \`cargo tauri build --no-bundle -- --locked\` (bridgeBuild) first.`,
        );
    }

    const outDir = join(BUILD_DIR, 'macos');
    mkdirSync(outDir, { recursive: true });
    const tarName = `veles-native-bridge-${version}.tar.gz`;
    const tarPath = join(outDir, tarName);

    // Allow-list: only files within the .app bundle, plus the host manifest
    // JSON at the archive root. The bundle is archived under its own name so
    // extraction reproduces "Veles Native Bridge.app/...".
    const { files, dirs } = walkFiles(bundlePath, MACOS_APP_BUNDLE);
    const entries = [];
    for (const d of dirs) {
        entries.push({ kind: 'dir', rel: d.rel });
    }
    for (const f of files) {
        entries.push({ kind: 'file', rel: f.rel, abs: f.abs });
    }
    entries.push({
        kind: 'file',
        rel: `${manifest.name}.json`,
        abs: null,
        content: JSON.stringify(manifest, null, 2) + '\n',
    });
    sortByRel(entries);

    const tarBuffer = createTar(entries);
    // gzip with fixed mtime (0) header metadata for determinism.
    const gzBuffer = gzipSync(tarBuffer, { level: 9, mtime: 0 });
    writeFileSync(tarPath, gzBuffer);
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
    if (!SUPPORTED_PLATFORMS.includes(platform)) {
        fail(
            `VELES_BRIDGE_PLATFORM is not set or unsupported ('${platform}').\n` +
                `Set VELES_BRIDGE_PLATFORM=windows or macos. ` +
                `Manifests are emitted separately by the bridgeManifests task.`,
        );
    }

    if (platform === 'windows') {
        const manifest = buildHostManifest('windows');
        packageWindows(version, manifest);
    } else {
        const manifest = buildHostManifest('macos');
        packageMacos(version, manifest);
    }
}

main();
