import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

import { error, mismatch } from './lib/checksum-manifest.mjs';

// Provenance gate for the vendored, patched bluest 0.6.9 that the OTP-24 production host links on macOS
// (native-bridge/host/Cargo.toml `[patch.crates-io]`). A path-vendored crate has no registry checksum,
// so the lockfile alone cannot prove it is the reviewed crate. This check proves, offline:
//   1. the two patched files match the hashes recorded in VELES-PATCH.md;
//   2. reverse-applying the recorded VELES-PATCH.diff reproduces the recorded pristine upstream hashes;
//   3. the recorded upstream crates.io checksum equals the checksum the reviewed feasibility lockfile pins;
//   4. the licence expression is unchanged and both licence texts ship with the crate;
//   5. the crate has no build script (no build.rs, `build = false`);
//   6. the host manifest patches bluest from vendor/bluest and the host lockfile treats bluest as a
//      source-less path package (so a registry/git substitution cannot go unnoticed).
export const VENDORED_BLUEST = {
  directory: join('native-bridge', 'host', 'vendor', 'bluest'),
  version: '0.6.9',
  license: 'BSD-2-Clause OR Apache-2.0',
  licenseFiles: ['LICENSE-APACHE', 'LICENSE-BSD'],
  patchedFiles: ['src/lib.rs', 'src/corebluetooth/characteristic.rs'],
  // Provenance metadata that is not part of the reviewed upstream tree. Everything else under the
  // directory must be listed in VELES-MANIFEST.sha256 with its exact hash (and nothing else may exist).
  manifest: 'VELES-MANIFEST.sha256',
  excluded: ['VELES-MANIFEST.sha256', 'VELES-PATCH.diff', 'VELES-PATCH.md'],
};

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

// Complete reviewed file-and-hash manifest: every regular file in the vendored crate (except the named
// provenance metadata) must be listed with its exact sha256, and no unlisted or missing file is allowed.
// The listed hashes of the unpatched files were compared with the crates.io bluest-0.6.9 sources when the
// manifest was generated (docs/reproducible-builds.md), so any edit, addition or deletion fails here.
async function verifyTreeManifest(vendor, patchedRows) {
  const text = await readFile(join(vendor, VENDORED_BLUEST.manifest), 'utf8').catch(() => { throw mismatch(`${VENDORED_BLUEST.directory}/${VENDORED_BLUEST.manifest} is missing`); });
  const expected = new Map();
  for (const line of text.split('\n').filter(Boolean)) {
    const match = line.match(/^([0-9a-f]{64})  (\S.*)$/);
    if (!match || expected.has(match[2]) || VENDORED_BLUEST.excluded.includes(match[2])) throw mismatch(`invalid or duplicate ${VENDORED_BLUEST.manifest} entry: ${line}`);
    expected.set(match[2], match[1]);
  }
  const actual = new Map();
  for (const entry of await readdir(vendor, { recursive: true, withFileTypes: true })) {
    const relative = join(entry.parentPath, entry.name).slice(vendor.length + 1).split('\\').join('/');
    if (entry.isDirectory()) continue;
    if (!entry.isFile()) throw mismatch(`unexpected non-regular vendored entry ${relative}`);
    if (VENDORED_BLUEST.excluded.includes(relative)) continue;
    actual.set(relative, sha256(await readFile(join(vendor, relative))));
  }
  for (const path of [...actual.keys()].sort()) {
    if (!expected.has(path)) throw mismatch(`unexpected vendored file ${path} is not in the reviewed ${VENDORED_BLUEST.manifest}`);
  }
  for (const [path, hash] of [...expected].sort()) {
    if (!actual.has(path)) throw mismatch(`reviewed vendored file ${path} is missing`);
    if (actual.get(path) !== hash) throw mismatch(`vendored file ${path} does not match the reviewed ${VENDORED_BLUEST.manifest}`);
  }
  for (const row of patchedRows) {
    if (expected.get(row.path) !== row.patched) throw mismatch(`${VENDORED_BLUEST.manifest} entry for ${row.path} disagrees with VELES-PATCH.md`);
  }
  return expected.size;
}

