// Behavior test for verify/native/provision-windows-tools.ps1's archive-root
// mapping and required-file completeness.
//
// The provisioner is PowerShell and pwsh is not available on this Linux
// verifier host, so this test cannot execute the script directly. Instead it
// replicates the provisioner's Resolve-ArchiveSource mapping in JS and drives
// it against archive-realistic fixture layouts on disk (flat WiX, nsis-3.08-
// prefixed NSIS) to prove the mapping produces the expected WixTools314/ and
// NSIS/ cache roots with every required file present and no extras. The
// authoritative PowerShell source is additionally verified by inspection in
// the fix report and by the structural assertions in
// native-environment-contract.test.mjs.
//
// The required-file set is parsed from the provisioner script itself so the
// test stays in lockstep with the script's $requiredFiles array.

import assert from 'node:assert/strict';
import { existsSync, readdirSync, statSync, readFileSync } from 'node:fs';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const scriptDir = fileURLToPath(new URL('../native/', import.meta.url));
const provisionerPath = join(scriptDir, 'provision-windows-tools.ps1');

// Parse the $requiredFiles array literal out of the provisioner script.
function requiredFilesFromScript(source) {
  const start = source.indexOf('$requiredFiles = @(');
  assert.notEqual(start, -1, 'could not find $requiredFiles = @(');
  let depth = 0;
  let end = -1;
  for (let i = start + '$requiredFiles = @('.length; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === '(') depth += 1;
    else if (ch === ')') {
      if (depth === 0) { end = i; break; }
      depth -= 1;
    }
  }
  assert.notEqual(end, -1, 'could not find closing ) of $requiredFiles array');
  const body = source.slice(start, end);
  const matches = [...body.matchAll(/'([^']+)'/g)];
  return matches.map((m) => m[1]);
}

// Replicate the provisioner's Resolve-ArchiveSource mapping (PowerShell):
//   WixTools314/<rest> -> <unpacked>/<rest>             (flat archive)
//   NSIS/<rest>        -> <unpacked>/nsis-3.08/<rest>   (nsis-3.08-prefixed archive)
function resolveArchiveSource(unpacked, cachePath) {
  if (cachePath.startsWith('WixTools314/')) {
    return join(unpacked, cachePath.slice('WixTools314/'.length));
  }
  if (cachePath.startsWith('NSIS/')) {
    return join(unpacked, 'nsis-3.08', cachePath.slice('NSIS/'.length));
  }
  throw new Error(`unexpected required cache path root: ${cachePath}`);
}

// The set of required files that come from the separately-downloaded
// nsis_tauri_utils plugin (not from either archive).
const PLUGIN_FILES = new Set(
  ['NSIS/Plugins/x86-unicode/nsis_tauri_utils.dll', 'NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll'],
);

// Build an archive-realistic unpacked tree from a fixture spec. The spec maps
// archive-relative paths to file content; we materialize flat WiX entries at
// <unpacked>/<name> and nsis-3.08-prefixed NSIS entries at
// <unpacked>/nsis-3.08/<name>, mirroring what Expand-Archive would produce.
async function buildUnpacked(root, wixEntries, nsisEntries) {
  const unpacked = join(root, 'unpacked');
  await mkdir(join(unpacked, 'nsis-3.08'), { recursive: true });
  for (const [rel, content] of Object.entries(wixEntries)) {
    const path = join(unpacked, rel);
    await mkdir(join(path, '..'), { recursive: true });
    await writeFile(path, content);
  }
  for (const [rel, content] of Object.entries(nsisEntries)) {
    const path = join(unpacked, 'nsis-3.08', rel);
    await mkdir(join(path, '..'), { recursive: true });
    await writeFile(path, content);
  }
  return unpacked;
}

// Recursively collect regular-file relative paths under a directory, using
// forward slashes, sorted lexicographically by raw byte comparison.
function collectFiles(dir, prefix = '') {
  const out = [];
  for (const name of readdirSync(join(dir, prefix))) {
    const rel = prefix ? `${prefix}/${name}` : name;
    const st = statSync(join(dir, rel));
    if (st.isDirectory()) out.push(...collectFiles(dir, rel));
    else out.push(rel);
  }
  return out.sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
}

