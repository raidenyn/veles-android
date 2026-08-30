import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = new URL('../..', import.meta.url);
const repository = fileURLToPath(root);

async function workflow(name) {
  return readFile(new URL(`.github/workflows/${name}`, root), 'utf8');
}

function mappingEntry(line) {
  const match = line.match(/^([ \t]*)(?:-\s+)?(?:"([^"]+)"|'([^']+)'|([A-Za-z][A-Za-z0-9_-]*))\s*:\s*(.*)$/);
  if (!match) return null;
  return { indent: match[1].length, key: match[2] ?? match[3] ?? match[4], value: match[5] };
}

function flowValues(line, wantedKey) {
  const values = [];
  for (let start = line.indexOf('{'); start !== -1; start = line.indexOf('{', start + 1)) {
    let index = start + 1;
    let depth = 1;
    while (index < line.length && depth > 0) {
      while (/\s|,/.test(line[index] ?? '')) index += 1;
      let key = '';
      if (line[index] === '"' || line[index] === "'") {
        const quote = line[index++];
        const end = line.indexOf(quote, index);
        if (end === -1) break;
        key = line.slice(index, end);
        index = end + 1;
      } else {
        const keyMatch = line.slice(index).match(/^[A-Za-z][A-Za-z0-9_-]*/);
        if (!keyMatch) break;
        key = keyMatch[0];
        index += key.length;
      }
      while (/\s/.test(line[index] ?? '')) index += 1;
      if (line[index] !== ':') break;
      index += 1;
      while (/\s/.test(line[index] ?? '')) index += 1;
      const valueStart = index;
      let quote = null;
      for (; index < line.length; index += 1) {
        const character = line[index];
        if (quote) {
          if (character === quote) quote = null;
        } else if (character === '"' || character === "'") {
          quote = character;
        } else if (character === '{') {
          depth += 1;
        } else if (character === '}') {
          depth -= 1;
          if (depth === 0) break;
        } else if (character === ',' && depth === 1) {
          break;
        }
      }
      if (key === wantedKey) values.push(line.slice(valueStart, index).trim());
      if (line[index] === ',') index += 1;
    }
  }
  return values;
}

function steps(source) {
  const result = [];
  let step = [];
  for (const line of source.split('\n')) {
    if (/^[ \t]*-\s/.test(line) && step.length > 0) {
      result.push(step);
      step = [];
    }
    step.push(line);
  }
  if (step.length > 0) result.push(step);
  return result;
}

