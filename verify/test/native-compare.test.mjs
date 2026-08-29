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
