import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const root = new URL('../..', import.meta.url);

async function workflow(name) {
  return readFile(new URL(`.github/workflows/${name}`, root), 'utf8');
}

function assertReusableWorkflow(source, name) {
  assert.match(source, /workflow_call:/, `${name} must be reusable`);
  assert.match(source, /commit-sha:[\s\S]*?required:\s*true/, `${name} must require the source commit`);
  assert.match(source, /artifact-name:[\s\S]*?required:\s*true/, `${name} must require an artifact name`);
  assert.match(source, /permissions:\s*\n\s*contents:\s*read/, `${name} must use read-only contents permission`);
  assert.match(source, /runs-on:\s*ubuntu-latest/, `${name} must run on Linux`);
  assert.doesNotMatch(source, /verify\/verify-all\.sh/, `${name} must invoke only its component verifier`);
  assert.match(source, /actions\/upload-artifact@v7[\s\S]*?retention-days:\s*\d+/, `${name} must retain its explicit artifact`);
}

test('Android workflow builds and verifies the explicit release evidence tree', async () => {
  const source = await workflow('build-android.yml');
  assertReusableWorkflow(source, 'build-android.yml');
  assert.match(source, /secrets:[\s\S]*?KEYSTORE_BASE64/, 'Android workflow must accept signing secrets');
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
  assert.match(source, /path:\s*\|[\s\S]*?build\/web-extension\/veles-extension-\*\.zip[\s\S]*?build\/web-extension\/veles-extension-\*\.zip\.sha256[\s\S]*?build\/web-extension\/SHA256SUMS/, 'Web workflow must upload exactly the package files');
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
  assert.match(source, /verify\/verify-supply-chain\.sh/, 'Supply-chain workflow must enforce supply-chain policy');
  assert.match(source, /build\/sbom\/web-extension\.cdx\.json[\s\S]*?build\/sbom\/rust\.cdx\.json[\s\S]*?build\/sbom\/native-bridge\.cdx\.json/, 'Supply-chain workflow must publish exactly three SBOMs');
  assert.match(source, /build\/verification\/supply-chain\//, 'Supply-chain workflow must publish named enforcement reports');
  assert.doesNotMatch(source, /secrets:/, 'Supply-chain workflow must not accept signing secrets');
});
