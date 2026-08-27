// OTP-01 sub-project 1a — deterministic packaging for the MV3 web extension.
//
// Replaces the former Gradle `extensionPackage` + `validateExtensionManifest`
// tasks. Runs `vite build` is expected to have already produced `dist/` (the
// `package` npm script invokes `vite build && node scripts/package.mjs`), but
// this script also verifies the manifest baseline before zipping, so a missing
// or stale build fails loudly.
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
//   - unix file mode 0644, dir mode 0755
//
// Manifest baseline guard (exact match, was the Kotlin `validateExtensionManifest`):
//   - manifest_version === 3
//   - permissions present and exactly []
//   - host_permissions absent
//   - content_security_policy exactly { extension_pages: "script-src 'self'; object-src 'self'" }

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
// for every regular file. Directories are not emitted as entries.
function walkFiles(dir, base = '') {
    const out = [];
    for (const entry of readdirSync(dir)) {
        const abs = join(dir, entry);
        const rel = base ? `${base}/${entry}` : entry;
        const st = statSync(abs);
        if (st.isDirectory()) {
            out.push(...walkFiles(abs, rel));
        } else if (st.isFile()) {
            out.push({ rel: rel.split(sep).join('/'), abs });
        }
    }
    return out;
}

function validateManifest(manifest) {
    const errors = [];
    if (manifest.manifest_version !== 3) {
        errors.push(`manifest_version must be 3, got ${String(manifest.manifest_version)}`);
    }
    if (!Object.prototype.hasOwnProperty.call(manifest, 'permissions')) {
        errors.push('permissions key must be present and equal to []');
    } else if (!Array.isArray(manifest.permissions) || manifest.permissions.length !== 0) {
        errors.push(`permissions must be exactly [], got ${JSON.stringify(manifest.permissions)}`);
    }
    if (Object.prototype.hasOwnProperty.call(manifest, 'host_permissions')) {
        errors.push(
            `host_permissions must be absent in 1a, got ${JSON.stringify(manifest.host_permissions)}`,
        );
    }
    const expectedCsp = { extension_pages: "script-src 'self'; object-src 'self'" };
    const actualCsp = manifest.content_security_policy;
    if (JSON.stringify(actualCsp) !== JSON.stringify(expectedCsp)) {
        errors.push(
            `content_security_policy must be exactly ${JSON.stringify(expectedCsp)}, got ${JSON.stringify(actualCsp)}`,
        );
    }
    if (errors.length) {
        fail(`manifest guard failed:\n  - ${errors.join('\n  - ')}`);
    }
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

    // Manifest baseline guard against the emitted dist/manifest.json.
    const manifestPath = join(DIST_DIR, 'manifest.json');
    try {
        validateManifest(readJson(manifestPath));
    } catch (e) {
        fail(`could not read/parse ${manifestPath}: ${e.message}`);
    }
    console.error('web-extension manifest guard: MV3 baseline (exact match) OK.');

    // Collect and sort file entries lexicographically by relative path.
    const files = walkFiles(DIST_DIR).sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));
    if (files.length === 0) {
        fail(`${DIST_DIR} contains no files; nothing to package.`);
    }

    // Prepare the output directory.
    mkdirSync(BUILD_DIR, { recursive: true });
    const zipName = `veles-extension-${version}.zip`;
    const zipPath = join(BUILD_DIR, zipName);

    // yazl streams the zip; dist/ is tiny (background.js, content.js,
    // manifest.json), so collect the chunks synchronously then write and
    // digest in one pass. Fixed mtime + sorted entries + fixed mode make
    // the output byte-identical across runs on the same input.
    const zipfile = new yazl.ZipFile();
    for (const { rel, abs } of files) {
        zipfile.addBuffer(readFileSync(abs), rel, {
            mtime: FIXED_MTIME,
            mode: 0o644,
        });
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
