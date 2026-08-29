import { lstat, readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { error, mismatch, parseStandardManifest, sha256 } from './lib/checksum-manifest.mjs';

const JNI_PATHS = [
  'jni/arm64-v8a/libveles_crypto.so',
  'jni/armeabi-v7a/libveles_crypto.so',
  'jni/x86_64/libveles_crypto.so',
];
const SUMS = 'SHA256SUMS';

async function filesUnder(root, prefix = '') {
  const entries = await readdir(join(root, prefix), { withFileTypes: true });
  const paths = [];
  for (const entry of entries) {
    const path = `${prefix}${entry.name}`;
    if (entry.isDirectory()) paths.push(...await filesUnder(root, `${path}/`));
    else if (entry.isFile()) paths.push(path);
    else throw mismatch(`unsupported artifact path: ${path}`);
  }
  return paths;
}

export async function validateRustPackage(root, label) {
  let rootEntries;
  try {
    rootEntries = await readdir(root, { withFileTypes: true });
  } catch {
    throw error(`cannot read ${label} artifact directory`);
  }
  const expectedRoot = ['SHA256SUMS', 'jni', 'wasm'];
  const actualRoot = rootEntries.map((entry) => entry.name).sort();
  if (
    actualRoot.length !== expectedRoot.length
    || actualRoot.some((path, index) => path !== expectedRoot[index])
    || rootEntries.some((entry) => entry.name === SUMS ? !entry.isFile() : !entry.isDirectory())
  ) throw mismatch(`${label}: artifact path set is invalid`);

  let paths;
  try {
    paths = (await filesUnder(root)).sort();
  } catch (caught) {
    if (caught?.exitCode) throw caught;
    throw error(`cannot read ${label} artifact tree`);
  }
  const artifacts = paths.filter((path) => path !== SUMS);
  if (
    artifacts.filter((path) => path.startsWith('jni/')).join('\n') !== JNI_PATHS.join('\n')
    || !artifacts.some((path) => path.startsWith('wasm/'))
  ) throw mismatch(`${label}: artifact path set is invalid`);

  let manifest;
  try {
    manifest = parseStandardManifest(await readFile(join(root, SUMS), 'utf8'), { selfPath: SUMS });
  } catch (caught) {
    if (caught?.exitCode) {
      caught.message = `${label}: ${caught.message}`;
      throw caught;
    }
    throw error(`cannot read ${label} manifest`);
  }
  if (manifest.size !== artifacts.length || artifacts.some((path) => !manifest.has(path))) {
    throw mismatch(`${label}: manifest path set is invalid`);
  }
  for (const path of artifacts) {
    try {
      const stat = await lstat(join(root, path));
      if (!stat.isFile() || stat.nlink !== 1) throw mismatch(`${label}: unsupported artifact: ${path}`);
      if (sha256(await readFile(join(root, path))) !== manifest.get(path)) {
        throw mismatch(`${label}: checksum mismatch: ${path}`);
      }
    } catch (caught) {
      if (caught?.exitCode) throw caught;
      throw error(`cannot read ${label} artifact: ${path}`);
    }
  }
  return { paths: artifacts, manifest };
}

export async function verifyRustPackages(candidateRoot, referenceRoot) {
  const candidate = await validateRustPackage(candidateRoot, 'candidate');
  const reference = await validateRustPackage(referenceRoot, 'reference');
  if (candidate.paths.length !== reference.paths.length || candidate.paths.some((path, index) => path !== reference.paths[index])) {
    throw mismatch('candidate/reference artifact path sets differ');
  }
  for (const path of candidate.paths) {
    const [candidateBytes, referenceBytes] = await Promise.all([
      readFile(join(candidateRoot, path)), readFile(join(referenceRoot, path)),
    ]);
    if (!candidateBytes.equals(referenceBytes)) {
      throw mismatch(`byte mismatch: ${path}\ncandidate: ${sha256(candidateBytes)}\nreference: ${sha256(referenceBytes)}`);
    }
  }
}

async function main() {
  const [candidateRoot, referenceRoot] = process.argv.slice(2);
  if (!candidateRoot || !referenceRoot || process.argv.length !== 4) {
    throw error('usage: verify-rust.mjs <candidate-artifact-dir> <reference-artifact-dir>');
  }
  await verifyRustPackages(candidateRoot, referenceRoot);
  console.log('VERIFIED: rust-jni-wasm package is byte-identical to the reference build.');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((caught) => {
    console.error(`ERROR: ${caught.message}`);
    process.exit(caught.exitCode ?? 2);
  });
}
