import { lstat, readFile, readlink } from 'node:fs/promises';
import { join } from 'node:path';

import { error, mismatch, sha256, validateRelativePath } from './checksum-manifest.mjs';

function comparePaths(left, right) {
  return Buffer.compare(Buffer.from(left, 'utf8'), Buffer.from(right, 'utf8'));
}

function mode(stat) {
  return (stat.mode & 0o7777).toString(8).padStart(4, '0');
}

function validateEntry(entry) {
  if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) {
    throw mismatch('metadata entry must be an object');
  }
  validateRelativePath(entry.path);
  if (!['file', 'directory', 'symlink'].includes(entry.type) || !/^[0-7]{4}$/.test(entry.mode)) {
    throw mismatch(`invalid metadata entry: ${entry.path}`);
  }

  const allowed = entry.type === 'directory'
    ? ['path', 'type', 'mode']
    : entry.type === 'file'
      ? ['path', 'type', 'mode', 'sha256']
      : ['path', 'type', 'mode', 'target', 'sha256'];
  if (Object.keys(entry).length !== allowed.length || Object.keys(entry).some((key) => !allowed.includes(key))) {
    throw mismatch(`invalid metadata fields: ${entry.path}`);
  }
  if (entry.type !== 'directory' && (typeof entry.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(entry.sha256))) {
    throw mismatch(`invalid metadata checksum: ${entry.path}`);
  }
  if (entry.type === 'symlink' && typeof entry.target !== 'string') {
    throw mismatch(`invalid symlink target: ${entry.path}`);
  }
  return entry;
}

export async function createNativeMetadata(root, entries) {
  if (!Array.isArray(entries)) throw error('metadata paths must be an array');
  const paths = [...entries].map((path) => validateRelativePath(path, error)).sort(comparePaths);
  for (let index = 1; index < paths.length; index += 1) {
    if (paths[index] === paths[index - 1]) throw error(`duplicate metadata path: ${paths[index]}`);
  }

  const records = await Promise.all(paths.map(async (path) => {
    try {
      const stat = await lstat(join(root, path));
      if (stat.isFile()) {
        if (stat.nlink !== 1) throw mismatch(`hard link metadata entry: ${path}`);
        return { path, type: 'file', mode: mode(stat), sha256: sha256(await readFile(join(root, path))) };
      }
      if (stat.isDirectory()) return { path, type: 'directory', mode: mode(stat) };
      if (stat.isSymbolicLink()) {
        const target = await readlink(join(root, path));
        return { path, type: 'symlink', mode: mode(stat), target, sha256: sha256(Buffer.from(target, 'utf8')) };
      }
      throw mismatch(`unsupported metadata entry: ${path}`);
    } catch (caught) {
      if (caught?.exitCode) throw caught;
      throw error(`cannot read metadata entry: ${path}`);
    }
  }));
  return `${records.map(JSON.stringify).join('\n')}\n`;
}

export function parseNativeMetadata(text) {
  if (typeof text !== 'string') throw error('metadata text must be a string');
  if (text.includes('\r') || !text.endsWith('\n')) {
    throw mismatch('metadata must use LF and end with one LF');
  }
  const lines = text.slice(0, -1).split('\n');
  if (lines.length === 1 && lines[0] === '') return [];

  const entries = [];
  let previousPath;
  for (const line of lines) {
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      throw mismatch('invalid metadata JSON');
    }
    validateEntry(entry);
    if (entry.type === 'symlink' && entry.sha256 !== sha256(Buffer.from(entry.target, 'utf8'))) {
      throw mismatch(`symlink target checksum mismatch: ${entry.path}`);
    }
    if (previousPath !== undefined && comparePaths(previousPath, entry.path) >= 0) {
      throw mismatch(`metadata paths are not sorted: ${entry.path}`);
    }
    entries.push(entry);
    previousPath = entry.path;
  }
  return entries;
}
