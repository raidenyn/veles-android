// END-TO-END transport/aggregate contract test (OTP-01 1d fix #4).
//
// Proves the aligned contract holds across the whole native verification flow:
//   - create-run hashes ONLY the package + sidecar into the native manifest
//     (the product SHA256SUMS component manifest is transported but excluded
//     from the manifest, and is validated against its own content);
//   - extract-view assembles a view containing ONLY raw product files + the
//     extracted host manifest (no outer package/sidecar/SHA256SUMS duplicates);
//   - compare-runs accepts two identical real-layout transports;
//   - aggregate-checksums accepts the verified tree and emits the aggregate
//     manifest with exactly the expected records; and
//   - aggregate-checksums rejects a view that leaks a component SHA256SUMS or
//     a .zip duplicate of the outer package.
//
// The fixture uses the REAL producer layout: the host manifest JSON is
// archived inside the deterministic package (built here via the real producer
// native-bridge/scripts/package.mjs), and the product dir contains only
// package + sidecar + SHA256SUMS.

import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile, readFile, chmod, symlink, cp } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const scriptDir = fileURLToPath(new URL('../native/', import.meta.url));
const repoRoot = fileURLToPath(new URL('../../', import.meta.url));
const packageScript = join(repoRoot, 'native-bridge', 'scripts', 'package.mjs');
const extractViewScript = join(scriptDir, 'extract-view.mjs');
const createRunScript = join(scriptDir, 'create-run.mjs');
const compareRunsScript = join(scriptDir, 'compare-runs.mjs');
const COMMIT = '0123456789abcdef0123456789abcdef01234567';
const HOST_MANIFEST_NAME = 'app.veles.native_bridge.json';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function runNode(script, args, env, options = {}) {
  const result = spawnSync('node', [script, ...args], { encoding: 'utf8', env: { ...process.env, ...env }, cwd: options.cwd });
  if (result.status !== 0) {
    throw new Error(`node ${script.split('/').at(-1)} ${args.join(' ')} exited ${result.status}: ${result.stderr}`);
  }
  return result;
}

// Run the REAL producer to create the real product layout (package containing
// the host manifest + sidecar + SHA256SUMS) for a platform.
async function produce(root, platform, release, extraEnv = {}) {
  const productRoot = join(root, 'product-root');
  const env = {
    VELES_BRIDGE_PLATFORM: platform,
    VELES_BRIDGE_RELEASE_DIR: release,
    VELES_BRIDGE_BUILD_OUT_DIR: productRoot,
    ...extraEnv,
  };
  runNode(packageScript, [], env);
  return join(productRoot, platform);
}

async function buildWindowsRelease(release) {
  await writeFile(join(release, 'veles-native-bridge.exe'), 'win-host-binary');
  await mkdir(join(release, 'bundle', 'nsis'), { recursive: true });
  await mkdir(join(release, 'bundle', 'msi'), { recursive: true });
  await writeFile(join(release, 'bundle', 'nsis', 'Veles Native Bridge_0.1.0_x64-setup.exe'), 'nsis-installer');
  await writeFile(join(release, 'bundle', 'msi', 'Veles Native Bridge_0.1.0_x64_en-US.msi'), 'msi-installer');
}

async function buildMacosRelease(release) {
  const appBundle = join(release, 'bundle', 'macos', 'Veles Native Bridge.app');
  await mkdir(join(appBundle, 'Contents', 'MacOS'), { recursive: true });
  await writeFile(join(appBundle, 'Contents', 'MacOS', 'veles-native-bridge'), 'mac-host', { mode: 0o755 });
  await chmod(join(appBundle, 'Contents', 'MacOS', 'veles-native-bridge'), 0o755);
  await mkdir(join(release, 'bundle', 'dmg'), { recursive: true });
  await writeFile(join(release, 'bundle', 'dmg', 'Veles Native Bridge_0.1.0_x64.dmg'), 'dmg-installer');
}

