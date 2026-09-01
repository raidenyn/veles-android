// OTP-01 sub-project 1c — extraction-level packaging tests.
//
// These tests build REAL fixture trees that mimic the Tauri bundle output,
// run scripts/package.mjs as a subprocess, and inspect the EXTRACTED archive
// contents (mode bits, symlinks, empty dirs, in-memory manifest bytes, long
// USTAR prefix paths). They never grep the source of package.mjs.
//
// The packaging script reads from src-tauri/target/release/bundle/<platform>/
// and writes to build/native-bridge/<platform>/. To avoid a real Tauri build,
// the tests point the script at a temp fixture tree via the
// VELES_BRIDGE_RELEASE_DIR and VELES_BRIDGE_BUILD_OUT_DIR env overrides and a
// concrete VELES_BRIDGE_INSTALL_ROOT (so the macOS manifest is absolute).

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
    existsSync,
    mkdirSync,
    mkdtempSync,
    readFileSync,
    readdirSync,
    rmSync,
    symlinkSync,
    writeFileSync,
    chmodSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { readTar, readZip, type TarEntry } from './archive-reader';

// Assert an entry search result is present (avoids non-null assertions, which
// the strict ESLint config forbids in this repo).
function requireEntry<T>(entry: T | undefined, name: string): T {
    if (entry === undefined) {
        throw new Error(`expected archive entry not found: ${name}`);
    }
    return entry;
}

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const BRIDGE_DIR = resolve(__dirname, '..');
const PACKAGE_SCRIPT = resolve(BRIDGE_DIR, 'scripts/package.mjs');
const APP_BUNDLE = 'Veles Native Bridge.app';
// Tauri's .app bundle's Contents/MacOS/ binary is the Cargo [[bin]] name
// ("veles-native-bridge"), NOT the productName. The fixture and assertions
// mirror the real Tauri bundle layout the packaging script must consume.
const EXEC_NAME = 'veles-native-bridge';

