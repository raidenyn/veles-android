import { chmod, lstat, mkdir, readdir, readFile, readlink, rm, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createTar, parseTar } from './deterministic-tar.mjs';

async function entries(root, prefix = '') {
  const result = [];
  const children = await readdir(join(root, prefix), { withFileTypes: true });
  for (const child of children.sort((left, right) => Buffer.compare(Buffer.from(left.name), Buffer.from(right.name)))) {
    const path = prefix ? `${prefix}/${child.name}` : child.name;
    const stat = await lstat(join(root, path));
    const mode = stat.mode & 0o7777;
    if (stat.isDirectory()) {
      result.push({ path: `${path}/`, type: 'directory', mode });
      result.push(...await entries(root, path));
    } else if (stat.isFile()) {
      result.push({ path, type: 'file', mode, data: await readFile(join(root, path)) });
    } else if (stat.isSymbolicLink()) {
      result.push({ path, type: 'symlink', mode, target: await readlink(join(root, path)) });
    } else {
      throw new Error(`unsupported verified artifact entry: ${path}`);
    }
  }
  return result;
}

export async function stageVerifiedArtifact(source, output) {
  const tree = await entries(source);
  await mkdir(join(output, '..'), { recursive: true });
  await writeFile(output, createTar(tree));
}

export async function restoreVerifiedArtifact(archive, output) {
  const tree = parseTar(await readFile(archive));
  await rm(output, { recursive: true, force: true });
  await mkdir(output, { recursive: true });
  for (const entry of tree) {
    const path = join(output, entry.path);
    if (entry.type === 'directory') {
      await mkdir(path, { recursive: true, mode: entry.mode });
      await chmod(path, entry.mode);
    } else {
      await mkdir(join(path, '..'), { recursive: true });
      if (entry.type === 'file') {
        await writeFile(path, entry.data, { mode: entry.mode });
        await chmod(path, entry.mode);
      } else {
        await symlink(entry.target, path);
      }
    }
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [operation, source, output] = process.argv.slice(2);
  if (!source || !output || !['stage', 'restore'].includes(operation) || process.argv.length !== 5) {
    throw new Error('usage: verified-artifact.mjs <stage|restore> <source> <output>');
  }
  await (operation === 'stage' ? stageVerifiedArtifact(source, output) : restoreVerifiedArtifact(source, output));
}