// Simulate the provisioner's copy loop: for every required file, resolve the
// archive source and copy it into the cache stage at the expected cache path.
// Plugin files (nsis_tauri_utils.dll) come from a separate plugin fixture.
async function simulateProvision(root, requiredFiles, wixEntries, nsisEntries, pluginContent) {
  const unpacked = await buildUnpacked(root, wixEntries, nsisEntries);
  const plugin = join(root, 'nsis_tauri_utils.dll');
  await writeFile(plugin, pluginContent);
  const cacheStage = join(root, 'cache');
  await mkdir(cacheStage, { recursive: true });
  for (const path of requiredFiles) {
    const source = PLUGIN_FILES.has(path) ? plugin : resolveArchiveSource(unpacked, path);
    if (!existsSync(source) || !statSync(source).isFile()) {
      throw new Error(`missing Tauri archive file: ${path} (looked at ${source})`);
    }
    const destination = join(cacheStage, path);
    await mkdir(join(destination, '..'), { recursive: true });
    await writeFile(destination, readFileSync(source));
  }
  return cacheStage;
}

// The fixture content for every required WiX file (flat after extraction).
function wixFixture() {
  const files = [
    'candle.exe', 'candle.exe.config', 'darice.cub', 'light.exe', 'light.exe.config',
    'wconsole.dll', 'winterop.dll', 'wix.dll', 'WixUIExtension.dll', 'WixUtilExtension.dll',
  ];
  const out = {};
  for (const name of files) out[name] = `wix:${name}`;
  return out;
}

// The fixture content for every required NSIS file, expressed as archive
// entries under nsis-3.08/ (mirroring the real nsis-3.zip layout).
function nsisFixture() {
  const files = [
    'makensis.exe', 'Bin/makensis.exe',
    'Stubs/lzma-x86-unicode', 'Stubs/lzma_solid-x86-unicode',
    'Plugins/x86-unicode/nsDialogs.dll', 'Plugins/x86-unicode/System.dll',
    'Include/MUI2.nsh', 'Include/FileFunc.nsh', 'Include/x64.nsh',
    'Include/nsDialogs.nsh', 'Include/WinMessages.nsh', 'Include/WordFunc.nsh',
    'Include/StrFunc.nsh', 'Include/Win/COM.nsh', 'Include/Win/Propkey.nsh',
    'Include/LogicLib.nsh', 'Include/LangFile.nsh', 'Include/Sections.nsh', 'Include/Util.nsh',
    'Contrib/Modern UI 2/MUI2.nsh',
    'Contrib/Modern UI 2/Deprecated.nsh',
    'Contrib/Modern UI 2/Interface.nsh',
    'Contrib/Modern UI 2/Localization.nsh',
    'Contrib/Modern UI 2/Pages.nsh',
    'Contrib/Modern UI 2/Pages/Components.nsh',
    'Contrib/Modern UI 2/Pages/Directory.nsh',
    'Contrib/Modern UI 2/Pages/Finish.nsh',
    'Contrib/Modern UI 2/Pages/InstallFiles.nsh',
    'Contrib/Modern UI 2/Pages/License.nsh',
    'Contrib/Modern UI 2/Pages/StartMenu.nsh',
    'Contrib/Modern UI 2/Pages/UninstallConfirm.nsh',
    'Contrib/Modern UI 2/Pages/Welcome.nsh',
  ];
  const out = {};
  for (const name of files) out[name] = `nsis:${name}`;
  return out;
}

const source = readFileSync(provisionerPath, 'utf8');
const requiredFiles = requiredFilesFromScript(source);

test('provisioner maps the flat WiX archive into the WixTools314/ cache root', () => {
  // wix314-binaries.zip extracts flat: candle.exe at the archive root.
  assert.equal(
    resolveArchiveSource('/unpacked', 'WixTools314/candle.exe'),
    join('/unpacked', 'candle.exe'),
  );
  assert.equal(
    resolveArchiveSource('/unpacked', 'WixTools314/WixUIExtension.dll'),
    join('/unpacked', 'WixUIExtension.dll'),
  );
});

test('provisioner maps the nsis-3.08-prefixed NSIS archive into the NSIS/ cache root', () => {
  // nsis-3.zip extracts under nsis-3.08/: makensis.exe at nsis-3.08/makensis.exe.
  assert.equal(
    resolveArchiveSource('/unpacked', 'NSIS/makensis.exe'),
    join('/unpacked', 'nsis-3.08', 'makensis.exe'),
  );
  assert.equal(
    resolveArchiveSource('/unpacked', 'NSIS/Contrib/Modern UI 2/MUI2.nsh'),
    join('/unpacked', 'nsis-3.08', 'Contrib', 'Modern UI 2', 'MUI2.nsh'),
  );
  assert.equal(
    resolveArchiveSource('/unpacked', 'NSIS/Include/Win/COM.nsh'),
    join('/unpacked', 'nsis-3.08', 'Include', 'Win', 'COM.nsh'),
  );
});

