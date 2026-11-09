import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import {
  createStandardManifest,
  parseNativeManifest,
  parseStandardManifest,
} from '../lib/checksum-manifest.mjs';
import { EXIT_ERROR, EXIT_MISMATCH } from '../lib/exit-codes.mjs';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

async function withTree(files, run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-manifest-'));
  try {
    for (const [path, content] of Object.entries(files)) {
      const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
      if (parent) await mkdir(join(root, parent), { recursive: true });
      await writeFile(join(root, path), content);
    }
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function isMismatch(error) {
  return error?.exitCode === EXIT_MISMATCH;
}

function isError(error) {
  return error?.exitCode === EXIT_ERROR;
}

test('creates lowercase SHA-256 records with two spaces, UTF-8 byte order, and one LF', async () => {
  await withTree({ '\uE000': 'private', '\u{10000}': 'supplementary' }, async (root) => {
    const manifest = await createStandardManifest(root, ['\uE000', '\u{10000}']);

    assert.equal(
      manifest,
      `${sha256('private')}  \uE000\n${sha256('supplementary')}  \u{10000}\n`,
    );
  });
});

test('rejects malformed standard-manifest evidence without normalizing it', () => {
  const digest = 'a'.repeat(64);
  const cases = [
    `${digest}  a\r\n`,
    `# comment\n${digest}  a\n`,
    `${digest}  SHA256SUMS\n`,
    `${digest}  a\n${digest}  a\n`,
    `${'b'.repeat(64)}  b\n${digest}  a\n`,
    `${digest}  /absolute\n`,
    `${digest}  a//b\n`,
    `${digest}  a/./b\n`,
    `${digest}  a/../b\n`,
  ];

  for (const text of cases) {
    assert.throws(
      () => parseStandardManifest(text, { selfPath: 'SHA256SUMS' }),
      isMismatch,
      text,
    );
  }
});

test('rejects empty paths and missing final LF', () => {
  const digest = 'a'.repeat(64);
  assert.throws(() => parseStandardManifest(`${digest}  \n`), isMismatch);
  assert.throws(() => parseStandardManifest(`${digest}  a`), isMismatch);
});

test('rejects invalid writer paths before filesystem access', async () => {
  await withTree({ a: 'value' }, async (root) => {
    for (const path of ['', '/a', 'a//b', 'a/./b', 'a/../b', '..\\secret', 'C:\\absolute', '\\\\server\\share']) {
      await assert.rejects(() => createStandardManifest(root, [path]), isError, path);
    }
  });
});

test('rejects Windows path spellings in parsed evidence without normalizing them', () => {
  const digest = 'a'.repeat(64);
  for (const path of ['..\\secret', 'C:\\absolute', '\\\\server\\share']) {
    assert.throws(() => parseStandardManifest(`${digest}  ${path}\n`), isMismatch, path);
  }
});

test('parses native manifests only with the required ordered identity headers', () => {
  const digest = 'a'.repeat(64);
  const valid = `# ImageOS=macos\n# ImageVersion=26.0\n# RUNNER_ARCH=ARM64\n${digest}  host\n`;

  assert.deepEqual(parseNativeManifest(valid), {
    identity: { ImageOS: 'macos', ImageVersion: '26.0', RUNNER_ARCH: 'ARM64' },
    checksums: new Map([['host', digest]]),
  });

  for (const text of [
    valid.replace('# ImageOS=macos\n', ''),
    valid.replace('# ImageOS=macos\n# ImageVersion=26.0', '# ImageVersion=26.0\n# ImageOS=macos'),
    valid.replace('# ImageVersion=26.0', '# Unknown=value'),
    valid.replace('# RUNNER_ARCH=ARM64', '# RUNNER_ARCH='),
    valid.replace(`${digest}  host`, `# ImageOS=other\n${digest}  host`),
    valid.replace(`${digest}  host`, `# Unknown=value\n${digest}  host`),
  ]) {
    assert.throws(() => parseNativeManifest(text), isError);
  }
});

test('classifies generic post-identity comments as malformed standard evidence', () => {
  const digest = 'a'.repeat(64);
  for (const line of ['# comment', '#not-a-header']) {
    const manifest = `# ImageOS=macos\n# ImageVersion=26.0\n# RUNNER_ARCH=ARM64\n${line}\n${digest}  host\n`;
    assert.throws(() => parseNativeManifest(manifest), isMismatch, line);
  }
});
