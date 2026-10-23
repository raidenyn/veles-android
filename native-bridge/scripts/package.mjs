// OTP-01 sub-project 1c — deterministic packaging for the native-bridge.
//
// `cargo tauri build` (the bridgeBuild/bundle Gradle tasks) is expected to have
// already produced the platform bundle output under
// `src-tauri/target/release/bundle/<platform>/`. This script packages that
// bundle output plus the Chrome native-messaging host manifest
// deterministically into build/native-bridge/<platform>/.
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
//   - no directory entries for empty dirs in zip; tar keeps directory entries
//   - DEFLATE compression (zip) / fixed gzip metadata (tar.gz)
//   - unix file mode preserved from disk (lstat, not stat): 0755 executables,
//     0644 regular files, 0755 directories; symlinks archived as symlinks
//
// The host platform to package is supplied via the VELES_BRIDGE_PLATFORM env
// var (set by the `bridgePackage` Gradle task). This script only packages the
// already-built bundle payload for that platform — it never emits manifests as
// a fallback (that is the `bridgeManifests` task's job). A missing or
// unsupported platform, or a missing bundle payload, is a hard error.
//
// Testability hooks (env overrides, unset in production):
//   VELES_BRIDGE_RELEASE_DIR    override src-tauri/target/release (bundle root)
//   VELES_BRIDGE_BUILD_OUT_DIR  override build/native-bridge/ output root
//   VELES_BRIDGE_INSTALL_ROOT   macOS install root (default /Applications —
//                               matches the .app layout the .dmg installs to)
//                               — must be absolute; '~' is rejected because
//                               Chrome does not expand it. Never derived from
//                               the build machine's $HOME.

import { createHash } from 'node:crypto';
import {
    lstatSync,
    readFileSync,
    readdirSync,
    mkdirSync,
    rmSync,
    writeFileSync,
    existsSync,
    readlinkSync,
} from 'node:fs';
import { join, resolve, sep, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import yazl from 'yazl';
import { buildHostManifest } from '../src/manifest.mjs';
import { createStandardManifest } from '../../verify/lib/checksum-manifest.mjs';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const BRIDGE_DIR = resolve(__dirname, '..');
const SRC_TAURI_DIR = join(BRIDGE_DIR, 'src-tauri');
const DEFAULT_RELEASE_DIR = join(SRC_TAURI_DIR, 'target', 'release');
const DEFAULT_BUILD_DIR = resolve(BRIDGE_DIR, '..', 'build', 'native-bridge');
const FIXED_MTIME = new Date(0);

const SUPPORTED_PLATFORMS = ['windows', 'macos'];

// Tauri productName (from src-tauri/tauri.conf.json) drives the emitted .app
// bundle name on macOS. Keep this in sync with that file.
const MACOS_APP_BUNDLE = 'Veles Native Bridge.app';
const WINDOWS_BINARY = 'veles-native-bridge.exe';

// The documented per-user macOS install destination for the packaged native
// bridge. This is the absolute installation root the distributable manifest
// points at by DEFAULT (when no VELES_BRIDGE_INSTALL_ROOT override is given).
//
// It MUST NOT be derived from the build machine's $HOME: a distributable
// archive's manifest is baked into the .tar.gz at packaging time and shipped
// to other machines, so any $HOME-derived path would be wrong on the target
// and would leak the builder's home directory. Chrome requires an absolute
// path on macOS and does not expand '~'.
//
// /Applications matches the package layout: the .app bundle is archived at
// the archive root, and the macOS installer (the .dmg produced by the bundle
// build) conventionally copies the .app into /Applications. A CI/system-wide
// producer may override this via VELES_BRIDGE_INSTALL_ROOT.
const DEFAULT_MACOS_INSTALL_ROOT = '/Applications';

function fail(reason) {
    console.error(`native-bridge package: ${reason}`);
    process.exit(1);
}

function readJson(path) {
    return JSON.parse(readFileSync(path, 'utf8'));
}

function resolveReleaseDir() {
    const override = process.env.VELES_BRIDGE_RELEASE_DIR;
    return override && override.length > 0 ? resolve(override) : DEFAULT_RELEASE_DIR;
}

function resolveBuildDir() {
    const override = process.env.VELES_BRIDGE_BUILD_OUT_DIR;
    return override && override.length > 0 ? resolve(override) : DEFAULT_BUILD_DIR;
}

function resolveMacosInstallRoot() {
    const override = process.env.VELES_BRIDGE_INSTALL_ROOT;
    const root = override && override.length > 0 ? override : DEFAULT_MACOS_INSTALL_ROOT;
    if (root.includes('~')) {
        fail(`VELES_BRIDGE_INSTALL_ROOT must not contain '~' (Chrome does not expand it): ${root}`);
    }
    if (!isAbsolute(root)) {
        fail(`VELES_BRIDGE_INSTALL_ROOT must be an absolute path (received: ${root}).`);
    }
    return root;
}

// Walk a directory tree collecting entries for archiving. Uses lstat so
// symlinks are NOT followed (preserving .app/Frameworks symlinks). Paths are
// returned with forward slashes regardless of host OS.
//
// Returned entry shape:
//   { kind: 'dir'|'file'|'symlink', rel, abs, mode }
// Empty directories ARE included (the macOS tar preserves them); the zip
// step skips directory entries for determinism per the web-extension recipe.
function walkBundle(dir, base = '') {
    const entries = [];
    for (const entry of readdirSync(dir)) {
        const abs = join(dir, entry);
        const rel = base ? `${base}/${entry}` : entry;
        const st = lstatSync(abs);
        const relPosix = rel.split(sep).join('/');
        if (st.isSymbolicLink()) {
            entries.push({ kind: 'symlink', rel: relPosix, abs, mode: st.mode });
        } else if (st.isDirectory()) {
            entries.push({ kind: 'dir', rel: relPosix, abs, mode: st.mode });
            entries.push(...walkBundle(abs, rel));
        } else if (st.isFile()) {
            entries.push({ kind: 'file', rel: relPosix, abs, mode: st.mode });
        }
    }
    return entries;
}

// Read a symlink target relative to its own location (for the tar linkname).
// readlinkSync does NOT follow the link; readFileSync would dereference it.
function readSymlinkTarget(abs) {
    return readlinkSync(abs, 'utf8');
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

async function writeChecksumManifest(outputDir, artifactName) {
    const sidecarName = `${artifactName}.sha256`;
    const sumsPath = join(outputDir, 'SHA256SUMS');
    writeFileSync(sumsPath, await createStandardManifest(outputDir, [artifactName, sidecarName]));
    console.log(sumsPath);
}

function sortByRel(entries) {
    entries.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));
}

