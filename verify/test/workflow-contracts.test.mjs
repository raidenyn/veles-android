import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readdir, readFile } from 'node:fs/promises';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = new URL('../..', import.meta.url);
const repository = fileURLToPath(root);

async function workflow(name) {
  return readFile(new URL(`.github/workflows/${name}`, root), 'utf8');
}

async function workflowFiles() {
  const entries = await readdir(new URL('.github/workflows', root), { withFileTypes: true });
  return entries.filter((entry) => entry.isFile() && entry.name.endsWith('.yml')).map((entry) => entry.name);
}

// Reviewed immutable action pins (Task 12 brief). Every non-local `uses:` must
// reference the exact 40-hex SHA followed by a `# vN` version comment.
const ACTION_PINS = {
  'actions/checkout': { sha: '3d3c42e5aac5ba805825da76410c181273ba90b1', version: 'v7' },
  'actions/setup-java': { sha: 'b6effb05e454b25005698d916606bdc6ffcbf961', version: 'v5' },
  'gradle/actions/setup-gradle': { sha: '4733eaac7c1b0da527e4206b7671e0061de1ce37', version: 'v6' },
  'actions/cache': { sha: 'caa296126883cff596d87d8935842f9db880ef25', version: 'v5' },
  'actions/upload-artifact': { sha: '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a', version: 'v7' },
  'actions/download-artifact': { sha: '37930b1c2abaa49bbe596cd826c3c89aef350131', version: 'v7' },
  'actions/setup-node': { sha: '249970729cb0ef3589644e2896645e5dc5ba9c38', version: 'v6' },
  'actions/attest-build-provenance': { sha: '43d14bc2b83dec42d39ecae14e916627a18bb661', version: 'v3' },
  'reactivecircus/android-emulator-runner': { sha: '4c44018e59b437e86cdfc41da381398f93ed8808', version: 'v2' },
  'actions/configure-pages': { sha: '983d7736d9b0ae728b81ab479565c72886d7745b', version: 'v5' },
  'actions/upload-pages-artifact': { sha: '7b1f4a764d45c48632c6b24a0339c27f5614fb0b', version: 'v4' },
  'actions/deploy-pages': { sha: 'd6db90164ac5ed86f2b6aed7e0febac5b3c0c03e', version: 'v4' },
};

function escapeRegexp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function usesReferences(source) {
  const refs = [];
  for (const line of source.split('\n')) {
    const entry = mappingEntry(line);
    if (entry?.key === 'uses') refs.push(entry.value.trim());
    for (const value of flowValues(line, 'uses')) refs.push(value.trim());
  }
  return refs;
}

