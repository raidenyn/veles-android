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
  // Verified against the upstream v6 tag (git/ref/tags/v6 -> this commit) on 2026-11-01; used by otp23-feasibility.yml.
  'actions/setup-python': { sha: 'ece7cb06caefa5fff74198d8649806c4678c61a1', version: 'v6' },
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

// Reviewed input allowlists for the pinned artifact actions. `actions/
// download-artifact` has NO `if-no-files-found` input (that belongs to
// upload-artifact); downloading a missing named artifact fails on its own.
// Every `with:` key declared next to these actions must appear here, so an
// unknown input fails loudly instead of being silently ignored by the action.
const ARTIFACT_ACTION_INPUTS = {
  'actions/upload-artifact': ['name', 'path', 'if-no-files-found', 'retention-days', 'compression-level', 'include-hidden-files'],
  'actions/download-artifact': ['name', 'pattern', 'path', 'merge-multiple', 'run-id', 'github-token'],
};

function stepUses(step) {
  return step.flatMap((line) => {
    const entry = mappingEntry(line);
    return [entry?.key === 'uses' ? entry.value : null, ...flowValues(line, 'uses')];
  }).filter((value) => value !== null).map((value) => value.trim());
}

function stepWithKeys(step) {
  const withIndex = step.findIndex((line) => {
    const entry = mappingEntry(line);
    return entry?.key === 'with' && entry.value.trim() === '';
  });
  if (withIndex === -1) return null;
  const withIndent = mappingEntry(step[withIndex]).indent;
  const keys = [];
  for (let index = withIndex + 1; index < step.length; index += 1) {
    const line = step[index];
    if (line.trim() && line.match(/^\s*/)[0].length <= withIndent) break;
    const entry = mappingEntry(line);
    if (entry && entry.indent === withIndent + 2) keys.push(entry.key);
  }
  return keys;
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

test('Android workflow builds one release variant and stages its APK, mapping, and checksums', async () => {
  const source = await workflow('build-android.yml');
  assertReusableWorkflow(source, 'build-android.yml');
  assert.match(source, /secrets:[\s\S]*?KEYSTORE_BASE64/, 'Android workflow must accept signing secrets');
  assert.doesNotMatch(source, /release-evidence-artifact-name/, 'Android workflow must drop the release-evidence artifact input');
  assert.match(source, /artifact-name:[\s\S]*?default:\s*android-release/, 'Android artifact name must default to the neutral android-release');
  assert.match(source, /env:\s*\n\s*KEYSTORE_BASE64:\s*\$\{\{ secrets\.KEYSTORE_BASE64 \}\}/, 'Android workflow must map the keystore secret into env');
  assert.match(source, /if:\s*env\.KEYSTORE_BASE64 != ''/, 'Android workflow must test secret presence through env');
  assert.doesNotMatch(source, /if:\s*secrets\./, 'Android workflow must not reference secrets directly in if conditions');
  for (const secret of ['KEYSTORE_PASSWORD', 'KEY_ALIAS', 'KEY_PASSWORD']) {
    assert.match(source, new RegExp(`env:\\s*\\n[\\s\\S]*?${secret}:\\s*\\$\\{\\{ secrets\\.${secret} \\}\\}`), `Android workflow must map ${secret} into env`);
  }
  assertNoSecretRunInterpolation(source, 'build-android.yml');
  // One mutually exclusive signed/unsigned assembly path: exactly two
  // assembleRelease steps exist in the file, gated on opposite env
  // conditions, so every invocation assembles exactly one release variant
  // (the signed APK when signing material is present, the unsigned APK
  // otherwise) with no second canonical/reference build.
  assert.equal((source.match(/\.\/gradlew assembleRelease/g) ?? []).length, 2, 'Android workflow must declare exactly one signed and one unsigned assembly step');
  const signedStep = source.slice(source.indexOf('- name: Build signed release APK'), source.indexOf('- name: Build unsigned release APK'));
  assert.match(signedStep, /if:\s*env\.KEYSTORE_BASE64 != ''/, 'the signed assembly must run only when signing material is present');
  assert.match(signedStep, /\.\/gradlew assembleRelease/, 'the signed assembly must build the release APK');
  assert.match(signedStep, /VELES_KEYSTORE_FILE:\s*\$\{\{ runner\.temp \}\}\/keystore\.jks/, 'the signed assembly must receive the decoded keystore');
  assert.match(signedStep, /VELES_KEYSTORE_PASSWORD:\s*\$\{\{ env\.KEYSTORE_PASSWORD \}\}/, 'the signed assembly must receive signing material through env');
  const unsignedStep = source.slice(source.indexOf('- name: Build unsigned release APK'), source.indexOf('- name: Verify APK JNI allow-list'));
  assert.match(unsignedStep, /if:\s*env\.KEYSTORE_BASE64 == ''/, 'the unsigned assembly must run only when signing material is absent');
  assert.match(unsignedStep, /\.\/gradlew assembleRelease/, 'the unsigned assembly must build the release APK');
  assert.doesNotMatch(unsignedStep, /VELES_KEYSTORE/, 'the unsigned assembly must not receive any signing environment');
  // JNI validation routes to the APK that was actually assembled.
  const jniStep = source.slice(source.indexOf('- name: Verify APK JNI allow-list'), source.indexOf('- name: Stage release APK, mapping, and checksums'));
  assert.match(jniStep, /if \[ -n "\$KEYSTORE_BASE64" \]; then[\s\S]*?APK="app\/build\/outputs\/apk\/release\/app-release\.apk"/, 'JNI validation must select the signed APK on the signed path');
  assert.match(jniStep, /APK="app\/build\/outputs\/apk\/release\/app-release-unsigned\.apk"/, 'JNI validation must select the unsigned APK on the unsigned path');
  assert.match(jniStep, /rust\/scripts\/verify-apk-jni\.sh "\$APK"/, 'JNI allow-list must run against the selected APK');
  // No Docker reference rebuild and no canonical unsigned export.
  assert.doesNotMatch(source, /verify\/verify\.sh/, 'Android workflow must not run the Docker reference rebuild');
  assert.doesNotMatch(source, /build\/verification\/android\/app-release-unsigned\.apk/, 'Android workflow must not export a canonical unsigned APK');
  // Staging: the actual APK, the mapping, and SHA256SUMS travel together.
  const stageStep = source.slice(source.indexOf('- name: Stage release APK, mapping, and checksums'), source.indexOf('- uses: actions/upload-artifact'));
  assert.match(stageStep, /if \[ -n "\$KEYSTORE_BASE64" \]; then[\s\S]*?APK="app-release\.apk"/, 'staging must select the signed APK name on the signed path');
  assert.match(stageStep, /APK="app-release-unsigned\.apk"/, 'staging must select the unsigned APK name on the unsigned path');
  assert.match(stageStep, /cp "app\/build\/outputs\/apk\/release\/\$APK" "build\/verification\/android-release\/\$APK"/, 'Android workflow must stage the actual release APK under its selected name');
  assert.match(stageStep, /cp app\/build\/outputs\/mapping\/release\/mapping\.txt build\/verification\/android-release\/mapping\.txt/, 'Android workflow must stage the mapping');
  assert.match(stageStep, /sha256sum "\$APK" mapping\.txt > SHA256SUMS/, 'Android workflow must stage SHA256SUMS over the APK and mapping');
  assert.match(source, /name:\s*\$\{\{ inputs\.artifact-name \}\}/, 'Android workflow must upload the caller-selected artifact');
  assertExactUploadPaths(source, 'build-android.yml', ['build/verification/android-release/']);
});

test('web extension workflow packages the extension after its ordinary checks', async () => {
  const source = await workflow('build-web-extension.yml');
  assertReusableWorkflow(source, 'build-web-extension.yml');
  assert.match(source, /actions\/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38[\s\S]*?node-version:\s*['"]26\.8\.1['"]/, 'Web workflow must use Node 26.8.1');
  assert.match(source, /npm ci --ignore-scripts/, 'Web workflow must disable install scripts');
  assert.match(source, /npm run format:check && npm run lint && npm run typecheck && npm test && npm run build && npm run test:bundle && npm run package/, 'Web workflow must retain its format/lint/typecheck/test/bundle checks and package');
  assert.doesNotMatch(source, /verify\/verify-web\.sh/, 'Web workflow must not run the reference verifier automatically');
  assert.match(source, /artifact-name:[\s\S]*?default:\s*web-extension/, 'Web artifact name must default to the neutral web-extension');
  assertExactUploadPaths(source, 'build-web-extension.yml', [
    'build/web-extension/veles-extension-*.zip',
    'build/web-extension/veles-extension-*.zip.sha256',
    'build/web-extension/SHA256SUMS',
  ]);
  assert.doesNotMatch(source, /secrets:/, 'Web workflow must not accept signing secrets');
});

test('Rust workflow packages rust-jni-wasm without a reference rebuild', async () => {
  const source = await workflow('build-rust.yml');
  assertReusableWorkflow(source, 'build-rust.yml');
  assert.match(source, /actions\/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38[\s\S]*?node-version:\s*['"]26\.8\.1['"]/, 'Rust workflow must use Node 26.8.1');
  assert.match(source, /\.\/gradlew rustPackage/, 'Rust workflow must produce the Rust package');
  assert.doesNotMatch(source, /verify\/verify-rust\.sh/, 'Rust workflow must not run the reference verifier automatically');
  assert.match(source, /default:\s*rust-jni-wasm/, 'Rust workflow must retain the stable rust-jni-wasm artifact name');
  assert.match(source, /name:\s*\$\{\{ inputs\.artifact-name \}\}/, 'Rust workflow must publish the requested artifact name');
  assert.match(source, /path:\s*build\/rust-package\//, 'Rust workflow must upload only its package tree');
  assertExactUploadPaths(source, 'build-rust.yml', ['build/rust-package/']);
  assert.match(source, /include-hidden-files:\s*true/, 'Rust workflow must upload the manifest-listed wasm/.gitignore');
  assert.doesNotMatch(source, /secrets:/, 'Rust workflow must not accept signing secrets');
});

test('supply-chain workflow enforces reports before uploading exactly six SBOMs', async () => {
  const source = await workflow('build-supply-chain.yml');
  assertReusableWorkflow(source, 'build-supply-chain.yml');
  assert.match(source, /actions\/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38[\s\S]*?node-version:\s*['"]26\.8\.1['"]/, 'Supply-chain workflow must use Node 26.8.1');
  assert.match(source, /\.\/gradlew installCargoDeny/, 'Supply-chain workflow must provision cargo-deny');
  assert.match(source, /verify\/verify-supply-chain\.sh/, 'Supply-chain workflow must enforce supply-chain policy');
  assert.ok(source.indexOf('./gradlew installCargoDeny') < source.indexOf('verify/verify-supply-chain.sh'), 'Supply-chain workflow must provision cargo-deny before enforcement');
  assert.ok(source.indexOf('verify/verify-supply-chain.sh') < source.indexOf('actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a'), 'Supply-chain workflow must enforce policy before upload');
  assert.match(source, /artifact-name:[\s\S]*?default:\s*supply-chain-reports/, 'Supply-chain artifact name must default to the neutral supply-chain-reports');
  assertExactUploadPaths(source, 'build-supply-chain.yml', [
    'build/sbom/web-extension.cdx.json',
    'build/sbom/protocol.cdx.json',
    'build/sbom/rust.cdx.json',
    'build/sbom/native-bridge.cdx.json',
    'build/sbom/native-host.cdx.json',
    'build/sbom/native-installer.cdx.json',
    'build/verification/supply-chain/npm-licenses.txt',
    'build/verification/supply-chain/cargo-licenses.txt',
    'build/verification/supply-chain/npm-install-scripts.txt',
    'build/verification/supply-chain/cargo-build-scripts.txt',
    'build/verification/supply-chain/vendored-patches.txt',
    'build/verification/supply-chain/remote-code.txt',
  ]);
  assert.doesNotMatch(source, /secrets:/, 'Supply-chain workflow must not accept signing secrets');
});

// OTP-25 Task 14/15: the native producers take an explicit `build-kind`. `release` builds the
// standalone installers' UNSIGNED input trees with the installer Gradle graph (two A/B slots); it
// has no Tauri scaffold path, no secret, no protected environment and no artifact taken from
// another run. `debug` builds the finished fixture-channel DEBUG installer (one slot, debug
// Rust/Swift, setup.exe / ad-hoc-signed DMG, metadata and symbols). There is no config-file probe.
const NATIVE_PRODUCERS = {
  windows: {
    file: 'build-native-windows.yml',
    runner: 'windows-2025',
    artifact: 'native-unsigned-windows',
    gradle: String.raw`.\gradlew.bat`,
    debugInstaller: 'setup.exe',
  },
  macos: {
    file: 'build-native-macos.yml',
    runner: 'macos-26',
    artifact: 'native-unsigned-macos',
    gradle: './gradlew',
    debugInstaller: 'Veles Bridge Installer.dmg',
  },
};
const RELEASE_OUTPUT = ['tree.tar', 'provenance.json', 'report-ab.json', 'SHA256SUMS'].map((name) => `build/native-bridge/installer/out/production/${name}`);
const DEBUG_OUTPUT = (spec) => [spec.debugInstaller, 'metadata.json', 'SHA256SUMS', 'symbols'].map((name) => `build/native-bridge/installer/out/debug/${name}`);

function assertInstallerProducer(source, platform) {
  const spec = NATIVE_PRODUCERS[platform];
  const name = spec.file;
  assert.match(source, /workflow_call:/, `${name} must be reusable`);
  assert.match(source, /commit-sha:[\s\S]*?required:\s*true/, `${name} must require the source commit`);
  assert.match(source, new RegExp(`artifact-name:\\s*\\n\\s*required:\\s*true\\s*\\n\\s*type:\\s*string\\s*\\n\\s*default:\\s*${spec.artifact}\\b`), `${name} must default to the neutral ${spec.artifact} artifact name`);
  assert.match(source, /build-kind:\s*\n\s*required:\s*true\s*\n\s*type:\s*string\b/, `${name} must require a string build-kind`);
  assert.doesNotMatch(source, /build-kind:\s*\n\s*required:\s*true\s*\n\s*type:\s*string\s*\n\s*default:/, `${name} must not default build-kind`);
  assert.doesNotMatch(source, /-latest/, `${name} must use pinned target runner labels`);
  assert.match(source, new RegExp(`runs-on:\\s*${spec.runner.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`), `${name} must target ${spec.runner}`);
  assert.equal((source.match(/runs-on:/g) ?? []).length, 1, `${name} must build and package in exactly one job`);
  assert.deepEqual(Object.keys(callerJobKeys(source)), ['build'], `${name} must define exactly one native job`);
  assert.doesNotMatch(source, /secrets:/, `${name} must not accept signing secrets`);
  assert.doesNotMatch(source, /^\s*environment:/m, `${name} must not enter a protected environment`);
  assertNoSecretRunInterpolation(source, name);
  assert.match(source, /ref:\s*\$\{\{ inputs\.commit-sha \}\}/, `${name} must build the requested commit`);
  // No Tauri scaffold production path, no Node, no A/B transports, no offline wrapper, no other run's artifacts.
  const executable = source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');
  for (const forbidden of [
    /tauri/i, /\bnpm\b/, /setup-node/, /bridgePackage/, /bridgeBundle/, /bridgeBuild\b/, /bridgeManifests/, /provision-windows-tools/, /NSIS_PATH/,
    /veles-native-bridge/, /network-deny-windows\.ps1/, /network-deny-macos\.sh/, /sandbox-exec/, /NetFirewallRule/, /pfctl/,
    /extract-view\.mjs/, /prepare-run\.(?:sh|ps1)/, /verify\/verify-native\.sh/, /verified-artifact\.mjs/,
    /native-runs/, /unverified-/, /needs:/, /actions\/download-artifact@/, /--offline/, /\bgh run\b/, /run-id/,
  ]) {
    assert.doesNotMatch(executable, forbidden, `${name} must not use ${forbidden}`);
  }
  // The build kind is an explicit, validated selection; no config-file probe and no fixture fallback.
  const validation = executable.indexOf('build-kind must be release or debug');
  assert.ok(validation >= 0, `${name} must validate build-kind as release or debug`);
  assert.doesNotMatch(executable, new RegExp(`release-${platform}\\.json`), `${name} must not select its channel from config-file existence`);
  assert.equal(executable.split('bridgeInstallerUnsigned').length - 1, 1, `${name} must invoke bridgeInstallerUnsigned exactly once`);
  assert.equal(executable.split('bridgeInstallerDebug').length - 1, 1, `${name} must invoke bridgeInstallerDebug exactly once`);
  assert.doesNotMatch(executable, /bridgeInstallerPackageFixture/, `${name} must not silently build a fixture input tree`);
  assert.ok(validation < Math.min(executable.indexOf('bridgeInstallerUnsigned'), executable.indexOf('bridgeInstallerDebug')), `${name} must validate build-kind before any build`);
  const selection = (task, kind) => {
    const index = executable.indexOf(task);
    const condition = executable.lastIndexOf(`inputs.build-kind == '${kind}'`, index);
    return index >= 0 && condition >= 0 && index - condition < 400;
  };
  assert.ok(selection('bridgeInstallerUnsigned', 'release'), `${name} must build production inputs only for build-kind release`);
  assert.ok(selection('bridgeInstallerDebug', 'debug'), `${name} must build the debug installer only for build-kind debug`);
  assert.ok(executable.includes(`VELES_WORKFLOW_RUN_ID: \${{ github.run_id }}`), `${name} must pass the workflow run id to the debug packaging`);
  assert.ok(executable.includes(`${spec.gradle} bridgeInstallerUnsigned`) && executable.includes(`${spec.gradle} bridgeInstallerDebug`), `${name} must invoke Gradle directly with the platform wrapper`);
  for (const scaffold of ['bridgePackage', 'bridgeHostBuild']) assert.ok(!executable.includes(scaffold), `${name} must not invoke ${scaffold}`);
  for (const directory of ['native-bridge/installer', 'native-bridge/host']) {
    assert.match(executable, new RegExp(`working-directory:\\s*${directory.replace(/\//g, '\\/')}\\s*\\n\\s*run:\\s*rustup show active-toolchain`), `${name} must activate the pinned Rust toolchain of ${directory}`);
  }
  assert.match(executable, /actions\/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1[\s\S]*?python-version:\s*['"]3\.12['"]/, `${name} must pin Python 3.12 for the assembler`);
  // Hashes and modes travel inside a mode-preserving tar plus checksums; the debug artifact is the
  // finished installer with metadata and symbols. Uploads are exact files, bounded.
  const uploads = uploadPaths(source, name);
  assert.deepEqual(uploads, [RELEASE_OUTPUT, DEBUG_OUTPUT(spec)], `${name} must upload exactly the release input tree and the finished debug installer`);
  const uploadSteps = steps(source).filter((step) => step.some((line) => isUploadArtifact(mappingEntry(line)?.value ?? '')));
  assert.equal(uploadSteps.length, 2, `${name} must have exactly two uploads`);
  const [release, debug] = uploadSteps.map((step) => step.join('\n'));
  assert.match(release, /if: inputs\.build-kind == 'release'/, `${name} release upload must be conditional on build-kind release`);
  assert.match(debug, /if: inputs\.build-kind == 'debug'/, `${name} debug upload must be conditional on build-kind debug`);
  for (const step of [release, debug]) {
    assert.match(step, /name:\s*\$\{\{ inputs\.artifact-name \}\}/, `${name} must publish the caller-selected artifact name`);
    assert.match(step, /if-no-files-found:\s*error/, `${name} uploads must fail on missing files`);
    assert.match(step, /retention-days:\s*\d+/, `${name} uploads must be retention-bounded`);
    assert.doesNotMatch(step, /path:[^\n]*\*|^\s+build\/[^\n]*veles-native-host(?:\.exe)?\s*$/m, `${name} uploads must name exact files, never globs or bare executables whose exec bit upload drops`);
  }
  assert.doesNotMatch(release, /out\/debug\//, `${name} release upload must not carry the debug installer`);
  assert.doesNotMatch(debug, /out\/production\//, `${name} debug upload must not carry the production input tree`);
}

test('Windows native workflow builds unsigned installer inputs in one job with the pinned NSIS', async () => {
  const source = await workflow('build-native-windows.yml');
  assertInstallerProducer(source, 'windows');
  const executable = source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');
  const provision = executable.indexOf('provision-nsis.ps1');
  const firstBuild = executable.search(/bridgeInstaller(?:Unsigned|Debug)/);
  assert.ok(provision !== -1 && provision < firstBuild, 'Windows workflow must provision the pinned NSIS before building');
  assert.match(executable, /build\/native-bridge\/installer\/tools\/nsis/, 'NSIS must be provisioned under the mandated build root');
  assert.match(executable, /if \(\$LASTEXITCODE -ne 0\) \{ exit \$LASTEXITCODE \}/, 'provisioning failure must propagate explicitly');
});

test('macOS native workflow builds unsigned installer inputs in one job under the pinned Xcode', async () => {
  const source = await workflow('build-native-macos.yml');
  assertInstallerProducer(source, 'macos');
  const jobBlock = source.slice(source.indexOf('  build:'));
  assert.match(jobBlock, /^    env:\n      DEVELOPER_DIR: \/Applications\/Xcode_26\.6\.app$/m, 'macOS workflow must set the pinned Xcode in job environment so it reaches every build step');
  for (const contract of ['17F113', 'macosx26.5']) {
    assert.match(jobBlock, new RegExp(contract.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), `macOS workflow must enforce the pinned ${contract}`);
  }
  assert.doesNotMatch(jobBlock, /export DEVELOPER_DIR/, 'macOS Xcode selection must not depend on a per-step shell export');
});

function mutateProducer(source, label, platform) {
  const spec = NATIVE_PRODUCERS[platform];
  const mutations = {
    'scaffold packaging': (text) => `${text}\n      - run: ${spec.gradle} bridgePackage\n`,
    'node tooling': (text) => `${text}\n      - run: npm ci --prefix native-bridge\n`,
    'duplicate unsigned build': (text) => `${text}\n      - run: ${spec.gradle} bridgeInstallerUnsigned\n`,
    'duplicate debug build': (text) => `${text}\n      - run: ${spec.gradle} bridgeInstallerDebug\n`,
    'fixture fallback': (text) => `${text}\n      - run: ${spec.gradle} bridgeInstallerPackageFixture\n`,
    'unconditional production build': (text) => text.replace("        if: inputs.build-kind == 'release'\n", ''),
    'unconditional debug build': (text) => text.replace("        if: inputs.build-kind == 'debug'\n", ''),
    'secret input': (text) => text.replace('permissions:\n  contents: read', 'permissions:\n  contents: read\n# secrets:\n    secrets:\n      X:\n        required: true'),
    'protected environment': (text) => text.replace('    steps:', '    environment: native-release\n    steps:'),
    'artifact from another run': (text) => text.replace('    steps:', '    steps:\n      - uses: actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131 # v7\n        with:\n          name: x\n          run-id: 1\n'),
    'floating action': (text) => text.replace('actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1', 'actions/checkout@v4'),
    'glob upload': (text) => text.replace('build/native-bridge/installer/out/production/tree.tar', 'build/native-bridge/installer/out/production/*'),
    'dropped provenance': (text) => text.replace('            build/native-bridge/installer/out/production/provenance.json\n', ''),
    'dropped debug metadata': (text) => text.replace('            build/native-bridge/installer/out/debug/metadata.json\n', ''),
    'dropped debug symbols': (text) => text.replace('            build/native-bridge/installer/out/debug/symbols\n', ''),
    'scaffold artifact default': (text) => text.replace(`default: ${spec.artifact}`, `default: native-${platform}`),
    'debug mislabeled release': (text) => text.replace("if: inputs.build-kind == 'debug'", "if: inputs.build-kind == 'release'"),
    'missing build kind': (text) => text.replace('      build-kind:\n        required: true\n        type: string\n', ''),
    'defaulted build kind': (text) => text.replace('      build-kind:\n        required: true\n        type: string\n', '      build-kind:\n        required: true\n        type: string\n        default: release\n'),
    'unvalidated build kind': (text) => text.replace('build-kind must be release or debug', 'build-kind is whatever'),
    'config-file probe': (text) => text.replace('      - name: Validate the build kind', `      - name: Probe for the approved release config\n        run: test -f native-bridge/installer/config/release-${platform}.json\n      - name: Validate the build kind`),
    'unactivated installer toolchain': (text) => text.replace('working-directory: native-bridge/installer\n        run: rustup show active-toolchain', 'run: true'),
    'silent upload': (text) => text.replaceAll('if-no-files-found: error', 'if-no-files-found: ignore'),
    'offline wrapper': (text) => text.replace(`${spec.gradle} bridgeInstallerDebug`, `${spec.gradle} --offline bridgeInstallerDebug`),
    'write permission': (text) => text.replace('contents: read', 'contents: write'),
  };
  return mutations[label](source);
}

test('native producer contract names every invariant it enforces (negative mutations)', async () => {
  const labels = [
    'scaffold packaging', 'node tooling', 'duplicate unsigned build', 'duplicate debug build', 'fixture fallback',
    'unconditional production build', 'unconditional debug build', 'secret input', 'protected environment',
    'artifact from another run', 'floating action', 'glob upload', 'dropped provenance', 'dropped debug metadata',
    'dropped debug symbols', 'scaffold artifact default', 'debug mislabeled release', 'missing build kind',
    'defaulted build kind', 'unvalidated build kind', 'config-file probe', 'unactivated installer toolchain',
    'silent upload', 'offline wrapper', 'write permission',
  ];
  for (const platform of Object.keys(NATIVE_PRODUCERS)) {
    const source = await workflow(NATIVE_PRODUCERS[platform].file);
    for (const label of labels) {
      const mutated = mutateProducer(source, label, platform);
      assert.notEqual(mutated, source, `${platform}: mutation "${label}" must change the workflow`);
      let rejected = false;
      try {
        assertInstallerProducer(mutated, platform);
        assertActionsPinned(mutated, NATIVE_PRODUCERS[platform].file);
        assert.match(mutated, /permissions:\n  contents: read\n/);
      } catch {
        rejected = true;
      }
      assert.ok(rejected, `${platform}: the contract must reject "${label}"`);
    }
  }
});

test('host check workflow runs the installer format, lint and tests on all three platforms', async () => {
  const source = await workflow('build-native-host.yml');
  const executable = source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');
  for (const task of ['bridgeInstallerFormat', 'bridgeInstallerLint', 'bridgeInstallerTest']) {
    assert.ok(executable.split(task).length - 1 >= 3, `${task} must run on the portable, Windows and macOS jobs`);
  }
  assert.equal(executable.split('bridgeInstallerScriptsTest').length - 1, 1, 'the Python script suites run once, in the portable job');
  assert.ok(executable.slice(0, executable.indexOf('  windows:')).includes('bridgeInstallerScriptsTest'), 'the Python script suites belong to the portable job');
  for (const task of ['bridgeInstallerUnsigned', 'bridgeInstallerPackageFixture', 'bridgeInstallerDebug']) {
    assert.ok(!executable.includes(task), `the check workflow must not build installer inputs (${task})`);
  }
  assert.match(executable, /working-directory:\s*native-bridge\/installer\s*\n\s*run:\s*rustup show active-toolchain/, 'the installer toolchain must be activated');
});

test('only the protected release workflow may reference signing credentials or sign anything', async () => {
  for (const name of await workflowFiles()) {
    if (name === 'release-native-installers.yml') continue;
    const executable = (await workflow(name)).split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');
    assert.doesNotMatch(executable, /VELES_WIN_|VELES_MAC_|VELES_NOTARY_|VELES_APPROVED_|native-release|signtool|notarytool|codesign/, `${name} must not reference signing credentials or tools`);
  }
});

test('the release gate builds the selected platform in the same owner-master run', async () => {
  const source = await workflow('release-native-installers.yml');
  assertReleaseInstallerCaller(source);
  // The owner guard is the only authorization; the old tag/two-run/approved-digest dispatch is gone.
  const comments = source.split('\n').filter((line) => line.trimStart().startsWith('#')).join('\n');
  assert.match(comments, /owner-only master dispatch/i, 'the header states the authorization model');
  assert.doesNotMatch(comments, /immutable release tag|refs\/tags|two approved|approved descriptor/i, 'no tag or manual digest ceremony is described');
});

const RELEASE_BUILDERS = { windows: 'build-native-windows.yml', macos: 'build-native-macos.yml' };
const RELEASE_ARTIFACTS = {
  windows: 'native-release-windows-${{ github.run_id }}-${{ github.run_attempt }}',
  macos: 'native-release-macos-${{ github.run_id }}-${{ github.run_attempt }}',
};

function releaseJobBlock(source, job) {
  const match = source.match(new RegExp(`^  ${escapeRegexp(job)}:\\s*$`, 'm'));
  assert.ok(match, `release caller must define job ${job}`);
  const rest = source.slice(match.index + match[0].length);
  const next = rest.match(/^  [A-Za-z0-9_-]+:\s*$/m);
  return next ? source.slice(match.index, match.index + match[0].length + next.index) : source.slice(match.index);
}

function evaluateJobCondition(condition, { platform, guard = 'success' }) {
  const expression = condition
    .replaceAll('${{', '')
    .replaceAll('}}', '')
    .replaceAll('github.event_name', JSON.stringify('workflow_dispatch'))
    .replaceAll('github.ref', JSON.stringify('refs/heads/master'))
    .replaceAll('github.actor', JSON.stringify('raidenyn'))
    .replaceAll('github.triggering_actor', JSON.stringify('raidenyn'))
    .replace(/inputs\.platform/g, JSON.stringify(platform))
    .replace(/needs\.([A-Za-z0-9_-]+)\.result/g, JSON.stringify(guard));
  assert.doesNotMatch(expression, /github\.|inputs\.|needs\.|vars\.|secrets\./, `condition uses an unsupported context: ${condition}`);
  const evaluate = new Function('cancelled', 'always', `"use strict"; return (${expression});`);
  return evaluate(() => false, () => true);
}

function assertReleaseInstallerCaller(source) {
  const name = 'release-native-installers.yml';
  const executable = source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');

  // One trigger and one input only: the platform selection.
  const onBlock = prWorkflowOnBlock(source);
  const triggerKeys = onBlock.split('\n').flatMap((line) => {
    const entry = mappingEntry(line);
    return entry && entry.indent === 2 ? [entry.key] : [];
  });
  assert.deepEqual(triggerKeys, ['workflow_dispatch'], `${name} must trigger only on manual dispatch`);
  assert.match(onBlock, /^  workflow_dispatch:$/m, `${name} must be manual-only`);
  assert.match(onBlock, /^      platform:\n        description: [^\n]+\n        required: true\n        type: choice\n        options: \[windows, macos, both\]\n        default: windows$/m, `${name} must expose exactly the platform selection`);
  for (const obsolete of [/commit:/, /descriptor_sha256/, /unsigned_run/, /windows_only/, /inputs\.commit/, /inputs\.descriptor/, /inputs\.unsigned/, /inputs\.windows_only/]) {
    assert.doesNotMatch(executable, obsolete, `${name} must not carry the obsolete dispatch input ${obsolete}`);
  }

  // Read-only, dispatch-only; secrets exist only in the signing steps checked below.
  assert.deepEqual(prWorkflowPermissions(source), ['contents: read'], `${name} must declare only contents: read`);
  assertNoSecretRunInterpolation(executable, name);

  // The guard rejects before checkout/build and every job repeats it, so reruns re-evaluate it.
  const guard = releaseJobBlock(source, 'guard');
  assert.ok(source.indexOf('  guard:') < source.indexOf('uses: ./.github/workflows/build-native-windows.yml'), `${name} guard must precede every build`);
  assert.doesNotMatch(guard, /actions\/checkout/, `${name} guard must reject before checkout`);
  for (const check of [
    /"\$EVENT" = workflow_dispatch/,
    /"\$REF" = refs\/heads\/master/,
    /"\$ACTOR" = raidenyn/,
    /"\$TRIGGERING_ACTOR" = raidenyn/,
    /\^\[0-9a-f\]\{40\}\$/,
    /windows\|macos\|both/,
  ]) {
    assert.match(guard, check, `${name} guard must enforce ${check}`);
  }
  const jobs = callerJobKeys(source);
  const allJobs = [
    'guard', 'build-windows', 'build-macos', 'verify-windows', 'verify-macos',
    'sign-windows-nested', 'sign-windows-outer', 'sign-macos-nested', 'sign-macos-outer',
    'assemble-windows', 'assemble-macos', 'gate',
  ];
  assert.deepEqual(Object.keys(jobs).sort(), allJobs.slice().sort(), `${name} must declare exactly the release graph`);
  for (const job of allJobs.filter((entry) => entry !== 'guard')) {
    const block = releaseJobBlock(source, job);
    const match = block.match(/^    if: (.+)$/m);
    assert.ok(match, `${name} job ${job} must declare its guard`);
    for (const part of ["github.event_name == 'workflow_dispatch'", "github.ref == 'refs/heads/master'", "github.actor == 'raidenyn'", "github.triggering_actor == 'raidenyn'"]) {
      assert.ok(match[1].includes(part), `${name} job ${job} must re-check ${part}`);
    }
  }

  // Build the selected platforms from this run's master SHA, with per-run artifact names.
  for (const [platform, builder] of Object.entries(RELEASE_BUILDERS)) {
    const block = releaseJobBlock(source, `build-${platform}`);
    assert.match(block, new RegExp(`^    uses: \\./\\.github/workflows/${escapeRegexp(builder)}$`, 'm'), `build-${platform} must reuse ${builder}`);
    assert.match(block, /^      build-kind: release$/m, `build-${platform} must request the release profile`);
    assert.match(block, /^      commit-sha: \$\{\{ github\.sha \}\}$/m, `build-${platform} must build this run's master commit`);
    assert.doesNotMatch(block, /github\.event\.pull_request|inputs\.commit|github\.head_ref/, `build-${platform} must not accept another source`);
    const artifact = block.match(/^      artifact-name: (.+)$/m);
    assert.ok(artifact, `build-${platform} must name its artifact`);
    assert.equal(artifact[1].trim(), RELEASE_ARTIFACTS[platform], `build-${platform} must name a per-run artifact`);
  }
  assert.doesNotMatch(executable, /\bgh run view\b/, `${name} must not query an external run`);
  assert.doesNotMatch(executable, /\brun-id:/, `${name} must download only same-run artifacts`);
  assert.doesNotMatch(executable, /unsigned_run|native-unsigned-windows|native-unsigned-macos/, `${name} must not consume another run's producer artifact`);

  // Verification and signing consume the same-run artifact, and every credential lives in native-release.
  const signJobs = ['sign-windows-nested', 'sign-windows-outer', 'sign-macos-nested', 'sign-macos-outer'];
  const credentialed = allJobs.filter((job) => /secrets\./.test(releaseJobBlock(source, job)));
  assert.deepEqual(credentialed.sort(), signJobs.slice().sort(), `${name} must map secrets only in the signing jobs`);
  for (const job of allJobs) {
    const block = releaseJobBlock(source, job);
    const protectedJob = /^    environment: native-release$/m.test(block);
    assert.equal(protectedJob, signJobs.includes(job), `${name} job ${job} must enter native-release only when it signs`);
  }
  for (const job of signJobs) {
    const block = releaseJobBlock(source, job);
    assert.match(block, /^      VELES_SIGNING_ENVIRONMENT: native-release$/m, `${job} must run in the release context`);
    assert.match(block, /^      COMMIT: \$\{\{ github\.sha \}\}$/m, `${job} must sign this run's commit`);
    assert.match(block, /^      VELES_DESCRIPTOR_SHA256: \$\{\{ needs\.verify-[a-z]+\.outputs\.descriptor-sha256 \}\}$/m, `${job} must bind the verified descriptor`);
  }
  assert.doesNotMatch(executable, /VELES_APPROVED_DESCRIPTOR_SHA256/, `${name} must not keep the obsolete approval variable`);

  // Each platform is independent: a Windows-only run neither needs nor names a macOS job/artifact.
  for (const platform of Object.keys(RELEASE_BUILDERS)) {
    const other = platform === 'windows' ? 'macos' : 'windows';
    for (const job of [`build-${platform}`, `verify-${platform}`, `sign-${platform}-nested`, `assemble-${platform}`, `sign-${platform}-outer`]) {
      const block = releaseJobBlock(source, job);
      assert.match(block, new RegExp(`inputs\\.platform == '${platform}' \\|\\| inputs\\.platform == 'both'`), `${job} must only run for its selected platform`);
      assert.ok(!block.includes(`inputs.platform == '${other}'`), `${job} must not select the other platform`);
      assert.ok(!new RegExp(`needs[^\\n]*\\b${other}\\b`).test(block), `${job} must not depend on ${other}`);
    }
    const verify = releaseJobBlock(source, `verify-${platform}`);
    assert.match(verify, new RegExp(`name: ${escapeRegexp(RELEASE_ARTIFACTS[platform])}`), `verify-${platform} must download its same-run artifact`);
    assert.ok(verify.includes('sha256sum -c SHA256SUMS'), `verify-${platform} must check the same-run artifact digest manifest`);
    assert.ok(!verify.includes(RELEASE_ARTIFACTS[other]), `verify-${platform} must not download the other platform`);
  }

  // Behaviour matrix: windows -> windows jobs and gate; macos -> macOS jobs and gate; both -> all.
  const selectionJobs = {
    windows: ['build-windows', 'verify-windows', 'sign-windows-nested', 'assemble-windows', 'sign-windows-outer'],
    macos: ['build-macos', 'verify-macos', 'sign-macos-nested', 'assemble-macos', 'sign-macos-outer'],
  };
  for (const platform of ['windows', 'macos', 'both']) {
    const expected = platform === 'both' ? [...selectionJobs.windows, ...selectionJobs.macos] : selectionJobs[platform];
    for (const platformJobs of Object.values(selectionJobs)) {
      for (const job of platformJobs) {
        const match = releaseJobBlock(source, job).match(/^    if: (.+)$/m);
        assert.equal(evaluateJobCondition(match[1], { platform }), expected.includes(job), `${name} job ${job} must run=${expected.includes(job)} for platform=${platform}`);
      }
    }
    const gateCondition = releaseJobBlock(source, 'gate').match(/^    if: (.+)$/m)[1];
    assert.equal(evaluateJobCondition(gateCondition, { platform }), true, `${name} gate must always report the selected status`);
  }

  // The gate composes only the selected targets and fails only when a selected platform failed.
  const gate = source.slice(source.indexOf('  gate:'));
  for (const command of [' bundle ', ' validate ', '--platform']) {
    assert.ok(gate.includes(command), `gate must use ${command}`);
  }
  assert.doesNotMatch(gate, /--descriptor-digest/, 'the gate must not take an external descriptor digest');
  assert.doesNotMatch(gate, /descriptor-set-digest/, 'the gate must not recompute an owner-approved digest');
  for (const result of ['RESULT_WINDOWS', 'RESULT_MACOS', 'RESULT_BUILD_WINDOWS', 'RESULT_BUILD_MACOS']) {
    assert.ok(gate.includes(result), `gate must report ${result}`);
  }
  for (const token of ['gh release', 'attest-build-provenance', 'git tag', 'git push']) {
    assert.ok(!executable.includes(token), `${name} must not publish (${token})`);
  }
}

test('the release caller contract rejects tag, cross-run and approval regressions', async () => {
  const source = await workflow('release-native-installers.yml');
  const mutations = {
    'push trigger': (text) => text.replace('on:\n  workflow_dispatch:', 'on:\n  push:\n    branches: [master]\n  workflow_dispatch:'),
    'commit input': (text) => text.replace('      platform:\n', '      commit:\n        required: true\n        type: string\n      platform:\n'),
    'descriptor input': (text) => text.replace('      platform:\n', '      descriptor_sha256_windows:\n        required: true\n        type: string\n      platform:\n'),
    'producer run input': (text) => text.replace('      platform:\n', '      unsigned_run_a:\n        required: true\n        type: string\n      platform:\n'),
    'windows-only input': (text) => text.replace('      platform:\n', '      windows_only:\n        required: false\n        type: boolean\n      platform:\n'),
    'missing guard job': (text) => text.replace('  guard:\n', '  guard-disabled:\n'),
    'guard checks no actor': (text) => text.replace('[ "$ACTOR" = raidenyn ]', '[ true = true ]'),
    'guard checks no rerun actor': (text) => text.replace('[ "$TRIGGERING_ACTOR" = raidenyn ]', '[ true = true ]'),
    'guard checks no master ref': (text) => text.replace('[ "$REF" = refs/heads/master ]', '[ true = true ]'),
    'job without the guard': (text) => text.replace(/^    if: (.+)$/m, '    if: ${{ !cancelled() }}'),
    'builder without release kind': (text) => text.replace('      build-kind: release', '      build-kind: debug'),
    'builder reads another commit': (text) => text.replace('commit-sha: ${{ github.sha }}', 'commit-sha: ${{ inputs.commit }}', 1),
    'external run download': (text) => text.replace('          name: native-release-windows-${{ github.run_id }}-${{ github.run_attempt }}\n', '          name: native-release-windows-${{ github.run_id }}-${{ github.run_attempt }}\n          run-id: 1\n'),
    'gh run view': (text) => `${text}\n  probe:\n    runs-on: ubuntu-latest\n    steps:\n      - run: gh run view 1\n`,
    'approved descriptor variable': (text) => text.replace('      VELES_SIGNING_ENVIRONMENT: native-release\n', '      VELES_SIGNING_ENVIRONMENT: native-release\n      VELES_APPROVED_DESCRIPTOR_SHA256: ${{ vars.VELES_APPROVED_DESCRIPTOR_SHA256_WINDOWS }}\n', 1),
    'credentials outside native-release': (text) => text.replaceAll('    environment: native-release\n', ''),
    'windows job needs macOS': (text) => text.replace('    needs: [guard, verify-windows]\n', '    needs: [guard, verify-windows, verify-macos]\n'),
    'macOS job runs for windows': (text) => text.replaceAll("inputs.platform == 'macos' || inputs.platform == 'both'", "inputs.platform == 'windows' || inputs.platform == 'both'"),
    'gate external digest': (text) => text.replace('--platform "$PLATFORM"', '--platform "$PLATFORM" --descriptor-digest "$DIGEST"'),
    'gate publishes': (text) => `${text}\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n      - run: gh release create x\n`,
  };
  for (const [label, mutate] of Object.entries(mutations)) {
    const mutated = mutate(source);
    assert.notEqual(mutated, source, `mutation "${label}" must change the workflow`);
    assert.throws(() => assertReleaseInstallerCaller(mutated), `the contract must reject "${label}"`);
  }
});

test('supply-chain consumers cover the installer crate, Swift frontend and NSIS toolchain', async () => {
  const generate = await readFile(new URL('verify/generate-sboms.sh', root), 'utf8');
  assert.match(generate, /generate_cargo native-bridge\/installer\/Cargo\.toml native-bridge\/installer native-installer\.cdx\.json/);
  const sboms = await import(new URL('verify/verify-sboms.mjs', root));
  assert.equal(sboms.PRODUCTION_SBOM_ROOTS['native-installer'], 'veles-bridge-installer@0.1.0');
  assert.deepEqual(sboms.PRODUCTION_LOCKFILES['native-installer'], { path: ['native-bridge', 'installer', 'Cargo.lock'], ecosystem: 'cargo' });
  assert.equal(typeof sboms.verifyInstallerToolchainCoverage, 'function');
  assert.deepEqual(await sboms.verifyInstallerToolchainCoverage(repository), [], 'the repository installer toolchain must be fully covered');
  const scan = await readFile(new URL('verify/check-remote-code.mjs', root), 'utf8');
  for (const scope of ['native-bridge/installer/src', 'native-bridge/installer/scripts', 'native-bridge/installer/windows', 'native-bridge/installer/macos/Package.swift', 'native-bridge/installer/macos/Sources']) {
    assert.ok(scan.includes(`'${scope}'`), `the remote-code scan must cover ${scope}`);
  }
  assert.match(await readFile(new URL('verify/check-cargo-build-scripts.mjs', root), 'utf8'), /native-bridge\/installer\/Cargo\.toml/);
  assert.match(await readFile(new URL('verify/check-npm-licenses.mjs', root), 'utf8'), /native-bridge\/installer\/Cargo\.toml/);
  assert.match(await workflow('build-supply-chain.yml'), /build\/sbom\/native-installer\.cdx\.json/);
});

test('installer toolchain coverage fails closed on an uncovered Swift package or an unpinned NSIS archive', async () => {
  const { verifyInstallerToolchainCoverage } = await import(new URL('verify/verify-sboms.mjs', root));
  const { mkdtemp, mkdir, writeFile, cp } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const copy = async (mutate) => {
    const directory = await mkdtemp(join(tmpdir(), 'installer-coverage-'));
    await mkdir(join(directory, 'native-bridge/installer/macos'), { recursive: true });
    await mkdir(join(directory, 'native-bridge/installer/config'), { recursive: true });
    await cp(new URL('native-bridge/installer/macos/Package.swift', root), join(directory, 'native-bridge/installer/macos/Package.swift'));
    await cp(new URL('native-bridge/installer/config/tools.lock.json', root), join(directory, 'native-bridge/installer/config/tools.lock.json'));
    await cp(new URL('native-bridge/installer/Cargo.lock', root), join(directory, 'native-bridge/installer/Cargo.lock'));
    await mutate(directory);
    return directory;
  };
  assert.deepEqual(await verifyInstallerToolchainCoverage(await copy(async () => {})), []);
  const external = await copy(async (directory) => {
    const path = join(directory, 'native-bridge/installer/macos/Package.swift');
    await writeFile(path, (await readFile(path, 'utf8')).replace('targets: [', 'dependencies: [.package(url: "https://example.invalid/x.git", from: "1.0.0")],\n    targets: ['));
  });
  assert.match((await verifyInstallerToolchainCoverage(external)).join('\n'), /Swift package declares an external dependency/);
  const resolved = await copy(async (directory) => { await writeFile(join(directory, 'native-bridge/installer/macos/Package.resolved'), '{}'); });
  assert.match((await verifyInstallerToolchainCoverage(resolved)).join('\n'), /Package\.resolved/);
  const unpinned = await copy(async (directory) => {
    const path = join(directory, 'native-bridge/installer/config/tools.lock.json');
    const lock = JSON.parse(await readFile(path, 'utf8'));
    lock.archives.nsis.sha256 = 'latest';
    await writeFile(path, JSON.stringify(lock));
  });
  assert.match((await verifyInstallerToolchainCoverage(unpinned)).join('\n'), /NSIS archive is not pinned/);
  const lockless = await copy(async (directory) => { await writeFile(join(directory, 'native-bridge/installer/Cargo.lock'), ''); });
  assert.match((await verifyInstallerToolchainCoverage(lockless)).join('\n'), /Cargo\.lock/);
});

// OTP-25 Task 1: the standalone installer toolchain is pinned independently of
// the runner labels. These tests read the checked-in tool lock directly (they do
// not shell out to the Python validator) and tie each pinned identity back to the
// workflow that has to enforce it.
const INSTALLER_LOCK = 'native-bridge/installer/config/tools.lock.json';
const INSTALLER_TOOLCHAIN = 'native-bridge/installer/rust-toolchain.toml';
const REVIEWED_NSIS_URL = 'https://github.com/tauri-apps/binary-releases/releases/download/nsis-3/nsis-3.zip';
const REVIEWED_NSIS_SHA256 = '1bb9fc85ee5b220d3869325dbb9d191dfe6537070f641c30fbb275c97051fd0c';
const INEXACT_PIN_VALUES = new Set(['', '*', 'latest', 'current', 'stable', 'nightly', 'any', 'unspecified', 'tbd', 'todo']);

async function installerToolLock() {
  return JSON.parse(await readFile(new URL(INSTALLER_LOCK, root), 'utf8'));
}

// Every string (keys included) in a nested JSON value, as `path=value` entries.
function jsonStrings(value, path = '') {
  if (Array.isArray(value)) return value.flatMap((item, index) => jsonStrings(item, `${path}[${index}]`));
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) => [
      `${path}.${key}=${key}`,
      ...jsonStrings(item, `${path}.${key}`),
    ]);
  }
  return [`${path}=${value}`];
}

test('installer tool lock pins exact NSIS, Rust and Xcode/SDK identities', async () => {
  const lock = await installerToolLock();
  assert.equal(lock.schema, 1, 'the installer tool lock must declare schema 1');

  // Exactly one archive, the reviewed NSIS mirror. No WiX, no Tauri plugin.
  assert.deepEqual(Object.keys(lock.archives), ['nsis'], 'the lock must pin exactly the reviewed NSIS archive');
  const nsis = lock.archives.nsis;
  assert.equal(nsis.url, REVIEWED_NSIS_URL, 'NSIS must come from the reviewed archive URL');
  assert.equal(nsis.sha256, REVIEWED_NSIS_SHA256, 'NSIS must be pinned to the reviewed SHA-256');
  assert.equal(nsis.version, '3.08', 'the reviewed NSIS archive is 3.08');
  assert.equal(nsis.extract_root, 'nsis-3.08', 'the archive extracts under nsis-3.08/');
  assert.equal(nsis.makensis, 'nsis-3.08/makensis.exe', 'the locked compiler path must be explicit');

  // No pin may be a wildcard, placeholder or "latest" alias.
  for (const entry of jsonStrings(lock)) {
    const value = entry.slice(entry.indexOf('=') + 1);
    assert.ok(!value.includes('*'), `installer tool pin must not contain a wildcard: ${entry}`);
    assert.ok(!INEXACT_PIN_VALUES.has(value.toLowerCase()), `installer tool pin must be exact: ${entry}`);
  }
  const lockText = await readFile(new URL(INSTALLER_LOCK, root), 'utf8');
  assert.doesNotMatch(lockText, /wix|nsis_tauri_utils/i, 'the standalone installer lock must not name WiX or the Tauri NSIS plugin');

  // Runner labels are recorded for provenance, but they are not the pin: the
  // compiler/SDK identities below carry the contract.
  assert.deepEqual(Object.keys(lock.targets).sort(), ['macos-arm64', 'windows-x86_64']);
  assert.equal(lock.targets['windows-x86_64'].runner, 'windows-2025');
  assert.equal(lock.targets['windows-x86_64'].build_target, 'x86_64-pc-windows-msvc');
  assert.equal(lock.targets['macos-arm64'].runner, 'macos-26');
  assert.equal(lock.targets['macos-arm64'].build_target, 'aarch64-apple-darwin');

  // A recorded identity the workflow does not actually enforce would be fiction:
  // each pin must appear in the workflow that builds that target.
  const windows = await workflow('build-native-windows.yml');
  assert.match(windows, new RegExp(`runs-on:\\s*${lock.targets['windows-x86_64'].runner}\\b`), 'the Windows producer must run on the locked runner');
  const macos = await workflow('build-native-macos.yml');
  assert.match(macos, new RegExp(`runs-on:\\s*${lock.targets['macos-arm64'].runner}\\b`), 'the macOS producer must run on the locked runner');
  const xcode = lock.tools.xcode.identity;
  const sdk = lock.tools.macos_sdk.identity;
  assert.equal(xcode.build, '17F113', 'the locked Xcode build must be the reviewed 17F113');
  assert.equal(xcode.version, '26.6');
  assert.equal(sdk.version, 'macosx26.5');
  assert.match(macos, /DEVELOPER_DIR:\s*\/Applications\/Xcode_26\.6\.app/, 'the macOS producer must select the locked Xcode');
  assert.ok(macos.includes(xcode.build), 'the macOS producer must enforce the locked Xcode build');
  assert.ok(macos.includes(sdk.version), 'the macOS producer must enforce the locked macOS SDK');

  // Rust is pinned by rust-toolchain.toml and the lock must agree with it.
  const toolchain = await readFile(new URL(INSTALLER_TOOLCHAIN, root), 'utf8');
  const rust = lock.tools.rust.identity;
  assert.equal(rust.channel, '1.98.0', 'the installer crate pins Rust 1.98.0');
  assert.match(toolchain, /^\[toolchain\]$/m, 'the installer crate must carry rust-toolchain.toml');
  assert.match(toolchain, new RegExp(`channel\\s*=\\s*"${rust.channel.replace(/\./g, '\\.')}"`), 'rust-toolchain.toml must pin the locked Rust channel');
  assert.match(toolchain, /profile\s*=\s*"minimal"/, 'the installer crate must use the minimal Rust profile');
  for (const component of rust.components.split(',')) {
    assert.match(toolchain, new RegExp(`components\\s*=\\s*\\[[^\\]]*"${component}"`), `rust-toolchain.toml must declare ${component}`);
  }
  for (const target of rust.targets.split(',')) {
    assert.match(toolchain, new RegExp(`targets\\s*=\\s*\\[[^\\]]*"${target.replace(/\./g, '\\.')}"`), `rust-toolchain.toml must declare the ${target} target`);
  }
});

test('installer tool lock pins the Windows compiler and SDK by version', async () => {
  const lock = await installerToolLock();
  const expectations = {
    msvc: ['cl_version', 'linker_version', 'msvc_toolset_version'],
    windows_sdk: ['windows_sdk_version', 'ucrt_version'],
  };
  for (const [tool, required] of Object.entries(expectations)) {
    const entry = lock.tools[tool];
    assert.ok(entry, `the lock must record the ${tool} tool`);
    assert.deepEqual(entry.targets, ['windows-x86_64'], `${tool} applies to the Windows target only`);
    assert.equal(entry.required_unresolved, undefined, `${tool} is pinned from the approved runner`);
    assert.ok(entry.identity, `${tool} must carry the observed identity`);
    assert.deepEqual(Object.keys(entry.identity).sort(), required.slice().sort(), `${tool} pins exactly the agreed version fields`);
    for (const field of required) {
      assert.ok(field in entry.identity, `${tool} must pin ${field}`);
    }
    // Owner decision 2026-11-08: digests are deferred, never invented.
    for (const value of jsonStrings(entry)) {
      assert.doesNotMatch(value.slice(value.indexOf('=') + 1), /^[0-9a-f]{64}$/, `${tool} must not contain a fabricated hash: ${value}`);
    }
  }

  // Every Windows-target tool is either a pinned identity or explicitly unresolved;
  // there is no third, silently permissive state.
  for (const [tool, entry] of Object.entries(lock.tools)) {
    if (!entry.targets.includes('windows-x86_64')) continue;
    const resolved = entry.identity !== undefined;
    const unresolved = entry.required_unresolved !== undefined;
    assert.ok(resolved !== unresolved, `${tool} must be exactly one of pinned or required_unresolved`);
  }
});

test('producer workflows never invoke reproducibility verifiers or A/B machinery', async () => {
  for (const name of ['build-android.yml', 'build-web-extension.yml', 'build-rust.yml', 'build-native-windows.yml', 'build-native-macos.yml', 'build-supply-chain.yml']) {
    const source = await workflow(name);
    for (const verifier of [/verify\/verify\.sh/, /verify\/verify-web\.sh/, /verify\/verify-rust\.sh/, /verify\/verify-native\.sh/, /verify\/verify-all\.sh/, /verify\/aggregate-checksums\.sh/, /docker/i]) {
      assert.doesNotMatch(source, verifier, `${name} must not invoke the reproducibility verifier ${verifier}`);
    }
    for (const machinery of [/native-runs/, /unverified-/, /extract-view\.mjs/, /prepare-run\.(?:sh|ps1)/, /verified-artifact\.mjs/, /network-deny-windows\.ps1/, /network-deny-macos\.sh/]) {
      assert.doesNotMatch(source, machinery, `${name} must not create A/B run transports or offline wrappers`);
    }
  }
});

test('Windows wrapper and provisioner declare parameters before executable statements', async () => {
  for (const name of ['network-deny-windows.ps1', 'provision-windows-tools.ps1']) {
    const source = await readFile(new URL(`verify/native/${name}`, root), 'utf8');
    assert.match(source, /^\s*(?:#.*\n\s*)*param\(/, `${name} must declare param before every executable statement`);
  }
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

test('release-build caller graph composes single-build workflows with default-read and publisher-only write', async () => {
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
    ...['build-android.yml', 'build-web-extension.yml', 'build-rust.yml', 'build-supply-chain.yml', 'build-native-windows.yml', 'build-native-macos.yml']
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

  // Labeled PR runs the complete component graph (every full-graph job).
  assert.ok(FULL_GRAPH_JOBS.every((job) => source.includes(`  ${job}:`)), 'release-build labeled PR must run every full-graph component');

  // The aggregate reusable workflow and its CI dependencies are gone.
  assert.doesNotMatch(source, /build-toolchain-manifest\.yml/, 'release-build must not call the removed aggregate workflow');
  assert.doesNotMatch(source, /\n  aggregate:\s*$/, 'release-build must not define an aggregate job');
  assert.doesNotMatch(source, /release-evidence-artifact-name/, 'release-build must not pass the removed release-evidence input');
  assert.doesNotMatch(source, /android-release-evidence/, 'release-build must not reference the removed release-evidence artifact');
  // No automatic reproducibility machinery may creep into the manual graph —
  // same forbidden set as the producers (the tag workflow keeps exactly one
  // user-facing verify.sh string in release notes, guarded by its own test).
  for (const forbidden of [
    /verify\/verify\.sh/, /verify\/verify-web\.sh/, /verify\/verify-rust\.sh/, /verify\/verify-native\.sh/,
    /verify\/verify-all\.sh/, /verify\/aggregate-checksums\.sh/, /docker/i,
    /network-deny-windows\.ps1/, /network-deny-macos\.sh/, /sandbox-exec/, /NetFirewallRule/,
    /extract-view\.mjs/, /prepare-run\.(?:sh|ps1)/, /verified-artifact\.mjs/, /native-runs/, /unverified-/,
  ]) {
    assert.doesNotMatch(source, forbidden, `release-build.yml must not contain automatic reproducibility machinery (${forbidden})`);
  }

  // Every caller passes the neutral component artifact names from Task 1.
  const neutralNames = {
    android: 'android-release',
    'web-extension': 'web-extension',
    rust: 'rust-jni-wasm',
    'supply-chain': 'supply-chain-reports',
    'native-windows': 'native-unsigned-windows',
    'native-macos': 'native-unsigned-macos',
  };
  for (const [job, artifact] of Object.entries(neutralNames)) {
    const start = source.indexOf(`  ${job}:`);
    const nextJobMatch = source.slice(start + 1).match(/\n  [A-Za-z0-9_-]+:\s*$/);
    const end = nextJobMatch ? start + 1 + nextJobMatch.index : source.length;
    const jobBlock = source.slice(start, end);
    assert.ok(jobBlock.length > 0, `release-build must define job ${job}`);
    assert.match(jobBlock, new RegExp(`artifact-name: ${artifact}`), `release-build job ${job} must pass the neutral artifact name ${artifact}`);
  }

  // Manual publication depends on the guard and every full-graph producer and
  // policy job (no aggregate safety net), and is the only writer.
  const publishStart = source.indexOf('  publish:');
  const publishBlock = source.slice(publishStart);
  assert.match(publishBlock, /needs: \[guard, android, web-extension, rust, supply-chain, native-windows, native-macos\]/, 'release-build publish job must depend on guard and every full-graph producer and policy job');
  for (const need of ['guard', 'android', 'web-extension', 'rust', 'supply-chain', 'native-windows', 'native-macos']) {
    assert.match(publishBlock, new RegExp(`needs\\.${need}\\.result == 'success'`), `release-build publish job must explicitly require ${need} success`);
  }
  assert.doesNotMatch(publishBlock, /needs\.aggregate/, 'release-build publish job must not depend on the removed aggregate');
  assert.match(publishBlock, /github\.event_name == 'workflow_dispatch'[\s\S]*?github\.ref == 'refs\/heads\/master'/, 'release-build publish job must only run on a master manual dispatch');
  assert.match(publishBlock, /permissions:\n      contents: write/, 'release-build publish job must declare contents: write');
  // No other job grants contents: write.
  const beforePublish = source.slice(0, publishStart);
  assert.doesNotMatch(beforePublish, /contents: write/, 'release-build must grant contents: write only in the publish job');

  // Publisher downloads the single android-release artifact and publishes the
  // actual APK, mapping, and its SHA256SUMS.
  assert.match(publishBlock, /actions\/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131 # v7/, 'release-build publisher must download the Android artifact via the pinned download-artifact action');
  assert.match(publishBlock, /name: android-release/, 'release-build publisher must download the single android-release artifact');
  assert.match(publishBlock, /APK="android-release\/app-release\.apk"/, 'signed configuration must select the actual signed APK');
  assert.match(publishBlock, /APK="android-release\/app-release-unsigned\.apk"/, 'unsigned configuration must select the actual unsigned APK');
  assert.match(publishBlock, /"android-release\/mapping\.txt"/, 'release-build publisher must publish the mapping');
  assert.match(publishBlock, /"android-release\/SHA256SUMS"/, 'release-build publisher must publish the artifact checksums');
  assert.match(publishBlock, /gh release view "\$VERSION" --json isPrerelease/, 'release-build publish must preserve prerelease collision/replacement behavior');
  assert.match(publishBlock, /gh release delete "\$VERSION" --cleanup-tag --yes/, 'release-build publish must replace a previous manual prerelease');
  assert.match(publishBlock, /gh release create "\$VERSION"/, 'release-build publish must create the prerelease from the single-build Android output');
  assertNoSecretRunInterpolation(publishBlock, 'release-build publish job');
});

test('tag release publishes from one build with provenance, checksums, and digest integrity intact', async () => {
  const source = await workflow('release.yml');
  // Semver tag trigger and collision-safe concurrency.
  assert.match(source, /tags:\s*\n\s*- "\[0-9\]\+\\\\\.\[0-9\]\+\\\\\.\[0-9\]\+"/, 'release must trigger on semver tags only');
  assert.match(source, /group: release-\$\{\{ github\.ref \}\}\s*\n\s*cancel-in-progress: false/, 'release concurrency must never cancel an in-flight publication');
  // Build and publish only: no reproducibility verify job and no verify-only artifact.
  assert.deepEqual(Object.keys(callerJobKeys(source)).sort(), ['build', 'publish'], 'tag release must define exactly build and publish jobs');
  assert.doesNotMatch(source, /\n  verify:\s*$/, 'tag release must not run an independent reproducibility rebuild');
  assert.doesNotMatch(source, /name: release-apk/, 'tag release must not upload a verify-only APK artifact');
  assert.doesNotMatch(source, /Rebuild from source/, 'tag release must not rebuild from source in CI');
  assert.doesNotMatch(source, /verify\/verify-web\.sh|verify\/verify-rust\.sh|verify\/verify-native\.sh|verify-all\.sh/, 'tag release must not add substitute rebuild gates');
  // verify.sh survives only as user-facing release-notes instructions, never as a run gate.
  assert.equal((source.match(/verify\/verify\.sh/g) ?? []).length, 1, 'verify.sh may appear only inside the user-facing rebuild instructions');
  const buildBlock = source.slice(source.indexOf('  build:'), source.indexOf('  publish:'));
  // Build-job protections: version/tag validation and release collision guard.
  assert.match(buildBlock, /Verify built version matches tag/, 'tag release must validate the built version against the tag');
  assert.match(buildBlock, /if \[ "\$BUILT_NAME" != "\$GITHUB_REF_NAME" \]/, 'version mismatch must fail the build');
  assert.match(buildBlock, /Guard against pre-existing release/, 'tag release must guard against a pre-existing release');
  assert.match(buildBlock, /Deleting stale draft release/, 'a stale draft for the tag must be replaced');
  assert.match(buildBlock, /A published release for tag \$GITHUB_REF_NAME already exists/, 'a published release for the tag must abort the run');
  // Signed and unsigned references both handled.
  assert.match(buildBlock, /cp app\/build\/outputs\/apk\/release\/app-release\.apk "veles-\$GITHUB_REF_NAME\.apk"/, 'signed path must publish the signed APK');
  assert.match(buildBlock, /cp app\/build\/outputs\/apk\/release\/app-release-unsigned\.apk "veles-\$GITHUB_REF_NAME\.apk"/, 'unsigned path must publish the unsigned APK');
  assert.match(buildBlock, /suffix= \(unsigned\)/, 'unsigned publications must be titled unsigned');
  // Provenance, checksums, and draft assets.
  assert.match(buildBlock, /actions\/attest-build-provenance@43d14bc2b83dec42d39ecae14e916627a18bb661 # v3/, 'tag release must attest build provenance');
  assert.match(buildBlock, /subject-path:/, 'provenance subjects must be declared explicitly');
  assert.match(buildBlock, /sha256sum "veles-\$GITHUB_REF_NAME\.apk" mapping\.txt > SHA256SUMS/, 'tag release must produce SHA256SUMS over APK and mapping');
  assert.match(buildBlock, /--draft/, 'tag release must create a draft first');
  const draftAssets = buildBlock.slice(buildBlock.indexOf('gh release create'));
  for (const asset of ['"veles-$GITHUB_REF_NAME.apk"', 'mapping.txt', 'SHA256SUMS']) {
    assert.match(draftAssets, new RegExp(escapeRegexp(asset)), `draft release must attach ${asset}`);
  }
  // User-facing rebuild instructions remain (optional diagnostics, not a gate).
  const notesBlock = source.slice(source.indexOf('- name: Append verification instructions'));
  assert.match(notesBlock, /gh attestation verify veles-\$GITHUB_REF_NAME\.apk --repo \$GITHUB_REPOSITORY/, 'user-facing attestation verification instructions must remain');
  assert.match(notesBlock, /verify\/verify\.sh veles-\$GITHUB_REF_NAME\.apk \$GITHUB_REF_NAME/, 'user-facing rebuild instructions must remain');
  // Publish depends on build only and keeps the draft-asset digest integrity check.
  const publishBlock = source.slice(source.indexOf('  publish:'));
  assert.match(publishBlock, /needs: \[build\]/, 'tag publish must depend on the build job only');
  assert.match(publishBlock, /RELEASE_ID: \$\{\{ needs\.build\.outputs\.release_id \}\}/, 'publish must consume the draft release ID');
  assert.match(publishBlock, /EXPECTED_SHA256: \$\{\{ needs\.build\.outputs\.apk_sha256 \}\}/, 'publish must consume the build APK digest');
  assert.match(publishBlock, /DRAFT_SHA256=\$\(sha256sum "\$RUNNER_TEMP\/draft-check\/veles-\$GITHUB_REF_NAME\.apk" \| awk '\{print \$1\}'\)/, 'publish must hash the downloaded draft asset');
  assert.match(publishBlock, /if \[ "\$DRAFT_SHA256" != "\$EXPECTED_SHA256" \]/, 'publish must refuse on draft asset digest mismatch');
  assert.match(publishBlock, /-F draft=false/, 'publish must flip the draft to published');
  assert.match(publishBlock, /if \[ "\$PUBLISHED_TAG" != "\$GITHUB_REF_NAME" \]/, 'publish must verify the release landed on the triggering tag');
});

test('every workflow pins external actions to the reviewed SHA plus version comment', async () => {
  const files = await workflowFiles();
  assert.ok(files.length >= 10, `expected at least 10 workflow files, found ${files.length}`);
  for (const name of files) {
    const source = await workflow(name);
    assertActionsPinned(source, name);
  }
});

test('a wrong SHA or version comment for setup-python is still rejected by the pin snapshot', () => {
  const good = 'jobs:\n  a:\n    steps:\n      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6\n';
  assert.doesNotThrow(() => assertActionsPinned(good, 'x.yml'));
  assert.throws(() => assertActionsPinned(good.replace('ece7cb06', 'aaaaaaaa'), 'x.yml'), /reviewed SHA/);
  assert.throws(() => assertActionsPinned(good.replace('# v6', '# v5'), 'x.yml'), /must carry/);
});

test('artifact actions declare only their known inputs', async () => {
  const files = await workflowFiles();
  let checked = 0;
  for (const name of files) {
    const source = await workflow(name);
    for (const step of steps(source)) {
      const artifactUses = stepUses(step).filter((value) => /^actions\/(?:upload|download)-artifact@/.test(value));
      if (artifactUses.length === 0) continue;
      const withKeys = stepWithKeys(step) ?? [];
      for (const use of artifactUses) {
        const action = use.slice(0, use.indexOf('@'));
        const allowed = ARTIFACT_ACTION_INPUTS[action];
        assert.ok(allowed, `${name} uses unreviewed artifact action ${use}`);
        for (const key of withKeys) {
          assert.ok(allowed.includes(key), `${name} declares unknown ${action} input "${key}"; known inputs: ${allowed.join(', ')}`);
        }
        checked += 1;
      }
    }
  }
  assert.ok(checked > 0, 'expected at least one artifact upload/download step to validate');
});

test('reusable component workflows referenced by the caller graph exist and remain reusable', async () => {
  const caller = await workflow('release-build.yml');
  for (const file of ['build-android.yml', 'build-web-extension.yml', 'build-rust.yml', 'build-supply-chain.yml', 'build-native-windows.yml', 'build-native-macos.yml']) {
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

// OTP-25 Task 2: a labeled pull request may build the finished DEBUG installer
// for Windows and/or macOS, but only after a secret-free `native-installer-debug`
// approval. The approval job is a workflow-local convenience gate, not a
// hostile-writer boundary (the workflow header states this); these tests pin the
// exact triggers, the current-label predicates, the exact PR head SHA, approval
// ordering, the read-only zero-secret scope, per-PR cancellation and fork
// behavior.
const PR_INSTALLER_FILE = 'build-pr-installers.yml';
const PR_INSTALLER_EVENTS = ['opened', 'reopened', 'labeled', 'unlabeled', 'synchronize', 'ready_for_review'];
const PR_INSTALLER_LABELS = {
  windows: 'windows installer build',
  macos: 'mac installer build',
};
const PR_INSTALLER_BUILDERS = {
  windows: 'build-native-windows.yml',
  macos: 'build-native-macos.yml',
};
const PR_INSTALLER_ARTIFACT_NAMES = {
  windows: 'windows-debug-installer-pr-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}-run-${{ github.run_id }}',
  macos: 'macos-debug-installer-pr-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}-run-${{ github.run_id }}',
};

function prWorkflowOnBlock(source) {
  const lines = source.split('\n');
  const start = lines.findIndex((line) => /^on:\s*$/.test(line));
  assert.notEqual(start, -1, 'workflow must declare a top-level on: block');
  const block = [];
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim() && !line.startsWith(' ')) break;
    block.push(line);
  }
  return block.join('\n');
}

function prWorkflowPermissions(source) {
  const lines = source.split('\n');
  const start = lines.findIndex((line) => /^permissions:\s*$/.test(line));
  assert.notEqual(start, -1, 'workflow must declare permissions at workflow scope');
  const values = [];
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim() && !line.startsWith(' ')) break;
    const entry = mappingEntry(line);
    if (entry) values.push(`${entry.key}: ${entry.value}`.trim());
  }
  return values;
}

function prJobBlock(source, job) {
  const match = source.match(new RegExp(`^  ${escapeRegexp(job)}:\\s*$`, 'm'));
  assert.ok(match, `workflow must define job ${job}`);
  const rest = source.slice(match.index + match[0].length);
  const next = rest.match(/^  [A-Za-z0-9_-]+:\s*$/m);
  return next ? source.slice(match.index, match.index + match[0].length + next.index) : source.slice(match.index);
}

function prJobCondition(block) {
  const match = block.match(/^    if: \|\n((?:      [^\n]*\n)+)/m);
  assert.ok(match, 'job must declare a folded if: block');
  return match[1].split('\n').map((line) => line.trim()).filter(Boolean).join(' ');
}

function prContainsPredicate(label) {
  return new RegExp(`contains\\(github\\.event\\.pull_request\\.labels\\.\\*\\.name, '${escapeRegexp(label)}'\\)`);
}

// Evaluate a job `if:` expression against a synthetic pull_request context.
// Only the contexts this workflow is allowed to use are understood; anything
// else (for example a pre-event `github.event.label.name` shortcut) fails.
function evaluatePrLabels(condition, { labels, draft = false, eventName = 'pull_request' }) {
  const expression = condition
    .replaceAll('github.event_name', JSON.stringify(eventName))
    .replaceAll('github.event.pull_request.draft', String(draft))
    .replace(/contains\(github\.event\.pull_request\.labels\.\*\.name,\s*'([^']*)'\)/g, (_, label) => `labels.includes(${JSON.stringify(label)})`);
  assert.doesNotMatch(expression, /github\./, `condition uses an unsupported pull_request context: ${condition}`);
  return new Function('labels', `"use strict"; return (${expression});`)(labels);
}

function assertPrInstallerCaller(source) {
  const name = PR_INSTALLER_FILE;
  const executable = source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');

  // Trigger: exactly the reviewed pull_request events, never pull_request_target.
  const onBlock = prWorkflowOnBlock(source);
  const triggerKeys = onBlock.split('\n').flatMap((line) => {
    const entry = mappingEntry(line);
    return entry && entry.indent === 2 ? [entry.key] : [];
  });
  assert.deepEqual(triggerKeys, ['pull_request'], `${name} must trigger only on pull_request`);
  const typesMatch = onBlock.match(/^  pull_request:\n    types: \[([^\]]+)\]$/m);
  assert.ok(typesMatch, `${name} must declare the pull_request types list`);
  assert.deepEqual(typesMatch[1].split(',').map((value) => value.trim()), PR_INSTALLER_EVENTS, `${name} must use exactly the reviewed pull_request events`);
  assert.doesNotMatch(executable, /pull_request_target/, `${name} must never use pull_request_target`);
  assert.doesNotMatch(executable, /github\.event\.label\.name/, `${name} must re-evaluate the post-event label set, not the event's own label`);

  // Scope: read-only, zero secrets, no cache/write privileges, no cross-run artifact.
  assert.deepEqual(prWorkflowPermissions(source), ['contents: read'], `${name} must declare only contents: read at workflow scope`);
  assert.equal((source.match(/^permissions:\s*$/gm) ?? []).length, 1, `${name} must declare workflow permissions exactly once`);
  for (const forbidden of [/contents:\s*write/, /\bwrite-all\b/, /^\s*id-token:/m, /^\s*pull-requests:/m, /^\s*packages:/m, /^\s*cache:/m]) {
    assert.doesNotMatch(executable, forbidden, `${name} must not grant ${forbidden}`);
  }
  assert.doesNotMatch(executable, /\$\{\{\s*secrets\./, `${name} must not reference any secret`);
  assert.doesNotMatch(executable, /^\s*secrets:/m, `${name} must not declare a secrets mapping`);
  assertNoSecretRunInterpolation(executable, name);
  assert.doesNotMatch(executable, /actions\/download-artifact@/, `${name} must not download artifacts from another run`);

  // Per-PR concurrency cancels a stale run when the head SHA or labels change.
  assert.match(source, /^concurrency:\n  group: pr-installers-\$\{\{ github\.event\.pull_request\.number \}\}\n  cancel-in-progress: true$/m, `${name} must cancel stale runs per pull request`);

  // Exactly the approval job and the two platform callers.
  assert.deepEqual(Object.keys(callerJobKeys(source)).sort(), ['approve', 'macos', 'windows'], `${name} must declare exactly the approval job and both platform callers`);

  const approve = prJobBlock(source, 'approve');
  const windows = prJobBlock(source, 'windows');
  const macos = prJobBlock(source, 'macos');

  // The approval job is a secret-free environment gate that runs no PR code.
  assert.match(approve, /^    runs-on: ubuntu-latest$/m, `${name} approval must run on ubuntu-latest`);
  assert.match(approve, /^    environment: native-installer-debug$/m, `${name} approval must enter the secret-free native-installer-debug environment`);
  assert.match(approve, /^    permissions:\n      contents: read$/m, `${name} approval must be read-only`);
  assert.doesNotMatch(approve, /^\s+uses:/m, `${name} approval must call no action`);
  assert.doesNotMatch(approve, /actions\/checkout|gradlew|bridgeInstaller|setup-java|setup-python/, `${name} approval must not check out or build anything`);
  assert.doesNotMatch(approve, /secrets/, `${name} approval must be secret-free`);
  assert.equal((executable.match(/^\s*environment:/gm) ?? []).length, 1, `${name} must assign a protected environment only to the approval job`);
  const approveIndex = source.search(/^  approve:\s*$/m);
  assert.ok(approveIndex !== -1, `${name} must declare the approve job`);

  // The conditions: pull_request, non-draft, current label presence.
  const conditions = {
    approve: prJobCondition(approve),
    windows: prJobCondition(windows),
    macos: prJobCondition(macos),
  };
  assert.match(conditions.approve, /github\.event_name == 'pull_request'/, `${name} approval must be pull_request-only`);
  assert.match(conditions.approve, /github\.event\.pull_request\.draft == false/, `${name} approval must skip drafts`);
  for (const label of Object.values(PR_INSTALLER_LABELS)) {
    assert.match(conditions.approve, prContainsPredicate(label), `${name} approval must run for the ${label} label`);
  }

  for (const [platform, builder] of Object.entries(PR_INSTALLER_BUILDERS)) {
    const block = platform === 'windows' ? windows : macos;
    const jobIndex = source.search(new RegExp(`^  ${platform}:\\s*$`, 'm'));
    assert.ok(approveIndex < jobIndex, `${name} approval must precede the ${platform} build`);
    assert.match(block, new RegExp(`^    uses: \\./\\.github/workflows/${escapeRegexp(builder)}$`, 'm'), `${platform} must call the ${builder} producer`);
    assert.match(block, /^    needs: approve$/m, `${platform} must wait for approval`);
    assert.doesNotMatch(block, /^\s*environment:/m, `${platform} must not enter a protected environment`);
    assert.doesNotMatch(block, /^\s*secrets:/m, `${platform} must pass no secret`);
    assert.doesNotMatch(block, /github\.sha\b/, `${platform} must build the pull request head commit, never the merge commit`);
    assert.match(block, /^      commit-sha: \$\{\{ github\.event\.pull_request\.head\.sha \}\}$/m, `${platform} must pass the exact PR head SHA`);
    assert.match(block, /^      build-kind: debug$/m, `${platform} must build the debug installer only`);
    assert.doesNotMatch(block, /build-kind: release/, `${platform} must never build a release channel`);
    const artifact = block.match(/^      artifact-name: (.+)$/m);
    assert.ok(artifact, `${platform} must name its debug artifact`);
    assert.equal(artifact[1].trim(), PR_INSTALLER_ARTIFACT_NAMES[platform], `${platform} must encode the PR number, head SHA and run ID in the artifact name`);
    const condition = conditions[platform];
    assert.match(condition, /github\.event_name == 'pull_request'/, `${platform} must only run on pull_request`);
    assert.match(condition, /github\.event\.pull_request\.draft == false/, `${platform} must skip drafts`);
    assert.match(condition, prContainsPredicate(PR_INSTALLER_LABELS[platform]), `${platform} must require the ${PR_INSTALLER_LABELS[platform]} label`);
    for (const [other, label] of Object.entries(PR_INSTALLER_LABELS)) {
      if (other === platform) continue;
      assert.doesNotMatch(condition, prContainsPredicate(label), `${platform} must not build for ${other}'s label`);
    }
  }

  // Behavior: no label -> nothing, each label -> its platform, both -> both.
  // `unlabeled` is modelled by the post-event label set, so removing a label
  // deselects that platform on the next run.
  const selected = (labels) => Object.keys(PR_INSTALLER_BUILDERS).filter((platform) => evaluatePrLabels(conditions[platform], { labels }));
  const matrix = [
    { labels: [], expected: [] },
    { labels: ['unrelated label'], expected: [] },
    { labels: [PR_INSTALLER_LABELS.windows], expected: ['windows'] },
    { labels: [PR_INSTALLER_LABELS.macos], expected: ['macos'] },
    { labels: [PR_INSTALLER_LABELS.windows, PR_INSTALLER_LABELS.macos], expected: ['windows', 'macos'] },
    { labels: ['unrelated label', PR_INSTALLER_LABELS.macos], expected: ['macos'] },
    { labels: [PR_INSTALLER_LABELS.macos], expected: ['macos'] }, // Windows label removed
    { labels: [PR_INSTALLER_LABELS.windows], expected: ['windows'] }, // macOS label removed
  ];
  for (const { labels, expected } of matrix) {
    assert.deepEqual(selected(labels), expected, `${name} labels ${JSON.stringify(labels)} must select ${JSON.stringify(expected)}`);
    assert.equal(
      evaluatePrLabels(conditions.approve, { labels }),
      expected.length > 0,
      `${name} approval must run exactly when a labeled platform is selected (${JSON.stringify(labels)})`,
    );
  }
  for (const job of Object.keys(conditions)) {
    assert.equal(evaluatePrLabels(conditions[job], { labels: Object.values(PR_INSTALLER_LABELS), draft: true }), false, `${name} job ${job} must skip drafts`);
    assert.equal(evaluatePrLabels(conditions[job], { labels: Object.values(PR_INSTALLER_LABELS), eventName: 'push' }), false, `${name} job ${job} must only run on pull_request`);
  }

  // Approval-boundary honesty: a PR editing its own pull_request workflow can
  // remove this workflow-local gate; resisting a malicious writer needs a
  // separately approved trusted dispatcher. Do not claim tamper resistance.
  const comments = source.split('\n').filter((line) => line.trimStart().startsWith('#')).join('\n');
  assert.match(comments, /workflow-local convenience gate/, `${name} must document the approval boundary as a workflow-local convenience gate`);
  assert.match(comments, /not a tamper-resistant/, `${name} must not claim the approval gate is tamper-resistant`);
  assert.match(comments, /edits its own `?pull_request`? workflow/, `${name} must state that a PR can edit its own pull_request workflow`);
  assert.match(comments, /can remove this job/, `${name} must state that such an edit can remove the approval job`);
  assert.match(comments, /trusted-collaborator[^.]*zero-secret/, `${name} must scope the gate to the trusted-collaborator zero-secret debug model`);
  assert.match(comments, /separately approved trusted dispatcher/, `${name} must point a hostile-writer requirement at a separately approved trusted dispatcher`);
  assert.match(comments, /fork[^.]*workflow approval/i, `${name} must document GitHub's additional fork workflow approval`);
  assert.match(comments, /NOT FOR RELEASE/, `${name} must mark the debug output as not for release`);
}

test('PR installer caller builds only labeled platforms from the exact PR head SHA after approval', async () => {
  const source = await workflow(PR_INSTALLER_FILE);
  assertPrInstallerCaller(source);
  // The build jobs carry no secret and no environment, so fork PRs get nothing
  // beyond the read-only token (GitHub may additionally hold fork runs for its
  // own workflow approval; the workflow header says so).
  for (const platform of Object.keys(PR_INSTALLER_BUILDERS)) {
    const block = prJobBlock(source, platform);
    assert.doesNotMatch(block, /secrets:/, `${platform} must remain secret-free for fork pull requests`);
    assert.doesNotMatch(block, /^\s*environment:/m, `${platform} must remain outside the approval environment for fork pull requests`);
  }
});

test('PR installer caller re-evaluates the current label set on every reviewed pull_request event', async () => {
  const source = await workflow(PR_INSTALLER_FILE);
  const onBlock = prWorkflowOnBlock(source);
  assert.deepEqual(
    onBlock.split('\n').flatMap((line) => {
      const entry = mappingEntry(line);
      return entry && entry.indent === 2 ? [entry.key] : [];
    }),
    ['pull_request'],
    'the only trigger may be pull_request',
  );
  const types = onBlock.match(/^  pull_request:\n    types: \[([^\]]+)\]$/m);
  assert.ok(types, 'pull_request types must be declared inline');
  assert.deepEqual(types[1].split(',').map((value) => value.trim()), PR_INSTALLER_EVENTS, 'every reviewed event must re-evaluate the label set');
  const executable = source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');
  for (const forbidden of [/pull_request_target/, /github\.event\.label\.name/, /^\s*push:/m, /^\s*schedule:/m, /^\s*workflow_dispatch:/m]) {
    assert.doesNotMatch(executable, forbidden, `the PR installer caller must not use ${forbidden}`);
  }
  // The post-event label set is what `unlabeled` and `synchronize` see.
  const conditions = {
    approve: prJobCondition(prJobBlock(source, 'approve')),
    windows: prJobCondition(prJobBlock(source, 'windows')),
    macos: prJobCondition(prJobBlock(source, 'macos')),
  };
  assert.deepEqual(
    Object.keys(PR_INSTALLER_BUILDERS).filter((platform) => evaluatePrLabels(conditions[platform], { labels: [PR_INSTALLER_LABELS.macos] })),
    ['macos'],
    'removing the Windows label must leave only the macOS build selected',
  );
  assert.deepEqual(
    Object.keys(PR_INSTALLER_BUILDERS).filter((platform) => evaluatePrLabels(conditions[platform], { labels: [PR_INSTALLER_LABELS.windows] })),
    ['windows'],
    'removing the macOS label must leave only the Windows build selected',
  );
  assert.equal(evaluatePrLabels(conditions.approve, { labels: [] }), false, 'removing every label must not request approval');
});

test('PR installer approval job is secret-free and gates both platform builds', async () => {
  const source = await workflow(PR_INSTALLER_FILE);
  const approve = prJobBlock(source, 'approve');
  assert.match(approve, /^    runs-on: ubuntu-latest$/m, 'the approval job must run on ubuntu-latest');
  assert.match(approve, /^    environment: native-installer-debug$/m, 'the approval job must reference native-installer-debug');
  assert.match(approve, /^    permissions:\n      contents: read$/m, 'the approval job must be read-only');
  assert.doesNotMatch(approve, /secrets|actions\/checkout|gradlew|bridgeInstaller|bridgePackage/, 'the approval job must not use secrets, checkout or build');
  const approveIndex = source.search(/^  approve:\s*$/m);
  const windowsIndex = source.search(/^  windows:\s*$/m);
  const macosIndex = source.search(/^  macos:\s*$/m);
  assert.ok(approveIndex !== -1 && windowsIndex !== -1 && macosIndex !== -1, 'all three jobs must exist');
  assert.ok(approveIndex < windowsIndex && approveIndex < macosIndex, 'the approval job must precede both platform builds');
  for (const platform of Object.keys(PR_INSTALLER_BUILDERS)) {
    const block = prJobBlock(source, platform);
    assert.match(block, /^    needs: approve$/m, `${platform} must depend on the approval job`);
    assert.doesNotMatch(block, /^\s*environment:/m, `${platform} must not repeat the environment`);
  }
});

test('PR installer caller keeps a read-only zero-secret scope and cancels stale per-PR runs', async () => {
  const source = await workflow(PR_INSTALLER_FILE);
  assert.deepEqual(prWorkflowPermissions(source), ['contents: read'], 'workflow scope must be contents: read only');
  assert.equal((source.match(/^permissions:\s*$/gm) ?? []).length, 1, 'only the workflow scope may declare permissions');
  assert.match(source, /^concurrency:\n  group: pr-installers-\$\{\{ github\.event\.pull_request\.number \}\}\n  cancel-in-progress: true$/m, 'per-PR concurrency must cancel stale runs');
  const executable = source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');
  assert.doesNotMatch(executable, /\$\{\{\s*secrets\./, 'no secret expression may appear');
  assert.doesNotMatch(executable, /^\s*secrets:/m, 'no secrets mapping may appear');
  assert.doesNotMatch(executable, /actions\/download-artifact@/, 'the PR path must not consume another run\'s artifacts');
  for (const forbidden of [/contents:\s*write/, /write-all/, /id-token:/, /pull-requests:/, /packages:/, /cache:/]) {
    assert.doesNotMatch(executable, forbidden, `the PR path must not grant ${forbidden}`);
  }
  for (const platform of Object.keys(PR_INSTALLER_BUILDERS)) {
    const block = prJobBlock(source, platform);
    assert.match(block, /^      build-kind: debug$/m, `${platform} must request a debug build`);
    assert.match(block, /^      commit-sha: \$\{\{ github\.event\.pull_request\.head\.sha \}\}$/m, `${platform} must build the PR head SHA`);
    assert.doesNotMatch(block, /github\.sha\b/, `${platform} must not fall back to the merge SHA`);
  }
});

test('PR installer caller contract rejects routing, approval, scope and boundary regressions', async () => {
  const source = await workflow(PR_INSTALLER_FILE);
  const mutations = {
    'merge SHA instead of head SHA': (text) => text.replaceAll('commit-sha: ${{ github.event.pull_request.head.sha }}', 'commit-sha: ${{ github.sha }}'),
    'missing approval dependency': (text) => text.replaceAll('    needs: approve\n', ''),
    'draft PR allowed': (text) => text.replaceAll('      github.event.pull_request.draft == false &&\n', ''),
    'wrong Windows label': (text) => text.replaceAll("'windows installer build'", "'windows build'"),
    'pre-event label shortcut': (text) => text.replace(
      "contains(github.event.pull_request.labels.*.name, 'windows installer build')",
      "github.event.label.name == 'windows installer build'",
    ),
    'pull_request_target trigger': (text) => text.replace('  pull_request:\n    types:', '  pull_request_target:\n    types:'),
    'extra push trigger': (text) => text.replace('  pull_request:\n    types:', '  push:\n  pull_request:\n    types:'),
    'edited event list': (text) => text.replace('synchronize, ready_for_review]', 'synchronize, edited]'),
    'secret mapping': (text) => text.replace(
      '    uses: ./.github/workflows/build-native-windows.yml',
      '    secrets:\n      TOKEN: ${{ secrets.TOKEN }}\n    uses: ./.github/workflows/build-native-windows.yml',
    ),
    'write permission': (text) => text.replace('permissions:\n  contents: read', 'permissions:\n  contents: write'),
    'cache permission': (text) => text.replace('permissions:\n  contents: read', 'permissions:\n  contents: read\n  cache: write'),
    'environment on a build job': (text) => text.replace(
      '    uses: ./.github/workflows/build-native-macos.yml',
      '    environment: native-installer-debug\n    uses: ./.github/workflows/build-native-macos.yml',
    ),
    'wrong approval environment': (text) => text.replace('environment: native-installer-debug', 'environment: native-installer'),
    'release build kind': (text) => text.replaceAll('      build-kind: debug', '      build-kind: release'),
    'artifact without head SHA': (text) => text.replaceAll('-${{ github.event.pull_request.head.sha }}-run-', '-run-'),
    'artifact without run ID': (text) => text.replaceAll('}-run-${{ github.run_id }}', '}'),
    'approval checks out code': (text) => text.replace(
      '    steps:\n',
      '    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7\n',
    ),
    'approval runs a build': (text) => text.replace('    steps:\n', '    steps:\n      - run: ./gradlew bridgeInstallerDebug\n'),
    'non-cancelling concurrency': (text) => text.replace('cancel-in-progress: true', 'cancel-in-progress: false'),
    'wrong concurrency group': (text) => text.replace('group: pr-installers-${{ github.event.pull_request.number }}', 'group: pr-installers'),
    'artifact download': (text) => `${text}\n  stray:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131 # v7\n`,
    'removed approval-boundary honesty': (text) => text.replaceAll('/', ' '),
    'claimed tamper resistance': (text) => text.replace('not a tamper-resistant', 'a tamper-resistant'),
  };
  for (const [label, mutate] of Object.entries(mutations)) {
    const mutated = mutate(source);
    assert.notEqual(mutated, source, `mutation "${label}" must change the workflow`);
    assert.throws(() => assertPrInstallerCaller(mutated), `the contract must reject "${label}"`);
  }
});
