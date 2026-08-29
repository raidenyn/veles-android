import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

async function withRun(run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-native-transport-'));
  try {
    const product = join(root, 'product');
    const view = join(root, 'view');
    await mkdir(product);
    await mkdir(view);
    await writeFile(join(product, 'bridge.zip'), 'package');
    await writeFile(join(product, 'bridge.zip.sha256'), `${sha256('package')}  bridge.zip\n`);
    await writeFile(join(product, 'SHA256SUMS'), [
      `${sha256('package')}  bridge.zip`,
      `${sha256(`${sha256('package')}  bridge.zip\n`)}  bridge.zip.sha256`,
    ].join('\n') + '\n');
    await mkdir(join(view, 'Veles.app'));
    await chmod(join(view, 'Veles.app'), 0o755);
    await writeFile(join(view, 'Veles.app', 'host'), 'host');
    await chmod(join(view, 'Veles.app', 'host'), 0o755);
    await symlink('host', join(view, 'Veles.app', 'current'));
    await run({ root, product, view });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

test('creates a deterministic native transport with the exact evidence entries', async () => {
  const { createRun, readTransport } = await import('../native/create-run.mjs');
  await withRun(async ({ root, product, view }) => {
    const first = join(root, 'first.tar');
    const second = join(root, 'second.tar');
    const options = {
      sourceCommit: '0123456789abcdef0123456789abcdef01234567',
      identity: { ImageOS: 'macOS', ImageVersion: '26.0', RUNNER_ARCH: 'ARM64' },
      product,
      view,
    };
    await createRun({ ...options, output: first });
    await createRun({ ...options, output: second });

    assert.deepEqual(await readFile(first), await readFile(second));
    assert.equal(await readFile(`${first}.sha256`, 'utf8'), `${sha256(await readFile(first))}  first.tar\n`);
    const transport = await readTransport(first, `${first}.sha256`);
    assert.equal(transport.sourceCommit, options.sourceCommit);
    assert.match(transport.manifest, /# ImageOS=macOS\n# ImageVersion=26\.0\n# RUNNER_ARCH=ARM64\n/);
    assert.deepEqual(transport.entries.map((entry) => entry.path), [
      'METADATA.native-bridge.jsonl',
      'SHA256SUMS.native-bridge',
      'SOURCE-COMMIT',
      'product/',
      'product/SHA256SUMS',
      'product/bridge.zip',
      'product/bridge.zip.sha256',
      'view/',
      'view/Veles.app/',
      'view/Veles.app/current',
      'view/Veles.app/host',
    ]);
    assert.equal(transport.entries.find((entry) => entry.path === 'view/Veles.app/current').target, 'host');
    assert.equal(transport.entries.find((entry) => entry.path === 'view/Veles.app/host').mode, 0o755);
  });
});

test('rejects malformed sidecars and unsafe transport entries before extraction', async () => {
  const { createRun, readTransport } = await import('../native/create-run.mjs');
  const { createTar } = await import('../native/deterministic-tar.mjs');
  await withRun(async ({ root, product, view }) => {
    const output = join(root, 'run.tar');
    await createRun({
      output,
      sourceCommit: '0123456789abcdef0123456789abcdef01234567',
      identity: { ImageOS: 'Windows', ImageVersion: '2025', RUNNER_ARCH: 'X64' },
      product,
      view,
    });
    await writeFile(`${output}.sha256`, '0'.repeat(64) + '  run.tar\n');
    await assert.rejects(() => readTransport(output, `${output}.sha256`), (error) => error.exitCode === 1);

    assert.throws(
      () => createTar([{ path: '../escape', type: 'file', data: Buffer.from('bad'), mode: 0o644 }]),
      (error) => error.exitCode === 1,
    );
  });
});
