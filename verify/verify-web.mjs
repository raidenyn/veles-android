import { lstat, readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  error,
  mismatch,
  parseStandardManifest,
  sha256,
} from './lib/checksum-manifest.mjs';

const ZIP = 'veles-extension-0.1.0.zip';
const SIDECAR = `${ZIP}.sha256`;
const SUMS = 'SHA256SUMS';
const expectedPaths = [ZIP, SIDECAR];

function artifactMismatch(label, message) {
  const failure = mismatch(`${label}: ${message}`);
  return failure;
}

async function validateArtifactTree(root, label) {
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch {
    throw error(`cannot read ${label} artifact directory`);
  }
  const paths = entries.map((entry) => entry.name).sort();
  const expectedFiles = [...expectedPaths, SUMS].sort();
  if (
    paths.length !== expectedFiles.length
    || paths.some((path, index) => path !== expectedFiles[index])
    || entries.some((entry) => !entry.isFile())
  ) {
    throw artifactMismatch(label, 'artifact path set is invalid');
  }

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
  if (
    manifest.size !== expectedPaths.length
    || expectedPaths.some((path) => !manifest.has(path))
  ) {
    throw artifactMismatch(label, 'manifest path set is invalid');
  }

  for (const path of expectedPaths) {
    try {
      const stat = await lstat(join(root, path));
      if (!stat.isFile() || stat.nlink !== 1) throw artifactMismatch(label, `unsupported artifact: ${path}`);
      const actual = sha256(await readFile(join(root, path)));
      if (actual !== manifest.get(path)) throw artifactMismatch(label, `checksum mismatch: ${path}`);
    } catch (caught) {
      if (caught?.exitCode) throw caught;
      throw error(`cannot read ${label} artifact: ${path}`);
    }
  }
  return manifest;
}

export function verifyWebPreparation({ nodeVersion, npmVersion, dockerStatus = 0 }) {
  if (nodeVersion !== 'v26.8.1') throw error(`Node version drift: expected v26.8.1, got ${nodeVersion}`);
  if (npmVersion !== '11.19.0') throw error(`npm version drift: expected 11.19.0, got ${npmVersion}`);
  if (dockerStatus !== 0) throw error(`Docker failed with status ${dockerStatus}`);
}

export async function verifyWebArtifacts(candidateRoot, referenceRoot, runDocker) {
  const candidateManifest = await validateArtifactTree(candidateRoot, 'candidate');
  const referenceManifest = await validateArtifactTree(referenceRoot, 'reference');
  if (typeof runDocker === 'function') runDocker();

  for (const path of expectedPaths) {
    const [candidate, reference] = await Promise.all([
      readFile(join(candidateRoot, path)),
      readFile(join(referenceRoot, path)),
    ]);
    if (!candidate.equals(reference)) {
      throw mismatch(
        `byte mismatch: ${path}\ncandidate: ${sha256(candidate)}\nreference: ${sha256(reference)}`,
      );
    }
  }
  return { candidateManifest, referenceManifest };
}

async function main() {
  if (process.argv[2] === '--validate' && process.argv.length === 4) {
    await validateArtifactTree(process.argv[3], 'candidate');
    return;
  }
  const [candidateRoot, referenceRoot] = process.argv.slice(2);
  if (!candidateRoot || !referenceRoot || process.argv.length !== 4) {
    throw error('usage: verify-web.mjs <candidate-artifact-dir> <reference-artifact-dir>');
  }
  await verifyWebArtifacts(candidateRoot, referenceRoot);
  console.log('VERIFIED: web package is byte-identical to the reference build.');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((caught) => {
    console.error(`ERROR: ${caught.message}`);
    process.exit(caught.exitCode ?? 2);
  });
}
