import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

async function makeRun(root, name, overrides = {}) {
  const { createRun } = await import('../native/create-run.mjs');
  const product = join(root, `${name}-product`);
  const view = join(root, `${name}-view`);
  await mkdir(product);
  await mkdir(view);
  await writeFile(join(product, 'artifact'), overrides.product ?? 'artifact');
  await writeFile(join(view, 'host'), overrides.host ?? 'host');
  const output = join(root, `${name}.tar`);
  await createRun({
    output,
    sourceCommit: overrides.sourceCommit ?? '0123456789abcdef0123456789abcdef01234567',
    identity: overrides.identity ?? { ImageOS: 'Windows', ImageVersion: '2025', RUNNER_ARCH: 'X64' },
    product,
    view,
  });
  return output;
}

async function withRuns(run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-native-compare-'));
  try {
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

async function rewriteRun(tarPath, mutate) {
  const { readTransport } = await import('../native/create-run.mjs');
  const { createTar } = await import('../native/deterministic-tar.mjs');
  const transport = await readTransport(tarPath);
  const tar = createTar(transport.entries.map((entry) => mutate(entry) ?? entry));
  await writeFile(tarPath, tar);
  await writeFile(`${tarPath}.sha256`, `${createHash('sha256').update(tar).digest('hex')}  ${tarPath.split('/').at(-1)}\n`);
}

test('rejects source and runner identity gates as environment errors', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  await withRuns(async (root) => {
    const left = await makeRun(root, 'left');
    const sourceMismatch = await makeRun(root, 'source', { sourceCommit: 'fedcba9876543210fedcba9876543210fedcba98' });
    await assert.rejects(() => compareRuns('0123456789abcdef0123456789abcdef01234567', left, sourceMismatch), (error) => error.exitCode === 2);

    const identityMismatch = await makeRun(root, 'identity', {
      identity: { ImageOS: 'Windows', ImageVersion: '2026', RUNNER_ARCH: 'X64' },
    });
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, identityMismatch),
      (error) => error.exitCode === 2 && error.message.includes('re-run on matched image'),
    );

    for (const manifest of [
      '# ImageVersion=2025\n# RUNNER_ARCH=X64\n',
      '# ImageOS=\n# ImageVersion=2025\n# RUNNER_ARCH=X64\n',
      '# RUNNER_ARCH=X64\n# ImageOS=Windows\n# ImageVersion=2025\n',
    ]) {
      const malformed = await makeRun(root, `identity-${manifest.length}`);
      await rewriteRun(malformed, (entry) => entry.path === 'SHA256SUMS.native-bridge'
        ? { ...entry, data: Buffer.from(`${manifest}not-a-manifest\n`) }
        : entry);
      await assert.rejects(
        () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, malformed),
        (error) => error.exitCode === 2,
      );
    }

    const ordered = await makeRun(root, 'ordered', {
      identity: { ImageOS: 'Windows', ImageVersion: '2026', RUNNER_ARCH: 'X64' },
    });
    await rewriteRun(ordered, (entry) => entry.path === 'product/artifact'
      ? { ...entry, data: Buffer.from('corrupt-product') }
      : entry);
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, ordered),
      (error) => error.exitCode === 2 && error.message.includes('re-run on matched image'),
    );
  });
});

test('reports the first product byte drift and emits a tree only after a verified comparison', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  await withRuns(async (root) => {
    const left = await makeRun(root, 'left');
    const right = await makeRun(root, 'right', { host: 'changed' });
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, right),
      (error) => error.exitCode === 1 && error.message.includes('view/host') && error.message.includes('left:') && error.message.includes('right:'),
    );

    const matching = await makeRun(root, 'matching');
    const output = join(root, 'verified');
    await compareRuns('0123456789abcdef0123456789abcdef01234567', left, matching, output);
    assert.equal(await readFile(join(output, 'view', 'host'), 'utf8'), 'host');
    assert.equal(await readFile(join(output, 'product', 'artifact'), 'utf8'), 'artifact');
  });
});

test('rejects metadata mode drift even when archived view bytes match', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  const { readTransport } = await import('../native/create-run.mjs');
  const { createTar } = await import('../native/deterministic-tar.mjs');
  await withRuns(async (root) => {
    const left = await makeRun(root, 'left');
    const right = await makeRun(root, 'right');
    const transport = await readTransport(right);
    const metadata = transport.metadata.replace(/"mode":"[0-7]{4}"/, '"mode":"0000"');
    assert.notEqual(metadata, transport.metadata);
    const tar = createTar(transport.entries.map((entry) => entry.path === 'METADATA.native-bridge.jsonl'
      ? { ...entry, data: Buffer.from(metadata) }
      : entry));
    await writeFile(right, tar);
    await writeFile(`${right}.sha256`, `${createHash('sha256').update(tar).digest('hex')}  right.tar\n`);
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, right),
      (error) => error.exitCode === 1 && error.message.includes('native metadata mismatch: host'),
    );
  });
});

test('rejects metadata symlink target drift even when archived target is unchanged', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  const { createRun } = await import('../native/create-run.mjs');
  await withRuns(async (root) => {
    const left = await makeRun(root, 'left');
    const product = join(root, 'right-product');
    const view = join(root, 'right-view');
    await mkdir(product);
    await mkdir(view);
    await writeFile(join(product, 'artifact'), 'artifact');
    await writeFile(join(view, 'host'), 'host');
    await (await import('node:fs/promises')).symlink('host', join(view, 'current'));
    const right = join(root, 'right.tar');
    await createRun({ output: right, sourceCommit: '0123456789abcdef0123456789abcdef01234567', identity: { ImageOS: 'Windows', ImageVersion: '2025', RUNNER_ARCH: 'X64' }, product, view });
    await rewriteRun(right, (entry) => entry.path === 'METADATA.native-bridge.jsonl'
      ? {
        ...entry,
        data: Buffer.from(entry.data.toString('utf8')
          .replace('"target":"host"', '"target":"other"')
          .replace(createHash('sha256').update('host').digest('hex'), createHash('sha256').update('other').digest('hex')),
        ),
      }
      : entry);
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, right),
      (error) => error.exitCode === 1 && error.message.includes('native metadata mismatch: current'),
    );
  });
});