test('provisioner produces the exact expected cache from archive-realistic fixtures', async () => {
  const root = await mkdtemp(join(tmpdir(), 'veles-provision-'));
  try {
    const cacheStage = await simulateProvision(
      root, requiredFiles, wixFixture(), nsisFixture(), 'plugin:nsis_tauri_utils',
    );
    const actual = collectFiles(cacheStage);
    const expected = [...requiredFiles].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
    assert.deepEqual(actual, expected, 'cache stage must contain exactly the required files, no more, no less');
    // Spot-check that content was copied through correctly from each archive root.
    assert.equal(readFileSync(join(cacheStage, 'WixTools314/candle.exe'), 'utf8'), 'wix:candle.exe');
    assert.equal(readFileSync(join(cacheStage, 'NSIS/makensis.exe'), 'utf8'), 'nsis:makensis.exe');
    assert.equal(
      readFileSync(join(cacheStage, 'NSIS/Contrib/Modern UI 2/MUI2.nsh'), 'utf8'),
      'nsis:Contrib/Modern UI 2/MUI2.nsh',
    );
    assert.equal(
      readFileSync(join(cacheStage, 'NSIS/Plugins/x86-unicode/nsis_tauri_utils.dll'), 'utf8'),
      'plugin:nsis_tauri_utils',
    );
    assert.equal(
      readFileSync(join(cacheStage, 'NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll'), 'utf8'),
      'plugin:nsis_tauri_utils',
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('provisioner required-file set includes every NSIS header/plugin MUI2 transitively pulls', () => {
  // installer.nsi unconditional !includes:
  for (const h of ['MUI2.nsh', 'FileFunc.nsh', 'x64.nsh', 'WordFunc.nsh', 'StrFunc.nsh', 'Win/COM.nsh', 'Win/Propkey.nsh']) {
    assert.ok(requiredFiles.includes(`NSIS/Include/${h}`), `missing installer.nsi !include: NSIS/Include/${h}`);
  }
  // MUI2.nsh -> Contrib\Modern UI 2\MUI2.nsh pulls LogicLib/LangFile/nsDialogs + the tree:
  for (const h of ['LogicLib.nsh', 'LangFile.nsh', 'Sections.nsh', 'Util.nsh']) {
    assert.ok(requiredFiles.includes(`NSIS/Include/${h}`), `missing MUI2-pulled header: NSIS/Include/${h}`);
  }
  for (const h of [
    'Contrib/Modern UI 2/MUI2.nsh', 'Contrib/Modern UI 2/Deprecated.nsh',
    'Contrib/Modern UI 2/Interface.nsh', 'Contrib/Modern UI 2/Localization.nsh',
    'Contrib/Modern UI 2/Pages.nsh', 'Contrib/Modern UI 2/Pages/Components.nsh',
    'Contrib/Modern UI 2/Pages/Directory.nsh', 'Contrib/Modern UI 2/Pages/Finish.nsh',
    'Contrib/Modern UI 2/Pages/InstallFiles.nsh', 'Contrib/Modern UI 2/Pages/License.nsh',
    'Contrib/Modern UI 2/Pages/StartMenu.nsh', 'Contrib/Modern UI 2/Pages/UninstallConfirm.nsh',
    'Contrib/Modern UI 2/Pages/Welcome.nsh',
  ]) {
    assert.ok(requiredFiles.includes(`NSIS/${h}`), `missing MUI2 Contrib tree entry: NSIS/${h}`);
  }
  // Plugin DLLs used by utils.nsh / FileAssociation.nsh:
  for (const p of ['nsDialogs.dll', 'System.dll', 'nsis_tauri_utils.dll']) {
    assert.ok(
      requiredFiles.includes(`NSIS/Plugins/x86-unicode/${p}`),
      `missing plugin: NSIS/Plugins/x86-unicode/${p}`,
    );
  }
  assert.ok(requiredFiles.includes('NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll'));
  // MultiUser.nsh is NOT required (currentUser mode is the default).
  assert.ok(!requiredFiles.includes('NSIS/Include/MultiUser.nsh'), 'MultiUser.nsh must not be required under currentUser mode');
});

test('provisioner rejects a missing required file from the archive layout', async () => {
  const root = await mkdtemp(join(tmpdir(), 'veles-provision-missing-'));
  try {
    const incompleteNsis = nsisFixture();
    delete incompleteNsis['Contrib/Modern UI 2/MUI2.nsh']; // drop one MUI2 tree entry
    await assert.rejects(
      simulateProvision(root, requiredFiles, wixFixture(), incompleteNsis, 'plugin'),
      /missing Tauri archive file: NSIS\/Contrib\/Modern UI 2\/MUI2\.nsh/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});