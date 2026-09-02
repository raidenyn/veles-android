import { chmod, mkdir, readdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';

import { error, mismatch, parseNativeManifest, parseStandardManifest, sha256 } from '../lib/checksum-manifest.mjs';
import { parseNativeMetadata } from '../lib/native-metadata.mjs';
import { readTransport } from './create-run.mjs';
import { parseTar } from './deterministic-tar.mjs';
import { listZip, readZipEntry } from './zip-reader.mjs';

async function transportPath(path) {
  if (path.endsWith('.tar')) return path;
  try {
    const files = (await readdir(path)).filter((name) => name.endsWith('.tar'));
    if (files.length !== 1) throw error('run directory must contain exactly one transport tar');
    return join(path, files[0]);
  } catch (caught) {
    if (caught?.exitCode) throw caught;
    throw error('cannot read native run directory');
  }
}

function productEntries(run) {
  return run.entries.filter((entry) => entry.path.startsWith('product/') && entry.type === 'file');
}

function viewEntries(run) {
  return new Map(run.entries.filter((entry) => entry.path.startsWith('view/')).map((entry) => [entry.path.slice(5).replace(/\/$/, ''), entry]));
}

function firstDifference(left, right) {
  const paths = [...new Set([...left.keys(), ...right.keys()])].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
  return paths.find((path) => {
    const a = left.get(path);
    const b = right.get(path);
    return !a || !b || a.type !== b.type || a.mode !== b.mode || a.target !== b.target || (a.type === 'file' && !a.data.equals(b.data));
  });
}

function artifactDifference(path, left, right) {
  const a = left?.data ?? Buffer.alloc(0);
  const b = right?.data ?? Buffer.alloc(0);
  return mismatch(`byte mismatch: ${path}\nleft: ${sha256(a)}\nright: ${sha256(b)}`);
}

// Tauri's platform installers contain producer-controlled timestamps and image
// identifiers. Their enclosing package and checksum sidecar necessarily drift
// with those bytes too. Each run binds archive payloads to the extracted view
// and validates them against native/component manifests; cross-run comparison
// is limited to stable raw evidence.
function isNondeterministicTauriTransport(path) {
  return /^product\/[^/]+\.(?:zip|tar\.gz)(?:\.sha256)?$/.test(path)
    || /^view\/bundle\/(?:nsis\/[^/]+-setup\.exe|msi\/[^/]+\.msi|dmg\/[^/]+\.dmg)$/.test(path)
    || (/^view\/[^/]+\.app(?:\/|$)/.test(path)
      && !/^view\/[^/]+\.app\/Contents\/MacOS\/veles-native-bridge$/.test(path));
}

function archiveViewEntries(packageEntry) {
  if (packageEntry.path.endsWith('.zip')) {
    return new Map(listZip(packageEntry.data).map((path) => [path, {
      type: 'file', mode: undefined, data: readZipEntry(packageEntry.data, path), target: undefined,
    }]));
  }
  if (packageEntry.path.endsWith('.tar.gz')) {
    return new Map(parseTar(gunzipSync(packageEntry.data)).map((entry) => [entry.path.replace(/\/$/, ''), entry]));
  }
  return null;
}

function viewRequiresPackageBinding(run) {
  return [...viewEntries(run)].some(([path, entry]) => entry.type !== 'directory' && (
    /^bundle\/(?:nsis\/[^/]+-setup\.exe|msi\/[^/]+\.msi|dmg\/[^/]+\.dmg)$/.test(path)
    || (/^[^/]+\.app(?:\/|$)/.test(path)
      && !/^[^/]+\.app\/Contents\/MacOS\/veles-native-bridge$/.test(path))
  ));
}

function verifyPackageViewBinding(run) {
  const packages = productEntries(run).filter((entry) => /\.(?:zip|tar\.gz)$/.test(entry.path));
  if (packages.length !== 1) {
    if (viewRequiresPackageBinding(run)) {
      throw mismatch('native package/view binding mismatch: expected one package archive');
    }
    return; // Host-only synthetic fixtures contain no exempt producer payload.
  }
  let archived;
  try {
    archived = archiveViewEntries(packages[0]);
  } catch (caught) {
    throw mismatch(`native package/view binding mismatch: cannot read package archive (${caught.message})`);
  }
  const view = new Map([...viewEntries(run)].filter(([path]) => path !== ''));
  if (packages[0].path.endsWith('.zip')) {
    // ZIP stores Windows modes inconsistently across extractors; its payload
    // binding is file paths and bytes, while transport metadata validates view
    // modes independently.
    for (const [path, entry] of view) {
      if (entry.type !== 'file') continue;
      const archive = archived.get(path);
      if (!archive || !archive.data.equals(entry.data)) {
        throw mismatch(`native package/view binding mismatch: ${path}`);
      }
    }
    if ([...archived.keys()].some((path) => view.get(path)?.type !== 'file')) {
      throw mismatch('native package/view binding mismatch: archive path set');
    }
    return;
  }
  // The producer TAR omits parent directories for the appended DMG while the
  // view must create them to materialize the file. Bind payload files and
  // symlinks; view directory metadata is validated separately.
  const archivedPayloads = new Map([...archived].filter(([, entry]) => entry.type !== 'directory'));
  const viewPayloads = new Map([...view].filter(([, entry]) => entry.type !== 'directory'));
  const paths = [...new Set([...archivedPayloads.keys(), ...viewPayloads.keys()])].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
  for (const path of paths) {
    const archive = archivedPayloads.get(path);
    const entry = viewPayloads.get(path);
    if (!archive || !entry || archive.type !== entry.type
      || (archive.type === 'file' && !archive.data.equals(entry.data))
      || (archive.type === 'symlink' && archive.target !== entry.target)) {
      throw mismatch(`native package/view binding mismatch: ${path}`);
    }
  }
}

function verifyProductManifest(run) {
  const manifest = parseNativeManifest(run.manifest);
  // The native product manifest covers ONLY the package + sidecar. The
  // producer also writes a product-level SHA256SUMS (a component checksum
  // manifest) which the transport carries as product/SHA256SUMS but which the
  // design excludes from the native manifest. Exclude it from the path-set
  // comparison against the manifest, but REQUIRE it to be present and valid:
  // its records must correctly hash the package and sidecar the transport
  // carries. A missing or corrupted component manifest is a product mismatch
  // (exit 1) per the 0/1/2 contract.
  const productFiles = productEntries(run);
  const componentSumsEntry = productFiles.find((entry) => entry.path === 'product/SHA256SUMS');
  if (!componentSumsEntry) {
    throw mismatch('component SHA256SUMS is required in the product dir');
  }
  const artifactEntries = productFiles.filter((entry) => entry.path !== 'product/SHA256SUMS');
  const actual = new Map(artifactEntries.map((entry) => [entry.path.slice(8), entry]));
  if (actual.size !== manifest.checksums.size || [...actual.keys()].some((path) => !manifest.checksums.has(path))) {
    throw mismatch('native product path set mismatch');
  }
  for (const [path, digest] of manifest.checksums) {
    if (sha256(actual.get(path).data) !== digest) throw mismatch(`native product checksum mismatch: ${path}`);
  }
  const sums = parseStandardManifest(componentSumsEntry.data.toString('utf8'), { allowUnsorted: true });
  const sumsPaths = [...sums.keys()].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
  const artifactPaths = [...actual.keys()].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
  if (sumsPaths.length !== artifactPaths.length || sumsPaths.some((p, i) => p !== artifactPaths[i])) {
    throw mismatch('component SHA256SUMS path set must match the transport product artifact set');
  }
  for (const [path, expected] of sums) {
    if (sha256(actual.get(path).data) !== expected) {
      throw mismatch(`component SHA256SUMS mismatch: ${path}`);
    }
  }
  return manifest;
}

function verifyMetadata(run, metadata) {
  const actual = new Map([...viewEntries(run)].filter(([path]) => path !== ''));
  if (metadata.length !== actual.size || metadata.some((entry) => !actual.has(entry.path))) {
    throw mismatch('native metadata path set mismatch');
  }
  for (const record of metadata) {
    const entry = actual.get(record.path);
    if (record.type !== entry.type || record.mode !== entry.mode.toString(8).padStart(4, '0')) {
      throw mismatch(`native metadata mismatch: ${record.path}`);
    }
    if (record.type === 'file' && record.sha256 !== sha256(entry.data)) throw mismatch(`native metadata mismatch: ${record.path}`);
    if (record.type === 'symlink' && (record.target !== entry.target || record.sha256 !== sha256(Buffer.from(entry.target, 'utf8')))) {
      throw mismatch(`native metadata mismatch: ${record.path}`);
    }
  }
}

async function emit(root, run) {
  await rm(root, { recursive: true, force: true });
  for (const entry of run.entries.filter((entry) => entry.path.startsWith('product/') || entry.path.startsWith('view/'))) {
    const path = join(root, entry.path);
    if (entry.type === 'directory') {
      await mkdir(path, { recursive: true, mode: entry.mode });
      await chmod(path, entry.mode);
    }
    else {
      await mkdir(join(path, '..'), { recursive: true });
      if (entry.type === 'file') await writeFile(path, entry.data, { mode: entry.mode });
      else await symlink(entry.target, path);
      if (entry.type === 'file') await chmod(path, entry.mode);
    }
  }
  await writeFile(join(root, 'SHA256SUMS.native-bridge'), run.manifest);
  await writeFile(join(root, 'METADATA.native-bridge.jsonl'), run.metadata);
}

export async function compareRuns(resolvedCommit, leftPath, rightPath, output) {
  if (!/^[0-9a-f]{40}$/.test(resolvedCommit ?? '')) throw error('invalid resolved commit');
  const [left, right] = await Promise.all([transportPath(leftPath).then((path) => readTransport(path)), transportPath(rightPath).then((path) => readTransport(path))]);
  if (left.sourceCommit !== resolvedCommit || right.sourceCommit !== resolvedCommit) throw error('native run source commit does not match resolved commit');
  const [leftManifest, rightManifest] = [parseNativeManifest(left.manifest), parseNativeManifest(right.manifest)];
  if (JSON.stringify(leftManifest.identity) !== JSON.stringify(rightManifest.identity)) {
    throw error(`native runner identities differ\nleft: ${JSON.stringify(leftManifest.identity)}\nright: ${JSON.stringify(rightManifest.identity)}\nre-run on matched image`);
  }
  verifyProductManifest(left);
  verifyProductManifest(right);
  const [leftMetadata, rightMetadata] = [parseNativeMetadata(left.metadata), parseNativeMetadata(right.metadata)];
  verifyMetadata(left, leftMetadata);
  verifyMetadata(right, rightMetadata);
  verifyPackageViewBinding(left);
  verifyPackageViewBinding(right);
  const leftView = viewEntries(left);
  const rightView = viewEntries(right);
  // SHA256SUMS is validated above as component evidence. Do not compare its
  // generated bytes, which are not part of the native product manifest.
  const leftAll = new Map([
    ...productEntries(left)
      .filter((entry) => entry.path !== 'product/SHA256SUMS' && !isNondeterministicTauriTransport(entry.path))
      .map((entry) => [`product/${entry.path.slice(8)}`, entry]),
    ...[...leftView]
      .map(([path, entry]) => [`view/${path}`, entry])
      .filter(([path]) => !isNondeterministicTauriTransport(path)),
  ]);
  const rightAll = new Map([
    ...productEntries(right)
      .filter((entry) => entry.path !== 'product/SHA256SUMS' && !isNondeterministicTauriTransport(entry.path))
      .map((entry) => [`product/${entry.path.slice(8)}`, entry]),
    ...[...rightView]
      .map(([path, entry]) => [`view/${path}`, entry])
      .filter(([path]) => !isNondeterministicTauriTransport(path)),
  ]);
  const differing = firstDifference(leftAll, rightAll);
  if (differing) throw artifactDifference(differing, leftAll.get(differing), rightAll.get(differing));
  if (output) await emit(output, left);
}

async function main() {
  const [commit, left, right, output] = process.argv.slice(2);
  if (!right || process.argv.length > 6) throw error('usage: compare-runs.mjs <resolved-commit> <run-a-dir-or-tar> <run-b-dir-or-tar> [verified-tree]');
  await compareRuns(commit, left, right, output);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((caught) => { console.error(`ERROR: ${caught.message}`); process.exit(caught.exitCode ?? 2); });
}