// Assemble a run slot: produce the product, extract the view, optionally
// inject a leak into the view (added to BOTH runs identically so compare-runs
// still passes and aggregate's excluded-evidence check is what rejects it),
// create the transport tar. Returns the transport tar path.
async function makeRun(root, slot, platform, release, identity, injectView = null) {
  const productDir = await produce(root, platform, release, platform === 'macos' ? { VELES_BRIDGE_INSTALL_ROOT: '/Applications' } : {});
  const viewDir = join(root, `${slot}-view`);
  runNode(extractViewScript, [platform, release, productDir, viewDir]);
  if (injectView) {
    const target = join(viewDir, injectView.path);
    await mkdir(join(target, '..'), { recursive: true });
    await writeFile(target, injectView.content);
  }
  const transport = join(root, `${slot}.tar`);
  runNode(createRunScript, [transport, COMMIT, identity.ImageOS, identity.ImageVersion, identity.RUNNER_ARCH, productDir, viewDir]);
  return transport;
}

// Build the non-native aggregate fixture inputs (android/web/rust) so
// aggregate-checksums can run end-to-end.
async function buildAggregateInputs(root) {
  // Android: canonical unsigned APK.
  await mkdir(join(root, 'build/verification/android'), { recursive: true });
  await writeFile(join(root, 'build/verification/android/app-release-unsigned.apk'), 'apk-bytes');
  // Web extension: zip + sidecar + SHA256SUMS.
  await mkdir(join(root, 'build/web-extension'), { recursive: true });
  await writeFile(join(root, 'build/web-extension/veles-extension-0.1.0.zip'), 'web-zip');
  await writeFile(join(root, 'build/web-extension/veles-extension-0.1.0.zip.sha256'), `${sha256('web-zip')}  veles-extension-0.1.0.zip\n`);
  await writeFile(join(root, 'build/web-extension/SHA256SUMS'), `${sha256('web-zip')}  veles-extension-0.1.0.zip\n${sha256(`${sha256('web-zip')}  veles-extension-0.1.0.zip\n`)}  veles-extension-0.1.0.zip.sha256\n`);
  // Rust package: jni + wasm + SHA256SUMS.
  await mkdir(join(root, 'build/rust-package/jni/arm64-v8a'), { recursive: true });
  await mkdir(join(root, 'build/rust-package/jni/armeabi-v7a'), { recursive: true });
  await mkdir(join(root, 'build/rust-package/jni/x86_64'), { recursive: true });
  await mkdir(join(root, 'build/rust-package/wasm'), { recursive: true });
  await writeFile(join(root, 'build/rust-package/jni/arm64-v8a/libveles_crypto.so'), 'arm64');
  await writeFile(join(root, 'build/rust-package/jni/armeabi-v7a/libveles_crypto.so'), 'arm');
  await writeFile(join(root, 'build/rust-package/jni/x86_64/libveles_crypto.so'), 'x64');
  await writeFile(join(root, 'build/rust-package/wasm/veles_crypto_bg.wasm'), 'wasm');
  const rustRecords = [
    ['jni/arm64-v8a/libveles_crypto.so', 'arm64'],
    ['jni/armeabi-v7a/libveles_crypto.so', 'arm'],
    ['jni/x86_64/libveles_crypto.so', 'x64'],
    ['wasm/veles_crypto_bg.wasm', 'wasm'],
  ];
  await writeFile(
    join(root, 'build/rust-package/SHA256SUMS'),
    rustRecords.map(([p, c]) => `${sha256(c)}  ${p}`).join('\n') + '\n',
  );
}

// Copy a verified native-bridge tree (from compare-runs --verified-tree output)
// into the aggregate's expected input path.
async function placeNative(root, platform, verified) {
  const dst = join(root, 'build/verification/native-bridge', platform);
  await mkdir(join(dst, '..'), { recursive: true });
  await cp(verified, dst, { recursive: true });
}

