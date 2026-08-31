import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const digest = (value) => createHash('sha256').update(value).digest('hex');

async function withTree(files, run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-aggregate-'));
  try {
    for (const [path, content] of Object.entries(files)) {
      await mkdir(join(root, path, '..'), { recursive: true, mode: 0o755 });
      if (typeof content === 'object' && content.link) await symlink(content.link, join(root, path));
      else if (typeof content === 'object' && content.directory) await mkdir(join(root, path), { recursive: true, mode: 0o755 });
      else await writeFile(join(root, path), content, { mode: 0o644 });
    }
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function manifest(files) {
  return Object.entries(files)
    .sort(([left], [right]) => Buffer.compare(Buffer.from(left), Buffer.from(right)))
    .map(([path, content]) => `${digest(content)}  ${path}`)
    .join('\n') + '\n';
}

async function runAggregate(root) {
  const { aggregateChecksums } = await import('../aggregate-checksums.mjs');
  return aggregateChecksums(root, '0123456789abcdef0123456789abcdef01234567');
}

const nativeMetadata = {
  windows: `${JSON.stringify({ path: 'host.exe', type: 'file', mode: '0644', sha256: digest('windows-host') })}\n`,
  macos: [
    { path: 'Veles.app', type: 'directory', mode: '0755' },
    { path: 'Veles.app/Contents', type: 'directory', mode: '0755' },
    { path: 'Veles.app/Contents/MacOS', type: 'directory', mode: '0755' },
    { path: 'Veles.app/Contents/MacOS/current', type: 'symlink', mode: '0777', target: 'host', sha256: digest('host') },
    { path: 'Veles.app/Contents/MacOS/host', type: 'file', mode: '0644', sha256: digest('mac-host') },
  ].map(JSON.stringify).join('\n') + '\n',
};

const requiredFiles = {
  'build/verification/android/app-release-unsigned.apk': 'apk',
  'build/web-extension/veles-extension-0.1.0.zip': 'web',
  'build/web-extension/veles-extension-0.1.0.zip.sha256': 'web-sidecar',
  'build/rust-package/jni/arm64-v8a/libveles_crypto.so': 'arm64',
  'build/rust-package/jni/armeabi-v7a/libveles_crypto.so': 'arm',
  'build/rust-package/jni/x86_64/libveles_crypto.so': 'x64',
  'build/rust-package/wasm/veles_crypto_bg.wasm': 'wasm',
  'build/verification/native-bridge/windows/product/veles-native.zip': 'windows-package',
  'build/verification/native-bridge/windows/product/veles-native.zip.sha256': 'windows-sidecar',
  // The real producer (native-bridge/scripts/package.mjs writeChecksumManifest)
  // writes a product-level SHA256SUMS into the product dir. The transport
  // carries it as product/SHA256SUMS; the aggregate accepts it but excludes it
  // from records (it is a component checksum manifest, not a verified product
  // file per the design's aggregate exclusion list).
  'build/verification/native-bridge/windows/product/SHA256SUMS': manifest({
    'veles-native.zip': 'windows-package',
    'veles-native.zip.sha256': 'windows-sidecar',
  }),
  'build/verification/native-bridge/windows/view': { directory: true },
  'build/verification/native-bridge/windows/view/host.exe': 'windows-host',
  'build/verification/native-bridge/windows/METADATA.native-bridge.jsonl': nativeMetadata.windows,
  'build/verification/native-bridge/macos/product/veles-native.tar.gz': 'mac-package',
  'build/verification/native-bridge/macos/product/veles-native.tar.gz.sha256': 'mac-sidecar',
  'build/verification/native-bridge/macos/product/SHA256SUMS': manifest({
    'veles-native.tar.gz': 'mac-package',
    'veles-native.tar.gz.sha256': 'mac-sidecar',
  }),
  'build/verification/native-bridge/macos/view/Veles.app/Contents/MacOS/host': 'mac-host',
  'build/verification/native-bridge/macos/view/Veles.app/Contents/MacOS/current': { link: 'host' },
  'build/verification/native-bridge/macos/METADATA.native-bridge.jsonl': nativeMetadata.macos,
};

test('aggregates exactly the verified artifact namespaces in byte order', async () => {
  const files = {
    ...requiredFiles,
    'build/web-extension/SHA256SUMS': manifest({
      'veles-extension-0.1.0.zip': 'web',
      'veles-extension-0.1.0.zip.sha256': 'web-sidecar',
    }),
    'build/rust-package/SHA256SUMS': manifest({
      'jni/arm64-v8a/libveles_crypto.so': 'arm64',
      'jni/armeabi-v7a/libveles_crypto.so': 'arm',
      'jni/x86_64/libveles_crypto.so': 'x64',
      'wasm/veles_crypto_bg.wasm': 'wasm',
    }),
    'build/verification/native-bridge/windows/SHA256SUMS.native-bridge': `# ImageOS=Windows\n# ImageVersion=2025\n# RUNNER_ARCH=X64\n${manifest({ 'veles-native.zip': 'windows-package', 'veles-native.zip.sha256': 'windows-sidecar' })}`,
    'build/verification/native-bridge/macos/SHA256SUMS.native-bridge': `# ImageOS=macOS\n# ImageVersion=26.0\n# RUNNER_ARCH=ARM64\n${manifest({ 'veles-native.tar.gz': 'mac-package', 'veles-native.tar.gz.sha256': 'mac-sidecar' })}`,
  };
  await withTree(files, async (root) => {
    await runAggregate(root);
    const actual = await readFile(join(root, 'build/verification/SHA256SUMS.toolchains'), 'utf8');
    const expected = manifest({
      'android/app-release-unsigned.apk': 'apk',
      'web-extension/veles-extension-0.1.0.zip': 'web',
      'web-extension/veles-extension-0.1.0.zip.sha256': 'web-sidecar',
      'rust/jni/arm64-v8a/libveles_crypto.so': 'arm64',
      'rust/jni/armeabi-v7a/libveles_crypto.so': 'arm',
      'rust/jni/x86_64/libveles_crypto.so': 'x64',
      'rust/wasm/veles_crypto_bg.wasm': 'wasm',
      'native-bridge/windows/product/veles-native.zip': 'windows-package',
      'native-bridge/windows/product/veles-native.zip.sha256': 'windows-sidecar',
      'native-bridge/windows/view/host.exe': 'windows-host',
      'native-bridge/windows/METADATA.native-bridge.jsonl': nativeMetadata.windows,
      'native-bridge/macos/product/veles-native.tar.gz': 'mac-package',
      'native-bridge/macos/product/veles-native.tar.gz.sha256': 'mac-sidecar',
      'native-bridge/macos/view/Veles.app/Contents/MacOS/host': 'mac-host',
      'native-bridge/macos/METADATA.native-bridge.jsonl': nativeMetadata.macos,
    });
    assert.equal(actual, expected);
    await writeFile(join(root, 'build/verification/native-bridge/windows/product/SHA256SUMS'), 'not a product');
    await writeFile(join(root, 'build/verification/native-bridge/windows/SHA256SUMS.native-bridge'), `# ImageOS=Windows\n# ImageVersion=2025\n# RUNNER_ARCH=X64\n${manifest({ 'SHA256SUMS': 'not a product', 'veles-native.zip': 'windows-package', 'veles-native.zip.sha256': 'windows-sidecar' })}`);
    await assert.rejects(runAggregate(root), (error) => error.exitCode === 1);
    await assert.rejects(readFile(join(root, 'build/verification/SHA256SUMS.toolchains')));
  });
});

test('rejects missing namespaces, duplicate names, and excluded evidence', async () => {
  for (const [name, expectedExit, mutate] of [
    ['missing native namespace', 2, (files) => delete files['build/verification/native-bridge/macos/METADATA.native-bridge.jsonl']],
    ['signed APK', 1, (files) => { files['build/verification/android/app-release.apk'] = 'signed'; }],
    ['mapping', 1, (files) => { files['build/verification/android/mapping.txt'] = 'mapping'; }],
    ['transport', 1, (files) => { files['build/verification/native-bridge/windows/run.tar'] = 'transport'; }],
    ['identity', 1, (files) => { files['build/verification/native-bridge/windows/SOURCE-COMMIT'] = 'commit'; }],
    ['report', 1, (files) => { files['build/verification/native-bridge/windows/REPORT.txt'] = 'report'; }],
    ['nested matching report evidence', 1, (files) => {
      files['build/verification/native-bridge/windows/view/nested/report.txt'] = 'report';
      files['build/verification/native-bridge/windows/METADATA.native-bridge.jsonl'] = [
        { path: 'host.exe', type: 'file', mode: '0644', sha256: digest('windows-host') },
        { path: 'nested', type: 'directory', mode: '0755' },
        { path: 'nested/report.txt', type: 'file', mode: '0644', sha256: digest('report') },
      ].map(JSON.stringify).join('\n') + '\n';
    }],
    ['nested matching identity evidence', 1, (files) => {
      files['build/verification/native-bridge/windows/view/nested/SOURCE-COMMIT'] = 'commit';
      files['build/verification/native-bridge/windows/METADATA.native-bridge.jsonl'] = [
        { path: 'host.exe', type: 'file', mode: '0644', sha256: digest('windows-host') },
        { path: 'nested', type: 'directory', mode: '0755' },
        { path: 'nested/SOURCE-COMMIT', type: 'file', mode: '0644', sha256: digest('commit') },
      ].map(JSON.stringify).join('\n') + '\n';
    }],
    ['empty view and metadata', 1, (files) => {
      delete files['build/verification/native-bridge/windows/view/host.exe'];
      files['build/verification/native-bridge/windows/METADATA.native-bridge.jsonl'] = '\n';
    }],
    ['duplicate native manifest name', 1, (files) => {
      files['build/verification/native-bridge/windows/SHA256SUMS.native-bridge'] = `# ImageOS=Windows\n# ImageVersion=2025\n# RUNNER_ARCH=X64\n${digest('windows-package')}  veles-native.zip\n${digest('windows-package')}  veles-native.zip\n`;
    }],
    ['unexpected extra product file beyond SHA256SUMS', 1, (files) => {
      // A product file other than the package, sidecar, and SHA256SUMS is never
      // accepted; only the producer's product-level SHA256SUMS is permitted.
      files['build/verification/native-bridge/windows/product/extra.bin'] = 'extra';
    }],
  ]) {
    const files = {
      ...requiredFiles,
      'build/web-extension/SHA256SUMS': manifest({
        'veles-extension-0.1.0.zip': 'web',
        'veles-extension-0.1.0.zip.sha256': 'web-sidecar',
      }),
      'build/rust-package/SHA256SUMS': manifest({
        'jni/arm64-v8a/libveles_crypto.so': 'arm64',
        'jni/armeabi-v7a/libveles_crypto.so': 'arm',
        'jni/x86_64/libveles_crypto.so': 'x64',
        'wasm/veles_crypto_bg.wasm': 'wasm',
      }),
      'build/verification/native-bridge/windows/SHA256SUMS.native-bridge': `# ImageOS=Windows\n# ImageVersion=2025\n# RUNNER_ARCH=X64\n${manifest({ 'veles-native.zip': 'windows-package', 'veles-native.zip.sha256': 'windows-sidecar' })}`,
      'build/verification/native-bridge/macos/SHA256SUMS.native-bridge': `# ImageOS=macOS\n# ImageVersion=26.0\n# RUNNER_ARCH=ARM64\n${manifest({ 'veles-native.tar.gz': 'mac-package', 'veles-native.tar.gz.sha256': 'mac-sidecar' })}`,
    };
    mutate(files);
    await withTree(files, async (root) => {
      await assert.rejects(runAggregate(root), (error) => error.exitCode === expectedExit, name);
    });
  }
});
