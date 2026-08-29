import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const ZIP = 'veles-extension-0.1.0.zip';
const SIDECAR = `${ZIP}.sha256`;
const sha256 = (value) => createHash('sha256').update(value).digest('hex');

async function withArtifacts(files, run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-web-verifier-'));
  try {
    for (const [path, content] of Object.entries(files)) {
      await mkdir(join(root, path.slice(0, path.lastIndexOf('/'))), { recursive: true });
      await writeFile(join(root, path), content);
    }
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function manifest(zip, sidecar) {
  return `${sha256(zip)}  ${ZIP}\n${sha256(sidecar)}  ${SIDECAR}\n`;
}

test('rejects an invalid candidate manifest before starting Docker', async () => {
  const { verifyWebArtifacts } = await import('../verify-web.mjs');
  await withArtifacts({
    [`candidate/${ZIP}`]: 'candidate zip',
    [`candidate/${SIDECAR}`]: 'candidate sidecar',
    'candidate/SHA256SUMS': `${'0'.repeat(64)}  ${ZIP}\n${sha256('candidate sidecar')}  ${SIDECAR}\n`,
    [`reference/${ZIP}`]: 'reference zip',
    [`reference/${SIDECAR}`]: 'reference sidecar',
    'reference/SHA256SUMS': manifest('reference zip', 'reference sidecar'),
  }, async (root) => {
    let dockerStarted = false;
    await assert.rejects(
      () => verifyWebArtifacts(join(root, 'candidate'), join(root, 'reference'), () => {
        dockerStarted = true;
      }),
      (error) => error.exitCode === 1 && /candidate.*checksum mismatch.*\.zip/.test(error.message),
    );
    assert.equal(dockerStarted, false);
  });
});

test('reports the first byte-different artifact path and both hashes', async () => {
  const { verifyWebArtifacts } = await import('../verify-web.mjs');
  const candidateZip = 'candidate zip';
  const referenceZip = 'reference zip';
  const sidecar = 'sidecar';
  await withArtifacts({
    [`candidate/${ZIP}`]: candidateZip,
    [`candidate/${SIDECAR}`]: sidecar,
    'candidate/SHA256SUMS': manifest(candidateZip, sidecar),
    [`reference/${ZIP}`]: referenceZip,
    [`reference/${SIDECAR}`]: sidecar,
    'reference/SHA256SUMS': manifest(referenceZip, sidecar),
  }, async (root) => {
    await assert.rejects(
      () => verifyWebArtifacts(join(root, 'candidate'), join(root, 'reference')),
      (error) => error.exitCode === 1
        && error.message === `byte mismatch: ${ZIP}\ncandidate: ${sha256(candidateZip)}\nreference: ${sha256(referenceZip)}`,
    );
  });
});

test('maps Node/npm pin drift and Docker failure to exit 2', async () => {
  const { verifyWebPreparation } = await import('../verify-web.mjs');
  for (const failure of [
    () => verifyWebPreparation({ nodeVersion: 'v26.8.0', npmVersion: '11.19.0' }),
    () => verifyWebPreparation({ nodeVersion: 'v26.8.1', npmVersion: '11.19.1' }),
    () => verifyWebPreparation({ nodeVersion: 'v26.8.1', npmVersion: '11.19.0', dockerStatus: 1 }),
  ]) {
    assert.throws(failure, (error) => error.exitCode === 2);
  }
});
