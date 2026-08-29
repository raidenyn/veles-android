import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, rm, symlink, writeFile, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { createNativeMetadata, parseNativeMetadata } from '../lib/native-metadata.mjs';
import { EXIT_MISMATCH } from '../lib/exit-codes.mjs';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

async function withTree(run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-native-metadata-'));
  try {
    await mkdir(join(root, 'folder'));
    await chmod(join(root, 'folder'), 0o755);
    await writeFile(join(root, 'file'), 'content');
    await chmod(join(root, 'file'), 0o640);
    await symlink('../file', join(root, 'folder/link'));
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function isMismatch(error) {
  return error?.exitCode === EXIT_MISMATCH;
}

test('creates sorted JSONL metadata for files, directories, and literal symlinks', async () => {
  await withTree(async (root) => {
    const metadata = await createNativeMetadata(root, ['folder/link', 'file', 'folder']);
    assert.equal(
      metadata,
      `${JSON.stringify({ path: 'file', type: 'file', mode: '0640', sha256: sha256('content') })}\n`
      + `${JSON.stringify({ path: 'folder', type: 'directory', mode: '0755' })}\n`
      + `${JSON.stringify({ path: 'folder/link', type: 'symlink', mode: '0777', target: '../file', sha256: sha256('../file') })}\n`,
    );
  });
});

test('rejects duplicate metadata paths and invalid creation paths', async () => {
  await withTree(async (root) => {
    await assert.rejects(() => createNativeMetadata(root, ['file', 'file']), isMismatch);
    await assert.rejects(() => createNativeMetadata(root, ['../file']), isMismatch);
  });
});

test('parses only sorted JSONL entries with type-specific fields and four-digit modes', () => {
  const file = { path: 'file', type: 'file', mode: '0640', sha256: sha256('content') };
  const directory = { path: 'folder', type: 'directory', mode: '0755' };
  const symlinkEntry = {
    path: 'folder/link', type: 'symlink', mode: '0777', target: '../file', sha256: sha256('../file'),
  };
  const text = `${JSON.stringify(file)}\n${JSON.stringify(directory)}\n${JSON.stringify(symlinkEntry)}\n`;

  assert.deepEqual(parseNativeMetadata(text), [file, directory, symlinkEntry]);

  for (const invalid of [
    `${JSON.stringify(directory)}\n${JSON.stringify(file)}\n`,
    `${JSON.stringify({ ...file, mode: '640' })}\n`,
    `${JSON.stringify({ ...directory, sha256: sha256('unexpected') })}\n`,
    `${JSON.stringify({ ...symlinkEntry, target: undefined })}\n`,
    `${JSON.stringify({ ...file, target: 'unexpected' })}\n`,
    `${JSON.stringify(file)}\n${JSON.stringify(file)}\n`,
  ]) {
    assert.throws(() => parseNativeMetadata(invalid), isMismatch);
  }
});
