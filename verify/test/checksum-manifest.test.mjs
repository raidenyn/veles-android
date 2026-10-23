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
import { compareTrees, verifyManifestTree } from '../lib/filesystem-tree.mjs';
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

test('classifies invalid API inputs and filesystem failures as exit 2', async () => {
  await withTree({ a: 'value' }, async (root) => {
    await assert.rejects(() => createStandardManifest(root, 'a'), isError);
    await assert.rejects(() => createStandardManifest(root, ['missing']), isError);
    await assert.rejects(() => verifyManifestTree(root, {}), isError);
    await assert.rejects(() => compareTrees(root, root, 'a'), isError);
    await assert.rejects(() => compareTrees(root, root, ['missing']), isError);
    await assert.rejects(() => verifyManifestTree(join(root, 'missing'), new Map()), isError);
  });
});

test('validates checksum-map paths before reading a tree', async () => {
  await withTree({ a: 'value' }, async (root) => {
    await assert.rejects(
      () => verifyManifestTree(root, new Map([['..\\secret', sha256('value')]])),
      isError,
    );
  });
});

test('detects missing, changed, and unexpected tree entries', async () => {
  await withTree({ a: 'one', extra: 'two' }, async (root) => {
    const checksums = new Map([['a', sha256('one')], ['missing', sha256('none')]]);
    await assert.rejects(() => verifyManifestTree(root, checksums), isMismatch);

    const expected = new Map([['a', sha256('one')]]);
    await assert.rejects(() => verifyManifestTree(root, expected), isMismatch);

    await rm(join(root, 'extra'));
    await writeFile(join(root, 'a'), 'changed');
    await assert.rejects(() => verifyManifestTree(root, expected), isMismatch);
  });
});

test('treats a non-POSIX filename discovered in an artifact tree as a mismatch', async () => {
  await withTree({ 'bad\\name': 'value' }, async (root) => {
    await assert.rejects(() => verifyManifestTree(root, new Map()), isMismatch);
  });
});

test('compares listed files by bytes and rejects invalid comparison paths', async () => {
  await withTree({ 'left/a': 'same', 'right/a': 'same' }, async (root) => {
    await compareTrees(join(root, 'left'), join(root, 'right'), ['a']);
    await writeFile(join(root, 'right/a'), 'different');
    await assert.rejects(
      () => compareTrees(join(root, 'left'), join(root, 'right'), ['a']),
      isMismatch,
    );
    await assert.rejects(
      () => compareTrees(join(root, 'left'), join(root, 'right'), ['../a']),
      isError,
    );
  });
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
