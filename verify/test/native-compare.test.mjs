import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

async function makeRun(root, name, overrides = {}) {
  const { createRun } = await import('../native/create-run.mjs');
  const product = join(root, `${name}-product`);
  const view = join(root, `${name}-view`);
  await mkdir(product);
  await mkdir(view);
  const artifactName = overrides.artifactName ?? 'artifact';
  const artifactContent = overrides.product ?? 'artifact';
  await writeFile(join(product, artifactName), artifactContent);
  const sidecarName = `${artifactName}.sha256`;
  const sidecarContent = `${sha256(artifactContent)}  ${artifactName}\n`;
  await writeFile(join(product, sidecarName), sidecarContent);
  // The component SHA256SUMS is required and must validly cover the package
  // and sidecar the transport carries.
  await writeFile(join(product, 'SHA256SUMS'), overrides.componentSums ?? [
    `${sha256(artifactContent)}  ${artifactName}`,
    `${sha256(sidecarContent)}  ${sidecarName}`,
  ].join('\n') + '\n');
  await writeFile(join(view, 'host'), overrides.host ?? 'host');
  for (const [path, content] of Object.entries(overrides.viewEntries ?? {})) {
    const entry = join(view, path);
    await mkdir(dirname(entry), { recursive: true });
    await writeFile(entry, content);
  }
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

test('accepts separately serialized component SHA256SUMS evidence after validating each local product', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  await withRuns(async (root) => {
    const artifact = 'artifact';
    const sidecar = `${sha256(artifact)}  artifact\n`;
    const left = await makeRun(root, 'left');
    const right = await makeRun(root, 'right', {
      componentSums: [
        `${sha256(sidecar)}  artifact.sha256`,
        `${sha256(artifact)}  artifact`,
      ].join('\n') + '\n',
    });
    await compareRuns('0123456789abcdef0123456789abcdef01234567', left, right);
  });
});

test('reports the first actual product path when a validated package differs', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  await withRuns(async (root) => {
    const left = await makeRun(root, 'left');
    const right = await makeRun(root, 'right', { product: 'changed-artifact' });
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, right),
      (error) => error.exitCode === 1 && error.message.includes('byte mismatch: product/artifact'),
    );
  });
});

test('accepts self-validated Tauri installer transport drift but still compares stable view evidence', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  await withRuns(async (root) => {
    const left = await makeRun(root, 'left', {
      product: 'same package placeholder',
      viewEntries: { 'bundle/nsis/Veles Native Bridge_0.1.0_x64-setup.exe': 'first NSIS payload bytes' },
    });
    const right = await makeRun(root, 'right', {
      product: 'same package placeholder',
      viewEntries: { 'bundle/nsis/Veles Native Bridge_0.1.0_x64-setup.exe': 'second NSIS payload bytes' },
    });
    await compareRuns('0123456789abcdef0123456789abcdef01234567', left, right);

    const changedStableEvidence = await makeRun(root, 'changed-stable-evidence', {
      product: 'same package placeholder',
      host: 'changed raw host binary',
      viewEntries: { 'bundle/nsis/Veles Native Bridge_0.1.0_x64-setup.exe': 'second NSIS payload bytes' },
    });
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, changedStableEvidence),
      (error) => error.exitCode === 1 && error.message.includes('view/host'),
    );
  });
});

test('rejects macOS app host byte drift while allowing other app payloads to vary', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  await withRuns(async (root) => {
    const appHost = 'Veles Native Bridge.app/Contents/MacOS/veles-native-bridge';
    const left = await makeRun(root, 'left', {
      viewEntries: {
        [appHost]: 'first host bytes',
        'Veles Native Bridge.app/Contents/Info.plist': 'first producer timestamp',
      },
    });
    const right = await makeRun(root, 'right', {
      viewEntries: {
        [appHost]: 'second host bytes',
        'Veles Native Bridge.app/Contents/Info.plist': 'second producer timestamp',
      },
    });
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, right),
      (error) => error.exitCode === 1 && error.message.includes(`view/${appHost}`),
    );
  });
});

test('rejects a package that cannot bind its declared ZIP payload to the verification view', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  await withRuns(async (root) => {
    const invalidPackage = {
      artifactName: 'veles-native-bridge-0.1.0.zip',
      product: 'arbitrary text is not a ZIP archive',
      viewEntries: { 'bundle/nsis/Veles Native Bridge_0.1.0_x64-setup.exe': 'installer bytes' },
    };
    const left = await makeRun(root, 'left', invalidPackage);
    const right = await makeRun(root, 'right', invalidPackage);
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, right),
      (error) => error.exitCode === 1 && error.message.includes('native package/view binding mismatch'),
    );
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
    const sidecarContent = `${sha256('artifact')}  artifact\n`;
    await writeFile(join(product, 'artifact.sha256'), sidecarContent);
    await writeFile(join(product, 'SHA256SUMS'), [
      `${sha256('artifact')}  artifact`,
      `${sha256(sidecarContent)}  artifact.sha256`,
    ].join('\n') + '\n');
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

test('compare-runs rejects a transport missing the component SHA256SUMS as a product mismatch', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  const { readTransport } = await import('../native/create-run.mjs');
  const { createTar } = await import('../native/deterministic-tar.mjs');
  await withRuns(async (root) => {
    const left = await makeRun(root, 'left');
    const right = await makeRun(root, 'right');
    // Drop the component SHA256SUMS from the right transport; comparison must
    // reject it as a product mismatch (exit 1), not an environment error.
    const transport = await readTransport(right);
    const filtered = transport.entries.filter((entry) => entry.path !== 'product/SHA256SUMS');
    const tar = createTar(filtered);
    await writeFile(right, tar);
    await writeFile(`${right}.sha256`, `${createHash('sha256').update(tar).digest('hex')}  right.tar\n`);
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, right),
      (error) => error.exitCode === 1 && /component SHA256SUMS is required/.test(error.message),
    );
  });
});

test('compare-runs rejects a transport with a tampered component SHA256SUMS digest as a product mismatch', async () => {
  const { compareRuns } = await import('../native/compare-runs.mjs');
  await withRuns(async (root) => {
    const left = await makeRun(root, 'left');
    const right = await makeRun(root, 'right');
    // Corrupt the component SHA256SUMS digest for artifact.
    await rewriteRun(right, (entry) => {
      if (entry.path !== 'product/SHA256SUMS') return entry;
      const tampered = entry.data.toString('utf8').replace(/^[0-9a-f]{64}  artifact$/m, `${'0'.repeat(64)}  artifact`);
      return { ...entry, data: Buffer.from(tampered) };
    });
    await assert.rejects(
      () => compareRuns('0123456789abcdef0123456789abcdef01234567', left, right),
      (error) => error.exitCode === 1 && /component SHA256SUMS mismatch: artifact/.test(error.message),
    );
  });
});
