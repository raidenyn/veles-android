import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function updateChecksum(header) {
  header.fill(0x20, 148, 156);
  Buffer.from(header.reduce((sum, byte) => sum + byte, 0).toString(8).padStart(7, '0') + '\0').copy(header, 148);
}

function headerOffset(tar, index) {
  let offset = 0;
  for (let current = 0; current < index; current += 1) {
    const size = Number.parseInt(tar.subarray(offset + 124, offset + 136).toString('utf8'), 8);
    offset += 512 + size + ((512 - (size % 512)) % 512);
  }
  return offset;
}

async function rewriteTransport(tarPath, mutate) {
  const tar = Buffer.from(await readFile(tarPath));
  mutate(tar, (index) => tar.subarray(headerOffset(tar, index), headerOffset(tar, index) + 512));
  for (let offset = 0; offset + 512 <= tar.length && tar[offset] !== 0; offset += 512 + Number.parseInt(tar.subarray(offset + 124, offset + 136).toString('utf8'), 8) + ((512 - (Number.parseInt(tar.subarray(offset + 124, offset + 136).toString('utf8'), 8) % 512)) % 512)) {
    updateChecksum(tar.subarray(offset, offset + 512));
  }
  await writeFile(tarPath, tar);
  await writeFile(`${tarPath}.sha256`, `${sha256(tar)}  ${tarPath.split('/').at(-1)}\n`);
}

async function withRun(run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-native-transport-'));
  try {
    const product = join(root, 'product');
    const view = join(root, 'view');
    await mkdir(product);
    await mkdir(view);
    await writeFile(join(product, 'bridge.zip'), 'package');
    await writeFile(join(product, 'bridge.zip.sha256'), `${sha256('package')}  bridge.zip\n`);
    // The component SHA256SUMS is required and must validly cover the package
    // and sidecar the transport carries.
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

test('rejects adversarial USTAR entries before transport extraction', async () => {
  const { createRun, readTransport } = await import('../native/create-run.mjs');
  await withRun(async ({ root, product, view }) => {
    for (const [name, mutate] of [
      ['duplicate', (_tar, header) => { Buffer.from('view/\0').copy(header(3), 0); }],
      ['hard link', (_tar, header) => { header(4)[156] = '1'.charCodeAt(0); }],
      ['device', (_tar, header) => { header(4)[156] = '3'.charCodeAt(0); }],
      ['unexpected', (_tar, header) => { Buffer.from('unexpected/\0').copy(header(3), 0); }],
      ['absolute symlink', (_tar, header) => { Buffer.from('/outside\0').copy(header(9), 157); }],
      ['escaping symlink', (_tar, header) => { Buffer.from('../../../outside\0').copy(header(9), 157); }],
      ['nonzero uid', (_tar, header) => { Buffer.from('0000001\0').copy(header(0), 108); }],
      ['nonzero mtime', (_tar, header) => { Buffer.from('00000000001\0').copy(header(0), 136); }],
      ['wrong USTAR version', (_tar, header) => { Buffer.from('01').copy(header(0), 263); }],
      ['USTAR prefix', (_tar, header) => { Buffer.from('prefix\0').copy(header(0), 345); }],
    ]) {
      const output = join(root, `${name}.tar`);
      await createRun({
        output,
        sourceCommit: '0123456789abcdef0123456789abcdef01234567',
        identity: { ImageOS: 'Windows', ImageVersion: '2025', RUNNER_ARCH: 'X64' },
        product,
        view,
      });
      await rewriteTransport(output, mutate);
      await assert.rejects(() => readTransport(output), (error) => error.exitCode === 1, name);
    }
  });
});

test('rejects symlinked transport roots before emission', async () => {
  const { createRun, readTransport } = await import('../native/create-run.mjs');
  await withRun(async ({ root, product, view }) => {
    for (const [name, index, target] of [
      ['product escape', 3, '..'],
      ['view symlink', 7, '.'],
    ]) {
      const output = join(root, `${name}.tar`);
      await createRun({
        output,
        sourceCommit: '0123456789abcdef0123456789abcdef01234567',
        identity: { ImageOS: 'Windows', ImageVersion: '2025', RUNNER_ARCH: 'X64' },
        product,
        view,
      });
      await rewriteTransport(output, (_tar, header) => {
        header(index)[156] = '2'.charCodeAt(0);
        Buffer.from(`${target}\0`).copy(header(index), 157);
      });
      await assert.rejects(() => readTransport(output), (error) => error.exitCode === 1, name);
    }
  });
});

test('create-run requires a component SHA256SUMS in the product dir', async () => {
  const { createRun } = await import('../native/create-run.mjs');
  const root = await mkdtemp(join(tmpdir(), 'veles-native-transport-nosums-'));
  try {
    const product = join(root, 'product');
    const view = join(root, 'view');
    await mkdir(product);
    await mkdir(view);
    await writeFile(join(product, 'bridge.zip'), 'package');
    await writeFile(join(product, 'bridge.zip.sha256'), `${sha256('package')}  bridge.zip\n`);
    // No SHA256SUMS — transport creation must reject it as a product mismatch.
    await assert.rejects(
      () => createRun({
        output: join(root, 'run.tar'),
        sourceCommit: '0123456789abcdef0123456789abcdef01234567',
        identity: { ImageOS: 'Windows', ImageVersion: '2025', RUNNER_ARCH: 'X64' },
        product,
        view,
      }),
      (error) => error.exitCode === 1 && /component SHA256SUMS is required/.test(error.message),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('create-run rejects a component SHA256SUMS with a tampered digest', async () => {
  const { createRun } = await import('../native/create-run.mjs');
  const root = await mkdtemp(join(tmpdir(), 'veles-native-transport-tampered-'));
  try {
    const product = join(root, 'product');
    const view = join(root, 'view');
    await mkdir(product);
    await mkdir(view);
    await writeFile(join(product, 'bridge.zip'), 'package');
    await writeFile(join(product, 'bridge.zip.sha256'), `${sha256('package')}  bridge.zip\n`);
    // Component SHA256SUMS with a wrong digest for bridge.zip (tampered).
    await writeFile(join(product, 'SHA256SUMS'), [
      `${'0'.repeat(64)}  bridge.zip`,
      `${sha256(`${sha256('package')}  bridge.zip\n`)}  bridge.zip.sha256`,
    ].join('\n') + '\n');
    await assert.rejects(
      () => createRun({
        output: join(root, 'run.tar'),
        sourceCommit: '0123456789abcdef0123456789abcdef01234567',
        identity: { ImageOS: 'Windows', ImageVersion: '2025', RUNNER_ARCH: 'X64' },
        product,
        view,
      }),
      (error) => error.exitCode === 1 && /component SHA256SUMS mismatch: bridge\.zip/.test(error.message),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
