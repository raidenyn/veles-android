import assert from 'node:assert/strict';
import { chmod, lstat, mkdir, mkdtemp, readFile, readlink, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { restoreVerifiedArtifact, stageVerifiedArtifact } from '../native/verified-artifact.mjs';
import { createTar } from '../native/deterministic-tar.mjs';

async function entry(path) {
  const stat = await lstat(path);
  return {
    mode: (stat.mode & 0o7777).toString(8).padStart(4, '0'),
    type: stat.isDirectory() ? 'directory' : stat.isSymbolicLink() ? 'symlink' : 'file',
    value: stat.isSymbolicLink() ? await readlink(path) : stat.isFile() ? (await readFile(path)).toString('utf8') : undefined,
  };
}

test('verified native artifact staging preserves metadata through file-only transport', async () => {
  const root = await mkdtemp(join(tmpdir(), 'veles-verified-artifact-'));
  try {
    const source = join(root, 'source');
    const archive = join(root, 'verified-native.tar');
    const restored = join(root, 'restored');
    await mkdir(join(source, 'view'), { recursive: true });
    await writeFile(join(source, 'view', 'app.veles.native_bridge.json'), '{"name":"app.veles.native_bridge"}\n');
    await chmod(join(source, 'view', 'app.veles.native_bridge.json'), 0o751);
    await symlink('app.veles.native_bridge.json', join(source, 'view', 'current-manifest'));

    await stageVerifiedArtifact(source, archive);
    await restoreVerifiedArtifact(archive, restored);

    assert.deepEqual(await entry(join(restored, 'view', 'app.veles.native_bridge.json')), await entry(join(source, 'view', 'app.veles.native_bridge.json')));
    assert.deepEqual(await entry(join(restored, 'view', 'current-manifest')), await entry(join(source, 'view', 'current-manifest')));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('restore records archived macOS symlink modes for Linux aggregate validation', async () => {
  const root = await mkdtemp(join(tmpdir(), 'veles-verified-symlink-mode-'));
  try {
    const archive = join(root, 'verified-native.tar');
    const restored = join(root, 'restored');
    await writeFile(archive, createTar([
      { path: 'view/', type: 'directory', mode: 0o755 },
      { path: 'view/current', type: 'symlink', mode: 0o755, target: 'host' },
    ]));

    await restoreVerifiedArtifact(archive, restored);

    assert.equal((await lstat(join(restored, 'view', 'current'))).mode & 0o7777, 0o777, 'Linux represents symlinks as 0777');
    assert.equal(await readFile(join(restored, 'ARCHIVED-SYMLINK-MODES.jsonl'), 'utf8'), '{"path":"current","mode":"0755"}\n');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('restore populates a 0555 directory before applying its restrictive mode', async () => {
  const root = await mkdtemp(join(tmpdir(), 'veles-verified-directory-mode-'));
  try {
    const archive = join(root, 'verified-native.tar');
    const restored = join(root, 'restored');
    await writeFile(archive, createTar([
      { path: 'view/', type: 'directory', mode: 0o555 },
      { path: 'view/host', type: 'file', mode: 0o644, data: 'host' },
    ]));

    await restoreVerifiedArtifact(archive, restored);

    assert.equal(await readFile(join(restored, 'view', 'host'), 'utf8'), 'host');
    assert.equal((await lstat(join(restored, 'view'))).mode & 0o7777, 0o555);
    await chmod(join(restored, 'view'), 0o755);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