// Normalize a unix mode to the archive form: keep the low 9 permission bits,
// pin to a deterministic type. Executables (any execute bit set) keep 0755;
// regular files without execute get 0644; directories are 0755.
function normalizedMode(entry) {
    if (entry.kind === 'dir') return 0o755;
    const exec = (entry.mode & 0o111) !== 0;
    return exec ? 0o755 : 0o644;
}

// Find exactly one Tauri-emitted installer artifact in `dir` whose name ends
// with `suffix`. The Windows bundle build emits exactly one NSIS setup .exe
// (bundle/nsis/<product>_<version>_<arch>-setup.exe) and one WiX .msi
// (bundle/msi/<product>_<version>_<arch>_en-US.msi). We require exactly one
// such file AND that no other (unexpected/stale) files live alongside it, and
// that it is nonempty — so a leftover from a prior build or a broken/empty
// installer is never silently archived. Returns the bare file name.
function requireSingleInstaller(dir, kind, suffix, label) {
    if (!existsSync(dir) || !lstatSync(dir).isDirectory()) {
        fail(
            `${label} installer input not found. Expected Tauri bundle output at:\n` +
                `  ${dir}\n` +
                'Run bridgeBundle with bundle targets enabled first.',
        );
    }
    const all = readdirSync(dir);
    const matches = all.filter((n) => n.endsWith(suffix));
    if (matches.length === 0) {
        fail(
            `${label} installer not found under ${dir} (no file ending in ` +
                `"${suffix}"). Run bridgeBundle with the ` +
                `${kind} bundle target enabled first.`,
        );
    }
    if (matches.length > 1) {
        fail(
            `Multiple ${label} installers found under ${dir}; expected exactly one:\n` +
                matches.map((n) => `  ${n}`).join('\n') +
                `\nRemove stale installers before packaging.`,
        );
    }
    // Tauri's macOS DMG bundler emits these implementation details beside the
    // generated disk image. They are not archive payload; permit only their
    // exact names while retaining the strict installer allow-list for all
    // other files and for Windows bundle directories.
    const unexpected = all.filter(
        (n) =>
            !n.endsWith(suffix) &&
            !(kind === 'dmg' && (n === 'bundle_dmg.sh' || n === 'Veles Native Bridge.icns')),
    );
    if (unexpected.length > 0) {
        fail(
            `Unexpected files found alongside the ${label} installer in ${dir}:\n` +
                unexpected.map((n) => `  ${n}`).join('\n') +
                `\nOnly the Tauri-emitted ${label} (${suffix}) is allow-listed; ` +
                `remove stale/stray files before packaging.`,
        );
    }
    const name = matches[0];
    const abs = join(dir, name);
    if (readFileSync(abs).length === 0) {
        fail(
            `${label} installer is empty: ${abs}\n` +
                'A broken/empty installer is never archived. Re-run bridgeBundle.',
        );
    }
    return name;
}

