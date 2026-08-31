import { lstat, mkdir, readdir, readFile, readlink, writeFile } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createNativeMetadata } from '../lib/native-metadata.mjs';
import { createStandardManifest, error, mismatch, parseStandardManifest, sha256 } from '../lib/checksum-manifest.mjs';
import { createTar, parseTar } from './deterministic-tar.mjs';

const requiredNames = new Set(['SOURCE-COMMIT', 'SHA256SUMS.native-bridge', 'METADATA.native-bridge.jsonl']);

function identityText(identity, manifest) {
  if (!identity || ['ImageOS', 'ImageVersion', 'RUNNER_ARCH'].some((key) => typeof identity[key] !== 'string' || identity[key] === '')) {
    throw error('invalid native runner identity');
  }
  return `# ImageOS=${identity.ImageOS}\n# ImageVersion=${identity.ImageVersion}\n# RUNNER_ARCH=${identity.RUNNER_ARCH}\n${manifest}`;
}

async function treeEntries(root, prefix = '') {
  let entries;
  try {
    entries = await readdir(join(root, prefix), { withFileTypes: true });
  } catch {
    throw error(`cannot read run tree: ${prefix || '.'}`);
  }
  const result = [];
  for (const entry of entries.sort((left, right) => Buffer.compare(Buffer.from(left.name), Buffer.from(right.name)))) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    const stat = await lstat(join(root, path));
    if (stat.isFile()) {
      if (stat.nlink !== 1) throw mismatch(`hard link transport entry: ${path}`);
      result.push({ path, type: 'file', mode: stat.mode & 0o7777, data: await readFile(join(root, path)) });
    } else if (stat.isDirectory()) {
      result.push({ path: `${path}/`, type: 'directory', mode: stat.mode & 0o7777 });
      result.push(...await treeEntries(root, path));
    } else if (stat.isSymbolicLink()) {
      result.push({ path, type: 'symlink', mode: stat.mode & 0o7777, target: await readlink(join(root, path)) });
    } else {
      throw mismatch(`unsupported transport entry: ${path}`);
    }
  }
  return result;
}

function files(entries) {
  return entries.filter((entry) => entry.type === 'file').map((entry) => entry.path);
}

// The native product manifest (SHA256SUMS.native-bridge) covers ONLY the
// deterministic outer package and its sidecar. The producer also writes a
// product-level SHA256SUMS (a component checksum manifest listing the package
// and sidecar); the design excludes component checksum manifests from the
// transport manifest, but REQUIRES it to be present and valid (its records
// must correctly hash the package and sidecar that the transport carries).
// This returns the product file paths that the transport manifest must cover
// (package + sidecar only, excluding SHA256SUMS) and validates the component
// SHA256SUMS digests against the actual transported files.
async function productManifestFiles(root, productEntries) {
  const productFiles = files(productEntries);
  const manifestPaths = productFiles.filter((path) => path !== 'SHA256SUMS');
  if (!productFiles.includes('SHA256SUMS')) {
    throw mismatch('component SHA256SUMS is required in the product dir');
  }
  // Validate the component SHA256SUMS against the package + sidecar it
  // claims to cover. parseStandardManifest rejects malformed records; each
  // digest must match the corresponding transported file's bytes, and the
  // record path set must exactly match the transport product artifact set.
  const sumsText = (await readFile(join(root, 'SHA256SUMS'))).toString('utf8');
  const sums = parseStandardManifest(sumsText);
  const byPath = new Map(productEntries.filter((e) => e.type === 'file').map((e) => [e.path, e]));
  const sumsPaths = [...sums.keys()].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
  const manifestSorted = [...manifestPaths].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
  if (sumsPaths.length !== manifestSorted.length || sumsPaths.some((p, i) => p !== manifestSorted[i])) {
    throw mismatch('component SHA256SUMS path set must match the transport product artifact set');
  }
  for (const [path, digest] of sums) {
    const entry = byPath.get(path);
    if (!entry || sha256(entry.data) !== digest) {
      throw mismatch(`component SHA256SUMS mismatch: ${path}`);
    }
  }
  return manifestPaths;
}