export async function verifyVendoredPatches(repository) {
  const vendor = join(repository, VENDORED_BLUEST.directory);
  const notes = await readFile(join(vendor, 'VELES-PATCH.md'), 'utf8').catch(() => { throw mismatch(`${VENDORED_BLUEST.directory}/VELES-PATCH.md is missing`); });
  const rows = [...notes.matchAll(/`(src\/[\w/.]+)` \| `([0-9a-f]{64})` \| `([0-9a-f]{64})`/g)].map(([, path, pristine, patched]) => ({ path, pristine, patched }));
  if (rows.map((row) => row.path).sort().join() !== [...VENDORED_BLUEST.patchedFiles].sort().join()) {
    throw mismatch(`VELES-PATCH.md must record exactly ${VENDORED_BLUEST.patchedFiles.join(', ')}`);
  }
  for (const row of rows) {
    if (sha256(await readFile(join(vendor, row.path))) !== row.patched) throw mismatch(`${row.path} does not match its recorded patched hash`);
  }

  const work = await mkdtemp(join(tmpdir(), 'veles-bluest-'));
  try {
    for (const row of rows) {
      await mkdir(dirname(join(work, row.path)), { recursive: true });
      await writeFile(join(work, row.path), await readFile(join(vendor, row.path)));
    }
    const applied = spawnSync('git', ['apply', '-R', join(vendor, 'VELES-PATCH.diff')], { cwd: work, encoding: 'utf8' });
    if (applied.error) throw error(`cannot run git: ${applied.error.message}`);
    if (applied.status !== 0) throw mismatch(`VELES-PATCH.diff does not reverse-apply to the vendored sources: ${applied.stderr.trim()}`);
    for (const row of rows) {
      if (sha256(await readFile(join(work, row.path))) !== row.pristine) {
        throw mismatch(`reverse-applying VELES-PATCH.diff does not reproduce the recorded pristine hash of ${row.path}`);
      }
    }
  } finally {
    await rm(work, { recursive: true, force: true });
  }

  const upstream = notes.match(/upstream crate sha256 `([0-9a-f]{64})`/)?.[1];
  const feasibilityLock = await readFile(join(repository, 'native-bridge', 'feasibility', 'Cargo.lock'), 'utf8');
  const locked = feasibilityLock.match(/name = "bluest"\nversion = "0\.6\.9"\nsource = "registry[^"]*"\nchecksum = "([0-9a-f]{64})"/)?.[1];
  if (!upstream || !locked || upstream !== locked) throw mismatch('VELES-PATCH.md upstream crate checksum does not match the reviewed lockfile checksum for bluest 0.6.9');

  const manifest = await readFile(join(vendor, 'Cargo.toml'), 'utf8');
  if (!manifest.includes(`license = "${VENDORED_BLUEST.license}"`)) throw mismatch(`vendored bluest licence expression is not "${VENDORED_BLUEST.license}"`);
  if (!new RegExp(`^version = "${VENDORED_BLUEST.version.replaceAll('.', '\\.')}"$`, 'm').test(manifest)) throw mismatch(`vendored bluest is not version ${VENDORED_BLUEST.version}`);
  for (const file of VENDORED_BLUEST.licenseFiles) {
    if (!(await readFile(join(vendor, file)).then((bytes) => bytes.length > 0, () => false))) throw mismatch(`vendored bluest is missing licence file ${file}`);
  }
  const hasBuildScript = (await readdir(vendor, { recursive: true })).some((entry) => entry === 'build.rs' || entry.endsWith('/build.rs'));
  if (hasBuildScript || /^build\s*=\s*(?!false\b)\S/m.test(manifest)) throw mismatch('vendored bluest must not carry a build script (build.rs / build = ...)');

  await verifyTreeManifest(vendor, rows);

  const hostManifest = await readFile(join(repository, 'native-bridge', 'host', 'Cargo.toml'), 'utf8');
  if (!hostManifest.includes('bluest = { path = "vendor/bluest" }')) throw mismatch('native-bridge/host/Cargo.toml does not patch bluest from vendor/bluest');
  const hostLock = await readFile(join(repository, 'native-bridge', 'host', 'Cargo.lock'), 'utf8');
  const block = hostLock.split('[[package]]').find((entry) => /^\nname = "bluest"\nversion = "0\.6\.9"\n/.test(entry));
  if (!block) throw mismatch('native-bridge/host/Cargo.lock does not lock bluest 0.6.9');
  if (/^(?:source|checksum) = /m.test(block)) throw mismatch('native-bridge/host/Cargo.lock bluest must be the path-vendored package (it carries a registry/git source or checksum)');

  return `bluest ${VENDORED_BLUEST.version} vendored at ${VENDORED_BLUEST.directory}\nlicense ${VENDORED_BLUEST.license}\nupstream crate sha256 ${upstream}\n${rows.map((row) => `${row.path} pristine ${row.pristine} patched ${row.patched}`).join('\n')}\n`;
}

async function main() {
  const repository = process.cwd();
  const report = await verifyVendoredPatches(repository);
  const reports = join(repository, 'build', 'verification', 'supply-chain');
  await mkdir(reports, { recursive: true });
  await writeFile(join(reports, 'vendored-patches.txt'), report);
}

if (import.meta.main) main().catch((caught) => { console.error(caught.message); process.exitCode = caught.exitCode ?? 2; });
