import assert from 'node:assert/strict';
import { mkdir, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const SCRIPT = join(import.meta.dirname, '..', 'check-npm-licenses.mjs');

// Run check-npm-licenses.mjs from a controlled cwd that mimics the repo layout
// the script expects, with the verification binaries deliberately absent. A
// missing tool binary must surface as an environment error (exit 2), not a
// policy mismatch (exit 1).
async function withSkeleton(run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-npm-licenses-'));
  try {
    // Minimal layout: .license-policy.json, empty project dirs, an empty
    // verify/node_modules/.bin (so license-checker is absent), and an empty
    // build/verify-tools/cargo-deny/bin (so cargo-deny is absent).
    await writeFile(join(root, '.license-policy.json'), '{"allowed":[],"denied":[]}');
    await mkdir(join(root, 'web-extension'), { recursive: true });
    await mkdir(join(root, 'native-bridge'), { recursive: true });
    await mkdir(join(root, 'verify', 'node_modules', '.bin'), { recursive: true });
    await mkdir(join(root, 'build', 'verify-tools', 'cargo-deny', 'bin'), { recursive: true });
    await mkdir(join(root, 'build', 'verification', 'supply-chain'), { recursive: true });
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function runScript(cwd) {
  return spawnSync(process.execPath, [SCRIPT], { cwd, encoding: 'utf8' });
}

test('a missing license-checker-rseidelsohn binary exits 2 (environment error, not mismatch)', async () => {
  await withSkeleton((root) => {
    const result = runScript(root);
    assert.equal(result.status, 2, `expected exit 2, got ${result.status}\nstderr: ${result.stderr}`);
    assert.match(result.stderr, /cannot run|failed/i);
  });
});

test('a missing cargo-deny binary exits 2 (environment error, not mismatch)', async () => {
  await withSkeleton(async (root) => {
    // Provide a working license-checker-rseidelsohn stub so the npm phase
    // passes and the script reaches the cargo-deny phase, which must then
    // fail with exit 2 because cargo-deny is absent.
    const stubDir = join(root, 'verify', 'node_modules', '.bin');
    const stub = join(stubDir, 'license-checker-rseidelsohn');
    await writeFile(stub, `#!/usr/bin/env node\nprocess.stdout.write('{}');\n`);
    await writeFile(`${stub}.json`, '{}');
    const { chmod } = await import('node:fs/promises');
    await chmod(stub, 0o755);
    const result = runScript(root);
    assert.equal(result.status, 2, `expected exit 2, got ${result.status}\nstderr: ${result.stderr}`);
  });
});