export async function createRun({ output, sourceCommit, identity, product, view }) {
  if (!/^[0-9a-f]{40}$/.test(sourceCommit ?? '')) throw error('invalid source commit');
  const [productEntries, viewEntries] = await Promise.all([treeEntries(product), treeEntries(view)]);
  const manifestPaths = await productManifestFiles(product, productEntries);
  const [productManifest, metadata] = await Promise.all([
    createStandardManifest(product, manifestPaths),
    createNativeMetadata(view, viewEntries.map((entry) => entry.path.replace(/\/$/, ''))),
  ]);
  const entries = [
    { path: 'METADATA.native-bridge.jsonl', type: 'file', mode: 0o644, data: Buffer.from(metadata) },
    { path: 'product/', type: 'directory', mode: 0o755 },
    ...productEntries.map((entry) => ({ ...entry, path: `product/${entry.path}` })),
    { path: 'SHA256SUMS.native-bridge', type: 'file', mode: 0o644, data: Buffer.from(identityText(identity, productManifest)) },
    { path: 'SOURCE-COMMIT', type: 'file', mode: 0o644, data: Buffer.from(`${sourceCommit}\n`) },
    { path: 'view/', type: 'directory', mode: 0o755 },
    ...viewEntries.map((entry) => ({ ...entry, path: `view/${entry.path}` })),
  ];
  const tar = createTar(entries.sort((left, right) => Buffer.compare(Buffer.from(left.path), Buffer.from(right.path))));
  await mkdir(join(output, '..'), { recursive: true });
  await writeFile(output, tar);
  await writeFile(`${output}.sha256`, `${sha256(tar)}  ${basename(output)}\n`);
}

function entryMap(entries) {
  return new Map(entries.map((entry) => [entry.path, entry]));
}

export async function readTransport(tarPath, sidecarPath = `${tarPath}.sha256`) {
  const [tar, sidecar] = await Promise.all([readFile(tarPath), readFile(sidecarPath)]);
  const expected = `${sha256(tar)}  ${basename(tarPath)}\n`;
  if (sidecar.toString('utf8') !== expected) throw mismatch('native transport sidecar mismatch');
  const entries = parseTar(tar);
  const names = entryMap(entries);
  for (const name of requiredNames) if (!names.has(name) || names.get(name).type !== 'file') throw mismatch(`missing transport entry: ${name}`);
  for (const entry of entries) {
    if (!requiredNames.has(entry.path) && !entry.path.startsWith('product/') && !entry.path.startsWith('view/')) {
      throw mismatch(`unexpected transport entry: ${entry.path}`);
    }
  }
  for (const path of ['product/', 'view/']) {
    if (names.get(path)?.type !== 'directory') throw mismatch(`invalid transport tree: ${path}`);
  }
  const sourceCommit = names.get('SOURCE-COMMIT').data.toString('utf8');
  if (!/^[0-9a-f]{40}\n$/.test(sourceCommit)) throw error('invalid transport source commit');
  return { entries, sourceCommit: sourceCommit.slice(0, -1), manifest: names.get('SHA256SUMS.native-bridge').data.toString('utf8'), metadata: names.get('METADATA.native-bridge.jsonl').data.toString('utf8') };
}

async function main() {
  const [output, sourceCommit, imageOS, imageVersion, runnerArch, product, view] = process.argv.slice(2);
  if (!view || process.argv.length !== 9) throw error('usage: create-run.mjs <output.tar> <commit> <ImageOS> <ImageVersion> <RUNNER_ARCH> <product-dir> <view-dir>');
  await createRun({ output, sourceCommit, identity: { ImageOS: imageOS, ImageVersion: imageVersion, RUNNER_ARCH: runnerArch }, product, view });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((caught) => { console.error(`ERROR: ${caught.message}`); process.exit(caught.exitCode ?? 2); });
}
