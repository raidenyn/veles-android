// OTP-01 sub-project 1a — deterministic packaging for the MV3 web extension.
//
// Replaces the former Gradle `extensionPackage` task. `vite build` is expected
// to have already produced `dist/` (the `package` npm script invokes
// `vite build && node scripts/package.mjs`); this script zips it
// deterministically and emits a sha256 sidecar.
//
// Manifest baseline validation is a test concern, not a packaging step:
//   - source-level via test/manifest-guard.test.ts (against
//     buildExtensionManifest())
//   - bundle-level via test/bundle.test.ts (against built dist/manifest.json,
//     round-trip exact match), run in CI before `npm run package`.
//
// Outputs (at the repo-root `build/web-extension/`, NOT inside web-extension/):
//   - veles-extension-<version>.zip      deterministic zip of dist/
//   - veles-extension-<version>.zip.sha256  "<hexdigest>  <zipname>\n"
//
// Deterministic-zip recipe (mirrors the former Kotlin DSL):
//   - entries sorted lexicographically by path
//   - fixed mtimes (all entries stamped with the Unix epoch, 1980-01-01 for
//     the zip DOS date floor)
//   - no directory entries for empty dirs
//   - DEFLATE compression
//   - unix file mode 100644 (regular file + 0644 perms), dir mode 040755
//     (directory + 0755 perms). yazl's `mode` carries both the file-type
//     and permission bits, so the leading type bits are required for the
//     entry to read as a regular file / directory rather than `?`.

import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync, mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import yazl from 'yazl';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const WEB_EXT_DIR = resolve(__dirname, '..');
const DIST_DIR = join(WEB_EXT_DIR, 'dist');
const BUILD_DIR = resolve(WEB_EXT_DIR, '..', 'build', 'web-extension');

// Fixed mtime: zip DOS date floor is 1980-01-01, but any constant works for
// determinism. The Unix epoch (1970) is clamped to the DOS floor by yazl.
const FIXED_MTIME = new Date(0);

function fail(reason) {
    console.error(`web-extension package: ${reason}`);
    process.exit(1);
}

function readJson(path) {
    return JSON.parse(readFileSync(path, 'utf8'));
}

// Walk a directory, returning relative POSIX-style paths (forward slashes)
// for every regular file plus the non-empty directories that contain them.
// Directories that contain no files (empty) are omitted, mirroring the
// recipe's `includeEmptyDirs = false` equivalent. Both lists are returned
// unsorted here; the caller sorts them together lexicographically so each
// directory entry precedes its contents.
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
                // Only record directories that contain at least one file.
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

function main() {
    // Ensure dist/ exists and is current. The `package` npm script runs
    // `vite build` first, but be defensive: if dist/ is missing, fail.
    const distStat = (() => {
        try {
            return statSync(DIST_DIR);
        } catch {
            return null;
        }
    })();
    if (!distStat || !distStat.isDirectory()) {
        fail(`expected ${DIST_DIR} to exist (run "npm run build" first).`);
    }

    const pkg = readJson(join(WEB_EXT_DIR, 'package.json'));
    if (typeof pkg.version !== 'string' || pkg.version.length === 0) {
        fail('web-extension/package.json missing a string "version".');
    }
    const version = pkg.version;

    // Collect entries (files + non-empty directories) and sort them
    // lexicographically by relative path, so each directory entry precedes
    // its contents.
    const { files, dirs } = walkFiles(DIST_DIR);
    if (files.length === 0) {
        fail(`${DIST_DIR} contains no files; nothing to package.`);
    }
    const entries = [...dirs, ...files].sort((a, b) =>
        a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0,
    );

    // Prepare the output directory.
    mkdirSync(BUILD_DIR, { recursive: true });
    const zipName = `veles-extension-${version}.zip`;
    const zipPath = join(BUILD_DIR, zipName);

    // yazl streams the zip; dist/ is tiny (background.js, content.js,
    // manifest.json), so collect the chunks synchronously then write and
    // digest in one pass. Fixed mtime + sorted entries + fixed mode make
    // the output byte-identical across runs on the same input.
    const zipfile = new yazl.ZipFile();
    for (const entry of entries) {
        if (entry.kind === 'dir') {
            zipfile.addEmptyDirectory(entry.rel, {
                mtime: FIXED_MTIME,
                mode: 0o40755,
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
        const digest = createHash('sha256').update(readFileSync(zipPath)).digest('hex');
        const sidecarPath = join(BUILD_DIR, `${zipName}.sha256`);
        writeFileSync(sidecarPath, `${digest}  ${zipName}\n`);
        console.log(zipPath);
        console.log(sidecarPath);
        console.log(`sha256: ${digest}`);
    });
}

main();
