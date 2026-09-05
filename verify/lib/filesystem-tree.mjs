import { readdir, readFile, lstat } from 'node:fs/promises';
import { join } from 'node:path';

import { error, mismatch, sha256, validateRelativePath } from './checksum-manifest.mjs';

async function readRegularFile(root, path) {
  validateRelativePath(path, error);
  try {
    const stat = await lstat(join(root, path));
    if (!stat.isFile() || stat.nlink !== 1) throw mismatch(`unsupported manifest entry: ${path}`);
    return await readFile(join(root, path));
  } catch (caught) {
    if (caught?.exitCode) throw caught;
    throw error(`cannot read manifest entry: ${path}`);
  }
}

async function listFiles(root, prefix = '') {
  let entries;
  try {
    entries = await readdir(join(root, prefix), { withFileTypes: true });
  } catch {
    throw error(`cannot read manifest tree: ${prefix || '.'}`);
  }
  const paths = [];
  for (const entry of entries) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    validateRelativePath(path);
    if (entry.isDirectory()) {
      paths.push(...await listFiles(root, path));
    } else if (entry.isFile()) {
      let stat;
      try {
        stat = await lstat(join(root, path));
      } catch {
        throw error(`cannot read manifest entry: ${path}`);
      }
      if (stat.nlink !== 1) throw mismatch(`unsupported manifest entry: ${path}`);
      paths.push(path);
    } else {
      throw mismatch(`unsupported manifest entry: ${path}`);
    }
  }
  return paths;
}

export async function verifyManifestTree(root, checksums) {
  if (!(checksums instanceof Map)) throw error('checksums must be a map');
  for (const path of checksums.keys()) validateRelativePath(path, error);
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
  if (!Array.isArray(paths)) throw error('comparison paths must be an array');
  for (const path of paths) {
    const [leftBytes, rightBytes] = await Promise.all([
      readRegularFile(left, path),
      readRegularFile(right, path),
    ]);
    if (!leftBytes.equals(rightBytes)) throw mismatch(`tree mismatch: ${path}`);
  }
}
