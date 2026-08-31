// OTP-01 sub-project 1d — assemble the native-bridge extracted verification
// view from the Tauri bundle build outputs.
//
// The design (docs/superpowers/specs/2026-08-28-otp-01-1d-verification-supply-
// chain-design.md:228-241) requires each native run to create an extracted
// verification view containing:
//   - the raw host binary;
//   - the native-messaging host manifest;
//   - NSIS and MSI installer outputs on Windows;
//   - the .app tree, including executable modes and symlink targets, on macOS;
//   - the DMG on macOS;
//   - the deterministic outer package and sidecar; and
//   - the standard checksum manifest.
//
// `create-run.mjs` transports the product/ and view/ trees. The product tree
// is build/native-bridge/<platform>/ (package + sidecar + SHA256SUMS, produced
// by bridgePackage). The Chrome native-messaging host manifest JSON is emitted
// ONLY inside the deterministic archive (zip for Windows, tar.gz for macOS) by
// native-bridge/scripts/package.mjs; there is no standalone manifest file in
// the product dir. This script therefore reads the manifest out of the archive
// (zip via verify/native/zip-reader.mjs, tar.gz via verify/native/
// deterministic-tar.mjs + node:zlib gunzip) and writes it into the view as the
// design's "native-messaging host manifest" evidence entry.
//
// Usage:
//   node extract-view.mjs <platform> <release-dir> <product-dir> <view-dir>
//
//   platform    "windows" | "macos"
//   release-dir src-tauri/target/release (bundle root)
//   product-dir build/native-bridge/<platform> (package + sidecar + SHA256SUMS)
//   view-dir    destination view directory (created, replaced if present)
//
// The view mirrors the platform's raw build outputs:
//   windows: host.exe, bundle/nsis/<setup>.exe, bundle/msi/<msi>, <manifest>.json
//   macos:   bundle/macos/<app>/... (tree, preserving symlinks),
//            bundle/dmg/<dmg>, <manifest>.json
// Per the transport/aggregate contract alignment, the view contains ONLY raw
// product files (the installer/app files the design's view requires) plus the
// extracted host manifest. It does NOT copy the outer deterministic package,
// sidecar, or product SHA256SUMS — those live in product/ and are transported
// as product/ records; duplicating them into the view would let component
// manifests leak into the aggregate records.

import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';

import { parseTar } from './deterministic-tar.mjs';
import { readZipEntry, listZip } from './zip-reader.mjs';

const SUPPORTED = new Set(['windows', 'macos']);
const WINDOWS_BINARY = 'veles-native-bridge.exe';
const MACOS_BINARY = 'veles-native-bridge';
const MACOS_APP_BUNDLE = 'Veles Native Bridge.app';

function fail(reason) {
  console.error(`extract-view: ${reason}`);
  process.exit(2);
}

function requireDir(path, label) {
  if (!existsSync(path) || !statSync(path).isDirectory()) {
    fail(`${label} not found or not a directory: ${path}`);
  }
}

function requireFile(path, label) {
  if (!existsSync(path) || !statSync(path).isFile()) {
    fail(`${label} not found or not a file: ${path}`);
  }
}

function singleMatch(dir, suffix, label) {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) {
    fail(`${label} directory not found: ${dir}`);
  }
  const matches = readdirSync(dir).filter((n) => n.endsWith(suffix));
  if (matches.length === 0) fail(`${label} not found under ${dir} (no file ending in "${suffix}")`);
  if (matches.length > 1) fail(`multiple ${label} found under ${dir}: ${matches.join(', ')}`);
  const name = matches[0];
  const abs = join(dir, name);
  requireFile(abs, label);
  if (statSync(abs).size === 0) fail(`${label} is empty: ${abs}`);
  return { name, abs };
}

function copyEntry(src, dst) {
  // lstat-preserving copy: symlinks copied as symlinks (cpSync recursive with
  // verbatimSymlinks preserves link targets rather than dereferencing).
  cpSync(src, dst, { recursive: true, verbatimSymlinks: true });
}

// Locate the single deterministic outer package in the product dir and return
// its name + absolute path. Windows -> .zip, macOS -> .tar.gz.
function findPackage(productDir, platform) {
  const ext = platform === 'windows' ? '.zip' : '.tar.gz';
  const matches = readdirSync(productDir).filter((n) => n.endsWith(ext));
  if (matches.length === 0) fail(`no deterministic outer package (${ext}) found in ${productDir}`);
  if (matches.length > 1) fail(`multiple ${ext} packages found in ${productDir}: ${matches.join(', ')}`);
  const name = matches[0];
  return { name, abs: join(productDir, name) };
}

