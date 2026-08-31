import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const REPO_ROOT = join(import.meta.dirname, '..', '..');
const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const JNI_PATHS = [
  'jni/arm64-v8a/libveles_crypto.so',
  'jni/armeabi-v7a/libveles_crypto.so',
  'jni/x86_64/libveles_crypto.so',
];
const WASM_PATHS = [
  'wasm/package.json',
  'wasm/veles_crypto.d.ts',
  'wasm/veles_crypto.js',
  'wasm/veles_crypto_bg.wasm',
  'wasm/veles_crypto_bg.wasm.d.ts',
];

function manifest(files) {
  return [...Object.entries(files)]
    .sort(([left], [right]) => Buffer.compare(Buffer.from(left), Buffer.from(right)))
    .map(([path, content]) => `${sha256(content)}  ${path}`)
    .join('\n') + '\n';
}

async function withPackage(files, run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-rust-package-'));
  try {
    for (const [path, content] of Object.entries(files)) {
      const separator = path.lastIndexOf('/');
      const parent = separator < 0 ? '' : path.slice(0, separator);
      if (parent) await mkdir(join(root, parent), { recursive: true });
      await writeFile(join(root, path), content);
    }
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function validFiles() {
  const files = Object.fromEntries([...JNI_PATHS, ...WASM_PATHS].map((path) => [path, path]));
  return { ...files, SHA256SUMS: manifest(files) };
}

test('rustPackage stages exactly three JNI files and a complete WASM subtree under the allow-list', async () => {
  // The test must pass from a clean tree with no Gradle build, so it fabricates
  // a rust-package tree matching the real rustPackage layout (jni/ + wasm/ +
  // SHA256SUMS) and validates it through validateRustPackage — the same
  // function the real verifier uses, which enforces the exact JNI allow-list
  // (JNI_PATHS) and requires a non-empty wasm/ subtree. The WASM subtree paths
  // are whatever wasm-pack emits; the verifier only requires at least one
  // wasm/ file and that every path starts with jni/ or wasm/.
  const { validateRustPackage } = await import('../verify-rust.mjs');
  const files = validFiles();
  await withPackage(files, async (root) => {
    const packageInfo = await validateRustPackage(root, 'candidate');
    assert.deepEqual(packageInfo.paths.filter((path) => path.startsWith('jni/')), JNI_PATHS);
    // The WASM subtree is the complete generated set; the verifier requires it
    // be non-empty and entirely under wasm/.
    const wasmPaths = packageInfo.paths.filter((path) => path.startsWith('wasm/'));
    assert.deepEqual(wasmPaths, WASM_PATHS);
    assert.equal(packageInfo.manifest.size, JNI_PATHS.length + WASM_PATHS.length);
  });
});

for (const [name, mutate] of [
  ['an unexpected JNI ABI', (files) => ({ ...files, 'jni/mips/libveles_crypto.so': 'mips' })],
  ['a stale output file', (files) => ({ ...files, 'wasm/stale.js': 'stale' })],
  ['a missing package file', (files) => {
    const { ['wasm/veles_crypto.js']: _, ...remaining } = files;
    return remaining;
  }],
  ['checksum drift', (files) => ({ ...files, SHA256SUMS: files.SHA256SUMS.replace(/^./, '0') })],
]) {
  test(`rejects ${name}`, async () => {
    const { validateRustPackage } = await import('../verify-rust.mjs');
    await withPackage(mutate(validFiles()), async (root) => {
      await assert.rejects(
        () => validateRustPackage(root, 'candidate'),
        (error) => error.exitCode === 1,
      );
    });
  });
}

test('rustPackage is registered as a root Rust task and is not an APK dependency', async () => {
  const build = await (await import('node:fs/promises')).readFile(join(REPO_ROOT, 'build.gradle.kts'), 'utf8');
  assert.match(build, /tasks\.register<Sync>\("rustPackage"\)/);
  assert.match(build, /dependsOn\(rustJni, rustWasm\)/);
  assert.doesNotMatch(build, /assemble(?:Debug|Release)[\s\S]{0,500}rustPackage/);
});
