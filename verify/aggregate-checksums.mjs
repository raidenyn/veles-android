import { lstat, mkdir, readdir, readFile, readlink, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { error, mismatch, parseNativeManifest, parseStandardManifest, sha256 } from './lib/checksum-manifest.mjs';
import { parseNativeMetadata } from './lib/native-metadata.mjs';

function comparePaths(left, right) {
  return Buffer.compare(Buffer.from(left), Buffer.from(right));
}

async function files(root, prefix = '') {
  let entries;
  try {
    entries = await readdir(join(root, prefix), { withFileTypes: true });
  } catch {
    throw error(`cannot read artifact directory: ${prefix || '.'}`);
  }
  const result = [];
  for (const entry of entries) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) result.push(...await files(root, path));
    else if (entry.isFile()) result.push(path);
    else throw mismatch(`unsupported artifact entry: ${path}`);
  }
  return result.sort(comparePaths);
}

async function viewEntries(root, prefix = '') {
  let entries;
  try {
    entries = await readdir(join(root, prefix), { withFileTypes: true });
  } catch {
    throw error(`cannot read native view: ${prefix || '.'}`);
  }
  const result = [];
  for (const entry of entries) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    const stat = await lstat(join(root, path));
    if (stat.isDirectory()) {
      result.push({ path, type: 'directory', mode: (stat.mode & 0o7777).toString(8).padStart(4, '0') });
      result.push(...await viewEntries(root, path));
    }
    else if (stat.isFile()) result.push({ path, type: 'file', mode: (stat.mode & 0o7777).toString(8).padStart(4, '0'), sha256: sha256(await readFile(join(root, path))) });
    else if (stat.isSymbolicLink()) {
      const target = await readlink(join(root, path));
      result.push({ path, type: 'symlink', mode: (stat.mode & 0o7777).toString(8).padStart(4, '0'), target, sha256: sha256(Buffer.from(target)) });
    } else throw mismatch(`unsupported native view entry: ${path}`);
  }
  return result;
}

function exact(actual, expected, name) {
  const sorted = [...expected].sort(comparePaths);
  if (actual.length !== sorted.length || actual.some((path, index) => path !== sorted[index])) {
    throw mismatch(`${name} contains unexpected or missing files`);
  }
}

function excludedNativeViewPath(path) {
  return path.split('/').some((name) => (
    name === 'SOURCE-COMMIT'
    || name === 'IDENTITY'
    || name === 'SHA256SUMS'
    || name === 'SHA256SUMS.native-bridge'
    || name === 'SHA256SUMS.toolchains'
    || name === 'METADATA.native-bridge.jsonl'
    || /^REPORT(?:\..*)?$/i.test(name)
    // The view contains only raw product files (installers/.app tree/dmg) and
    // the extracted host manifest. The deterministic outer package, its
    // sidecar, and any zipped/tarred component manifest that leaked out of
    // product/ are excluded so component manifests never enter the aggregate
    // records. .dmg is a raw installer and stays allowed.
    || /\.zip$/i.test(name)
    || /\.tar(?:\.gz)?$/i.test(name)
    || /\.sha256$/i.test(name)
  ));
}

async function readManifest(root, name, native = false) {
  let text;
  try {
    text = await readFile(join(root, name), 'utf8');
  } catch {
    throw error(`missing component manifest: ${name}`);
  }
  return native ? parseNativeManifest(text) : parseStandardManifest(text, { selfPath: name.split('/').at(-1) });
}

async function recordsFromManifest(root, manifest, prefix) {
  const records = [];
  for (const [path, digest] of manifest) {
    let bytes;
    try {
      bytes = await readFile(join(root, path));
    } catch {
      throw error(`missing component artifact: ${path}`);
    }
    if (sha256(bytes) !== digest) throw mismatch(`component checksum mismatch: ${path}`);
    records.push([`${prefix}${path}`, digest]);
  }
  return records;
}