async function packageWindows(version, manifest) {
    const releaseDir = resolveReleaseDir();
    const bundleRoot = join(releaseDir, 'bundle');
    const nsisDir = join(bundleRoot, 'nsis');
    const msiDir = join(bundleRoot, 'msi');
    const binaryPath = join(releaseDir, WINDOWS_BINARY);

    if (!existsSync(binaryPath)) {
        fail(
            `Windows payload not found. Expected the built binary at:\n  ${binaryPath}\n` +
                `Run the Tauri bundle build (bridgeBuild) first.`,
        );
    }
    // The Windows bundle build requests BOTH nsis and msi targets (see
    // bridgeBundleTargetsFor in build.gradle.kts), so both installer artifacts
    // must be present. Require exactly one nonempty Tauri-emitted installer in
    // each directory and reject any unexpected/stale files alongside them so a
    // leftover from a prior build is never silently archived.
    const nsisExe = requireSingleInstaller(nsisDir, 'nsis', '-setup.exe', 'NSIS setup');
    const msiFile = requireSingleInstaller(msiDir, 'msi', '.msi', 'WiX msi');

    const outDir = join(resolveBuildDir(), 'windows');
    rmSync(outDir, { recursive: true, force: true });
    mkdirSync(outDir, { recursive: true });
    const zipName = `veles-native-bridge-${version}.zip`;
    const zipPath = join(outDir, zipName);

    // Exact allow-list: the raw executable, the single NSIS setup .exe, the
    // single WiX .msi, plus the in-memory host manifest JSON. Nothing else is
    // archived (no whole-tree walk, no stray/stale files).
    const entries = [
        { kind: 'file', rel: WINDOWS_BINARY, abs: binaryPath, mode: lstatSync(binaryPath).mode },
        {
            kind: 'file',
            rel: `bundle/nsis/${nsisExe}`,
            abs: join(nsisDir, nsisExe),
            mode: lstatSync(join(nsisDir, nsisExe)).mode,
        },
        {
            kind: 'file',
            rel: `bundle/msi/${msiFile}`,
            abs: join(msiDir, msiFile),
            mode: lstatSync(join(msiDir, msiFile)).mode,
        },
        {
            kind: 'file',
            rel: `${manifest.name}.json`,
            abs: null,
            content: JSON.stringify(manifest, null, 2) + '\n',
            mode: 0o644,
        },
    ];
    sortByRel(entries);

    const zipfile = new yazl.ZipFile();
    for (const entry of entries) {
        // zip: no directory entries (determinism per web-extension recipe);
        // symlinks are stored as regular files (zip has no portable symlink
        // mode that extracts cleanly cross-platform — the Windows payload has
        // no symlinks in practice).
        if (entry.kind === 'dir') continue;
        const mode = normalizedMode(entry);
        if (entry.content !== undefined) {
            zipfile.addBuffer(Buffer.from(entry.content), entry.rel, {
                mtime: FIXED_MTIME,
                mode: 0o100000 | mode,
            });
        } else {
            zipfile.addBuffer(readFileSync(entry.abs), entry.rel, {
                mtime: FIXED_MTIME,
                mode: 0o100000 | mode,
            });
        }
    }
    const chunks = [];
    await new Promise((resolve, reject) => {
        zipfile.outputStream.on('data', (c) => chunks.push(c));
        zipfile.outputStream.once('end', resolve);
        zipfile.outputStream.once('error', reject);
        zipfile.end();
    });
    writeFileSync(zipPath, Buffer.concat(chunks));
    console.log(zipPath);
    writeSidecar(zipPath, zipName);
    await writeChecksumManifest(outDir, zipName);
}

