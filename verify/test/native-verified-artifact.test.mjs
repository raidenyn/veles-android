import assert from 'node:assert/strict';
import { chmod, lstat, mkdir, mkdtemp, readFile, readlink, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { restoreVerifiedArtifact, stageVerifiedArtifact } from '../native/verified-artifact.mjs';

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
