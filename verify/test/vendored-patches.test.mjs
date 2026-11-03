import assert from 'node:assert/strict';
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';

import { verifyVendoredPatches, VENDORED_BLUEST } from '../check-vendored-patches.mjs';

const repository = new URL('../../', import.meta.url).pathname;

async function copy() {
  const directory = await mkdtemp(join(tmpdir(), 'veles-vendored-'));
  const target = join(directory, VENDORED_BLUEST.directory);
  await cp(join(repository, VENDORED_BLUEST.directory), target, { recursive: true });
  await cp(join(repository, 'native-bridge', 'host', 'Cargo.toml'), join(directory, 'native-bridge', 'host', 'Cargo.toml'));
  await cp(join(repository, 'native-bridge', 'host', 'Cargo.lock'), join(directory, 'native-bridge', 'host', 'Cargo.lock'));
  await cp(join(repository, 'native-bridge', 'feasibility', 'Cargo.lock'), join(directory, 'native-bridge', 'feasibility', 'Cargo.lock'));
  return { directory, target };
}

test('accepts the repository vendored bluest: recorded hashes, reverse-applied pristine hashes, licence, lock', async () => {
  const report = await verifyVendoredPatches(repository);
  assert.match(report, /bluest 0\.6\.9/);
  assert.match(report, /BSD-2-Clause OR Apache-2\.0/);
  assert.match(report, /upstream crate sha256 a0c911a4/);
});

test('rejects a patched source file that no longer matches the recorded hash', async () => {
  const { directory, target } = await copy();
  await writeFile(join(target, 'src', 'lib.rs'), `${await readFile(join(target, 'src', 'lib.rs'), 'utf8')}\n// tamper\n`);
  await assert.rejects(verifyVendoredPatches(directory), /src\/lib\.rs.*recorded patched hash/);
});

test('rejects a diff that does not reproduce the recorded pristine upstream hash', async () => {
  const { directory, target } = await copy();
  const diff = await readFile(join(target, 'VELES-PATCH.diff'), 'utf8');
  await writeFile(join(target, 'VELES-PATCH.diff'), diff.replace('let _guard = &guard;', 'let _guard = &guard; // altered'));
  await assert.rejects(verifyVendoredPatches(directory), /reverse-apply|pristine/);
});

test('rejects a vendored crate with a missing licence file or a build script', async () => {
  const missing = await copy();
  await rm(join(missing.target, 'LICENSE-BSD'));
  await assert.rejects(verifyVendoredPatches(missing.directory), /LICENSE-BSD/);
  const script = await copy();
  const manifest = await readFile(join(script.target, 'Cargo.toml'), 'utf8');
  await writeFile(join(script.target, 'Cargo.toml'), manifest.replace('build = false', 'build = "build.rs"'));
  await assert.rejects(verifyVendoredPatches(script.directory), /build script/);
});

test('rejects tampering with an ordinary unpatched source file', async () => {
  const { directory, target } = await copy();
  const file = join(target, 'src', 'corebluetooth', 'device.rs');
  await writeFile(file, `${await readFile(file, 'utf8')}\n// not upstream\n`);
  await assert.rejects(verifyVendoredPatches(directory), /src\/corebluetooth\/device\.rs.*(manifest|reviewed)/);
});

test('rejects an added non-build file anywhere in the vendored tree', async () => {
  for (const relative of ['src/extra.rs', 'src/corebluetooth/shim.rs', 'payload.bin', 'scripts/run.sh']) {
    const { directory, target } = await copy();
    await mkdir(dirname(join(target, relative)), { recursive: true });
    await writeFile(join(target, relative), 'x\n');
    await assert.rejects(verifyVendoredPatches(directory), new RegExp(`unexpected.*${relative.replace('.', '\\.')}`), relative);
  }
});

test('rejects a deleted vendored file and a tampered manifest entry', async () => {
  const deleted = await copy();
  await rm(join(deleted.target, 'src', 'util.rs'));
  await assert.rejects(verifyVendoredPatches(deleted.directory), /src\/util\.rs.*missing/);
  const manifest = await copy();
  const path = join(manifest.target, 'VELES-MANIFEST.sha256');
  const text = await readFile(path, 'utf8');
  await writeFile(path, text.replace(/^[0-9a-f]{64}(  src\/util\.rs)$/m, `${'0'.repeat(64)}$1`));
  await assert.rejects(verifyVendoredPatches(manifest.directory), /src\/util\.rs/);
});

test('manifest covers every vendored file except the explicit provenance-metadata exclusions', async () => {
  const text = await readFile(join(repository, VENDORED_BLUEST.directory, 'VELES-MANIFEST.sha256'), 'utf8');
  const listed = text.trim().split('\n').map((line) => line.slice(66));
  assert.deepEqual(VENDORED_BLUEST.excluded.slice().sort(), ['VELES-MANIFEST.sha256', 'VELES-PATCH.diff', 'VELES-PATCH.md']);
  for (const excluded of VENDORED_BLUEST.excluded) assert.ok(!listed.includes(excluded));
  assert.ok(listed.includes('src/util.rs') && listed.includes('Cargo.toml') && listed.includes('src/lib.rs'));
  assert.equal(new Set(listed).size, listed.length);
});

test('rejects a host lockfile that pins bluest to a registry source instead of the vendored path', async () => {
  const { directory } = await copy();
  const lock = join(directory, 'native-bridge', 'host', 'Cargo.lock');
  const text = await readFile(lock, 'utf8');
  await writeFile(lock, text.replace('name = "bluest"\nversion = "0.6.9"\n', 'name = "bluest"\nversion = "0.6.9"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\n'));
  await assert.rejects(verifyVendoredPatches(directory), /path-vendored|source/);
});