// Build a deterministic USTAR tar archive entirely in pure JS so the macOS
// packaging step does not depend on GNU tar flags (the macOS runner ships
// bsdtar, which rejects --sort=name / --mtime=@0). Every entry is normalized:
//   - sorted lexicographically by path
//   - mtime fixed at Unix epoch (0)
//   - uid/gid fixed at 0, uname/gname empty
//   - mode 0755 for directories/executables, 0644 for regular files
//   - symlinks archived as symlinks (typeflag '2', target in linkname)
//   - empty directories preserved as typeflag '5' entries
//   - USTAR prefix field used for paths >100 bytes (prefix <=155, name <=100)
// The archive is terminated by two 512-byte zero blocks (end-of-archive).
//
// Entries may carry an in-memory `content` string (for the manifest) instead
// of an on-disk `abs` path; createTar handles both without dereferencing.
function createTar(entries) {
    const blocks = [];
    for (const entry of entries) {
        const isDir = entry.kind === 'dir';
        const isSymlink = entry.kind === 'symlink';
        let data;
        if (isDir || isSymlink) {
            data = Buffer.alloc(0);
        } else if (entry.content !== undefined) {
            data = Buffer.from(entry.content, 'utf8');
        } else {
            data = readFileSync(entry.abs);
        }

        // Split the path into a USTAR name (<=100 bytes) and prefix (<=155).
        // If the whole path fits in 100 bytes, prefix is empty. Otherwise find
        // ANY split point where name <=100 bytes AND prefix <=155 bytes (both
        // constraints must hold simultaneously). The split that puts only the
        // last component in the name (longest prefix) may satisfy name<=100 but
        // exceed prefix<=155; an earlier split (more components in the name,
        // shorter prefix) can be the only valid one. The search must therefore
        // not stop at the first name-valid candidate — it must keep looking
        // until BOTH bounds are satisfied.
        const relBytes = Buffer.from(entry.rel, 'utf8');
        let name = entry.rel;
        let prefix = '';
        if (relBytes.length > 100) {
            const parts = entry.rel.split('/');
            let chosen = -1;
            // Iterate so that name grows (more trailing components) as we move
            // earlier in the array. The first split satisfying both name<=100
            // and prefix<=155 is accepted; any valid split is fine.
            for (let i = parts.length - 1; i >= 1; i--) {
                const candidateName = parts.slice(i).join('/');
                if (Buffer.from(candidateName, 'utf8').length > 100) {
                    // Adding more components only makes the name longer; stop.
                    break;
                }
                const candidatePrefix = parts.slice(0, i).join('/');
                if (Buffer.from(candidatePrefix, 'utf8').length <= 155) {
                    chosen = i;
                    break;
                }
            }
            if (chosen === -1) {
                throw new Error(
                    `tar entry name too long: no valid USTAR split (name<=100, ` +
                        `prefix<=155): ${entry.rel}`,
                );
            }
            name = parts.slice(chosen).join('/');
            prefix = parts.slice(0, chosen).join('/');
        }

        const header = Buffer.alloc(512, 0);
        header.write(name, 0, 'utf8');
        const mode = normalizedMode(entry);
        header.write(mode.toString(8).padStart(7, '0') + '\0', 100, 'ascii');
        header.write('0000000\0', 108, 'ascii');
        header.write('0000000\0', 116, 'ascii');
        header.write(data.length.toString(8).padStart(11, '0') + '\0', 124, 'ascii');
        header.write('00000000000\0', 136, 'ascii');
        // typeflag: '0' regular file, '5' directory, '2' symlink.
        header.write(isDir ? '5' : isSymlink ? '2' : '0', 156, 'ascii');
        // linkname for symlinks: the target relative to the link's directory.
        if (isSymlink) {
            const target = readSymlinkTarget(entry.abs);
            const targetBuf = Buffer.from(target, 'utf8');
            if (targetBuf.length > 100) {
                throw new Error(`tar symlink target too long (>100 bytes): ${target}`);
            }
            header.write(target, 157, 'utf8');
        }
        header.write('ustar\0', 257, 'ascii');
        header.write('00', 263, 'ascii');
        // uname/gname left zeroed (offset 265 / 297).
        // devmajor/devminor left zeroed (329 / 337).
        if (prefix.length > 0) {
            header.write(prefix, 345, 'utf8');
        }

        // checksum: sum of all header bytes with the 8-byte checksum field
        // (offset 148..156) treated as ASCII spaces (0x20). Written as 6 octal
        // digits, a NUL, and a space.
        let checksum = 0;
        for (let i = 0; i < 512; i++) {
            checksum += i >= 148 && i < 156 ? 0x20 : header[i];
        }
        header.write(checksum.toString(8).padStart(6, '0') + '\0 ', 148, 'ascii');

        blocks.push(header);
        if (!isDir && !isSymlink) {
            const pad = (512 - (data.length % 512)) % 512;
            blocks.push(pad === 0 ? data : Buffer.concat([data, Buffer.alloc(pad, 0)]));
        }
    }
    // End-of-archive: two 512-byte zero blocks.
    blocks.push(Buffer.alloc(512, 0), Buffer.alloc(512, 0));
    return Buffer.concat(blocks);
}

