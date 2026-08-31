import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile, symlink, chmod } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const scriptDir = fileURLToPath(new URL('../native/', import.meta.url));
const extractView = join(scriptDir, 'extract-view.mjs');

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
  const result = spawnSync('node', [extractView, platform, release, product, view], { encoding: 'utf8' });
  return result;
}

test('extract-view assembles the Windows verification view from real bundle outputs', async () => {
  await withTree(async ({ root, release, product, view }) => {
    // Raw host binary at release root.
    await writeFile(join(release, 'veles-native-bridge.exe'), 'host-binary');
    // NSIS + MSI installer outputs.
    await mkdir(join(release, 'bundle', 'nsis'), { recursive: true });
    await mkdir(join(release, 'bundle', 'msi'), { recursive: true });
    await writeFile(join(release, 'bundle', 'nsis', 'Veles Native Bridge_0.1.0_x64-setup.exe'), 'nsis');
    await writeFile(join(release, 'bundle', 'msi', 'Veles Native Bridge_0.1.0_x64_en-US.msi'), 'msi');
    // Product dir: package + sidecar + SHA256SUMS + host manifest JSON.
    await writeFile(join(product, 'veles-native-bridge-0.1.0.zip'), 'package');
    await writeFile(join(product, 'veles-native-bridge-0.1.0.zip.sha256'), 'sidecar');
    await writeFile(join(product, 'SHA256SUMS'), 'sums');
    await writeFile(join(product, 'app.veles.native-bridge.json'), '{}');

    const result = runExtract('windows', release, product, view);
    assert.equal(result.status, 0, result.stderr);
    // View contains the raw host binary, installer outputs, manifest, package,
    // sidecar, and SHA256SUMS — the full extracted evidence set.
    const entries = (await collectFiles(view)).sort();
    assert.ok(entries.some((p) => p.endsWith('-setup.exe')), `view must contain the NSIS setup exe: ${entries}`);
    assert.ok(entries.some((p) => p.endsWith('.msi')), `view must contain the MSI: ${entries}`);
    assert.ok(entries.includes('veles-native-bridge.exe'));
    assert.ok(entries.includes('veles-native-bridge-0.1.0.zip'));
    assert.ok(entries.includes('veles-native-bridge-0.1.0.zip.sha256'));
    assert.ok(entries.includes('SHA256SUMS'));
    assert.ok(entries.includes('app.veles.native-bridge.json'));
  });
});

test('extract-view assembles the macOS verification view preserving .app symlinks', async () => {
  await withTree(async ({ root, release, product, view }) => {
    const appBundle = join(release, 'bundle', 'macos', 'Veles Native Bridge.app');
    await mkdir(join(appBundle, 'Contents', 'MacOS'), { recursive: true });
    await writeFile(join(appBundle, 'Contents', 'MacOS', 'veles-native-bridge'), 'host', { mode: 0o755 });
    await chmod(join(appBundle, 'Contents', 'MacOS', 'veles-native-bridge'), 0o755);
    // A symlink inside the .app (e.g. Frameworks current) must be preserved.
    await mkdir(join(appBundle, 'Contents', 'Frameworks'), { recursive: true });
    await symlink('VelesHelper.framework', join(appBundle, 'Contents', 'Frameworks', 'VelesHelper.framework'));
    await mkdir(join(release, 'bundle', 'dmg'), { recursive: true });
    await writeFile(join(release, 'bundle', 'dmg', 'Veles Native Bridge_0.1.0_x64.dmg'), 'dmg');
    await writeFile(join(product, 'veles-native-bridge-0.1.0.tar.gz'), 'package');
    await writeFile(join(product, 'veles-native-bridge-0.1.0.tar.gz.sha256'), 'sidecar');
    await writeFile(join(product, 'SHA256SUMS'), 'sums');
    await writeFile(join(product, 'app.veles.native-bridge.json'), '{}');

    const result = runExtract('macos', release, product, view);
    assert.equal(result.status, 0, result.stderr);
    const entries = (await collectFiles(view)).sort();
    assert.ok(entries.some((p) => p.endsWith('.dmg')));
    assert.ok(entries.some((p) => p.includes('Veles Native Bridge.app/Contents/MacOS/veles-native-bridge')));
    assert.ok(entries.includes('veles-native-bridge-0.1.0.tar.gz'));
    assert.ok(entries.includes('veles-native-bridge-0.1.0.tar.gz.sha256'));
    assert.ok(entries.includes('SHA256SUMS'));
    assert.ok(entries.includes('app.veles.native-bridge.json'));
  });
});

test('extract-view rejects a missing installer and exits 2', async () => {
  await withTree(async ({ root, release, product, view }) => {
    await writeFile(join(release, 'veles-native-bridge.exe'), 'host');
    // No bundle/nsis or bundle/msi dirs.
    await writeFile(join(product, 'veles-native-bridge-0.1.0.zip'), 'package');
    await writeFile(join(product, 'veles-native-bridge-0.1.0.zip.sha256'), 'sidecar');
    await writeFile(join(product, 'SHA256SUMS'), 'sums');
    await writeFile(join(product, 'app.veles.native-bridge.json'), '{}');
    const result = runExtract('windows', release, product, view);
    assert.equal(result.status, 2, result.stderr);
  });
});

test('extract-view rejects an unsupported platform and exits 2', async () => {
  await withTree(async ({ root, release, product, view }) => {
    const result = runExtract('linux', release, product, view);
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