async function nativeRecords(root, platform) {
  const manifestName = 'SHA256SUMS.native-bridge';
  const metadataName = 'METADATA.native-bridge.jsonl';
  const manifest = await readManifest(root, manifestName, true);
  let metadataText;
  try {
    metadataText = await readFile(join(root, metadataName), 'utf8');
  } catch {
    throw error(`missing native metadata: ${platform}`);
  }
  const metadata = parseNativeMetadata(metadataText);
  const product = await files(join(root, 'product'));
  // The producer (native-bridge/scripts/package.mjs writeChecksumManifest)
  // writes a product-level SHA256SUMS into the product dir alongside the
  // package and sidecar. The transport carries it as product/SHA256SUMS. The
  // native manifest (SHA256SUMS.native-bridge) lists exactly the package and
  // sidecar; the product SHA256SUMS is a component checksum manifest that the
  // design excludes from the aggregate records, but REQUIRES to be present and
  // valid. Reject a native namespace whose component SHA256SUMS is missing or
  // whose digest is invalid; allow the real three-file layout (package +
  // sidecar + SHA256SUMS) but reject any other extra file.
  if (!product.includes('SHA256SUMS')) {
    throw mismatch(`native ${platform} product is missing the component SHA256SUMS`);
  }
  const productArtifactFiles = product.filter((path) => path !== 'SHA256SUMS');
  exact(productArtifactFiles, [...manifest.checksums.keys()], `native ${platform} product`);
  const packagePaths = productArtifactFiles.filter((path) => !path.endsWith('.sha256'));
  if (packagePaths.length !== 1 || productArtifactFiles.length !== 2 || productArtifactFiles[1] !== `${packagePaths[0]}.sha256`) {
    throw mismatch(`native ${platform} product must contain one package and sidecar`);
  }
  // The product SHA256SUMS is the only permitted non-manifest file.
  const extra = product.filter((path) => path !== 'SHA256SUMS' && !manifest.checksums.has(path));
  if (extra.length > 0) throw mismatch(`native ${platform} product contains unexpected files: ${extra.join(', ')}`);
  // Validate the component SHA256SUMS against the package + sidecar it claims
  // to cover. parseStandardManifest rejects malformed records; each digest
  // must match the corresponding file's bytes, and the record path set must
  // exactly match the product artifact set.
  const componentSumsText = await readFile(join(root, 'product', 'SHA256SUMS'), 'utf8');
  const componentSums = parseStandardManifest(componentSumsText);
  const sumsPaths = [...componentSums.keys()].sort(comparePaths);
  const artifactPathsSorted = [...productArtifactFiles].sort(comparePaths);
  if (sumsPaths.length !== artifactPathsSorted.length || sumsPaths.some((p, i) => p !== artifactPathsSorted[i])) {
    throw mismatch(`native ${platform} component SHA256SUMS path set must match the product artifact set`);
  }
  for (const [path, expected] of componentSums) {
    const bytes = await readFile(join(root, 'product', path));
    if (sha256(bytes) !== expected) {
      throw mismatch(`native ${platform} component SHA256SUMS mismatch: ${path}`);
    }
  }
  const view = await viewEntries(join(root, 'view'));
  const metadataByPath = new Map(metadata.map((entry) => [entry.path, entry]));
  const different = view.find((entry) => {
    const expected = metadataByPath.get(entry.path);
    return !expected || entry.type !== expected.type || entry.mode !== expected.mode || entry.sha256 !== expected.sha256 || entry.target !== expected.target;
  });
  if (view.length === 0 || metadata.length === 0 || view.length !== metadata.length || different) {
    throw mismatch(`native ${platform} view does not match metadata${different ? `: ${different.path}` : ''}`);
  }
  const excluded = view.find((entry) => excludedNativeViewPath(entry.path));
  if (excluded) throw mismatch(`native ${platform} view contains excluded evidence: ${excluded.path}`);
  // The aggregate retains both classes of verified native evidence. Package,
  // installer, and non-host app records are self-validated and package-bound
  // per run but intentionally not cross-run byte compared; stable host and
  // manifest entries were compared before this verified tree was emitted.
  const output = await recordsFromManifest(
    join(root, 'product'),
    manifest.checksums,
    `native-bridge/${platform}/product/`,
  );
  for (const entry of view) {
    if (entry.type === 'file') output.push([`native-bridge/${platform}/view/${entry.path}`, entry.sha256]);
  }
  output.push([`native-bridge/${platform}/${metadataName}`, sha256(metadataText)]);
  const allowed = ['product', 'view', manifestName, metadataName];
  const topLevel = await readdir(root);
  if (topLevel.some((path) => !allowed.includes(path))) {
    throw mismatch(`native ${platform} contains excluded evidence`);
  }
  return output;
}

export async function aggregateChecksums(root, resolvedCommit) {
  const verification = join(root, 'build', 'verification');
  const output = join(verification, 'SHA256SUMS.toolchains');
  await rm(output, { force: true });
  if (!/^[0-9a-f]{40}$/.test(resolvedCommit ?? '')) throw error('invalid resolved commit');
  const android = join(verification, 'android');
  exact(await files(android), ['app-release-unsigned.apk'], 'android verification');
  const androidBytes = await readFile(join(android, 'app-release-unsigned.apk'));

  const web = join(root, 'build', 'web-extension');
  const webManifest = await readManifest(web, 'SHA256SUMS');
  const webPaths = [...webManifest.keys()];
  if (webPaths.length !== 2 || !webPaths.some((path) => path.endsWith('.zip')) || !webPaths.some((path) => path.endsWith('.zip.sha256'))) {
    throw mismatch('web extension manifest must contain one ZIP and sidecar');
  }
  exact(await files(web), [...webPaths, 'SHA256SUMS'], 'web extension');

  const rust = join(root, 'build', 'rust-package');
  const rustManifest = await readManifest(rust, 'SHA256SUMS');
  const rustPaths = [...rustManifest.keys()];
  if (rustPaths.length === 0 || rustPaths.some((path) => !path.startsWith('jni/') && !path.startsWith('wasm/'))) {
    throw mismatch('rust manifest contains excluded evidence');
  }
  exact(await files(rust), [...rustPaths, 'SHA256SUMS'], 'rust package');

  const records = [
    ['android/app-release-unsigned.apk', sha256(androidBytes)],
    ...await recordsFromManifest(web, webManifest, 'web-extension/'),
    ...await recordsFromManifest(rust, rustManifest, 'rust/'),
    ...await nativeRecords(join(verification, 'native-bridge', 'windows'), 'windows'),
    ...await nativeRecords(join(verification, 'native-bridge', 'macos'), 'macos'),
  ].sort(([left], [right]) => comparePaths(left, right));
  for (let index = 1; index < records.length; index += 1) {
    if (records[index - 1][0] === records[index][0]) throw mismatch(`duplicate aggregate path: ${records[index][0]}`);
  }
  await mkdir(verification, { recursive: true });
  await writeFile(output, `${records.map(([path, digest]) => `${digest}  ${path}`).join('\n')}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [resolvedCommit] = process.argv.slice(2);
  aggregateChecksums(process.cwd(), resolvedCommit).catch((caught) => {
    console.error(`ERROR: ${caught.message}`);
    process.exit(caught.exitCode ?? 2);
  });
}