// Read the Chrome native-messaging host manifest JSON out of the deterministic
// archive. The producer (native-bridge/scripts/package.mjs) archives it as
// `${manifest.name}.json` (manifest.name = 'app.veles.native_bridge'); there is
// no standalone manifest file in the product dir. Returns the manifest's
// archive entry name and its decoded bytes.
function readManifestFromArchive(packageAbs, platform) {
  const bytes = readFileSync(packageAbs);
  if (platform === 'windows') {
    const names = listZip(bytes);
    const jsonNames = names.filter((n) => n.endsWith('.json') && !n.includes('/'));
    if (jsonNames.length !== 1) {
      fail(`expected exactly one host manifest .json at the zip root, found: ${jsonNames.join(', ') || 'none'}`);
    }
    const name = jsonNames[0];
    return { name, data: readZipEntry(bytes, name) };
  }
  // macOS: tar.gz. The deterministic-tar parser already validates the USTAR
  // structure; gunzip then parse.
  const tar = gunzipSync(bytes);
  const entries = parseTar(tar);
  const jsonEntries = entries.filter((e) => e.type === 'file' && e.path.endsWith('.json') && !e.path.includes('/'));
  if (jsonEntries.length !== 1) {
    fail(`expected exactly one host manifest .json at the tar root, found: ${jsonEntries.map((e) => e.path).join(', ') || 'none'}`);
  }
  const entry = jsonEntries[0];
  return { name: entry.path, data: Buffer.from(entry.data) };
}

function buildWindowsView(releaseDir, productDir, viewDir) {
  const binaryPath = join(releaseDir, WINDOWS_BINARY);
  requireFile(binaryPath, 'Windows host binary');
  const nsis = singleMatch(join(releaseDir, 'bundle', 'nsis'), '-setup.exe', 'NSIS setup');
  const msi = singleMatch(join(releaseDir, 'bundle', 'msi'), '.msi', 'WiX msi');
  const pkg = findPackage(productDir, 'windows');
  const manifest = readManifestFromArchive(pkg.abs, 'windows');
  rmSync(viewDir, { recursive: true, force: true });
  mkdirSync(viewDir, { recursive: true });
  copyEntry(binaryPath, join(viewDir, WINDOWS_BINARY));
  mkdirSync(join(viewDir, 'bundle', 'nsis'), { recursive: true });
  copyEntry(nsis.abs, join(viewDir, 'bundle', 'nsis', nsis.name));
  mkdirSync(join(viewDir, 'bundle', 'msi'), { recursive: true });
  copyEntry(msi.abs, join(viewDir, 'bundle', 'msi', msi.name));
  writeFileSync(join(viewDir, manifest.name), manifest.data);
}

function buildMacosView(releaseDir, productDir, viewDir) {
  const bundleMacosDir = join(releaseDir, 'bundle', 'macos');
  requireDir(bundleMacosDir, 'macOS bundle output');
  const entries = readdirSync(bundleMacosDir).filter((n) => n !== MACOS_APP_BUNDLE);
  if (entries.length > 0) fail(`unexpected entries alongside .app in ${bundleMacosDir}: ${entries.join(', ')}`);
  const appBundlePath = join(bundleMacosDir, MACOS_APP_BUNDLE);
  if (!existsSync(appBundlePath) || !statSync(appBundlePath).isDirectory()) {
    fail(`macOS .app bundle not found: ${appBundlePath}`);
  }
  const hostPath = join(appBundlePath, 'Contents', 'MacOS', MACOS_BINARY);
  requireFile(hostPath, 'macOS app host executable');
  const dmg = singleMatch(join(releaseDir, 'bundle', 'dmg'), '.dmg', 'macOS .dmg');
  const pkg = findPackage(productDir, 'macos');
  const manifest = readManifestFromArchive(pkg.abs, 'macos');
  rmSync(viewDir, { recursive: true, force: true });
  mkdirSync(viewDir, { recursive: true });
  // Archive the .app under its own name so extraction reproduces the bundle.
  copyEntry(appBundlePath, join(viewDir, MACOS_APP_BUNDLE));
  mkdirSync(join(viewDir, 'bundle', 'dmg'), { recursive: true });
  copyEntry(dmg.abs, join(viewDir, 'bundle', 'dmg', dmg.name));
  writeFileSync(join(viewDir, manifest.name), manifest.data);
}

function main() {
  const [platform, releaseDir, productDir, viewDir] = process.argv.slice(2);
  if (!platform || !releaseDir || !productDir || !viewDir || process.argv.length !== 6) {
    fail('usage: extract-view.mjs <platform> <release-dir> <product-dir> <view-dir>');
  }
  if (!SUPPORTED.has(platform)) fail(`unsupported platform: ${platform}`);
  const release = resolve(releaseDir);
  const product = resolve(productDir);
  const view = resolve(viewDir);
  requireDir(release, 'release directory');
  requireDir(product, 'product directory');
  if (platform === 'windows') buildWindowsView(release, product, view);
  else buildMacosView(release, product, view);
  console.log(view);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main();
}