import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { scanRemoteCode } from '../check-remote-code.mjs';

async function fixture(files) {
  const directory = await mkdtemp(join(tmpdir(), 'veles-remote-code-'));
  for (const [path, text] of Object.entries(files)) {
    await mkdir(join(directory, path, '..'), { recursive: true });
    await writeFile(join(directory, path), text);
  }
  return directory;
}

test('rejects remote CSP sources, updater wiring, npx, and shell-pipe downloads in scanned sources', async () => {
  for (const [path, text] of Object.entries({
    'extension/manifest.json': '{"content_security_policy":"script-src https://cdn.example"}',
    'bridge/tauri.conf.json': '{"plugins":{"updater":{"endpoints":["https://example"]}}}',
    'scripts/build.sh': 'npx esbuild',
    'scripts/install.sh': 'curl https://example | sh',
  })) {
    const directory = await fixture({ [path]: text });
    await assert.rejects(scanRemoteCode(directory, [path]), /remote-code/i);
  }
});

test('allows documentation and lockfile URLs outside declared source scopes', async () => {
  const directory = await fixture({
    'README.md': 'See https://example.com/docs',
    'package-lock.json': '{"resolved":"https://registry.npmjs.org/example.tgz"}',
    'src/main.ts': 'export const local = true;',
  });
  await assert.doesNotReject(scanRemoteCode(directory, ['src/main.ts']));
});

test('allows remote-code examples in comments while rejecting executable statements', async () => {
  const directory = await fixture({ 'src/main.ts': '// npx esbuild\nconst command = "curl | sh";\nexport const local = true;' });
  await assert.doesNotReject(scanRemoteCode(directory, ['src/main.ts']));
});

test('rejects a shell-pipe download passed to a JavaScript process executor', async () => {
  const directory = await fixture({ 'src/main.ts': 'exec("curl https://example.invalid/install | sh");' });
  await assert.rejects(scanRemoteCode(directory, ['src/main.ts']), /shell-pipe download/);
});

test('rejects shell-pipe downloads passed to synchronous executors', async () => {
  const directory = await fixture({ 'src/main.ts': 'execSync("curl https://example.invalid | sh");\nspawnSync("curl https://example.invalid | sh");' });
  await assert.rejects(scanRemoteCode(directory, ['src/main.ts']), /shell-pipe download/);
});