function assertActionsPinned(source, name) {
  const refs = usesReferences(source);
  assert.ok(refs.length > 0, `${name} must declare at least one uses: reference`);
  for (const ref of refs) {
    if (ref.startsWith('./')) continue; // repository-local reusable workflows are exempt
    const match = ref.match(/^([^@]+)@([0-9a-f]{40})(?:\s+#\s*(v\d+))?$/);
    assert.ok(match, `${name} uses "${ref}" which must be <owner>/<repo>@<40-hex SHA> # vN`);
    const [, action, sha, version] = match;
    const pin = ACTION_PINS[action];
    assert.ok(pin, `${name} uses "${ref}" whose action ${action} is not in the reviewed pin snapshot`);
    assert.equal(sha, pin.sha, `${name} uses "${ref}" which must pin ${action} to the reviewed SHA ${pin.sha}`);
    assert.equal(version, pin.version, `${name} uses "${ref}" which must carry the # ${pin.version} comment`);
  }
}

const FULL_GRAPH_JOBS = ['web-extension', 'rust', 'supply-chain', 'native-windows', 'native-macos'];

function callerJobKeys(source) {
  // Caller jobs reuse ./.github/workflows/*.yml; collect their top-level job keys.
  const lines = source.split('\n');
  const jobs = {};
  let currentJob = null;
  let inOnBlock = false;
  let inJobsBlock = false;
  for (const line of lines) {
    if (/^on:\s*$/.test(line)) {
      inOnBlock = true;
      currentJob = null;
      continue;
    }
    if (inOnBlock) {
      // The on: block ends at the next top-level key (indent 0, non-empty).
      if (line && !line.startsWith(' ') && !line.startsWith('#')) inOnBlock = false;
    }
    if (/^jobs:\s*$/.test(line)) {
      inJobsBlock = true;
      inOnBlock = false;
      currentJob = null;
      continue;
    }
    if (!inJobsBlock) continue;
    const jobMatch = line.match(/^  ([A-Za-z0-9_-]+):\s*$/);
    if (jobMatch) {
      currentJob = jobMatch[1];
      jobs[currentJob] = new Set();
      continue;
    }
    if (currentJob) {
      const entry = mappingEntry(line);
      // Top-level keys within a job sit at indent 4 (job key is at indent 2).
      if (entry && entry.indent === 4) jobs[currentJob].add(entry.key);
    }
  }
  for (const key of Object.keys(jobs)) jobs[key] = [...jobs[key]];
  return jobs;
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
  assert.match(source, /actions\/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38[\s\S]*?node-version:\s*['"]26\.8\.1['"]/, 'Web workflow must use Node 26.8.1');
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
  assert.match(source, /actions\/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38[\s\S]*?node-version:\s*['"]26\.8\.1['"]/, 'Rust workflow must use Node 26.8.1');
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
  assert.match(source, /actions\/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38[\s\S]*?node-version:\s*['"]26\.8\.1['"]/, 'Supply-chain workflow must use Node 26.8.1');
  assert.match(source, /\.\/gradlew installCargoDeny/, 'Supply-chain workflow must provision cargo-deny');
  assert.match(source, /verify\/verify-supply-chain\.sh/, 'Supply-chain workflow must enforce supply-chain policy');
  assert.ok(source.indexOf('./gradlew installCargoDeny') < source.indexOf('verify/verify-supply-chain.sh'), 'Supply-chain workflow must provision cargo-deny before enforcement');
  assert.ok(source.indexOf('verify/verify-supply-chain.sh') < source.indexOf('actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a'), 'Supply-chain workflow must enforce policy before upload');
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
  assert.equal((source.match(/actions\/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38[\s\S]*?node-version:\s*['"]26\.8\.1['"]/g) ?? []).length, 3, `${name} must pin Node 26.8.1 in every job`);
  for (const field of ['ImageOS', 'ImageVersion', 'RUNNER_ARCH']) {
    assert.match(source, new RegExp(`(?:test -n "\\$${field}"|foreach \\(\\$name in 'ImageOS', 'ImageVersion', 'RUNNER_ARCH'\\))`), `${name} must reject an empty ${field}`);
  }
  assert.match(source, new RegExp(wrapper.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), `${name} must use its offline package wrapper`);
  // Each run slot must assemble an extracted verification view from the real
  // Tauri bundle outputs (not a nonexistent native-bridge/dist) before
  // transporting it. The view dir must live under the run's own upload tree so
  // it is transported alongside the product.
  const artifactPlatform = platform.startsWith('windows') ? 'windows' : 'macos';
  assert.match(source, new RegExp(`node verify/native/extract-view\\.mjs ${artifactPlatform} native-bridge/src-tauri/target/release build/native-bridge/${artifactPlatform} build/verification/native-runs/run-a/view`), `${name} run-a must assemble its extracted verification view from the real bundle outputs`);
  assert.match(source, new RegExp(`node verify/native/extract-view\\.mjs ${artifactPlatform} native-bridge/src-tauri/target/release build/native-bridge/${artifactPlatform} build/verification/native-runs/run-b/view`), `${name} run-b must assemble its extracted verification view from the real bundle outputs`);
  assert.doesNotMatch(source, /native-bridge\/dist/, `${name} must not reference the nonexistent native-bridge/dist view directory`);
  for (const slot of ['a', 'b']) {
    assert.match(source, new RegExp(`prepare-run\\.(?:sh|ps1)[\\s\\S]{0,120}build/verification/native-runs/run-${slot}/view`), `${name} run-${slot} must pass its assembled view dir to prepare-run`);
  }
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
  assert.match(source, /RUNNER_ARCH['"]?\s*\)\s*-ne\s*'X64'/, 'Windows workflow must require an x64 runner');
  for (const [start, end] of [['  run-a:', '  run-b:'], ['  run-b:', '  compare:']]) {
    const slot = source.slice(source.indexOf(start), source.indexOf(end));
    assert.match(slot, /pwsh -NoProfile -File verify\/native\/network-deny-windows\.ps1 -TauriCachePath "native-bridge\/src-tauri\/target\/\.tauri"/, `${start.trim()} must execute the wrapper with the isolated cache path Tauri reads under useLocalToolsDir`);
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
  const download = source.indexOf('actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131');
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

test('release-build caller graph composes verified workflows with default-read and publisher-only write', async () => {
  const source = await workflow('release-build.yml');
  assert.match(source, /^permissions:\n  contents: read$/m, 'release-build default permissions must be contents: read only');

  // Triggers: master push, release-build labeled PR, manual dispatch.
  assert.match(source, /^on:\n  push:\n    branches: \[master\]/m, 'release-build must trigger on master push');
  assert.match(source, /^  pull_request:\n    types: \[labeled, synchronize\]/m, 'release-build must trigger on labeled/synchronize PR events');
  assert.doesNotMatch(source, /^  pull_request:\n    branches:/m, 'release-build must not restrict pull_request branches');
  assert.match(source, /^  workflow_dispatch:\s*$/m, 'release-build must remain manually dispatchable');

  // Non-master dispatch guard must fail before any build.
  const guardIndex = source.indexOf('  guard:');
  const firstBuildIndex = Math.min(
    ...['build-android.yml', 'build-web-extension.yml', 'build-rust.yml', 'build-supply-chain.yml', 'build-native-windows.yml', 'build-native-macos.yml', 'build-toolchain-manifest.yml']
      .map((file) => source.indexOf(`./.github/workflows/${file}`))
      .filter((index) => index !== -1),
  );
  assert.ok(guardIndex !== -1, 'release-build must declare a guard job');
  assert.ok(firstBuildIndex !== -1, 'release-build must call at least one reusable workflow');
  assert.ok(guardIndex < firstBuildIndex, 'release-build guard must precede every reusable-workflow call');
  const guardBlock = source.slice(guardIndex, firstBuildIndex);
  assert.match(guardBlock, /if: github\.event_name == 'workflow_dispatch'/, 'release-build guard must only run on workflow_dispatch');
  assert.match(guardBlock, /github\.ref != 'refs\/heads\/master'/, 'release-build guard must test for a non-master ref');
  assert.match(guardBlock, /::error::|exit 1/, 'release-build guard must fail the dispatch before builds');

  // Reusable-workflow caller jobs contain only uses/with/secrets/needs/if/permissions.
  const jobs = callerJobKeys(source);
  for (const [job, keys] of Object.entries(jobs)) {
    if (job === 'guard') continue; // guard is a local gate, not a reusable-workflow caller
    // Only reusable-workflow caller jobs (uses: ./.github/workflows/...) are constrained.
    const jobStart = source.indexOf(`  ${job}:`);
    const nextJobMatch = source.slice(jobStart + 1).match(/\n  [A-Za-z0-9_-]+:\s*$/);
    const jobEnd = nextJobMatch ? jobStart + 1 + nextJobMatch.index : source.length;
    const jobBlock = source.slice(jobStart, jobEnd);
    if (!/uses: \.\/\.github\/workflows\//.test(jobBlock)) continue;
    for (const key of keys) {
      assert.ok(
        ['uses', 'with', 'secrets', 'needs', 'if', 'permissions'].includes(key),
        `release-build reusable-workflow caller job "${job}" must contain only caller fields; found "${key}"`,
      );
    }
  }

  const labelCondition = "github.event.action == 'labeled' && github.event.label.name == 'release-build'";
  const synchronizeCondition = "github.event.action == 'synchronize' && contains(github.event.pull_request.labels.*.name, 'release-build')";

  // Android-only ordinary master push: android job's if includes push; the
  // full-graph jobs exclude push and require workflow_dispatch or the label.
  const androidBlock = source.slice(source.indexOf('  android:'), source.indexOf('  web-extension:'));
  assert.match(androidBlock, /github\.event_name == 'push'/, 'release-build android job must run on ordinary master push');

  for (const job of FULL_GRAPH_JOBS) {
    const start = source.indexOf(`  ${job}:`);
    const nextJobMatch = source.slice(start + 1).match(/\n  [A-Za-z0-9_-]+:\s*$/);
    const end = nextJobMatch ? start + 1 + nextJobMatch.index : source.length;
    const jobBlock = source.slice(start, end);
    assert.ok(jobBlock.length > 0, `release-build must define job ${job}`);
    assert.doesNotMatch(jobBlock, /github\.event_name == 'push'/, `release-build job ${job} must not run on ordinary master push`);
    assert.match(jobBlock, /github\.event_name == 'workflow_dispatch'/, `release-build job ${job} must gate on workflow_dispatch`);
    assert.match(jobBlock, new RegExp(escapeRegexp(labelCondition)), `release-build job ${job} must include the labeled pull_request trigger`);
    assert.match(jobBlock, new RegExp(escapeRegexp(synchronizeCondition)), `release-build job ${job} must include the synchronize pull_request trigger`);
  }

  // Labeled PR runs the complete component graph (every full-graph job + aggregate).
  assert.ok(FULL_GRAPH_JOBS.every((job) => source.includes(`  ${job}:`)), 'release-build labeled PR must run every full-graph component');
  const aggregateBlock = source.slice(source.indexOf('  aggregate:'), source.indexOf('  publish:'));
  assert.match(aggregateBlock, /needs: \[guard, android, web-extension, rust, supply-chain, native-windows, native-macos\]/, 'release-build aggregate job must need guard and every component');

  // Manual publication depends on Android, supply-chain, and aggregate, and is the only writer.
  const publishStart = source.indexOf('  publish:');
  const publishBlock = source.slice(publishStart);
  assert.match(publishBlock, /needs: \[guard, android, supply-chain, aggregate\]/, 'release-build publish job must depend on guard, android, supply-chain, and aggregate');
  assert.match(publishBlock, /github\.event_name == 'workflow_dispatch'[\s\S]*?github\.ref == 'refs\/heads\/master'/, 'release-build publish job must only run on a master manual dispatch');
  assert.match(publishBlock, /permissions:\n      contents: write/, 'release-build publish job must declare contents: write');
  // No other job grants contents: write.
  const beforePublish = source.slice(0, publishStart);
  assert.doesNotMatch(beforePublish, /contents: write/, 'release-build must grant contents: write only in the publish job');

  // Publisher downloads verified Android output and preserves prerelease collision/replacement.
  assert.match(publishBlock, /actions\/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131 # v7/, 'release-build publisher must download verified artifacts via the pinned download-artifact action');
  assert.match(publishBlock, /name: android-release-evidence/, 'release-build publisher must download the verified Android release-evidence artifact');
  assert.match(publishBlock, /gh release view "\$VERSION" --json isPrerelease/, 'release-build publish must preserve prerelease collision/replacement behavior');
  assert.match(publishBlock, /gh release delete "\$VERSION" --cleanup-tag --yes/, 'release-build publish must replace a previous manual prerelease');
  assert.match(publishBlock, /gh release create "\$VERSION"/, 'release-build publish must create the prerelease from verified Android output');
  assertNoSecretRunInterpolation(publishBlock, 'release-build publish job');
});

test('every workflow pins external actions to the reviewed SHA plus version comment', async () => {
  const files = await workflowFiles();
  assert.ok(files.length >= 11, `expected at least 11 workflow files, found ${files.length}`);
  for (const name of files) {
    const source = await workflow(name);
    assertActionsPinned(source, name);
  }
});

test('reusable component workflows referenced by the caller graph exist and remain reusable', async () => {
  const caller = await workflow('release-build.yml');
  for (const file of ['build-android.yml', 'build-web-extension.yml', 'build-rust.yml', 'build-supply-chain.yml', 'build-native-windows.yml', 'build-native-macos.yml', 'build-toolchain-manifest.yml']) {
    assert.match(caller, new RegExp(`uses: \\./\\.github/workflows/${file.replace(/\./g, '\\.')}`), `release-build caller must reuse ${file}`);
    const reusable = await workflow(file);
    assert.match(reusable, /workflow_call:/, `${file} must remain reusable (workflow_call)`);
  }
});

// A `run: <path>` step (with no explicit interpreter like `bash`, `pwsh`, or
// `node`) executes the named file directly, so it must carry the executable
// bit in git. Regression guard for the macOS `network-deny-macos.sh`
// "Permission denied" failure where the file was committed with mode 100644.
function runDirectScriptPaths(source) {
  const paths = new Set();
  for (const command of runCommands(source)) {
    const lines = command.split('\n');
    for (const line of lines) {
      // Match a leading script path with a repo-relative location (no
      // interpreter prefix, no `${{ }}` interpolation, no shell builtin).
      const match = line.match(/^\s*(\.\.?\/[^\s#]+|verify\/[^\s#]+|rust\/scripts\/[^\s#]+)\s*$/);
      if (!match) continue;
      // Skip steps that only chain through bash/pwsh/node via a leading
      // interpreter token (already covered by the negative lookabove).
      paths.add(match[1]);
    }
  }
  return [...paths];
}

test('every workflow script invoked directly is committed executable in git', async () => {
  const files = await workflowFiles();
  const direct = new Set();
  for (const name of files) {
    const source = await workflow(name);
    for (const path of runDirectScriptPaths(source)) direct.add(path);
  }
  assert.ok(direct.size > 0, 'expected at least one directly-invoked workflow script');
  for (const path of direct) {
    const result = spawnSync('git', ['ls-tree', 'HEAD', path], { cwd: repository, encoding: 'utf8' });
    assert.equal(result.status, 0, `git ls-tree HEAD ${path} failed: ${result.stderr}`);
    const [mode] = result.stdout.trim().split(/\s+/);
    assert.equal(mode, '100755', `directly-invoked workflow script ${path} must be committed with mode 100755, found ${mode}`);
  }
});
