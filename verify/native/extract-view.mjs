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
// by bridgePackage). This script assembles the view tree from the Tauri bundle
// build outputs under src-tauri/target/release/ so the transport carries the
// raw installer/host evidence the comparison job and aggregate rely on.
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
// The deterministic outer package and its sidecar (from product-dir) are also
// placed at the view root so the view is self-contained evidence of both the
// raw build and the final packaged artifact.

import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

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

function buildWindowsView(releaseDir, productDir, viewDir) {
  const binaryPath = join(releaseDir, WINDOWS_BINARY);
  requireFile(binaryPath, 'Windows host binary');
  const nsis = singleMatch(join(releaseDir, 'bundle', 'nsis'), '-setup.exe', 'NSIS setup');
  const msi = singleMatch(join(releaseDir, 'bundle', 'msi'), '.msi', 'WiX msi');
  // The host manifest JSON is produced by bridgePackage into the product dir
  // alongside the package; copy it from there (it is the same in-memory
  // manifest archived into the package).
  const manifestName = findManifest(productDir);
  rmSync(viewDir, { recursive: true, force: true });
  mkdirSync(viewDir, { recursive: true });
  copyEntry(binaryPath, join(viewDir, WINDOWS_BINARY));
  mkdirSync(join(viewDir, 'bundle', 'nsis'), { recursive: true });
  copyEntry(nsis.abs, join(viewDir, 'bundle', 'nsis', nsis.name));
  mkdirSync(join(viewDir, 'bundle', 'msi'), { recursive: true });
  copyEntry(msi.abs, join(viewDir, 'bundle', 'msi', msi.name));
  copyEntry(join(productDir, manifestName), join(viewDir, manifestName));
  copyPackageAndSidecar(productDir, viewDir);
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
  const manifestName = findManifest(productDir);
  rmSync(viewDir, { recursive: true, force: true });
  mkdirSync(viewDir, { recursive: true });
  // Archive the .app under its own name so extraction reproduces the bundle.
  copyEntry(appBundlePath, join(viewDir, MACOS_APP_BUNDLE));
  mkdirSync(join(viewDir, 'bundle', 'dmg'), { recursive: true });
  copyEntry(dmg.abs, join(viewDir, 'bundle', 'dmg', dmg.name));
  copyEntry(join(productDir, manifestName), join(viewDir, manifestName));
  copyPackageAndSidecar(productDir, viewDir);
}

function findManifest(productDir) {
  // The Chrome native-messaging host manifest JSON is emitted by bridgePackage
  // as <manifest.name>.json (manifest.mjs buildHostManifest -> name). Locate
  // the single .json file that is NOT the package sidecar.
  const candidates = readdirSync(productDir).filter((n) => n.endsWith('.json'));
  if (candidates.length !== 1) {
    fail(`expected exactly one host manifest .json in product dir ${productDir}, found: ${candidates.join(', ') || 'none'}`);
  }
  return candidates[0];
}

function copyPackageAndSidecar(productDir, viewDir) {
  // The deterministic outer package and its sidecar live in the product dir.
  // Copy both into the view so the view is self-contained evidence of the
  // final packaged artifact as well as the raw build.
  const pkg = readdirSync(productDir).find((n) => n.endsWith('.zip') || n.endsWith('.tar.gz'));
  if (!pkg) fail(`no deterministic outer package found in ${productDir}`);
  copyEntry(join(productDir, pkg), join(viewDir, pkg));
  const sidecar = `${pkg}.sha256`;
  if (existsSync(join(productDir, sidecar))) {
    copyEntry(join(productDir, sidecar), join(viewDir, sidecar));
  }
  if (existsSync(join(productDir, 'SHA256SUMS'))) {
    copyEntry(join(productDir, 'SHA256SUMS'), join(viewDir, 'SHA256SUMS'));
  }
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