import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const root = new URL('../..', import.meta.url);

async function workflow(name) {
  return readFile(new URL(`.github/workflows/${name}`, root), 'utf8');
}

function uploadPaths(source, name) {
  const uploads = [...source.matchAll(/actions\/upload-artifact@v7([\s\S]*?)(?=\n {6}-\s|$)/g)];
  assert.ok(uploads.length > 0, `${name} must upload an artifact`);
  return uploads.map(([, upload], index) => {
    const match = upload.match(/path:\s*\|\n((?: {12}.+\n)+)\s+if-no-files-found:/);
    assert.ok(match, `${name} upload ${index + 1} must declare explicit upload paths`);
    return match[1].trim().split('\n').map((line) => line.trim());
  });
}

function assertExactUploadPaths(source, name, expected) {
  assert.deepEqual(uploadPaths(source, name), [expected], `${name} must have exactly one upload with only the allowed paths`);
}

function runCommands(source) {
  const lines = source.split('\n');
  const commands = [];
  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(/^([ \t]*)(?:-\s+)?run:\s*(.*)$/);
    if (!match) continue;
    const indent = match[1].length;
    let command = match[2];
    if (/^[>|]/.test(command)) {
      while (index + 1 < lines.length) {
        const next = lines[index + 1];
        if (next.trim() && next.match(/^\s*/)[0].length <= indent) break;
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
  const permissions = [...source.matchAll(/^([ \t]*)permissions:[ \t]*(.*)$/gm)];
  assert.equal(permissions.length, 1, `${name} must declare permissions exactly once without job overrides`);
  assert.equal(permissions[0][1], '', `${name} permissions must be declared at workflow scope`);
  assert.equal(permissions[0][2], '', `${name} permissions must use a block mapping`);
  const workflowPermissions = source.match(/^permissions:[ \t]*\n((?: {2}.+\n)*)/m);
  assert.deepEqual(workflowPermissions?.[1].trim().split('\n'), ['contents: read'], `${name} must use only read-only contents permission`);
  assert.match(source, /runs-on:\s*ubuntu-latest/, `${name} must run on Linux`);
  assert.doesNotMatch(source, /verify\/verify-all\.sh/, `${name} must invoke only its component verifier`);
  const uploads = [...source.matchAll(/actions\/upload-artifact@v7([\s\S]*?)(?=\n {6}-\s|$)/g)];
  assert.ok(uploads.length > 0, `${name} must upload an artifact`);
  for (const [, upload] of uploads) {
    assert.match(upload, /path:/, `${name} must give every artifact upload an explicit path`);
    assert.match(upload, /retention-days:\s*\d+/, `${name} must retain every artifact upload`);
  }
}

test('Android workflow builds and verifies the explicit release evidence tree', async () => {
  const source = await workflow('build-android.yml');
  assertReusableWorkflow(source, 'build-android.yml');
  assert.match(source, /secrets:[\s\S]*?KEYSTORE_BASE64/, 'Android workflow must accept signing secrets');
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
  assert.match(source, /app\/build\/outputs\/apk\/release\//, 'Android workflow must upload the release APK evidence');
  assert.match(source, /app\/build\/outputs\/mapping\/release\/mapping\.txt/, 'Android workflow must upload the release mapping evidence');
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

test('workflow contract helpers reject elevated permissions, extra uploads, and every run syntax secret interpolation', () => {
  const upload = `      - uses: actions/upload-artifact@v7
        with:
          path: |
            allowed.txt
          if-no-files-found: error
          retention-days: 14`;
  assert.throws(() => assertExactUploadPaths(`${upload}\n${upload.replace('allowed.txt', 'unexpected.txt')}`, 'fixture', ['allowed.txt']), /exactly one upload/);

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
permissions:
  contents: read
jobs:
  build:
    permissions: write-all
`, 'fixture'), /permissions exactly once/);

  for (const run of [
    'run: echo "${{ secrets.KEY }}"',
    'run: |\n    echo "${{ secrets.KEY }}"',
    'run: >-\n    echo "${{ secrets.KEY }}"',
  ]) {
    assert.throws(() => assertNoSecretRunInterpolation(`steps:\n  - ${run}`, 'fixture'), /must not interpolate secrets/);
  }
});
