import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile, symlink, chmod, lstat, readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const scriptDir = fileURLToPath(new URL('../native/', import.meta.url));
const extractView = join(scriptDir, 'extract-view.mjs');
const repoRoot = fileURLToPath(new URL('../../', import.meta.url));
const packageScript = join(repoRoot, 'native-bridge', 'scripts', 'package.mjs');
const HOST_MANIFEST_NAME = 'app.veles.native_bridge.json';

async function withTree(run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-extract-view-'));
  try {
    const release = join(root, 'release');
    const product = join(root, 'product');
    const view = join(root, 'view');
    await mkdir(release, { recursive: true });
    await mkdir(product, { recursive: true });
    await run({ root, release, product, view });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function runExtract(platform, release, product, view) {
  return spawnSync('node', [extractView, platform, release, product, view], { encoding: 'utf8' });
}

// Run the REAL producer (native-bridge/scripts/package.mjs) against a fake
// release dir to produce the real product layout: package + sidecar +
// SHA256SUMS, with the host manifest JSON archived INSIDE the package. This is
// the layout extract-view must consume — the manifest is NOT a standalone file
// in the product dir.
async function runProducer(root, platform, release, extraEnv = {}) {
  const env = {
    ...process.env,
    VELES_BRIDGE_PLATFORM: platform,
    VELES_BRIDGE_RELEASE_DIR: release,
    VELES_BRIDGE_BUILD_OUT_DIR: join(root, 'product'),
    ...extraEnv,
  };
  const result = spawnSync('node', [packageScript], { encoding: 'utf8', env });
  if (result.status !== 0) throw new Error(`producer failed: ${result.stderr}`);
  return join(root, 'product', platform);
}

async function buildWindowsRelease(release) {
  await writeFile(join(release, 'veles-native-bridge.exe'), 'host-binary');
  await mkdir(join(release, 'bundle', 'nsis'), { recursive: true });
  await mkdir(join(release, 'bundle', 'msi'), { recursive: true });
  await writeFile(join(release, 'bundle', 'nsis', 'Veles Native Bridge_0.1.0_x64-setup.exe'), 'nsis');
  await writeFile(join(release, 'bundle', 'msi', 'Veles Native Bridge_0.1.0_x64_en-US.msi'), 'msi');
}

async function buildMacosRelease(release) {
  const appBundle = join(release, 'bundle', 'macos', 'Veles Native Bridge.app');
  await mkdir(join(appBundle, 'Contents', 'MacOS'), { recursive: true });
  await mkdir(join(appBundle, 'Contents', 'Frameworks'), { recursive: true });
  await writeFile(join(appBundle, 'Contents', 'MacOS', 'veles-native-bridge'), 'host', { mode: 0o755 });
  await chmod(join(appBundle, 'Contents', 'MacOS', 'veles-native-bridge'), 0o755);
  // A symlink inside the .app must be preserved through the producer tar and
  // the extract-view copy.
  await symlink('VelesHelper.framework', join(appBundle, 'Contents', 'Frameworks', 'VelesHelper.framework'));
  await mkdir(join(release, 'bundle', 'dmg'), { recursive: true });
  await writeFile(join(release, 'bundle', 'dmg', 'Veles Native Bridge_0.1.0_x64.dmg'), 'dmg');
}

test('extract-view assembles the Windows verification view from a real producer package', async () => {
  await withTree(async ({ root, release, product, view }) => {
    await buildWindowsRelease(release);
    // Real producer: package contains the host manifest; product dir has only
    // package + sidecar + SHA256SUMS (no standalone manifest JSON).
    const productDir = await runProducer(root, 'windows', release);
    const result = runExtract('windows', release, productDir, view);
    assert.equal(result.status, 0, result.stderr);
    const entries = (await collectFiles(view)).sort();
    // The view contains the raw host binary, installer outputs, and the
    // manifest EXTRACTED FROM THE ARCHIVE. Per the transport/aggregate
    // contract it must NOT contain the outer package/sidecar/SHA256SUMS.
    assert.ok(entries.some((p) => p.endsWith('-setup.exe')), `view must contain the NSIS setup exe: ${entries}`);
    assert.ok(entries.some((p) => p.endsWith('.msi')), `view must contain the MSI: ${entries}`);
    assert.ok(entries.includes('veles-native-bridge.exe'));
    assert.ok(entries.includes(HOST_MANIFEST_NAME), `view must contain the manifest extracted from the archive: ${entries}`);
    assert.ok(!entries.some((p) => p.endsWith('.zip')), 'view must not duplicate the outer package');
    assert.ok(!entries.some((p) => p.endsWith('.zip.sha256')), 'view must not duplicate the outer sidecar');
    assert.ok(!entries.includes('SHA256SUMS'), 'view must not duplicate the product SHA256SUMS');
    // The manifest must be the actual JSON the producer archived (not empty).
    const manifest = JSON.parse(await readFile(join(view, HOST_MANIFEST_NAME), 'utf8'));
    assert.equal(manifest.name, 'app.veles.native_bridge');
    assert.equal(manifest.type, 'stdio');
  });
});

test('extract-view assembles the macOS verification view preserving .app symlinks from a real producer package', async () => {
  await withTree(async ({ root, release, product, view }) => {
    await buildMacosRelease(release);
    const productDir = await runProducer(root, 'macos', release, { VELES_BRIDGE_INSTALL_ROOT: '/Applications' });
    const result = runExtract('macos', release, productDir, view);
    assert.equal(result.status, 0, result.stderr);
    const entries = (await collectFiles(view)).sort();
    assert.ok(entries.some((p) => p.endsWith('.dmg')));
    assert.ok(entries.some((p) => p.includes('Veles Native Bridge.app/Contents/MacOS/veles-native-bridge')));
    assert.ok(entries.includes(HOST_MANIFEST_NAME), `view must contain the manifest extracted from the tar.gz: ${entries}`);
    assert.ok(!entries.some((p) => p.endsWith('.tar.gz')), 'view must not duplicate the outer package');
    assert.ok(!entries.some((p) => p.endsWith('.tar.gz.sha256')), 'view must not duplicate the outer sidecar');
    assert.ok(!entries.includes('SHA256SUMS'), 'view must not duplicate the product SHA256SUMS');
    // The symlink inside the .app must be preserved as a symlink (lstat does
    // not follow, so a dangling target is still reported as a symlink).
    const linkPath = join(view, 'Veles Native Bridge.app', 'Contents', 'Frameworks', 'VelesHelper.framework');
    const lst = await lstat(linkPath);
    assert.ok(lst.isSymbolicLink(), 'symlink inside .app must be preserved');
    const manifest = JSON.parse(await readFile(join(view, HOST_MANIFEST_NAME), 'utf8'));
    assert.equal(manifest.name, 'app.veles.native_bridge');
    // macOS manifest path is absolute (resolved install root).
    assert.ok(manifest.path.endsWith('Veles Native Bridge.app/Contents/MacOS/veles-native-bridge'), `unexpected macos manifest path: ${manifest.path}`);
  });
});

test('extract-view rejects a missing installer and exits 2', async () => {
  await withTree(async ({ root, release, product, view }) => {
    await writeFile(join(release, 'veles-native-bridge.exe'), 'host');
    await buildWindowsRelease(release);
    // Build the producer package so the product dir is real, then DELETE the
    // installer dirs so extract-view cannot find them.
    const productDir = await runProducer(root, 'windows', release);
    await rm(join(release, 'bundle'), { recursive: true, force: true });
    const result = runExtract('windows', release, productDir, view);
    assert.equal(result.status, 2, result.stderr);
  });
});

test('extract-view rejects an unsupported platform and exits 2', async () => {
  await withTree(async ({ root, release, product, view }) => {
    const result = runExtract('linux', release, product, view);
    assert.equal(result.status, 2, result.stderr);
  });
});

test('extract-view rejects a product dir with no deterministic package', async () => {
  await withTree(async ({ root, release, product, view }) => {
    await buildWindowsRelease(release);
    // Product dir is empty (no producer run) -> no package to read manifest from.
    const result = runExtract('windows', release, product, view);
    assert.equal(result.status, 2, result.stderr);
  });
});

async function collectFiles(dir, prefix = '') {
  const { readdirSync, lstatSync } = await import('node:fs');
  const out = [];
  for (const name of readdirSync(join(dir, prefix))) {
    const rel = prefix ? `${prefix}/${name}` : name;
    const st = lstatSync(join(dir, rel));
    if (st.isDirectory()) out.push(...(await collectFiles(dir, rel)));
    else out.push(rel); // regular files and symlinks (do not follow)
  }
  return out;
}