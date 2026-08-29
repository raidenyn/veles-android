import { readdir, readFile, lstat } from 'node:fs/promises';
import { join } from 'node:path';

import { mismatch, sha256, validateRelativePath } from './checksum-manifest.mjs';

async function readRegularFile(root, path) {
  validateRelativePath(path);
  try {
    const stat = await lstat(join(root, path));
    if (!stat.isFile() || stat.nlink !== 1) throw mismatch(`unsupported manifest entry: ${path}`);
    return await readFile(join(root, path));
  } catch (error) {
    if (error?.exitCode) throw error;
    throw mismatch(`missing manifest entry: ${path}`);
  }
}

async function listFiles(root, prefix = '') {
  let entries;
  try {
    entries = await readdir(join(root, prefix), { withFileTypes: true });
  } catch {
    throw mismatch(`cannot read manifest tree: ${prefix || '.'}`);
  }
  const paths = [];
  for (const entry of entries) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    validateRelativePath(path);
    if (entry.isDirectory()) {
      paths.push(...await listFiles(root, path));
    } else if (entry.isFile()) {
      const stat = await lstat(join(root, path));
      if (stat.nlink !== 1) throw mismatch(`unsupported manifest entry: ${path}`);
      paths.push(path);
    } else {
      throw mismatch(`unsupported manifest entry: ${path}`);
    }
  }
  return paths;
}

export async function verifyManifestTree(root, checksums) {
  if (!(checksums instanceof Map)) throw mismatch('checksums must be a map');
  const actualPaths = await listFiles(root);
  if (actualPaths.length !== checksums.size) throw mismatch('manifest tree contains unexpected or missing files');
  for (const path of actualPaths) {
    if (!checksums.has(path)) throw mismatch(`unexpected manifest entry: ${path}`);
  }
  for (const [path, expected] of checksums) {
    if (sha256(await readRegularFile(root, path)) !== expected) {
      throw mismatch(`checksum mismatch: ${path}`);
    }
  }
}

export async function compareTrees(left, right, paths) {
  if (!Array.isArray(paths)) throw mismatch('comparison paths must be an array');
  for (const path of paths) {
    const [leftBytes, rightBytes] = await Promise.all([
      readRegularFile(left, path),
      readRegularFile(right, path),
    ]);
    if (!leftBytes.equals(rightBytes)) throw mismatch(`tree mismatch: ${path}`);
  }
}