function isUploadArtifact(value) {
  return /^['"]?actions\/upload-artifact@/.test(value?.trim() ?? '');
}

function uploadPaths(source, name) {
  const uploads = steps(source).filter((step) => {
    const uses = step.flatMap((line) => {
      const entry = mappingEntry(line);
      return [entry?.key === 'uses' ? entry.value : null, ...flowValues(line, 'uses')];
    });
    return uses.some(isUploadArtifact);
  });
  assert.ok(uploads.length > 0, `${name} must upload an artifact`);
  return uploads.map((step, index) => {
    const pathIndex = step.findIndex((line) => mappingEntry(line)?.key === 'path');
    assert.notEqual(pathIndex, -1, `${name} upload ${index + 1} must declare explicit upload paths`);
    const path = mappingEntry(step[pathIndex]);
    if (path.value.startsWith('|')) {
      const paths = [];
      for (let lineIndex = pathIndex + 1; lineIndex < step.length; lineIndex += 1) {
        const line = step[lineIndex];
        if (line.trim() && line.match(/^\s*/)[0].length <= path.indent) break;
        if (line.trim()) paths.push(line.trim());
      }
      return paths;
    }
    return [path.value.trim()];
  });
}

function assertExactUploadPaths(source, name, expected) {
  assert.deepEqual(uploadPaths(source, name), [expected], `${name} must have exactly one upload with only the allowed paths`);
}

function runCommands(source) {
  const lines = source.split('\n');
  const commands = [];
  for (let index = 0; index < lines.length; index += 1) {
    const flowRuns = flowValues(lines[index], 'run');
    if (flowRuns.length > 0) {
      commands.push(...flowRuns);
      continue;
    }
    const entry = mappingEntry(lines[index]);
    if (entry?.key !== 'run') continue;
    let command = entry.value;
    if (/^[>|]/.test(command)) {
      while (index + 1 < lines.length) {
        const next = lines[index + 1];
        if (next.trim() && next.match(/^\s*/)[0].length <= entry.indent) break;
        command += `\n${next}`;
        index += 1;
      }
    }
    commands.push(command);
  }
  return commands;
}

function assertNoSecretRunInterpolation(source, name) {
  for (const command of runCommands(source)) {
    assert.doesNotMatch(command, /\$\{\{\s*secrets\./, `${name} run commands must not interpolate secrets`);
  }
}

function assertReusableWorkflow(source, name) {
  assert.match(source, /workflow_call:/, `${name} must be reusable`);
  assert.match(source, /commit-sha:[\s\S]*?required:\s*true/, `${name} must require the source commit`);
  assert.match(source, /artifact-name:[\s\S]*?required:\s*true/, `${name} must require an artifact name`);
  const lines = source.split('\n');
  const permissions = lines.flatMap((line, index) => {
    const entry = mappingEntry(line);
    return entry?.key === 'permissions' ? [{ ...entry, index }] : [];
  });
  assert.equal(permissions.length, 1, `${name} must declare permissions exactly once without job overrides`);
  assert.equal(permissions[0].indent, 0, `${name} permissions must be declared at workflow scope`);
  assert.equal(permissions[0].value, '', `${name} permissions must use a block mapping`);
  const permissionValues = [];
  for (let index = permissions[0].index + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim() && line.match(/^\s*/)[0].length <= permissions[0].indent) break;
    const entry = mappingEntry(line);
    if (entry && entry.indent > permissions[0].indent) permissionValues.push(`${entry.key}: ${entry.value}`.trim());
  }
  assert.deepEqual(permissionValues, ['contents: read'], `${name} must use only read-only contents permission`);
  assert.match(source, /runs-on:\s*ubuntu-latest/, `${name} must run on Linux`);
  assert.doesNotMatch(source, /verify\/verify-all\.sh/, `${name} must invoke only its component verifier`);
  assertNoSecretRunInterpolation(source, name);
  const uploads = steps(source).filter((step) => step.some((line) => {
    const entry = mappingEntry(line);
    return (entry?.key === 'uses' && isUploadArtifact(entry.value)) || flowValues(line, 'uses').some(isUploadArtifact);
  }));
  assert.ok(uploads.length > 0, `${name} must upload an artifact`);
  for (const upload of uploads) {
    assert.ok(upload.some((line) => mappingEntry(line)?.key === 'path'), `${name} must give every artifact upload an explicit path`);
    assert.ok(upload.some((line) => mappingEntry(line)?.key === 'retention-days' && /^\d+$/.test(mappingEntry(line).value)), `${name} must retain every artifact upload`);
  }
}

test('Android workflow builds and verifies the explicit release evidence tree', async () => {
  const source = await workflow('build-android.yml');
  assertReusableWorkflow(source, 'build-android.yml');
  assert.match(source, /secrets:[\s\S]*?KEYSTORE_BASE64/, 'Android workflow must accept signing secrets');
  assert.match(source, /release-evidence-artifact-name:[\s\S]*?required:\s*true[\s\S]*?default:\s*android-release-evidence/, 'Android workflow must expose a downstream release-evidence artifact name');
  assert.match(source, /env:\s*\n\s*KEYSTORE_BASE64:\s*\$\{\{ secrets\.KEYSTORE_BASE64 \}\}/, 'Android workflow must map the keystore secret into env');
  assert.match(source, /if:\s*env\.KEYSTORE_BASE64 != ''/, 'Android workflow must test secret presence through env');
  assert.doesNotMatch(source, /if:\s*secrets\./, 'Android workflow must not reference secrets directly in if conditions');
  for (const secret of ['KEYSTORE_PASSWORD', 'KEY_ALIAS', 'KEY_PASSWORD']) {
    assert.match(source, new RegExp(`env:\\s*\\n[\\s\\S]*?${secret}:\\s*\\$\\{\\{ secrets\\.${secret} \\}\\}`), `Android workflow must map ${secret} into env`);
  }
  assertNoSecretRunInterpolation(source, 'Android workflow');
  assert.match(source, /- run: \.\/gradlew assembleRelease\n        if: env\.KEYSTORE_BASE64 != ''[\s\S]*?VELES_KEYSTORE_FILE/, 'Android workflow must set the signing file only in the signed build step');
  assert.match(source, /- run: \.\/gradlew assembleRelease\n        if: env\.KEYSTORE_BASE64 == ''\n/, 'Android workflow must build unsigned artifacts without signing environment');
  assert.match(source, /\.\/gradlew assembleRelease/, 'Android workflow must build a release APK');
  assert.match(source, /verify\/verify\.sh[\s\S]*?inputs\.commit-sha/, 'Android workflow must run Docker APK verification for the requested commit');
  assert.match(source, /rust\/scripts\/verify-apk-jni\.sh/, 'Android workflow must enforce the APK JNI allow-list');
  assert.match(source, /build\/verification\/android\/app-release-unsigned\.apk/, 'Android workflow must export the canonical unsigned APK');
  assert.match(source, /name:\s*\$\{\{ inputs\.release-evidence-artifact-name \}\}/, 'Android workflow must upload caller-selected release evidence');
  assert.match(source, /name:\s*\$\{\{ inputs\.artifact-name \}\}/, 'Android workflow must upload the caller-selected aggregate reference');
  assert.deepEqual(uploadPaths(source, 'build-android.yml'), [
    ['build/verification/android-release-evidence/'],
    ['build/verification/android/app-release-unsigned.apk'],
  ], 'Android workflow must keep release evidence separate from the aggregate reference');
  const signedBuild = source.indexOf('VELES_KEYSTORE_FILE');
  const staging = source.indexOf('- name: Stage signed release evidence');
  const unsignedBuild = source.indexOf('- name: Build canonical unsigned release');
  assert.ok(signedBuild < staging && staging < unsignedBuild, 'Android workflow must stage signed evidence before the unsigned rebuild replaces AGP outputs');
  assert.match(source.slice(staging, unsignedBuild), /cp app\/build\/outputs\/apk\/release\/app-release\.apk build\/verification\/android-release-evidence\/app-release\.apk/, 'Android workflow must stage the signed APK');
  assert.match(source.slice(staging, unsignedBuild), /cp app\/build\/outputs\/mapping\/release\/mapping\.txt build\/verification\/android-release-evidence\/mapping\.txt/, 'Android workflow must stage the signed mapping');
});

test('web extension workflow uses the exact Node reference and publishes only package files', async () => {
  const source = await workflow('build-web-extension.yml');
  assertReusableWorkflow(source, 'build-web-extension.yml');
  assert.match(source, /actions\/setup-node@v6[\s\S]*?node-version:\s*['"]26\.8\.1['"]/, 'Web workflow must use Node 26.8.1');
  assert.match(source, /npm ci --ignore-scripts/, 'Web workflow must disable install scripts');
  assert.match(source, /npm run package/, 'Web workflow must package the extension');
  assert.match(source, /verify\/verify-web\.sh/, 'Web workflow must run its reference comparison');
  assertExactUploadPaths(source, 'build-web-extension.yml', [
    'build/web-extension/veles-extension-*.zip',
    'build/web-extension/veles-extension-*.zip.sha256',
    'build/web-extension/SHA256SUMS',
  ]);
  assert.doesNotMatch(source, /secrets:/, 'Web workflow must not accept signing secrets');
});

test('Rust workflow packages and reference-verifies rust-jni-wasm', async () => {
  const source = await workflow('build-rust.yml');
  assertReusableWorkflow(source, 'build-rust.yml');
  assert.match(source, /actions\/setup-node@v6[\s\S]*?node-version:\s*['"]26\.8\.1['"]/, 'Rust workflow must use Node 26.8.1');
  assert.match(source, /\.\/gradlew rustPackage/, 'Rust workflow must produce the Rust package');
  assert.match(source, /verify\/verify-rust\.sh/, 'Rust workflow must run its reference comparison');
  assert.match(source, /default:\s*rust-jni-wasm/, 'Rust workflow must default to the stable artifact name');
  assert.match(source, /name:\s*\$\{\{ inputs\.artifact-name \}\}/, 'Rust workflow must publish the requested artifact name');
  assert.match(source, /path:\s*build\/rust-package\//, 'Rust workflow must upload only its package tree');
  assertExactUploadPaths(source, 'build-rust.yml', ['build/rust-package/']);
  assert.doesNotMatch(source, /secrets:/, 'Rust workflow must not accept signing secrets');
});

test('supply-chain workflow enforces reports before uploading exactly three SBOMs', async () => {
  const source = await workflow('build-supply-chain.yml');
  assertReusableWorkflow(source, 'build-supply-chain.yml');
  assert.match(source, /actions\/setup-node@v6[\s\S]*?node-version:\s*['"]26\.8\.1['"]/, 'Supply-chain workflow must use Node 26.8.1');
  assert.match(source, /\.\/gradlew installCargoDeny/, 'Supply-chain workflow must provision cargo-deny');
  assert.match(source, /verify\/verify-supply-chain\.sh/, 'Supply-chain workflow must enforce supply-chain policy');
  assert.ok(source.indexOf('./gradlew installCargoDeny') < source.indexOf('verify/verify-supply-chain.sh'), 'Supply-chain workflow must provision cargo-deny before enforcement');
  assert.ok(source.indexOf('verify/verify-supply-chain.sh') < source.indexOf('actions/upload-artifact@v7'), 'Supply-chain workflow must enforce policy before upload');
  assertExactUploadPaths(source, 'build-supply-chain.yml', [
    'build/sbom/web-extension.cdx.json',
    'build/sbom/rust.cdx.json',
    'build/sbom/native-bridge.cdx.json',
    'build/verification/supply-chain/npm-licenses.txt',
    'build/verification/supply-chain/cargo-licenses.txt',
    'build/verification/supply-chain/npm-install-scripts.txt',
    'build/verification/supply-chain/cargo-build-scripts.txt',
    'build/verification/supply-chain/remote-code.txt',
  ]);
  assert.doesNotMatch(source, /secrets:/, 'Supply-chain workflow must not accept signing secrets');
});

function assertNativeWorkflow(source, name, platform, wrapper) {
  assert.match(source, /workflow_call:/, `${name} must be reusable`);
  assert.match(source, /commit-sha:[\s\S]*?required:\s*true/, `${name} must require the source commit`);
  assert.match(source, /artifact-name:[\s\S]*?required:\s*true/, `${name} must require the verified artifact name`);
  assert.doesNotMatch(source, /-latest/, `${name} must use pinned runner labels`);
  assert.doesNotMatch(source, /secrets:/, `${name} must not accept signing secrets`);
  assertNoSecretRunInterpolation(source, name);
  for (const job of ['run-a', 'run-b', 'compare']) {
    assert.match(source, new RegExp(`\\n  ${job}:`), `${name} must define ${job}`);
  }
  assert.equal((source.match(new RegExp(`runs-on:\\s*${platform}`, 'g')) ?? []).length, 3, `${name} must run both slots and comparison on ${platform}`);
  assert.equal((source.match(/actions\/setup-node@v6[\s\S]*?node-version:\s*['"]26\.8\.1['"]/g) ?? []).length, 3, `${name} must pin Node 26.8.1 in every job`);
  for (const field of ['ImageOS', 'ImageVersion', 'RUNNER_ARCH']) {
    assert.match(source, new RegExp(`(?:test -n "\\$${field}"|foreach \\(\\$name in 'ImageOS', 'ImageVersion', 'RUNNER_ARCH'\\))`), `${name} must reject an empty ${field}`);
  }
  assert.match(source, new RegExp(wrapper.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), `${name} must use its offline package wrapper`);
  const artifactPlatform = platform.startsWith('windows') ? 'windows' : 'macos';
  for (const slot of ['a', 'b']) {
    assert.match(source, new RegExp(`name:\\s*unverified-${artifactPlatform}-run-${slot}`), `${name} must upload unverified run ${slot}`);
  }
  assert.equal((source.match(/retention-days:\s*1\b/g) ?? []).length, 2, `${name} must retain unverified runs for the shortest period`);
  assert.match(source, /needs:\s*\[run-a, run-b\]/, `${name} must compare both independent slots`);
  assert.match(source, /verify\/verify-native\.sh[\s\S]*?inputs\.commit-sha[\s\S]*?native-runs\/run-a[\s\S]*?native-runs\/run-b/, `${name} must compare both transports against the requested commit`);
  assert.match(source, /name:\s*\$\{\{ inputs\.artifact-name \}\}/, `${name} must expose only the verified caller-selected artifact`);
  assert.deepEqual(uploadPaths(source, name), [
    ['build/verification/native-runs/run-a/'],
    ['build/verification/native-runs/run-b/'],
    ['build/verification/native-bridge/'],
  ], `${name} must expose only the comparison output while retaining slot transports internally`);
}

test('Windows native workflow produces a verified comparison from two offline transports', async () => {
  const source = await workflow('build-native-windows.yml');
  assertNativeWorkflow(source, 'build-native-windows.yml', 'windows-2025', 'verify/native/network-deny-windows.ps1');
  assert.match(source, /node-version:\s*['"]26\.8\.1['"]/, 'Windows workflow must pin Node 26.8.1');
  assert.match(source, /RUNNER_ARCH\s*-ne\s*'X64'/, 'Windows workflow must require an x64 runner');
  for (const [start, end] of [['  run-a:', '  run-b:'], ['  run-b:', '  compare:']]) {
    const slot = source.slice(source.indexOf(start), source.indexOf(end));
    assert.match(slot, /pwsh -NoProfile -File verify\/native\/network-deny-windows\.ps1 -TauriCachePath "\$env:RUNNER_TEMP\\veles-tauri-cache"/, `${start.trim()} must execute the wrapper with its supported cache argument`);
  }
});

test('macOS native workflow produces a verified comparison with the approved sandbox denial', async () => {
  const source = await workflow('build-native-macos.yml');
  assertNativeWorkflow(source, 'build-native-macos.yml', 'macos-26', 'verify/native/network-deny-macos.sh');
  for (const contract of ['/Applications/Xcode_26.6.app', '17F113', 'macosx26.5']) {
    assert.match(source, new RegExp(contract.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), `macOS workflow must enforce ${contract}`);
  }
  assert.match(source, /\[ "\$RUNNER_ARCH" = 'ARM64' \]/, 'macOS workflow must require an ARM64 runner');
  assert.doesNotMatch(source, /pfctl/, 'macOS workflow must not mutate host PF configuration');
});

test('aggregate workflow consumes all and only verified component artifacts', async () => {
  const source = await workflow('build-toolchain-manifest.yml');
  assertReusableWorkflow(source, 'build-toolchain-manifest.yml');
  assert.doesNotMatch(source, /unverified-/, 'aggregate workflow must never download unverified transports');
  for (const input of ['android-artifact-name', 'web-extension-artifact-name', 'rust-artifact-name', 'native-windows-artifact-name', 'native-macos-artifact-name']) {
    assert.match(source, new RegExp(`${input}:[\\s\\S]*?required:\\s*true`), `aggregate workflow must require ${input}`);
  }
  for (const artifact of ['verified-android', 'verified-web-extension', 'rust-jni-wasm', 'verified-native-windows', 'verified-native-macos']) {
    assert.match(source, new RegExp(`default:\\s*${artifact}`), `aggregate workflow must default to ${artifact}`);
  }
  for (const path of ['build/verification/android', 'build/web-extension', 'build/rust-package', 'build/verification/native-bridge/windows', 'build/verification/native-bridge/macos']) {
    assert.match(source, new RegExp(`path:\\s*${path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`), `aggregate workflow must download into ${path}`);
  }
  assert.match(source, /verify\/aggregate-checksums\.sh[\s\S]*?inputs\.commit-sha/, 'aggregate workflow must directly validate and aggregate components');
  assertExactUploadPaths(source, 'build-toolchain-manifest.yml', ['build/verification/SHA256SUMS.toolchains']);
  assert.doesNotMatch(source, /secrets:/, 'aggregate workflow must not accept signing secrets');
});

test('Windows wrapper and provisioner declare parameters before executable statements', async () => {
  for (const name of ['network-deny-windows.ps1', 'provision-windows-tools.ps1']) {
    const source = await readFile(new URL(`verify/native/${name}`, root), 'utf8');
    assert.match(source, /^\s*(?:#.*\n\s*)*param\(/, `${name} must declare param before every executable statement`);
  }
});

test('aggregate producer and consumer agree on canonical verified artifact layouts', async () => {
  const [android, rust, aggregate] = await Promise.all([
    workflow('build-android.yml'),
    workflow('build-rust.yml'),
    workflow('build-toolchain-manifest.yml'),
  ]);
  assert.match(android, /cp app\/build\/outputs\/apk\/release\/app-release-unsigned\.apk build\/verification\/android\/app-release-unsigned\.apk/, 'Android workflow must export its canonical unsigned APK for aggregation');
  assert.deepEqual(uploadPaths(android, 'build-android.yml'), [
    ['build/verification/android-release-evidence/'],
    ['build/verification/android/app-release-unsigned.apk'],
  ], 'Android release evidence must not pollute the aggregate input artifact');
  assert.match(android, /artifact-name:[\s\S]*?default:\s*verified-android/, 'Android aggregate artifact must retain the stable verified-android default');
  assert.match(rust, /default:\s*rust-jni-wasm/, 'Rust producer default documents its verified artifact name');
  assert.match(aggregate, /rust-artifact-name:[\s\S]*?default:\s*rust-jni-wasm/, 'aggregate Rust input must default to the producer artifact name');
  assert.match(aggregate, /path:\s*build\/verification\/android/, 'aggregate must restore Android output into its canonical verification directory');
});

test('aggregate artifact-name guard accepts only the five verified producers', () => {
  for (const name of ['verified-android', 'verified-web-extension', 'rust-jni-wasm', 'verified-native-windows', 'verified-native-macos']) {
    const result = spawnSync('bash', ['verify/validate-verified-artifact-names.sh', name], { cwd: repository, encoding: 'utf8' });
    assert.equal(result.status, 0, `must accept ${name}: ${result.stderr}`);
  }
  for (const name of ['', 'unverified-windows-run-a', 'verified-evil', 'rust-jni-wasm-extra', '../verified-android']) {
    const result = spawnSync('bash', ['verify/validate-verified-artifact-names.sh', name], { cwd: repository, encoding: 'utf8' });
    assert.notEqual(result.status, 0, `must reject malicious artifact name ${JSON.stringify(name)}`);
  }
});

test('aggregate validates all artifact inputs before any download', async () => {
  const source = await workflow('build-toolchain-manifest.yml');
  const validation = source.indexOf('- name: Validate verified artifact names');
  const download = source.indexOf('actions/download-artifact@v8');
  assert.ok(validation !== -1 && validation < download, 'aggregate must validate artifact names before downloads');
  for (const input of ['android-artifact-name', 'web-extension-artifact-name', 'rust-artifact-name', 'native-windows-artifact-name', 'native-macos-artifact-name']) {
    assert.match(source.slice(validation, download), new RegExp(`inputs\\.${input}`), `aggregate guard must validate ${input}`);
  }
  assert.match(source.slice(validation, download), /run:\s*bash verify\/validate-verified-artifact-names\.sh/, 'aggregate must explicitly invoke the non-executable validator through bash');
});

test('workflow contract helpers reject elevated permissions, extra uploads, and every run syntax secret interpolation', () => {
  const upload = `      - uses: actions/upload-artifact@v7
        with:
          path: |
            allowed.txt
          if-no-files-found: error
          retention-days: 14`;
  assert.throws(() => assertExactUploadPaths(`${upload}\n${upload.replace('allowed.txt', 'unexpected.txt')}`, 'fixture', ['allowed.txt']), /exactly one upload/);
  assert.throws(() => assertExactUploadPaths(`${upload}\n${upload.replace('@v7', '@v6').replace('allowed.txt', 'unexpected.txt')}`, 'fixture', ['allowed.txt']), /exactly one upload/);
  assert.throws(() => assertExactUploadPaths(`${upload}\n${upload.replace('actions/upload-artifact@v7', "'actions/upload-artifact@feature-ref'").replace('allowed.txt', 'unexpected.txt')}`, 'fixture', ['allowed.txt']), /exactly one upload/);

  assert.throws(() => assertReusableWorkflow(`on:
  workflow_call:
    inputs:
      commit-sha:
        required: true
      artifact-name:
        required: true
permissions:
  contents: read
jobs:
  build:
    permissions:
      contents: write
`, 'fixture'), /permissions exactly once/);

  assert.throws(() => assertReusableWorkflow(`on:
  workflow_call:
    inputs:
      commit-sha:
        required: true
      artifact-name:
        required: true
'permissions':
  contents: read
jobs:
  build:
    'permissions': write-all
`, 'fixture'), /permissions exactly once/);

  assert.throws(() => assertReusableWorkflow(`on:
  workflow_call:
    inputs:
      commit-sha:
        required: true
      artifact-name:
        required: true
permissions:
  contents: read
jobs:
  build:
    permissions: write-all
`, 'fixture'), /permissions exactly once/);

  assert.throws(() => assertReusableWorkflow(`on:
  workflow_call:
    inputs:
      commit-sha:
        required: true
      artifact-name:
        required: true
permissions:
  contents: read
jobs:
  build:
    "permissions": write-all
`, 'fixture'), /permissions exactly once/);

  assert.throws(() => assertReusableWorkflow(`on:
  workflow_call:
    inputs:
      commit-sha:
        required: true
      artifact-name:
        required: true
"permissions": write-all
jobs:
  build:
`, 'fixture'), /permissions must use a block mapping/);

  for (const run of [
    'run: echo "${{ secrets.KEY }}"',
    'run: |\n    echo "${{ secrets.KEY }}"',
    'run: >-\n    echo "${{ secrets.KEY }}"',
    "{ run: 'echo \"${{ secrets.KEY }}\"' }",
    "{ 'run': 'echo \"${{ secrets.KEY }}\"' }",
    '{ "run": "echo ${{ secrets.KEY }}" }',
  ]) {
    assert.throws(() => assertNoSecretRunInterpolation(`steps:\n  - ${run}`, 'fixture'), /must not interpolate secrets/);
  }
});