async function packageMacos(version, manifest) {
    const releaseDir = resolveReleaseDir();
    const bundleMacosDir = join(releaseDir, 'bundle', 'macos');
    const bundleDmgDir = join(releaseDir, 'bundle', 'dmg');
    const appBundlePath = join(bundleMacosDir, MACOS_APP_BUNDLE);

    if (!existsSync(bundleMacosDir) || !lstatSync(bundleMacosDir).isDirectory()) {
        fail(
            `macOS payload not found. Expected the Tauri bundle output at:\n  ${bundleMacosDir}\n` +
                `Run the Tauri bundle build (bridgeBuild) first.`,
        );
    }
    const macosEntries = readdirSync(bundleMacosDir);
    const unexpectedMacosEntries = macosEntries.filter((name) => name !== MACOS_APP_BUNDLE);
    if (unexpectedMacosEntries.length > 0) {
        fail(
            `Unexpected files found alongside the macOS app bundle in ${bundleMacosDir}:\n` +
                unexpectedMacosEntries.map((name) => `  ${name}`).join('\n') +
                '\nOnly the Tauri-emitted app bundle is allowed; remove stale/stray files before packaging.',
        );
    }
    if (!existsSync(appBundlePath) || !lstatSync(appBundlePath).isDirectory()) {
        fail(
            `macOS .app bundle not found under the bundle output. Expected:\n  ${appBundlePath}\n` +
                `Run the Tauri bundle build (bridgeBuild) with the 'app' bundle target first.`,
        );
    }
    const hostPath = join(appBundlePath, 'Contents', 'MacOS', 'veles-native-bridge');
    if (!existsSync(hostPath)) {
        fail(`macOS app host executable not found: ${hostPath}`);
    }
    const hostStat = lstatSync(hostPath);
    if (!hostStat.isFile() || hostStat.size === 0 || (hostStat.mode & 0o111) === 0) {
        fail(`macOS app host executable is invalid: ${hostPath}`);
    }
    // The macOS bundle build requests BOTH the .app and the .dmg (see
    // bridgeBundleTargetsFor in build.gradle.kts). Require exactly one
    // nonempty .dmg installer from its actual Tauri bundle output location
    // (bundle/dmg/) and reject any unexpected/stale files alongside it.
    const dmgName = requireSingleInstaller(bundleDmgDir, 'dmg', '.dmg', 'macOS .dmg');
    const dmgAbs = join(bundleDmgDir, dmgName);

    const outDir = join(resolveBuildDir(), 'macos');
    rmSync(outDir, { recursive: true, force: true });
    mkdirSync(outDir, { recursive: true });
    const tarName = `veles-native-bridge-${version}.tar.gz`;
    const tarPath = join(outDir, tarName);

    // Allow-list: the whole bundle/macos/ tree archived under its own name so
    // extraction reproduces "Veles Native Bridge.app/...", the .dmg installer
    // from bundle/dmg/ archived at bundle/dmg/<name>, plus the in-memory host
    // manifest JSON at the archive root. Nothing else is archived.
    const walked = walkBundle(bundleMacosDir, '');
    const entries = [];
    for (const e of walked) {
        // Re-base paths so the .app is at the archive root.
        entries.push({ ...e, rel: e.rel });
    }
    entries.push({
        kind: 'file',
        rel: `bundle/dmg/${dmgName}`,
        abs: dmgAbs,
        mode: lstatSync(dmgAbs).mode,
    });
    entries.push({
        kind: 'file',
        rel: `${manifest.name}.json`,
        abs: null,
        content: JSON.stringify(manifest, null, 2) + '\n',
        mode: 0o644,
    });
    sortByRel(entries);

    const tarBuffer = createTar(entries);
    // gzip with fixed mtime (0) header metadata for determinism.
    const gzBuffer = gzipSync(tarBuffer, { level: 9, mtime: 0 });
    writeFileSync(tarPath, gzBuffer);
    console.log(tarPath);
    writeSidecar(tarPath, tarName);
    await writeChecksumManifest(outDir, tarName);
}

async function main() {
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
        await packageWindows(version, manifest);
    } else {
        // macOS: emit a concrete absolute host path (no {{INSTALL_DIR}}).
        const installRoot = resolveMacosInstallRoot();
        const manifest = buildHostManifest('macos', installRoot);
        await packageMacos(version, manifest);
    }
}

main().catch((caught) => fail(caught.message));
