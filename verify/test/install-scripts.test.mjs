import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { verifyCargoBuildScripts } from '../check-cargo-build-scripts.mjs';
import { verifyNpmInstallScripts } from '../check-npm-install-scripts.mjs';

const hash = (text) => createHash('sha256').update(text).digest('hex');

async function npmFixture({ command = 'node install.js', source = 'console.log("verified")', listed = true } = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'veles-install-script-'));
  const packageDirectory = join(directory, 'node_modules', 'fixture');
  await mkdir(packageDirectory, { recursive: true });
  await writeFile(join(packageDirectory, 'install.js'), source);
  await writeFile(join(packageDirectory, 'package.json'), JSON.stringify({ name: 'fixture', version: '1.0.0', scripts: { install: command } }));
  await writeFile(join(directory, 'package-lock.json'), JSON.stringify({ lockfileVersion: 3, packages: {
    'node_modules/fixture': { version: '1.0.0', integrity: 'sha512-fixture', hasInstallScript: true },
  } }));
  const policy = { policyPath: 'test-policy.json', exceptions: listed ? [{
    project: 'fixture-project', package: 'fixture', version: '1.0.0', integrity: 'sha512-fixture', command: 'node install.js',
    referencedFile: 'install.js', referencedFileSha256: hash('console.log("verified")'),
  }] : [] };
  return { directory, policy };
}

test('accepts only the exact reviewed lifecycle command and referenced bytes', async () => {
  const { directory, policy } = await npmFixture();
  await assert.doesNotReject(verifyNpmInstallScripts({ root: directory, project: 'fixture-project', policy }));
});

test('rejects a changed command, changed script bytes, or an unlisted package', async () => {
  for (const options of [{ command: 'node changed.js' }, { source: 'console.log("changed")' }, { listed: false }]) {
    const { directory, policy } = await npmFixture(options);
    await assert.rejects(verifyNpmInstallScripts({ root: directory, project: 'fixture-project', policy }), /test-policy\.json/);
  }
});

test('rejects Cargo checksum drift and suspicious build scripts', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'veles-cargo-script-'));
  const source = join(directory, 'registry', 'fixture-1.0.0');
  await mkdir(source, { recursive: true });
  await writeFile(join(source, '.cargo-checksum.json'), JSON.stringify({ package: 'expected', files: { 'build.rs': hash('println!("safe");') } }));
  await writeFile(join(source, 'build.rs'), 'println!("safe");');
  await assert.doesNotReject(verifyCargoBuildScripts({ sources: [source], policy: { policyPath: 'cargo-policy.json', exceptions: [] } }));
  await writeFile(join(source, 'build.rs'), 'std::process::Command::new("curl");');
  await assert.rejects(verifyCargoBuildScripts({ sources: [source], policy: { policyPath: 'cargo-policy.json', exceptions: [] } }), /checksum|suspicious/i);
});

test('binds Cargo registry sources to the locked package checksum', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'veles-cargo-lock-'));
  const source = join(directory, 'registry', 'fixture-1.0.0');
  await mkdir(source, { recursive: true });
  await writeFile(join(source, '.cargo-checksum.json'), JSON.stringify({ package: 'wrong', files: {} }));
  await assert.rejects(verifyCargoBuildScripts({ sources: [source], policy: { policyPath: 'cargo-policy.json', exceptions: [] }, lockedPackages: new Map([['fixture@1.0.0', 'expected']]) }), /locked checksum/i);
});