describe('package.mjs macOS tar.gz (extraction-tested)', () => {
    let fixtureBase: string;
    let releaseDir: string;
    let buildOutDir: string;
    const installRoot = '/Applications/Veles/NativeBridge';

    beforeAll(() => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-'));
        fixtureBase = base;
        releaseDir = join(base, 'target', 'release');
        buildOutDir = join(base, 'build', 'native-bridge');
        // Mimic Tauri bundle output: target/release/bundle/macos/<product>.app
        const bundleDir = join(releaseDir, 'bundle', 'macos');
        const appContents = join(bundleDir, APP_BUNDLE, 'Contents');
        const macosDir = join(appContents, 'MacOS');
        const resourcesDir = join(appContents, 'Resources');
        mkdirSync(macosDir, { recursive: true });
        mkdirSync(resourcesDir, { recursive: true });
        // Executable — must keep mode 0755 in the archive.
        const execPath = join(macosDir, EXEC_NAME);
        writeFileSync(execPath, '#!/bin/bash\necho veles\n');
        chmodSync(execPath, 0o755);
        // Regular resource file — mode 0644.
        writeFileSync(join(resourcesDir, 'icon.icns'), 'ICNS-BYTES');
        // A symlink inside the bundle (Frameworks symlinks are common in .app).
        const frameworksDir = join(appContents, 'Frameworks');
        mkdirSync(frameworksDir, { recursive: true });
        writeFileSync(join(frameworksDir, 'VelesCore.dylib'), 'dylib-bytes');
        symlinkSync('VelesCore.dylib', join(frameworksDir, 'VelesCore.framework'));
        // An empty directory inside the bundle (PlugIns, often empty).
        mkdirSync(join(appContents, 'PlugIns'));
        // The .dmg installer Tauri emits from the .app (bundle/dmg/). The
        // packaging step must require and archive this installer artifact,
        // produced from its actual Tauri bundle output location.
        const dmgDir = join(releaseDir, 'bundle', 'dmg');
        mkdirSync(dmgDir, { recursive: true });
        writeFileSync(join(dmgDir, 'Veles Native Bridge_0.1.0_aarch64.dmg'), 'DMG-BYTES');
    });

    afterAll(() => {
        rmSync(fixtureBase, { recursive: true, force: true });
    });

    function runPackage() {
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: releaseDir,
            VELES_BRIDGE_BUILD_OUT_DIR: buildOutDir,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        return execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env });
    }

    // Drop a nonempty .dmg installer into bundle/dmg/ so self-contained macOS
    // fixtures satisfy the "require the .dmg" contract (the bundle build emits
    // app+dmg). Used by tests that build their own fixture trees.
    function writeDmg(rel: string) {
        const dmgDir = join(rel, 'bundle', 'dmg');
        mkdirSync(dmgDir, { recursive: true });
        writeFileSync(join(dmgDir, 'Veles Native Bridge_0.1.0_aarch64.dmg'), 'DMG-BYTES');
    }

    it('fails when the bundle/macos/ directory is missing', () => {
        const emptyBase = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-empty-'));
        const emptyRelease = join(emptyBase, 'target', 'release');
        mkdirSync(emptyRelease, { recursive: true });
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: emptyRelease,
            VELES_BRIDGE_BUILD_OUT_DIR: join(emptyBase, 'build'),
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/bundle\/macos|payload not found/i);
        rmSync(emptyBase, { recursive: true, force: true });
    });

    it('fails when the bundle/dmg/ installer is missing (the bundle builds app+dmg)', () => {
        // The macOS bundle build requests both the .app and the .dmg; the
        // packaging step must require the .dmg installer artifact, not
        // silently ship an archive without it.
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-nodmg-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const macosDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(macosDir, { recursive: true });
        writeFileSync(join(macosDir, EXEC_NAME), 'x');
        chmodSync(join(macosDir, EXEC_NAME), 0o755);
        // No bundle/dmg/ created.
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/bundle\/dmg|\.dmg|installer/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('archives the .dmg installer from its actual Tauri bundle output location (bundle/dmg/)', () => {
        runPackage();
        const tarPath = join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const names = entries.map((e) => e.name);
        // Exactly one .dmg under bundle/dmg/ is archived, from the real Tauri
        // bundle output path (not the .app's own dir).
        const dmgEntries = names.filter((n) => n.startsWith('bundle/dmg/') && n.endsWith('.dmg'));
        expect(dmgEntries).toHaveLength(1);
        const dmgName = 'Veles Native Bridge_0.1.0_aarch64.dmg';
        expect(dmgEntries[0]).toBe(`bundle/dmg/${dmgName}`);
        const dmgEntry = entries.find((e) => e.name === `bundle/dmg/${dmgName}`);
        expect(requireEntry(dmgEntry, dmgName).data.toString('utf8')).toBe('DMG-BYTES');
    });

    it("accepts Tauri's bundle_dmg.sh helper beside the .dmg installer", () => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-dmg-helper-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const macosDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(macosDir, { recursive: true });
        writeFileSync(join(macosDir, EXEC_NAME), 'x');
        chmodSync(join(macosDir, EXEC_NAME), 0o755);
        writeDmg(rel);
        writeFileSync(join(rel, 'bundle', 'dmg', 'bundle_dmg.sh'), '#!/bin/sh\n');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).not.toThrow();
        rmSync(base, { recursive: true, force: true });
    });

    it("accepts Tauri's product icon beside the .dmg installer without archiving it", () => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-dmg-icon-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const macosDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(macosDir, { recursive: true });
        writeFileSync(join(macosDir, EXEC_NAME), 'x');
        chmodSync(join(macosDir, EXEC_NAME), 0o755);
        writeDmg(rel);
        writeFileSync(join(rel, 'bundle', 'dmg', 'Veles Native Bridge.icns'), 'ICNS-BYTES');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).not.toThrow();
        const entries = readTar(readFileSync(join(bOut, 'macos', 'veles-native-bridge-0.1.0.tar.gz')));
        expect(entries.some((entry) => entry.name.endsWith('.icns'))).toBe(false);
        rmSync(base, { recursive: true, force: true });
    });

    it('rejects an arbitrary sibling beside the .dmg installer', () => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-dmg-stray-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const macosDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(macosDir, { recursive: true });
        writeFileSync(join(macosDir, EXEC_NAME), 'x');
        chmodSync(join(macosDir, EXEC_NAME), 0o755);
        writeDmg(rel);
        writeFileSync(join(rel, 'bundle', 'dmg', 'unexpected.txt'), 'stray');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/unexpected|stray|installer/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('rejects a stale/unexpected .dmg (only the bundle/dmg/ installer is allow-listed)', () => {
        // A stray .dmg outside bundle/dmg/ must NOT be archived; the allow-list
        // is exactly the .app tree + bundle/dmg/*.dmg + the manifest.
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-staledmg-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const macosDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(macosDir, { recursive: true });
        writeFileSync(join(macosDir, EXEC_NAME), 'x');
        chmodSync(join(macosDir, EXEC_NAME), 0o755);
        const dmgDir = join(rel, 'bundle', 'dmg');
        mkdirSync(dmgDir, { recursive: true });
        writeFileSync(join(dmgDir, 'Veles Native Bridge_0.1.0_aarch64.dmg'), 'GOOD-DMG');
        // Stale stray .dmg dropped at the release root — must be ignored.
        writeFileSync(join(rel, 'Veles Native Bridge_0.1.0_stale.dmg'), 'STALE-DMG');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env });
        const tarPath = join(bOut, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const names = entries.map((e) => e.name);
        const dmgEntries = names.filter((n) => n.endsWith('.dmg'));
        expect(dmgEntries).toHaveLength(1);
        expect(dmgEntries[0]).toBe('bundle/dmg/Veles Native Bridge_0.1.0_aarch64.dmg');
        // The stale .dmg at the release root is NOT archived.
        expect(names.some((n) => n.includes('stale'))).toBe(false);
        rmSync(base, { recursive: true, force: true });
    });

    it('rejects stale siblings alongside the expected app bundle', () => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-staleapp-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const macosDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(macosDir, { recursive: true });
        writeFileSync(join(macosDir, EXEC_NAME), 'x');
        chmodSync(join(macosDir, EXEC_NAME), 0o755);
        writeFileSync(join(rel, 'bundle', 'macos', 'stale.app'), 'stale');
        writeDmg(rel);
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/unexpected|stale|app bundle/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('rejects an app bundle with no executable host payload', () => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-nohost-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        mkdirSync(join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS'), {
            recursive: true,
        });
        writeDmg(rel);
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/host|executable|payload/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('archives the whole bundle/macos/ tree plus an in-memory manifest with a concrete absolute path', () => {
        const stdout = runPackage();
        const tarPath = join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        expect(existsSync(tarPath)).toBe(true);
        expect(stdout).toContain(tarPath);

        const entries = readTar(readFileSync(tarPath));
        const names = entries.map((e) => e.name);

        // The .app bundle is archived under its own name.
        const appPrefix = `${APP_BUNDLE}/Contents`;
        expect(names.some((n) => n === `${appPrefix}/MacOS/${EXEC_NAME}`)).toBe(true);
        expect(names.some((n) => n === `${appPrefix}/Resources/icon.icns`)).toBe(true);

        // In-memory manifest entry at the archive root, NOT read from disk.
        const manifestName = 'app.veles.native_bridge.json';
        const manifestEntry = entries.find((e) => e.name === manifestName);
        const manifest = JSON.parse(
            requireEntry(manifestEntry, manifestName).data.toString('utf8'),
        );
        expect(manifest.name).toBe('app.veles.native_bridge');
        expect(manifest.type).toBe('stdio');
        // Concrete absolute path — no {{INSTALL_DIR}}, no leading '~'.
        expect(manifest.path).toBe(`${installRoot}/${APP_BUNDLE}/Contents/MacOS/${EXEC_NAME}`);
        expect(manifest.path).not.toContain('{{INSTALL_DIR}}');
        expect(manifest.path.startsWith('/')).toBe(true);
    });

    it('preserves the executable mode (0755) on the app binary', () => {
        runPackage();
        const tarPath = join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const exec = entries.find((e) => e.name === `${APP_BUNDLE}/Contents/MacOS/${EXEC_NAME}`);
        // Low 9 bits of mode must include execute for owner/group/other.
        expect(requireEntry(exec, EXEC_NAME).mode & 0o111).toBe(0o111);
    });

    it('preserves regular-file mode (0644) on non-executable resources', () => {
        runPackage();
        const tarPath = join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const icon = entries.find((e) => e.name === `${APP_BUNDLE}/Contents/Resources/icon.icns`);
        const iconEntry: TarEntry = requireEntry(icon, 'icon.icns');
        expect(iconEntry.mode & 0o111).toBe(0);
        expect(iconEntry.mode & 0o444).toBe(0o444);
    });

    it('preserves symlinks as symlinks (not followed/dereferenced)', () => {
        runPackage();
        const tarPath = join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const linkName = `${APP_BUNDLE}/Contents/Frameworks/VelesCore.framework`;
        const link = entries.find((e) => e.name === linkName);
        const linkEntry: TarEntry = requireEntry(link, linkName);
        expect(linkEntry.isSymlink).toBe(true);
        expect(linkEntry.linkname).toBe('VelesCore.dylib');
        // Symlink target bytes are empty in the archive.
        expect(linkEntry.data.length).toBe(0);
    });

    it('preserves empty directories as directory entries', () => {
        runPackage();
        const tarPath = join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const plugInsName = `${APP_BUNDLE}/Contents/PlugIns`;
        const plugIns = entries.find((e) => e.name === plugInsName);
        const plugInsEntry: TarEntry = requireEntry(plugIns, plugInsName);
        expect(plugInsEntry.isDirectory).toBe(true);
        expect(plugInsEntry.data.length).toBe(0);
    });

    it('supports USTAR prefix paths for entries >100 bytes', () => {
        // Build a fixture with a deeply nested path whose name exceeds 100 bytes.
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-long-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const bundleDir = join(rel, 'bundle', 'macos');
        // A directory chain long enough that a leaf file name > 100 bytes.
        const deep = join(
            bundleDir,
            APP_BUNDLE,
            'Contents',
            'Resources',
            'very-long-resource-subdirectory-name-padding-padding-padding-padding',
        );
        mkdirSync(deep, { recursive: true });
        const hostDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(hostDir, { recursive: true });
        writeFileSync(join(hostDir, EXEC_NAME), 'x');
        chmodSync(join(hostDir, EXEC_NAME), 0o755);
        const longName = 'x'.repeat(60) + '.dat';
        writeFileSync(join(deep, longName), 'long');
        writeDmg(rel);
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env });
        const tarPath = join(bOut, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const expected = `${APP_BUNDLE}/Contents/Resources/very-long-resource-subdirectory-name-padding-padding-padding-padding/${longName}`;
        const found = entries.find((e) => e.name === expected);
        expect(requireEntry(found, expected).data.toString('utf8')).toBe('long');
        rmSync(base, { recursive: true, force: true });
    });

    it('accepts any valid USTAR prefix/name split, not only the first candidate', () => {
        // Construct a path where the split that puts only the LAST component in
        // the name (longest prefix) has a valid name (<=100) but a prefix >155,
        // while an earlier split (one more component moved into the name) has
        // BOTH name <=100 and prefix <=155. The archive must accept the valid
        // split, not throw because the first name-valid split has a too-long
        // prefix.
        //
        // The fixed .app/Contents/Resources prelude is 42 bytes. With 13
        // path components of 8 bytes each and a 90-byte leaf file:
        //   - split at the leaf only (name=90 <=100): prefix = 43 + 13*8 + 12
        //     = 159 (>155)  -> invalid; the buggy "first name-valid match only"
        //     search would pick this and throw.
        //   - split one earlier (name = 8 + "/" + 90 = 99 <=100): prefix = 150
        //     (<=155) -> valid; the fixed search must accept this split.
        const seg = 'c'.repeat(8); // 8-byte path component
        const leaf = 'z'.repeat(90); // 90-byte leaf file name
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-ustar-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const dirs = Array.from({ length: 13 }, () => seg);
        const leafDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'Resources', ...dirs);
        mkdirSync(leafDir, { recursive: true });
        const hostDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(hostDir, { recursive: true });
        writeFileSync(join(hostDir, EXEC_NAME), 'x');
        chmodSync(join(hostDir, EXEC_NAME), 0o755);
        writeFileSync(join(leafDir, leaf), 'ustar-split');
        writeDmg(rel);
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: installRoot,
        };
        // Must not throw: a valid (name<=100, prefix<=155) split exists.
        execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env });
        const tarPath = join(bOut, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const expected = `${APP_BUNDLE}/Contents/Resources/${dirs.join('/')}/${leaf}`;
        const found = entries.find((e) => e.name === expected);
        expect(requireEntry(found, expected).data.toString('utf8')).toBe('ustar-split');
        rmSync(base, { recursive: true, force: true });
    });

    it('manifest path points at the Cargo binary inside the .app, not the productName', () => {
        runPackage();
        const tarPath = join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const manifestName = 'app.veles.native_bridge.json';
        const manifestEntry = entries.find((e) => e.name === manifestName);
        const manifest = JSON.parse(
            requireEntry(manifestEntry, manifestName).data.toString('utf8'),
        );
        // The .app bundle directory is named after the Tauri productName, but
        // the executable inside Contents/MacOS/ is the Cargo [[bin]] name
        // ("veles-native-bridge"). The manifest must launch the real binary,
        // not the product-name placeholder that does not exist on disk.
        expect(manifest.path).toContain(`${APP_BUNDLE}/Contents/MacOS/veles-native-bridge`);
        expect(manifest.path).not.toContain(`${APP_BUNDLE}/Contents/MacOS/Veles Native Bridge`);
    });

    it('uses a documented stable default install root when VELES_BRIDGE_INSTALL_ROOT is unset (never $HOME-derived)', () => {
        // Production behavior: when no VELES_BRIDGE_INSTALL_ROOT override is
        // supplied, the packaged macOS manifest must point at a documented
        // stable absolute install destination — never a path derived from the
        // build machine's $HOME (which would vary per machine and leak the
        // builder's home directory into a distributable archive).
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-default-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const macosDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(macosDir, { recursive: true });
        writeFileSync(join(macosDir, EXEC_NAME), '#!/bin/bash\necho veles\n');
        chmodSync(join(macosDir, EXEC_NAME), 0o755);
        writeDmg(rel);
        const env: Record<string, string | undefined> = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            // Deliberately NOT setting VELES_BRIDGE_INSTALL_ROOT.
            // Pin HOME so any $HOME-derived default would produce a
            // fixture-specific path we can detect, and a build-machine leak
            // would be observable.
            HOME: base,
        };
        execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env });
        const tarPath = join(bOut, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const entries = readTar(readFileSync(tarPath));
        const manifestName = 'app.veles.native_bridge.json';
        const manifestEntry = entries.find((e) => e.name === manifestName);
        const manifest = JSON.parse(
            requireEntry(manifestEntry, manifestName).data.toString('utf8'),
        );
        // The documented stable default. Matches the package layout: the .app
        // is archived at the archive root, and a macOS installer (the .dmg)
        // conventionally copies the .app into /Applications.
        expect(manifest.path).toBe(`/Applications/${APP_BUNDLE}/Contents/MacOS/${EXEC_NAME}`);
        // Must NOT be derived from the build machine's $HOME.
        expect(manifest.path).not.toContain(base);
        expect(manifest.path).not.toContain(tmpdir());
        rmSync(base, { recursive: true, force: true });
    });

    it('rejects a VELES_BRIDGE_INSTALL_ROOT derived from $HOME via a tilde (Chrome does not expand ~)', () => {
        // Already covered by manifest-install-path, but assert the packaging
        // script surfaces the error rather than emitting a ~ manifest.
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-macos-tilde-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        const macosDir = join(rel, 'bundle', 'macos', APP_BUNDLE, 'Contents', 'MacOS');
        mkdirSync(macosDir, { recursive: true });
        writeFileSync(join(macosDir, EXEC_NAME), 'x');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'macos',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
            VELES_BRIDGE_INSTALL_ROOT: '~/Library/Veles/NativeBridge',
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/~|absolute|install/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('is deterministic: two runs produce byte-identical archives', () => {
        runPackage();
        const first = readFileSync(join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz'));
        runPackage();
        const second = readFileSync(join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz'));
        expect(first.equals(second)).toBe(true);
    });

    it('pins fixed tar metadata and a fixed gzip header for cross-run determinism', () => {
        // The macOS product tarball is byte-compared across two CI runs on the
        // same image. Every tar header must carry mtime=0, uid=0, gid=0, empty
        // uname/gname, and zeroed devmajor/devminor, and the gzip wrapper must
        // carry mtime=0 so the archive is reproducible regardless of the build
        // host or wall clock. parseTar (archive-reader) already reads these
        // fields; assert them here as a determinism regression.
        runPackage();
        const gz = readFileSync(join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz'));
        // gzip header: bytes 4-7 are MTIME (must be 0 for a fixed header).
        expect(gz[0]).toBe(0x1f);
        expect(gz[1]).toBe(0x8b);
        expect(gz.subarray(4, 8).equals(Buffer.from([0, 0, 0, 0]))).toBe(true);
        const entries = readTar(gz);
        expect(entries.length).toBeGreaterThan(0);
        for (const entry of entries) {
            expect(entry.mtime, `tar entry ${entry.name} mtime must be epoch 0`).toBe(0);
        }
        // Re-parse the raw (unzipped) tar to assert uid/gid are 0 (readTar does
        // not expose uid/gid, so re-slice the header fields directly).
        const tar = gz[0] === 0x1f && gz[1] === 0x8b ? gunzipSync(gz) : gz;
        let off = 0;
        while (off + 512 <= tar.length) {
            const header = tar.subarray(off, off + 512);
            if (header.every((b) => b === 0)) break;
            const uid = parseInt(header.subarray(108, 116).toString('latin1').replace(/\0/g, ''), 8) || 0;
            const gid = parseInt(header.subarray(116, 124).toString('latin1').replace(/\0/g, ''), 8) || 0;
            const uname = header.subarray(265, 297).every((b) => b === 0);
            const gname = header.subarray(297, 329).every((b) => b === 0);
            const devmajor = header.subarray(329, 337).every((b) => b === 0);
            const devminor = header.subarray(337, 345).every((b) => b === 0);
            const name = header.subarray(0, header.indexOf(0) < 0 ? 100 : header.indexOf(0)).toString('utf8');
            expect(uid, `tar entry ${name} uid must be 0`).toBe(0);
            expect(gid, `tar entry ${name} gid must be 0`).toBe(0);
            expect(uname, `tar entry ${name} uname must be empty`).toBe(true);
            expect(gname, `tar entry ${name} gname must be empty`).toBe(true);
            expect(devmajor, `tar entry ${name} devmajor must be 0`).toBe(true);
            expect(devminor, `tar entry ${name} devminor must be 0`).toBe(true);
            const size = parseInt(header.subarray(124, 136).toString('latin1').replace(/\0/g, ''), 8) || 0;
            const blocks = Math.ceil(size / 512);
            off += 512 + blocks * 512;
        }
    });

    it('writes a sha256 sidecar matching the archive', () => {
        runPackage();
        const tarPath = join(buildOutDir, 'macos', 'veles-native-bridge-0.1.0.tar.gz');
        const sidecarPath = `${tarPath}.sha256`;
        expect(existsSync(sidecarPath)).toBe(true);
        const sidecar = readFileSync(sidecarPath).toString('utf8').trim();
        const digest = sidecar.split(/\s+/)[0];
        expect(digest).toMatch(/^[0-9a-f]{64}$/);
        const actual = createHash('sha256').update(readFileSync(tarPath)).digest('hex');
        expect(digest).toBe(actual);
    });

    it('replaces stale output and writes SHA256SUMS for only the package and sidecar', () => {
        runPackage();
        const outDir = join(buildOutDir, 'macos');
        const tarName = 'veles-native-bridge-0.1.0.tar.gz';
        const sidecarName = `${tarName}.sha256`;
        writeFileSync(join(outDir, 'stale-sentinel'), 'stale');

        runPackage();

        expect(readdirSync(outDir).sort()).toEqual(['SHA256SUMS', sidecarName, tarName].sort());
        const archive = readFileSync(join(outDir, tarName));
        const sidecar = readFileSync(join(outDir, sidecarName));
        expect(readFileSync(join(outDir, 'SHA256SUMS'), 'utf8')).toBe(
            `${createHash('sha256').update(archive).digest('hex')}  ${tarName}\n` +
                `${createHash('sha256').update(sidecar).digest('hex')}  ${sidecarName}\n`,
        );
    });
});

describe('package.mjs Windows zip (extraction-tested)', () => {
    let fixtureBase: string;
    let releaseDir: string;
    let buildOutDir: string;

    beforeAll(() => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-win-'));
        fixtureBase = base;
        releaseDir = join(base, 'target', 'release');
        buildOutDir = join(base, 'build', 'native-bridge');
        // Mimic Tauri bundle output: the raw exe + bundle/nsis setup exe + bundle/msi.
        mkdirSync(join(releaseDir), { recursive: true });
        writeFileSync(join(releaseDir, 'veles-native-bridge.exe'), 'PE-BYTES');
        const nsisDir = join(releaseDir, 'bundle', 'nsis');
        mkdirSync(nsisDir, { recursive: true });
        writeFileSync(join(nsisDir, 'Veles Native Bridge_0.1.0_x64-setup.exe'), 'NSIS-BYTES');
        const msiDir = join(releaseDir, 'bundle', 'msi');
        mkdirSync(msiDir, { recursive: true });
        writeFileSync(join(msiDir, 'Veles Native Bridge_0.1.0_x64_en-US.msi'), 'MSI-BYTES');
    });

    afterAll(() => {
        rmSync(fixtureBase, { recursive: true, force: true });
    });

    function runPackage() {
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'windows',
            VELES_BRIDGE_RELEASE_DIR: releaseDir,
            VELES_BRIDGE_BUILD_OUT_DIR: buildOutDir,
        };
        return execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env });
    }

    it('fails when both bundle/nsis and bundle/msi are missing', () => {
        const emptyBase = mkdtempSync(join(tmpdir(), 'veles-pkg-win-empty-'));
        const emptyRelease = join(emptyBase, 'target', 'release');
        mkdirSync(emptyRelease, { recursive: true });
        writeFileSync(join(emptyRelease, 'veles-native-bridge.exe'), 'PE-BYTES');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'windows',
            VELES_BRIDGE_RELEASE_DIR: emptyRelease,
            VELES_BRIDGE_BUILD_OUT_DIR: join(emptyBase, 'build'),
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/bundle\/nsis|bundle\/msi|installer input/i);
        rmSync(emptyBase, { recursive: true, force: true });
    });

    it('fails when the NSIS setup is missing (bundle requests nsis AND msi)', () => {
        // The Windows bundle build requests both nsis and msi targets, so the
        // packaging step must require BOTH installer artifacts, not accept an
        // either-or that silently ships without one.
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-win-nonsis-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        mkdirSync(rel, { recursive: true });
        writeFileSync(join(rel, 'veles-native-bridge.exe'), 'PE-BYTES');
        // Only the msi installer present.
        const msiDir = join(rel, 'bundle', 'msi');
        mkdirSync(msiDir, { recursive: true });
        writeFileSync(join(msiDir, 'Veles Native Bridge_0.1.0_x64_en-US.msi'), 'MSI-BYTES');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'windows',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/bundle\/nsis|nsis|installer/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('fails when the WiX msi is missing (bundle requests nsis AND msi)', () => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-win-nomsi-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        mkdirSync(rel, { recursive: true });
        writeFileSync(join(rel, 'veles-native-bridge.exe'), 'PE-BYTES');
        // Only the nsis installer present.
        const nsisDir = join(rel, 'bundle', 'nsis');
        mkdirSync(nsisDir, { recursive: true });
        writeFileSync(join(nsisDir, 'Veles Native Bridge_0.1.0_x64-setup.exe'), 'NSIS-BYTES');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'windows',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/bundle\/msi|msi|installer/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('fails when the NSIS setup .exe is empty (rejects a stale/broken installer)', () => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-win-emptynsis-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        mkdirSync(rel, { recursive: true });
        writeFileSync(join(rel, 'veles-native-bridge.exe'), 'PE-BYTES');
        const nsisDir = join(rel, 'bundle', 'nsis');
        mkdirSync(nsisDir, { recursive: true });
        writeFileSync(join(nsisDir, 'Veles Native Bridge_0.1.0_x64-setup.exe'), '');
        const msiDir = join(rel, 'bundle', 'msi');
        mkdirSync(msiDir, { recursive: true });
        writeFileSync(join(msiDir, 'Veles Native Bridge_0.1.0_x64_en-US.msi'), 'MSI-BYTES');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'windows',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/nsis|setup|empty|installer/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('rejects unexpected/stale files inside the bundle dirs (exact installer allow-list)', () => {
        // Only the Tauri-emitted NSIS setup .exe and WiX .msi are allow-listed
        // from bundle/nsis/ and bundle/msi/. A stale prior-version installer or
        // a stray file alongside them is rejected (hard fail) so a leftover
        // from a prior build is never silently archived.
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-win-stale-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        mkdirSync(rel, { recursive: true });
        writeFileSync(join(rel, 'veles-native-bridge.exe'), 'PE-BYTES');
        const nsisDir = join(rel, 'bundle', 'nsis');
        mkdirSync(nsisDir, { recursive: true });
        writeFileSync(join(nsisDir, 'Veles Native Bridge_0.1.0_x64-setup.exe'), 'NSIS-BYTES');
        // Stale prior-version setup left in the nsis dir -> hard fail.
        writeFileSync(join(nsisDir, 'Veles Native Bridge_0.0.9_x64-setup.exe'), 'STALE-NSIS');
        const msiDir = join(rel, 'bundle', 'msi');
        mkdirSync(msiDir, { recursive: true });
        writeFileSync(join(msiDir, 'Veles Native Bridge_0.1.0_x64_en-US.msi'), 'MSI-BYTES');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'windows',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/multiple|stale|setup|installer/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('rejects a stray non-installer file alongside the NSIS setup', () => {
        const base = mkdtempSync(join(tmpdir(), 'veles-pkg-win-stray-'));
        const rel = join(base, 'target', 'release');
        const bOut = join(base, 'build', 'native-bridge');
        mkdirSync(rel, { recursive: true });
        writeFileSync(join(rel, 'veles-native-bridge.exe'), 'PE-BYTES');
        const nsisDir = join(rel, 'bundle', 'nsis');
        mkdirSync(nsisDir, { recursive: true });
        writeFileSync(join(nsisDir, 'Veles Native Bridge_0.1.0_x64-setup.exe'), 'NSIS-BYTES');
        writeFileSync(join(nsisDir, 'build-tmp.txt'), 'STRAY');
        const msiDir = join(rel, 'bundle', 'msi');
        mkdirSync(msiDir, { recursive: true });
        writeFileSync(join(msiDir, 'Veles Native Bridge_0.1.0_x64_en-US.msi'), 'MSI-BYTES');
        const env = {
            ...process.env,
            VELES_BRIDGE_PLATFORM: 'windows',
            VELES_BRIDGE_RELEASE_DIR: rel,
            VELES_BRIDGE_BUILD_OUT_DIR: bOut,
        };
        expect(() =>
            execFileSync('node', [PACKAGE_SCRIPT], { cwd: BRIDGE_DIR, encoding: 'utf8', env }),
        ).toThrow(/unexpected|stray|allow-list|installer/i);
        rmSync(base, { recursive: true, force: true });
    });

    it('archives the raw exe, NSIS setup, WiX msi, and an in-memory manifest', () => {
        const stdout = runPackage();
        const zipPath = join(buildOutDir, 'windows', 'veles-native-bridge-0.1.0.zip');
        expect(existsSync(zipPath)).toBe(true);
        expect(stdout).toContain(zipPath);

        const entries = readZip(readFileSync(zipPath));
        const names = entries.map((e) => e.name);
        expect(names).toContain('veles-native-bridge.exe');
        expect(names.some((n) => n.startsWith('bundle/nsis/') && n.endsWith('-setup.exe'))).toBe(
            true,
        );
        expect(names.some((n) => n.startsWith('bundle/msi/') && n.endsWith('.msi'))).toBe(true);

        const manifestName = 'app.veles.native_bridge.json';
        const manifestEntry = entries.find((e) => e.name === manifestName);
        const manifest = JSON.parse(
            requireEntry(manifestEntry, manifestName).data.toString('utf8'),
        );
        expect(manifest.name).toBe('app.veles.native_bridge');
        expect(manifest.path).toBe('veles-native-bridge.exe');
        expect(manifest.type).toBe('stdio');
    });

    it('is deterministic: two runs produce byte-identical zips', () => {
        runPackage();
        const first = readFileSync(join(buildOutDir, 'windows', 'veles-native-bridge-0.1.0.zip'));
        runPackage();
        const second = readFileSync(join(buildOutDir, 'windows', 'veles-native-bridge-0.1.0.zip'));
        expect(first.equals(second)).toBe(true);
    });

    it('writes a sha256 sidecar matching the zip', () => {
        runPackage();
        const zipPath = join(buildOutDir, 'windows', 'veles-native-bridge-0.1.0.zip');
        const sidecarPath = `${zipPath}.sha256`;
        expect(existsSync(sidecarPath)).toBe(true);
        const sidecar = readFileSync(sidecarPath).toString('utf8').trim();
        const digest = sidecar.split(/\s+/)[0];
        const actual = createHash('sha256').update(readFileSync(zipPath)).digest('hex');
        expect(digest).toBe(actual);
    });

    it('replaces stale output and writes SHA256SUMS for only the package and sidecar', () => {
        runPackage();
        const outDir = join(buildOutDir, 'windows');
        const zipName = 'veles-native-bridge-0.1.0.zip';
        const sidecarName = `${zipName}.sha256`;
        writeFileSync(join(outDir, 'stale-sentinel'), 'stale');

        runPackage();

        expect(readdirSync(outDir).sort()).toEqual(['SHA256SUMS', sidecarName, zipName].sort());
        const archive = readFileSync(join(outDir, zipName));
        const sidecar = readFileSync(join(outDir, sidecarName));
        expect(readFileSync(join(outDir, 'SHA256SUMS'), 'utf8')).toBe(
            `${createHash('sha256').update(archive).digest('hex')}  ${zipName}\n` +
                `${createHash('sha256').update(sidecar).digest('hex')}  ${sidecarName}\n`,
        );
    });
});