test('end-to-end: real producer layout transports, compares, and aggregates successfully', async () => {
  const root = await mkdtemp(join(tmpdir(), 'veles-e2e-'));
  try {
    await buildAggregateInputs(root);
    const winRelease = join(root, 'win-release');
    const macRelease = join(root, 'mac-release');
    await mkdir(winRelease, { recursive: true });
    await mkdir(macRelease, { recursive: true });
    await buildWindowsRelease(winRelease);
    await buildMacosRelease(macRelease);
    const winIdentity = { ImageOS: 'win25', ImageVersion: '2025', RUNNER_ARCH: 'X64' };
    const macIdentity = { ImageOS: 'macos26', ImageVersion: '26.6', RUNNER_ARCH: 'ARM64' };

    // Two independent Windows runs + two independent macOS runs.
    const winA = await makeRun(root, 'win-a', 'windows', winRelease, winIdentity);
    const winB = await makeRun(root, 'win-b', 'windows', winRelease, winIdentity);
    const macA = await makeRun(root, 'mac-a', 'macos', macRelease, macIdentity);
    const macB = await makeRun(root, 'mac-b', 'macos', macRelease, macIdentity);

    const winVerified = join(root, 'win-verified');
    const macVerified = join(root, 'mac-verified');
    runNode(compareRunsScript, [COMMIT, winA, winB, winVerified]);
    runNode(compareRunsScript, [COMMIT, macA, macB, macVerified]);

    await placeNative(root, 'windows', winVerified);
    await placeNative(root, 'macos', macVerified);

    const aggregate = fileURLToPath(new URL('../aggregate-checksums.mjs', import.meta.url));
    runNode(aggregate, [COMMIT], {}, { cwd: root });

    const sums = await readFile(join(root, 'build/verification/SHA256SUMS.toolchains'), 'utf8');
    // The aggregate must contain the native product package + sidecar and the
    // view raw files, but NOT the component SHA256SUMS (it is excluded from
    // records). Spot-check key records are present and SHA256SUMS is absent.
    assert.match(sums, /native-bridge\/windows\/product\/veles-native-bridge-0\.1\.0\.zip$/m);
    assert.match(sums, /native-bridge\/windows\/product\/veles-native-bridge-0\.1\.0\.zip\.sha256$/m);
    assert.match(sums, /native-bridge\/windows\/view\/veles-native-bridge\.exe$/m);
    assert.match(sums, /native-bridge\/windows\/view\/app\.veles\.native_bridge\.json$/m);
    assert.match(sums, /native-bridge\/macos\/product\/veles-native-bridge-0\.1\.0\.tar\.gz$/m);
    assert.match(sums, /native-bridge\/macos\/view\/Veles Native Bridge\.app\/Contents\/MacOS\/veles-native-bridge$/m);
    // The component SHA256SUMS must NOT appear as an aggregate record.
    assert.doesNotMatch(sums, /native-bridge\/windows\/product\/SHA256SUMS$/m);
    assert.doesNotMatch(sums, /native-bridge\/macos\/product\/SHA256SUMS$/m);
    // No outer-package duplicate leaked into the view records.
    assert.doesNotMatch(sums, /native-bridge\/windows\/view\/veles-native-bridge-0\.1\.0\.zip$/m);
    assert.doesNotMatch(sums, /native-bridge\/macos\/view\/veles-native-bridge-0\.1\.0\.tar\.gz$/m);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('aggregate rejects a view that leaks a component SHA256SUMS', async () => {
  const root = await mkdtemp(join(tmpdir(), 'veles-e2e-leak-sums-'));
  try {
    await buildAggregateInputs(root);
    const winRelease = join(root, 'win-release');
    const macRelease = join(root, 'mac-release');
    await mkdir(winRelease, { recursive: true });
    await mkdir(macRelease, { recursive: true });
    await buildWindowsRelease(winRelease);
    await buildMacosRelease(macRelease);
    const winIdentity = { ImageOS: 'win25', ImageVersion: '2025', RUNNER_ARCH: 'X64' };
    const macIdentity = { ImageOS: 'macos26', ImageVersion: '26.6', RUNNER_ARCH: 'ARM64' };
    const winA = await makeRun(root, 'win-a', 'windows', winRelease, winIdentity);
    const winB = await makeRun(root, 'win-b', 'windows', winRelease, winIdentity);
    const macA = await makeRun(root, 'mac-a', 'macos', macRelease, macIdentity);
    const macB = await makeRun(root, 'mac-b', 'macos', macRelease, macIdentity);
    const winVerified = join(root, 'win-verified');
    const macVerified = join(root, 'mac-verified');
    runNode(compareRunsScript, [COMMIT, winA, winB, winVerified]);
    runNode(compareRunsScript, [COMMIT, macA, macB, macVerified]);
    await writeFile(join(winVerified, 'view', 'SHA256SUMS'), 'leaked');
    await writeFile(join(winVerified, 'METADATA.native-bridge.jsonl'), [
      { path: 'SHA256SUMS', type: 'file', mode: '0644', sha256: sha256('leaked') },
      { path: 'app.veles.native_bridge.json', type: 'file', mode: '0644', sha256: sha256(await readFile(join(winVerified, 'view', 'app.veles.native_bridge.json'))) },
      { path: 'bundle', type: 'directory', mode: '0755' },
      { path: 'bundle/msi', type: 'directory', mode: '0755' },
      { path: 'bundle/msi/Veles Native Bridge_0.1.0_x64_en-US.msi', type: 'file', mode: '0644', sha256: sha256('msi-installer') },
      { path: 'bundle/nsis', type: 'directory', mode: '0755' },
      { path: 'bundle/nsis/Veles Native Bridge_0.1.0_x64-setup.exe', type: 'file', mode: '0644', sha256: sha256('nsis-installer') },
      { path: 'veles-native-bridge.exe', type: 'file', mode: '0644', sha256: sha256('win-host-binary') },
    ].map(JSON.stringify).join('\n') + '\n');
    await placeNative(root, 'windows', winVerified);
    await placeNative(root, 'macos', macVerified);

    const aggregate = fileURLToPath(new URL('../aggregate-checksums.mjs', import.meta.url));
    const result = spawnSync('node', [aggregate, COMMIT], { encoding: 'utf8', cwd: root });
    assert.notEqual(result.status, 0, 'aggregate must reject a leaked SHA256SUMS in the view');
    assert.match(result.stderr, /view does not match metadata|excluded evidence/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('aggregate rejects a view that leaks a .zip duplicate of the outer package', async () => {
  const root = await mkdtemp(join(tmpdir(), 'veles-e2e-leak-zip-'));
  try {
    await buildAggregateInputs(root);
    const winRelease = join(root, 'win-release');
    const macRelease = join(root, 'mac-release');
    await mkdir(winRelease, { recursive: true });
    await mkdir(macRelease, { recursive: true });
    await buildWindowsRelease(winRelease);
    await buildMacosRelease(macRelease);
    const winIdentity = { ImageOS: 'win25', ImageVersion: '2025', RUNNER_ARCH: 'X64' };
    const macIdentity = { ImageOS: 'macos26', ImageVersion: '26.6', RUNNER_ARCH: 'ARM64' };
    const winA = await makeRun(root, 'win-a', 'windows', winRelease, winIdentity);
    const winB = await makeRun(root, 'win-b', 'windows', winRelease, winIdentity);
    const macA = await makeRun(root, 'mac-a', 'macos', macRelease, macIdentity);
    const macB = await makeRun(root, 'mac-b', 'macos', macRelease, macIdentity);
    const winVerified = join(root, 'win-verified');
    const macVerified = join(root, 'mac-verified');
    runNode(compareRunsScript, [COMMIT, winA, winB, winVerified]);
    runNode(compareRunsScript, [COMMIT, macA, macB, macVerified]);
    await writeFile(join(winVerified, 'view', 'leaked.zip'), 'leaked');
    await writeFile(join(winVerified, 'METADATA.native-bridge.jsonl'), [
      { path: 'app.veles.native_bridge.json', type: 'file', mode: '0644', sha256: sha256(await readFile(join(winVerified, 'view', 'app.veles.native_bridge.json'))) },
      { path: 'bundle', type: 'directory', mode: '0755' },
      { path: 'bundle/msi', type: 'directory', mode: '0755' },
      { path: 'bundle/msi/Veles Native Bridge_0.1.0_x64_en-US.msi', type: 'file', mode: '0644', sha256: sha256('msi-installer') },
      { path: 'bundle/nsis', type: 'directory', mode: '0755' },
      { path: 'bundle/nsis/Veles Native Bridge_0.1.0_x64-setup.exe', type: 'file', mode: '0644', sha256: sha256('nsis-installer') },
      { path: 'leaked.zip', type: 'file', mode: '0644', sha256: sha256('leaked') },
      { path: 'veles-native-bridge.exe', type: 'file', mode: '0644', sha256: sha256('win-host-binary') },
    ].map(JSON.stringify).join('\n') + '\n');
    await placeNative(root, 'windows', winVerified);
    await placeNative(root, 'macos', macVerified);
    const aggregate = fileURLToPath(new URL('../aggregate-checksums.mjs', import.meta.url));
    const result = spawnSync('node', [aggregate, COMMIT], { encoding: 'utf8', cwd: root });
    assert.notEqual(result.status, 0, 'aggregate must reject a leaked .zip in the view');
    assert.match(result.stderr, /view does not match metadata|excluded evidence/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
