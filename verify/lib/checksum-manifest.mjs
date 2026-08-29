import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { EXIT_MISMATCH } from './exit-codes.mjs';

const digestPattern = /^[0-9a-f]{64}$/;

export function mismatch(message) {
  const error = new Error(message);
  error.exitCode = EXIT_MISMATCH;
  return error;
}

export function validateRelativePath(path) {
  if (typeof path !== 'string' || path === '' || path.startsWith('/') || path.includes('\0')) {
    throw mismatch(`invalid relative path: ${path}`);
  }
  for (const segment of path.split('/')) {
    if (segment === '' || segment === '.' || segment === '..' || segment.includes('\r') || segment.includes('\n')) {
      throw mismatch(`invalid relative path: ${path}`);
    }
  }
  return path;
}

function comparePaths(left, right) {
  return Buffer.compare(Buffer.from(left, 'utf8'), Buffer.from(right, 'utf8'));
}

export function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

export async function createStandardManifest(root, paths) {
  if (!Array.isArray(paths)) throw mismatch('manifest paths must be an array');
  const sorted = [...paths].map(validateRelativePath).sort(comparePaths);
  for (let index = 1; index < sorted.length; index += 1) {
    if (sorted[index] === sorted[index - 1]) throw mismatch(`duplicate manifest path: ${sorted[index]}`);
  }

  const records = await Promise.all(sorted.map(async (path) => {
    try {
      return `${sha256(await readFile(join(root, path)))}  ${path}`;
    } catch (error) {
      if (error?.exitCode === EXIT_MISMATCH) throw error;
      throw mismatch(`cannot read manifest path: ${path}`);
    }
  }));
  return `${records.join('\n')}\n`;
}

export function parseStandardManifest(text, options = {}) {
  if (typeof text !== 'string' || text.includes('\r') || !text.endsWith('\n')) {
    throw mismatch('manifest must use LF and end with one LF');
  }
  const lines = text.slice(0, -1).split('\n');
  if (lines.length === 1 && lines[0] === '') return new Map();

  const checksums = new Map();
  let previousPath;
  for (const line of lines) {
    const match = /^([^ ]+) {2}(.+)$/.exec(line);
    if (!match || !digestPattern.test(match[1])) throw mismatch(`invalid manifest record: ${line}`);
    const [, digest, path] = match;
    validateRelativePath(path);
    if (path === options.selfPath) throw mismatch(`self-referential manifest path: ${path}`);
    if (checksums.has(path)) throw mismatch(`duplicate manifest path: ${path}`);
    if (previousPath !== undefined && comparePaths(previousPath, path) >= 0) {
      throw mismatch(`manifest paths are not sorted: ${path}`);
    }
    checksums.set(path, digest);
    previousPath = path;
  }
  return checksums;
}

export function parseNativeManifest(text, options = {}) {
  if (typeof text !== 'string' || text.includes('\r')) throw mismatch('native manifest must use LF');
  const headerNames = ['ImageOS', 'ImageVersion', 'RUNNER_ARCH'];
  const lines = text.split('\n');
  const identity = {};
  for (let index = 0; index < headerNames.length; index += 1) {
    const prefix = `# ${headerNames[index]}=`;
    if (!lines[index]?.startsWith(prefix) || lines[index].slice(prefix.length) === '') {
      throw mismatch(`invalid native identity header: ${headerNames[index]}`);
    }
    identity[headerNames[index]] = lines[index].slice(prefix.length);
  }
  if (lines.slice(0, 3).some((line) => line.includes('\n'))) throw mismatch('invalid native identity header');
  return {
    identity,
    checksums: parseStandardManifest(`${lines.slice(3).join('\n')}`, options),
  };
}
