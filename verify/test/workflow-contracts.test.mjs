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
  // Verified against the upstream v6 tag (git/ref/tags/v6 -> this commit) on 2026-11-01.
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
  assert.doesNotMatch(source, /^\s*secrets:/m, 'Android workflow must declare no secrets block');
  assert.doesNotMatch(source, /KEYSTORE|VELES_KEY|\$\{\{\s*secrets\./, 'Android workflow must carry no signing material');
  assert.doesNotMatch(source, /release-evidence-artifact-name/, 'Android workflow must drop the release-evidence artifact input');
  assert.match(source, /artifact-name:[\s\S]*?default:\s*android-release/, 'Android artifact name must default to the neutral android-release');
  assert.match(source, /version-tag:\s*\n\s*required:\s*false\s*\n\s*type:\s*string/, 'Android workflow must accept an optional string version-tag');
  assertNoSecretRunInterpolation(source, 'build-android.yml');
  assert.equal((source.match(/\.\/gradlew assembleRelease/g) ?? []).length, 1, 'Android workflow must declare exactly one assembly step');
  const tagStep = source.slice(source.indexOf('- name: Create local version tag'), source.indexOf('- name: Build unsigned release APK'));
  assert.match(tagStep, /if:\s*inputs\.version-tag != ''/, 'version tag must be created only when requested');
  assert.match(tagStep, /VERSION_TAG:\s*\$\{\{ inputs\.version-tag \}\}/, 'version tag must reach the shell through env');
  assert.match(tagStep, /git tag "\$VERSION_TAG" "\$COMMIT"/, 'version tag must be created locally on the checked-out commit');
  assert.doesNotMatch(tagStep.split('run:')[1], /\$\{\{/, 'version tag script must not interpolate expressions');
  assert.doesNotMatch(source, /git push/, 'Android workflow must never push');
  const versionStep = source.slice(source.indexOf('- name: Assert derived version matches'), source.indexOf('- name: Verify APK JNI allow-list'));
  assert.match(versionStep, /if:\s*inputs\.version-tag != ''/, 'version assertion must run only when a tag is requested');
  assert.doesNotMatch(versionStep.split('run:')[1], /\$\{\{/, 'version assertion script must not interpolate expressions');
  assert.match(versionStep, /:app:androidGitVersion/, 'version assertion must read androidGitVersion');
  assert.match(versionStep, /\$\{VERSION_TAG#android-v\}/, 'version assertion must strip the android-v prefix');
  const jniStep = source.slice(source.indexOf('- name: Verify APK JNI allow-list'), source.indexOf('- name: Stage release APK, mapping, and checksums'));
  assert.match(jniStep, /APK="app\/build\/outputs\/apk\/release\/app-release-unsigned\.apk"/, 'JNI validation must select the unsigned APK');
  assert.match(jniStep, /rust\/scripts\/verify-apk-jni\.sh "\$APK"/, 'JNI allow-list must run against the unsigned APK');
  // No Docker reference rebuild and no canonical unsigned export.
  assert.doesNotMatch(source, /verify\/verify\.sh/, 'Android workflow must not run the Docker reference rebuild');
  assert.doesNotMatch(source, /build\/verification\/android\/app-release-unsigned\.apk/, 'Android workflow must not export a canonical unsigned APK');
  // Staging: the actual APK, the mapping, and SHA256SUMS travel together.
  const stageStep = source.slice(source.indexOf('- name: Stage release APK, mapping, and checksums'), source.indexOf('- uses: actions/upload-artifact'));
  assert.match(stageStep, /APK="app-release-unsigned\.apk"/, 'staging must select the unsigned APK');
  assert.doesNotMatch(stageStep, /app-release\.apk/, 'staging must not name a signed APK');
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
  assert.match(source, /version:\s*\n\s*required:\s*false\s*\n\s*type:\s*string\s*\n\s*default:\s*''/, 'Web workflow must take an optional string version input defaulting to empty');
  assert.match(source, /npm run package\n\s*working-directory: web-extension\n\s*env:\n\s*VELES_EXTENSION_VERSION:\s*\$\{\{ inputs\.version \}\}/, 'Web workflow must map the version input to VELES_EXTENSION_VERSION via env');
  assert.doesNotMatch(source, /run:[^\n]*inputs\.version/, 'Web workflow must not interpolate the version input into a shell command');
  assert.match(source, /actions\/checkout@[0-9a-f]{40}[^\n]*\n\s*with:\n\s*ref:[^\n]*\n\s*fetch-depth:\s*0/, 'Web workflow must fetch full history so git describe sees component tags');
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
  // The planned installer version: an optional caller input exported, through the step env only,
  // to the build-kind release bridgeInstallerUnsigned step. The debug build keeps its fixture version.
  assert.match(executable, /\n      version:\s*\n\s*required:\s*false\s*\n\s*type:\s*string\s*\n\s*default:\s*''\s*\n/, `${name} must accept an optional version input defaulting to ''`);
  assert.equal(executable.split('VELES_INSTALLER_VERSION').length - 1, 1, `${name} must export VELES_INSTALLER_VERSION exactly once`);
  assert.equal(executable.split('inputs.version').length - 1, 1, `${name} must read inputs.version exactly once`);
  const versionSteps = executable.split(/\n(?=      - )/).filter((step) => step.includes('VELES_INSTALLER_VERSION'));
  assert.equal(versionSteps.length, 1, `${name} must export VELES_INSTALLER_VERSION from one step`);
  assert.match(versionSteps[0], /\n        env:\n          VELES_INSTALLER_VERSION: \$\{\{ inputs\.version \}\}\n/, `${name} must pass the version through the step env`);
  assert.ok(versionSteps[0].includes("if: inputs.build-kind == 'release'") && versionSteps[0].includes('bridgeInstallerUnsigned'), `${name} must export VELES_INSTALLER_VERSION only to the release build`);
  assert.ok(!versionSteps[0].includes('bridgeInstallerDebug') && !versionSteps[0].includes("build-kind == 'debug'"), `${name} must not give the debug build VELES_INSTALLER_VERSION`);
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
    'version dropped': (text) => text.replace('        env:\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n', ''),
    'version to debug': (text) => text.replace('        env:\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n', '').replace('          VELES_WORKFLOW_RUN_ID: ${{ github.run_id }}\n', '          VELES_WORKFLOW_RUN_ID: ${{ github.run_id }}\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n'),
    'version job env': (text) => text.replace('        env:\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n', '').replace('    steps:', '    env:\n      VELES_INSTALLER_VERSION: ${{ inputs.version }}\n    steps:'),
    'version inline': (text) => text.replace('        env:\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n', '').replace('bridgeInstallerUnsigned\n', 'bridgeInstallerUnsigned -PinstallerVersion=${{ inputs.version }}\n'),
    'version required': (text) => text.replace('      version:\n        required: false', '      version:\n        required: true'),
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
    'silent upload', 'offline wrapper', 'write permission', 'version dropped', 'version to debug',
    'version job env', 'version inline', 'version required',
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
  // The installer node owns the installer crate checks (check-installer.yml); the native-host node must not repeat them.
  const source = await workflow('check-installer.yml');
  const executable = source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');
  const jobs = workflowJobBlocks(executable);
  for (const task of ['bridgeInstallerFormat', 'bridgeInstallerLint', 'bridgeInstallerTest']) {
    const runners = Object.values(jobs).filter((block) => new RegExp(`\\b${task}\\b`).test(block)).map(jobRunner);
    for (const runner of ['ubuntu-latest', 'windows-2025', 'macos-26']) {
      assert.ok(runners.includes(runner), `${task} must run on ${runner}`);
    }
  }
  assert.equal(executable.split('bridgeInstallerScriptsTest').length - 1, 1, 'the Python script suites run once');
  assert.match(jobs['scripts-tests'] ?? '', /bridgeInstallerScriptsTest/, 'the Python script suites belong to installer / scripts-tests');
  for (const task of ['bridgeInstallerUnsigned', 'bridgeInstallerPackageFixture', 'bridgeInstallerDebug']) {
    assert.ok(!executable.includes(task), `the check workflow must not build installer inputs (${task})`);
  }
  for (const [job, block] of Object.entries(jobs)) {
    assert.match(block, /working-directory:\s*native-bridge\/installer\s*\n\s*run:\s*rustup show active-toolchain/, `${job} must activate the installer toolchain`);
  }
  const host = (await workflow('check-native-host.yml')).split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');
  assert.doesNotMatch(host, /bridgeInstaller/, 'the native-host node must not repeat the installer checks');
});

// ---------------------------------------------------------------------------------------------------------
// Component-tree CI (ci.yml -> check-<node>.yml), the PR title check and the post-merge commit guard.

const COMPONENT_MAP = JSON.parse(await readFile(new URL('.github/components.json', root), 'utf8'));
const CI_NODES = Object.keys(COMPONENT_MAP.nodes);
const CHECK_WORKFLOWS = ['android', 'web-extension', 'rust', 'protocol', 'native-host', 'installer', 'bridge-scaffold', 'tooling'];

// Every command ci.yml and build-native-host.yml ran before the component split. Each must run in exactly
// one check-*.yml (per owner: the host build-contract suite in native-host, the installer suites through
// bridgeInstallerScriptsTest in installer).
const CI_COMMAND_INVENTORY = [
  'spotlessCheck', 'detekt', 'android.util.Log', 'lintDebug', 'testDebugUnitTest', 'connectedDebugAndroidTest',
  'protocolCheck',
  'npm run format:check', 'npm run lint', 'npm run typecheck', 'npm test', 'npm run test:bundle', 'npm run test:browser',
  'rustFormat', 'rustLint', 'rustTest', 'verify-apk-jni.sh', 'assert-clean.sh',
  'bridgeHostToolchainCheck', 'bridgeHostFormat', 'bridgeHostLint', 'bridgeHostTest', 'bridgeHostBuild', 'test_build_contract.py',
  'bridgeInstallerFormat', 'bridgeInstallerLint', 'bridgeInstallerTest', 'bridgeInstallerScriptsTest',
  'bridgeFormat', 'bridgeLint', 'bridgeTypecheck', 'bridgeNpmTest', 'bridgeRustFormat', 'bridgeRustLint', 'bridgeRustTest',
  'workflow-contracts.test.mjs',
];

function executableYaml(source) {
  return source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');
}

function workflowJobBlocks(source) {
  const lines = source.split('\n');
  const start = lines.findIndex((line) => /^jobs:\s*$/.test(line));
  assert.notEqual(start, -1, 'workflow must declare jobs');
  const blocks = {};
  let current = null;
  for (const line of lines.slice(start + 1)) {
    if (line.trim() && !line.startsWith(' ')) break;
    const match = line.match(/^  ([A-Za-z0-9_-]+):\s*$/);
    if (match) {
      current = match[1];
      blocks[current] = '';
      continue;
    }
    if (current) blocks[current] += `${line}\n`;
  }
  return blocks;
}

function jobRunner(block) {
  return block.match(/^    runs-on:\s*(\S+)\s*$/m)?.[1] ?? null;
}

function jobNeeds(block) {
  const match = block.match(/^    needs:\s*\[([^\]]*)\]\s*$/m);
  return match ? match[1].split(',').map((need) => need.trim()).filter(Boolean) : [];
}

function commandPattern(command) {
  return new RegExp(`(?<![\\w-])${escapeRegexp(command)}(?![\\w-])`);
}

// Shell commands of a workflow: `run:` bodies plus the emulator runner's `script:` input.
function shellCommands(source) {
  const scripts = source.split('\n').map(mappingEntry).filter((entry) => entry?.key === 'script').map((entry) => entry.value);
  return [...runCommands(source), ...scripts];
}

function inventoryOwners(sources, command) {
  return Object.entries(sources).filter(([, source]) => shellCommands(executableYaml(source)).some((run) => commandPattern(command).test(run))).map(([name]) => name);
}

function assertComponentCi(source, nodes) {
  // Master pushes are never cancelled: a cancelled push run would skip commit-guard for its commits, and the
  // next push only validates its own before..after range.
  const concurrency = executableYaml(source).match(/^concurrency:\n((?:  [^\n]*\n)+)/m)?.[1] ?? '';
  assert.match(concurrency, /^  cancel-in-progress: \$\{\{ github\.event_name == 'pull_request' \}\}$/m, 'ci.yml may cancel superseded PR runs only, never master pushes');
  const jobs = workflowJobBlocks(executableYaml(source));
  const changes = jobs.changes;
  assert.ok(changes, 'ci.yml must define the changes job');
  assert.match(changes, /fetch-depth:\s*0/, 'changes needs the full history');
  assert.match(changes, /python3 -I scripts\/components\.py check/, 'changes must check the component map');
  assert.match(changes, /python3 -I scripts\/components\.py changed --base "\$BASE" --github-output "\$GITHUB_OUTPUT"/, 'changes must select nodes');
  assert.match(changes, /git merge-base "origin\/\$BASE_REF" HEAD/, 'PR base must be the merge base');
  assert.match(changes, /BASE="\$BEFORE"/, 'push base must be github.event.before');
  for (const node of nodes) {
    assert.ok(changes.includes(`      ${node}: \${{ steps.select.outputs.${node} }}`), `changes must expose the ${node} output`);
    const block = jobs[node];
    assert.ok(block, `ci.yml must call the ${node} node`);
    const file = node === 'supply-chain' ? 'build-supply-chain.yml' : `check-${node}.yml`;
    assert.match(block, new RegExp(`^    uses: \\./\\.github/workflows/${escapeRegexp(file)}\\s*$`, 'm'), `${node} must call ${file}`);
    assert.match(block, new RegExp(`^    name: ${escapeRegexp(node)}\\s*$`, 'm'), `${node} must report as ${node} / <check>`);
    const condition = block.match(/^    if:\s*(.+)$/m)?.[1] ?? '';
    assert.ok(condition.includes(`needs.changes.outputs.${node} == 'true'`), `${node} must run only when selected`);
    assert.ok(condition.includes('!cancelled()'), `${node} must still run when changes failed after selecting every node`);
    assert.deepEqual(jobNeeds(block), ['changes'], `${node} must depend on changes only`);
  }
  const callers = Object.entries(jobs).filter(([, block]) => /^    uses: \.\/\.github\/workflows\//m.test(block)).map(([job]) => job);
  assert.deepEqual(callers.sort(), [...nodes].sort(), 'ci.yml must call exactly one workflow per node');
  const guard = jobs['commit-guard'];
  assert.ok(guard, 'ci.yml must define commit-guard');
  assert.match(guard, /^    if: github\.event_name == 'push'\s*$/m, 'commit-guard runs on pushes only');
  assert.match(guard, /git rev-list "\$BEFORE\.\.\$AFTER"/, 'commit-guard must validate every pushed commit');
  assert.match(guard, /python3 -I scripts\/commitlint\.py --message "\$message"/, 'commit-guard must use the shared validator');
  const ok = jobs['ci-ok'];
  assert.ok(ok, 'ci.yml must define ci-ok');
  assert.match(ok, /^    if: always\(\)\s*$/m, 'ci-ok must always run');
  const needs = jobNeeds(ok);
  for (const job of ['changes', ...nodes, 'commit-guard']) assert.ok(needs.includes(job), `ci-ok must need ${job}`);
  assert.match(ok, /CHANGES_RESULT: \$\{\{ needs\.changes\.result \}\}/, 'ci-ok must read the changes result');
  assert.match(ok, /if \[ "\$CHANGES_RESULT" != success \]/, 'ci-ok must fail when changes did not succeed');
  assert.match(ok, /RESULTS: \$\{\{ join\(needs\.\*\.result, ' '\) \}\}/, 'ci-ok must read every needed result');
  assert.match(ok, /failure\|cancelled\)/, 'ci-ok must fail on failure or cancelled');
}

test('ci.yml calls one change-gated check workflow per component node and gates on ci-ok', async () => {
  const source = await workflow('ci.yml');
  assertComponentCi(source, CI_NODES);
  assert.match(source, /^permissions:\n  contents: read$/m, 'ci.yml must default to read-only contents');
  for (const node of CHECK_WORKFLOWS) {
    const check = await workflow(`check-${node}.yml`);
    assert.match(check, /^on:\n  workflow_call:\n/m, `check-${node}.yml must be reusable`);
    assert.match(check, /^permissions:\n  contents: read\n/m, `check-${node}.yml must be read-only`);
    assert.doesNotMatch(check, /secrets\./, `check-${node}.yml must not use secrets`);
  }
  const mutations = {
    'missing node': (text) => text.replace(/\n  tooling:\n[\s\S]*?(?=\n  [a-z-]+:\n)/, '\n'),
    'unconditional node': (text) => text.replace("needs.changes.outputs.android == 'true'", "true"),
    'ci-ok without always': (text) => text.replace('    if: always()\n', ''),
    'ci-ok ignores changes': (text) => text.replace('if [ "$CHANGES_RESULT" != success ]', 'if false'),
    'ci-ok misses a node': (text) => text.replace(/(    needs: \[changes, [^\]]*), tooling/, '$1'),
    'no map check': (text) => text.replace('python3 -I scripts/components.py check', 'true'),
    'push-only guard dropped': (text) => text.replace("    if: github.event_name == 'push'\n", ''),
    'master runs cancelled': (text) => text.replace("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", 'cancel-in-progress: true'),
    'concurrency dropped': (text) => text.replace(/^concurrency:\n(?:  [^\n]*\n)+/m, ''),
  };
  for (const [label, mutate] of Object.entries(mutations)) {
    const mutated = mutate(source);
    assert.notEqual(mutated, source, `mutation "${label}" must change ci.yml`);
    assert.throws(() => assertComponentCi(mutated, CI_NODES), `the contract must reject "${label}"`);
  }
});

test('every pre-split CI command runs in exactly one check workflow', async () => {
  const sources = {};
  for (const node of CHECK_WORKFLOWS) sources[`check-${node}.yml`] = await workflow(`check-${node}.yml`);
  for (const command of CI_COMMAND_INVENTORY) {
    assert.deepEqual(inventoryOwners(sources, command).length, 1, `${command} must run in exactly one check workflow, found ${inventoryOwners(sources, command).join(', ') || 'none'}`);
  }
  assert.deepEqual(inventoryOwners(sources, 'test_build_contract.py'), ['check-native-host.yml'], 'the host build-contract suite belongs to native-host');
  assert.deepEqual(inventoryOwners(sources, 'bridgeInstallerScriptsTest'), ['check-installer.yml'], 'the installer script suites belong to installer');
  assert.deepEqual(inventoryOwners(sources, 'workflow-contracts.test.mjs'), ['check-tooling.yml'], 'the workflow contracts belong to tooling');
  assert.match(sources['check-tooling.yml'], /\.\/gradlew scriptsTest/, 'tooling must run the scripts/ tests');
  for (const [name, source] of Object.entries(sources)) {
    for (const upload of uploadPathsOrEmpty(source)) {
      for (const path of upload) {
        assert.doesNotMatch(path, /\.(?:zip|apk)$|build\/web-extension\/?$|outputs\/apk/, `${name} must not upload a release zip or APK (${path})`);
      }
    }
  }
  assert.deepEqual(inventoryOwners({ 'a.yml': 'jobs:\n  a:\n    steps:\n      - run: ./gradlew bridgeHostLint\n', 'b.yml': 'jobs:\n  b:\n    steps:\n      - run: ./gradlew bridgeHostLint bridgeHostTest\n' }, 'bridgeHostLint'), ['a.yml', 'b.yml'], 'a duplicated command must be detected');
  assert.deepEqual(inventoryOwners({ 'a.yml': 'jobs:\n  a:\n    steps:\n      # - run: ./gradlew detekt\n' }, 'detekt'), [], 'a commented-out command must not count');
});

function uploadPathsOrEmpty(source) {
  return steps(source).some((step) => stepUses(step).some(isUploadArtifact)) ? uploadPaths(source, 'check workflow') : [];
}

const CLEAN_CONTRACT_ORDER = [
  ':app:assembleDebug', 'verify-apk-jni.sh', 'npm run build', './gradlew clean', 'assert-clean.sh', ':app:assembleDebug', 'verify-apk-jni.sh',
];

function assertCleanContractOrder(source) {
  const block = workflowJobBlocks(executableYaml(source))['jni-integration'];
  assert.ok(block, 'rust must define jni-integration');
  const runs = steps(block).map((step) => runCommands(step.join('\n')).join('\n'));
  let previous = -1;
  for (const marker of CLEAN_CONTRACT_ORDER) {
    const index = runs.findIndex((run, position) => position > previous && run.includes(marker));
    assert.ok(index > previous, `jni-integration must run ${marker} after step ${previous}`);
    previous = index;
  }
  assert.match(runs.slice(previous + 1).join('\n'), /\.\/gradlew rustInstall/, 'jni-integration must refill the Rust tool cache last');
}

test('rust / jni-integration keeps the clean contract order in one job', async () => {
  const source = await workflow('check-rust.yml');
  assertCleanContractOrder(source);
  const swapped = source.replace('      - run: ./gradlew clean\n      - run: rust/scripts/assert-clean.sh\n', '      - run: rust/scripts/assert-clean.sh\n      - run: ./gradlew clean\n');
  assert.notEqual(swapped, source);
  assert.throws(() => assertCleanContractOrder(swapped), /assert-clean\.sh|clean/);
  const dropped = source.replace('      - run: npm ci && npm run build\n', '');
  assert.notEqual(dropped, source);
  assert.throws(() => assertCleanContractOrder(dropped));
});

const PR_TITLE_EVENTS = '[opened, edited, reopened, synchronize]';

function assertNoPrTextInRun(source, name) {
  for (const command of runCommands(source)) {
    assert.doesNotMatch(command, /github\.event\.pull_request\.(?:title|body)|github\.head_ref/, `${name} run commands must not interpolate the PR title, body or head ref`);
  }
}

function assertPrTitleWorkflow(source) {
  assert.match(prWorkflowOnBlock(source), new RegExp(`^  pull_request:\\n    types: ${escapeRegexp(PR_TITLE_EVENTS)}\\n?$`), 'pr-title must trigger on exactly opened, edited, reopened, synchronize');
  assert.deepEqual(prWorkflowPermissions(source), ['contents: read'], 'pr-title must be read-only');
  const jobs = workflowJobBlocks(source);
  assert.deepEqual(Object.keys(jobs), ['pr-title'], 'pr-title must define exactly one job');
  const job = jobs['pr-title'];
  assert.match(job, /^    name: pr-title\s*$/m, 'the required check is pr-title');
  assert.doesNotMatch(job, /^    if:/m, 'pr-title must not skip any event');
  assert.match(job, /ref: \$\{\{ github\.event\.pull_request\.base\.sha \}\}/, 'pr-title must check out the base commit');
  assertNoPrTextInRun(source, 'pr-title.yml');
  // Title AND body (the future squash commit) reach the validator together, as a file (no argv size limit).
  const validate = steps(job).map((step) => step.join('\n')).filter((step) => step.includes('"$VALIDATOR"'));
  assert.equal(validate.length, 1, 'pr-title must run the validator in exactly one step');
  const [step] = validate;
  assert.match(step, /PR_TITLE: \$\{\{ github\.event\.pull_request\.title \}\}/, 'the title must come from env');
  assert.match(step, /PR_BODY: \$\{\{ github\.event\.pull_request\.body \}\}/, 'the body must come from env');
  const run = runCommands(step).join('\n');
  assert.match(run, /message_file="\$RUNNER_TEMP\/pr-message\.txt"/, 'the message must be written under RUNNER_TEMP');
  assert.match(run, /printf '%s\\n' "\$PR_TITLE" > "\$message_file"/, 'the title must start the validated message');
  assert.match(run, /printf '\\n%s\\n' "\$PR_BODY" >> "\$message_file"/, 'the body must follow the title after a blank line');
  assert.match(run, /python3 -I "\$VALIDATOR" --file "\$message_file"/, 'pr-title must validate the written title and body');
  assert.match(job, /if \[ -f scripts\/commitlint\.py \]/, 'the base validator is preferred');
  assert.match(job, /git show "\$HEAD_SHA:scripts\/commitlint\.py" > "\$RUNNER_TEMP\/commitlint\.py"/, 'bootstrap fallback reads the head validator');
  assert.match(job, /::warning::bootstrap: validator taken from the PR head/, 'bootstrap fallback must warn');
  assert.doesNotMatch(job, /--allow-git-generated/, 'pr-title must not accept git-generated subjects');
}

test('pr-title validates the PR title and body from env with the base validator', async () => {
  const source = await workflow('pr-title.yml');
  assertPrTitleWorkflow(source);
  assertActionsPinned(source, 'pr-title.yml');
  const mutations = {
    'title interpolated into run': (text) => text.replace('printf \'%s\\n\' "$PR_TITLE"', 'printf \'%s\\n\' "${{ github.event.pull_request.title }}"'),
    'body not validated': (text) => text.replace(/^ *if \[ -n "\$PR_BODY" \]; then\n *printf[^\n]*\n *fi\n/m, ''),
    'body env dropped': (text) => text.replace('          PR_BODY: ${{ github.event.pull_request.body }}\n', ''),
    'title only on argv': (text) => text.replace('--file "$message_file"', '--message "$PR_TITLE"'),
    'head checkout': (text) => text.replace('ref: ${{ github.event.pull_request.base.sha }}', 'ref: ${{ github.event.pull_request.head.sha }}'),
    'missing edited': (text) => text.replace(PR_TITLE_EVENTS, '[opened, reopened, synchronize]'),
    'silent bootstrap': (text) => text.replace('::warning::bootstrap: validator taken from the PR head', 'bootstrap'),
    'write permission': (text) => text.replace('permissions:\n  contents: read', 'permissions:\n  contents: write'),
    'git-generated accepted': (text) => text.replace('--file "$message_file"', '--allow-git-generated --file "$message_file"'),
  };
  for (const [label, mutate] of Object.entries(mutations)) {
    const mutated = mutate(source);
    assert.notEqual(mutated, source, `mutation "${label}" must change pr-title.yml`);
    assert.throws(() => assertPrTitleWorkflow(mutated), `the contract must reject "${label}"`);
  }
});

test('no workflow interpolates PR text into run and only pr-title triggers on edited', async () => {
  for (const name of await workflowFiles()) {
    const source = await workflow(name);
    assertNoPrTextInRun(source, name);
    if (name !== 'pr-title.yml') assert.doesNotMatch(prWorkflowOnBlock(source), /\bedited\b/, `${name} must not trigger on edited`);
    assert.doesNotMatch(executableYaml(source), /build-native-host\.yml/, `${name} must not reference the retired build-native-host.yml`);
  }
  assert.throws(() => assertNoPrTextInRun('steps:\n  - run: echo "${{ github.event.pull_request.body }}"', 'fixture'), /must not interpolate/);
  assert.throws(() => assertNoPrTextInRun("steps:\n  - { run: 'echo ${{ github.event.pull_request.title }}' }", 'fixture'), /must not interpolate/);
});

test('the remote-code scan scopes exist and cover the CI, tooling and hook sources', async () => {
  const { REMOTE_CODE_SCOPES, missingScopes } = await import(new URL('verify/check-remote-code.mjs', root));
  assert.deepEqual(await missingScopes(repository, REMOTE_CODE_SCOPES), [], 'every remote-code scope must exist');
  assert.deepEqual(await missingScopes(repository, ['web-extension/manifest.json']), ['web-extension/manifest.json'], 'a missing scope must be reported');
  for (const scope of ['.github/workflows/check-native-host.yml', '.github/workflows/check-installer.yml', '.github/workflows/ci.yml', '.github/workflows/pr-title.yml', 'scripts', '.githooks', 'web-extension/vite.config.ts']) {
    assert.ok(REMOTE_CODE_SCOPES.includes(scope), `the remote-code scan must cover ${scope}`);
  }
  for (const scope of ['.github/workflows/build-native-host.yml', 'web-extension/manifest.json']) {
    assert.ok(!REMOTE_CODE_SCOPES.includes(scope), `the remote-code scan must not list ${scope}`);
  }
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

function jobStepRun(block, name) {
  const lines = block.split('\n');
  const start = lines.findIndex((line) => line.trim() === `- name: ${name}`);
  assert.notEqual(start, -1, `job must declare the step "${name}"`);
  let runIndex = -1;
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^[ \t]*- /.test(lines[index])) break;
    if (mappingEntry(lines[index])?.key === 'run') {
      runIndex = index;
      break;
    }
  }
  assert.notEqual(runIndex, -1, `step "${name}" must declare a run command`);
  const entry = mappingEntry(lines[runIndex]);
  let command = entry.value;
  for (let index = runIndex + 1; index < lines.length; index += 1) {
    if (lines[index].trim() && lines[index].match(/^\s*/)[0].length <= entry.indent) break;
    command += `\n${lines[index]}`;
  }
  return command.replace(/^[|>][-+]?\s*\n?/, '');
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
    'sign-windows', 'sign-macos', 'gate',
  ];
  assert.deepEqual(Object.keys(jobs).sort(), allJobs.slice().sort(), `${name} must declare exactly the release graph`);
  for (const job of allJobs.filter((entry) => entry !== 'guard')) {
    const block = releaseJobBlock(source, job);
    const match = block.match(/^    if: (.+)$/m);
    assert.ok(match, `${name} job ${job} must declare its guard`);
    for (const part of ["github.event_name == 'workflow_dispatch'", "github.ref == 'refs/heads/master'", "github.actor == 'raidenyn'", "github.triggering_actor == 'raidenyn'"]) {
      assert.ok(match[1].includes(part), `${name} job ${job} must re-check ${part}`);
    }
    const needs = block.match(/^    needs: (.+)$/m);
    assert.ok(needs, `${name} job ${job} must declare its dependencies`);
    assert.match(needs[1], /\bguard\b/, `${name} job ${job} must depend directly on the guard`);
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
  const signJobs = ['sign-windows', 'sign-macos'];
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
  // Secret expressions exist only inside the actual signing steps of the protected jobs. Every other
  // step (download, unpack, assemble, cleanup, upload, gate) holds no credential.
  for (const job of allJobs) {
    const block = releaseJobBlock(source, job);
    for (const step of steps(block)) {
      const text = step.join('\n');
      if (!/\$\{\{\s*secrets\./.test(text)) continue;
      assert.ok(signJobs.includes(job), `${name} job ${job} must not map a secret`);
      assert.match(text, /- name: Sign/, `${name} job ${job} may map secrets only in a signing step`);
      assert.match(text, /sign-(?:windows\.ps1|macos\.sh)/, `${name} job ${job} may map secrets only in a signing step`);
    }
  }
  assert.doesNotMatch(executable, /VELES_APPROVED_DESCRIPTOR_SHA256/, `${name} must not keep the obsolete approval variable`);

  // Debug packaging is reachable only through build-pr-installers.yml calling the debug builders.
  for (const forbidden of [
    /build-pr-installers\.yml/, /build-kind:\s*debug/, /bridgeInstallerDebug/,
    /out\/debug\//, /debug-installer-pr-/, /debug trust/i,
  ]) {
    assert.doesNotMatch(executable, forbidden, `${name} must not invoke or select debug packaging (${forbidden})`);
  }

  // Each platform is independent: a Windows-only run neither needs nor names a macOS job/artifact.
  for (const platform of Object.keys(RELEASE_BUILDERS)) {
    const other = platform === 'windows' ? 'macos' : 'windows';
    for (const job of [`build-${platform}`, `verify-${platform}`, `sign-${platform}`]) {
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
    windows: ['build-windows', 'verify-windows', 'sign-windows'],
    macos: ['build-macos', 'verify-macos', 'sign-macos'],
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
  for (const result of ['RESULT_GUARD', 'RESULT_BUILD_WINDOWS', 'RESULT_BUILD_MACOS', 'RESULT_VERIFY_WINDOWS', 'RESULT_VERIFY_MACOS', 'RESULT_SIGN_WINDOWS', 'RESULT_SIGN_MACOS']) {
    assert.ok(gate.includes(result), `gate must report ${result}`);
  }
  // Every result the gate reads has a direct needs entry, so a missing job is never
  // silently reported as an empty result.
  const gateNeeds = gate.match(/^    needs: \[([^\]]+)\]$/m)[1].split(',').map((value) => value.trim());
  for (const job of ['guard', 'build-windows', 'build-macos', 'verify-windows', 'verify-macos', 'sign-windows', 'sign-macos']) {
    assert.ok(gateNeeds.includes(job), `the gate must depend directly on ${job}`);
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
    'secret in a verify step': (text) => text.replace(
      '      - name: Verify the same-run A/B provenance, report, payload and descriptor (no credential)\n',
      '      - name: Verify the same-run A/B provenance, report, payload and descriptor (no credential)\n        env:\n          LEAK: ${{ secrets.LEAK }}\n',
    ),
    'gate result not in direct needs': (text) => text.replace(
      '    needs: [guard, build-windows, build-macos, verify-windows, verify-macos, sign-windows, sign-macos]\n',
      '    needs: [guard, build-windows, build-macos, verify-windows, verify-macos, sign-windows]\n',
    ),
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

test('release gate reports per-selected-platform status before artifact downloads', async () => {
  const source = await workflow('release-native-installers.yml');
  const gate = releaseJobBlock(source, 'gate');

  // Direct needs: a missing job result is never silently empty, and adding a direct
  // need cannot make the gate succeed for a failed selected platform.
  const needs = gate.match(/^    needs: \[([^\]]+)\]$/m);
  assert.ok(needs, 'the gate must declare its dependencies inline');
  for (const job of ['guard', 'build-windows', 'build-macos', 'verify-windows', 'verify-macos', 'sign-windows', 'sign-macos']) {
    assert.ok(needs[1].split(',').map((value) => value.trim()).includes(job), `the gate must depend directly on ${job}`);
  }

  // Status reporting is a distinct step placed before any artifact download, so a
  // missing required artifact cannot hide the per-platform verdict.
  const statusIndex = gate.indexOf('- name: Report the selected platform build/sign status');
  const firstDownload = gate.indexOf('actions/download-artifact@');
  assert.notEqual(statusIndex, -1, 'the gate must declare a status step');
  assert.ok(firstDownload !== -1 && statusIndex < firstDownload, 'the gate must report status before any artifact download');
  assert.match(gate, /acceptance/i, 'the gate must separate build/sign status from acceptance');

  const script = jobStepRun(gate, 'Report the selected platform build/sign status');
  const results = (overrides = {}) => ({
    RESULT_GUARD: 'success',
    RESULT_BUILD_WINDOWS: 'skipped',
    RESULT_VERIFY_WINDOWS: 'skipped',
    RESULT_SIGN_WINDOWS: 'skipped',
    RESULT_BUILD_MACOS: 'skipped',
    RESULT_VERIFY_MACOS: 'skipped',
    RESULT_SIGN_MACOS: 'skipped',
    ...overrides,
  });
  const run = (platform, overrides) => spawnSync('bash', ['-c', script], {
    encoding: 'utf8',
    env: { ...process.env, PLATFORM: platform, ...results(overrides) },
  });
  const windowsSuccess = {
    RESULT_BUILD_WINDOWS: 'success', RESULT_VERIFY_WINDOWS: 'success', RESULT_SIGN_WINDOWS: 'success',
  };
  const macosSuccess = {
    RESULT_BUILD_MACOS: 'success', RESULT_VERIFY_MACOS: 'success', RESULT_SIGN_MACOS: 'success',
  };

  // Unselected platforms are irrelevant, even when their jobs failed or were skipped.
  const windowsOnly = run('windows', { ...windowsSuccess, RESULT_BUILD_MACOS: 'failure', RESULT_VERIFY_MACOS: 'failure', RESULT_SIGN_MACOS: 'failure' });
  assert.equal(windowsOnly.status, 0, windowsOnly.stderr);
  assert.match(windowsOnly.stdout, /windows: PASS/);
  assert.doesNotMatch(windowsOnly.stdout, /macos:/);
  const macosOnly = run('macos', { ...macosSuccess, RESULT_BUILD_WINDOWS: 'failure', RESULT_VERIFY_WINDOWS: 'failure', RESULT_SIGN_WINDOWS: 'failure' });
  assert.equal(macosOnly.status, 0, macosOnly.stderr);
  assert.match(macosOnly.stdout, /macos: PASS/);
  assert.doesNotMatch(macosOnly.stdout, /windows:/);

  // A selected platform failure blocks the whole gate; each selected platform is reported.
  const bothBlocked = run('both', { ...windowsSuccess, RESULT_SIGN_MACOS: 'failure' });
  assert.notEqual(bothBlocked.status, 0);
  assert.match(bothBlocked.stdout, /windows: PASS/);
  assert.match(bothBlocked.stdout, /macos: BLOCKED/);
  assert.match(bothBlocked.stderr, /selected platform\(s\) failed: macos/);
  const verifyBlocked = run('windows', { ...windowsSuccess, RESULT_VERIFY_WINDOWS: 'failure' });
  assert.notEqual(verifyBlocked.status, 0);
  assert.match(verifyBlocked.stdout, /windows: BLOCKED/);
  assert.doesNotMatch(verifyBlocked.stdout, /macos:/);

  // The guard still fails closed before any platform verdict.
  const guardBlocked = run('windows', { ...windowsSuccess, RESULT_GUARD: 'failure' });
  assert.notEqual(guardBlocked.status, 0);
  assert.match(guardBlocked.stderr, /guard did not pass/);
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

// release-build.yml (design D5/D8): label-driven PR builds, changed-only master builds and the master
// release preview. No dispatch, no publication, no secrets, no native installers.
const RELEASE_BUILD_FILE = 'release-build.yml';
const RELEASE_BUILD_PR_EVENTS = ['opened', 'reopened', 'synchronize', 'labeled', 'unlabeled'];
const RELEASE_BUILD_JOBS = ['changes', 'android', 'web-extension', 'rust', 'preview'];
const RELEASE_BUILD_PRODUCERS = {
  android: { file: 'build-android.yml', artifact: 'android-release', label: 'build-android' },
  'web-extension': { file: 'build-web-extension.yml', artifact: 'web-extension', label: 'build-web-extension' },
  rust: { file: 'build-rust.yml', artifact: 'rust-jni-wasm', label: null },
};
const LEGACY_BUILD_LABELS = ["'release-build'", 'windows-installer-build', 'mac-installer-build'];

function labelOrChangeCondition(node, label) {
  return `always() && ((github.event_name == 'pull_request' && contains(github.event.pull_request.labels.*.name, '${label}')) || (github.event_name == 'push' && needs.changes.outputs.${node} == 'true'))`;
}

function normalizedCondition(block) {
  const folded = block.match(/^    if: \|\n((?:      [^\n]*\n)+)/m);
  const inline = block.match(/^    if: (?!\|)(.+)$/m);
  assert.ok(folded || inline, 'job must declare an if: condition');
  const text = folded ? folded[1].split('\n').map((line) => line.trim()).filter(Boolean).join(' ') : inline[1].trim();
  return text.replace(/\(\s+/g, '(').replace(/\s+\)/g, ')');
}

// Evaluate a release-build job condition for a synthetic event; any unknown context fails.
function evaluateBuildCondition(condition, { eventName, labels = [], changes = {} }) {
  const expression = condition
    .replaceAll('always()', 'true')
    .replaceAll('github.event_name', JSON.stringify(eventName))
    .replace(/contains\(github\.event\.pull_request\.labels\.\*\.name,\s*'([^']*)'\)/g, (_, label) => `labels.includes(${JSON.stringify(label)})`)
    .replace(/needs\.changes\.outputs\.([A-Za-z0-9_-]+)/g, (_, node) => JSON.stringify(eventName === 'push' ? String(changes[node] ?? '') : ''));
  assert.doesNotMatch(expression, /github\.|needs\./, `condition uses an unsupported context: ${condition}`);
  return new Function('labels', `"use strict"; return (${expression});`)(labels);
}

function assertReleaseBuildCaller(source) {
  const name = RELEASE_BUILD_FILE;
  const executable = source.split('\n').filter((line) => !line.trimStart().startsWith('#')).join('\n');

  // Triggers: master push and the reviewed pull_request events only.
  const onBlock = prWorkflowOnBlock(source);
  const triggers = onBlock.split('\n').flatMap((line) => {
    const entry = mappingEntry(line);
    return entry && entry.indent === 2 ? [entry.key] : [];
  });
  assert.deepEqual(triggers, ['push', 'pull_request'], `${name} must trigger only on push and pull_request`);
  assert.match(onBlock, /^  push:\n    branches: \[master\]$/m, `${name} must build on master pushes only`);
  const types = onBlock.match(/^  pull_request:\n    types: \[([^\]]+)\]$/m);
  assert.ok(types, `${name} must declare the pull_request types inline`);
  assert.deepEqual(types[1].split(',').map((value) => value.trim()), RELEASE_BUILD_PR_EVENTS, `${name} must re-evaluate labels on exactly the reviewed events`);
  for (const forbidden of [/workflow_dispatch/, /pull_request_target/, /github\.event\.label\.name/, /^\s*schedule:/m]) {
    assert.doesNotMatch(executable, forbidden, `${name} must not use ${forbidden}`);
  }

  // Scope: read-only, zero secrets, no publication.
  assert.deepEqual(prWorkflowPermissions(source), ['contents: read'], `${name} must declare only contents: read at workflow scope`);
  for (const forbidden of [/contents:\s*write/, /\bwrite-all\b/, /^\s*id-token:/m, /^\s*pull-requests:/m, /^\s*packages:/m, /^\s*environment:/m]) {
    assert.doesNotMatch(executable, forbidden, `${name} must not grant ${forbidden}`);
  }
  assert.doesNotMatch(executable, /^\s*secrets:/m, `${name} must pass no secrets`);
  assert.doesNotMatch(executable, /\$\{\{\s*secrets\./, `${name} must reference no secret`);
  assertNoSecretRunInterpolation(executable, name);
  assert.doesNotMatch(executable, /gh release (?:create|delete|upload|edit)|git push|git tag/, `${name} must not publish, tag or push`);
  assert.doesNotMatch(executable, /HAS_KEYSTORE|KEYSTORE|VELES_KEYSTORE/, `${name} must not reference signing credentials`);

  // Superseded PR runs are cancelled; master pushes never are (changed-only builds use before..after).
  assert.match(source, /^concurrency:\n  group: release-build-\$\{\{ github\.ref \}\}\n  cancel-in-progress: \$\{\{ github\.event_name == 'pull_request' \}\}$/m, `${name} must cancel only superseded pull_request runs`);

  assert.deepEqual(Object.keys(callerJobKeys(source)), RELEASE_BUILD_JOBS, `${name} must declare exactly ${RELEASE_BUILD_JOBS.join(', ')}`);

  // changes: push-only, before..after selection with the component map.
  const changes = prJobBlock(source, 'changes');
  assert.equal(normalizedCondition(changes), "github.event_name == 'push'", 'changes must be push-only');
  assert.match(changes, /fetch-depth: 0/, 'changes must fetch full history for the before..after diff');
  assert.match(changes, /^          BEFORE: \$\{\{ github\.event\.before \}\}$/m, 'changes must take the push base from github.event.before via env');
  assert.match(changes, /python3 -I scripts\/components\.py changed --base "\$BEFORE" --github-output "\$GITHUB_OUTPUT"/, 'changes must select nodes with components.py changed');
  for (const node of Object.keys(RELEASE_BUILD_PRODUCERS)) {
    assert.match(changes, new RegExp(`^      ${escapeRegexp(node)}: \\$\\{\\{ steps\\.select\\.outputs\\.${escapeRegexp(node)} \\}\\}$`, 'm'), `changes must export the ${node} output`);
  }

  // Producers: android and web-extension by label on PRs or by change on master; rust by change on master only.
  for (const [job, { file, artifact, label }] of Object.entries(RELEASE_BUILD_PRODUCERS)) {
    const block = prJobBlock(source, job);
    const keys = callerJobKeys(source)[job];
    for (const key of keys) {
      assert.ok(['name', 'uses', 'with', 'needs', 'if', 'permissions'].includes(key), `${job} must contain only caller fields; found "${key}"`);
    }
    assert.match(block, new RegExp(`^    uses: \\./\\.github/workflows/${escapeRegexp(file)}$`, 'm'), `${job} must call ${file}`);
    assert.match(block, /^    needs: \[changes\]$/m, `${job} must depend on changes`);
    assert.match(block, new RegExp(`^      artifact-name: ${escapeRegexp(artifact)}$`, 'm'), `${job} must pass the neutral artifact name ${artifact}`);
    assert.doesNotMatch(block, /^      (?:version|version-tag):/m, `${job} must not inject a release version`);
    const condition = normalizedCondition(block);
    if (label) {
      assert.equal(condition, labelOrChangeCondition(job, label), `${job} must build by the ${label} label on PRs or by change on master`);
      assert.match(block, /^      commit-sha: \$\{\{ github\.event_name == 'pull_request' && github\.event\.pull_request\.head\.sha \|\| github\.sha \}\}$/m, `${job} must build the PR head or the pushed commit`);
    } else {
      assert.equal(condition, `always() && github.event_name == 'push' && needs.changes.outputs.${job} == 'true'`, `${job} must build only on master pushes that change it`);
      assert.match(block, /^      commit-sha: \$\{\{ github\.sha \}\}$/m, `${job} must build the pushed commit`);
    }
    const run = (event) => evaluateBuildCondition(condition, event);
    assert.equal(run({ eventName: 'pull_request', labels: [] }), false, `${job} must not build an unlabeled PR`);
    assert.equal(run({ eventName: 'pull_request', labels: ['release-build', 'windows-installer-build', 'mac-installer-build'] }), false, `${job} must ignore the retired labels`);
    assert.equal(run({ eventName: 'pull_request', labels: ['build-android', 'build-web-extension'] }), Boolean(label), `${job} PR selection must follow only its own label`);
    for (const [other, spec] of Object.entries(RELEASE_BUILD_PRODUCERS)) {
      if (other === job || !spec.label) continue;
      assert.equal(run({ eventName: 'pull_request', labels: [spec.label] }), false, `${job} must not build for ${other}'s label`);
    }
    assert.equal(run({ eventName: 'push', changes: { [job]: 'true' } }), true, `${job} must build on a master push that changes it`);
    assert.equal(run({ eventName: 'push', changes: { [job]: 'false' } }), false, `${job} must not build on a master push that leaves it unchanged`);
    assert.equal(run({ eventName: 'push', labels: label ? [label] : [], changes: {} }), false, `${job} must not build on a master push without a change output`);
  }

  // preview: push-only release plan into the job summary, read-only token, no secrets.
  const preview = prJobBlock(source, 'preview');
  assert.equal(normalizedCondition(preview), "github.event_name == 'push'", 'preview must be push-only');
  assert.match(preview, /^    runs-on: ubuntu-latest$/m, 'preview must run on ubuntu-latest');
  assert.match(preview, /^    permissions:\n      contents: read$/m, 'preview must be read-only');
  assert.match(preview, /^      GH_TOKEN: \$\{\{ github\.token \}\}$/m, 'preview must use the read-only workflow token');
  assert.match(preview, /fetch-depth: 0/, 'preview must fetch full history and tags (the planner refuses shallow clones)');
  assert.match(preview, /\.\/gradlew installGitCliff/, 'preview must install the pinned git-cliff');
  assert.match(preview, /gh release list --limit 100 --json tagName,isDraft,isPrerelease,publishedAt > releases\.json/, 'preview must list releases with the planner fields');
  assert.match(preview, /TAG="\$\(python3 -I scripts\/release_plan\.py latest --releases-json releases\.json\)"/, 'preview must resolve the latest combined release with the planner');
  assert.match(preview, /gh release download "\$TAG" --pattern release-manifest\.json/, 'preview must download the previous release manifest');
  assert.match(preview, /echo '\{\}' > manifest\.json/, 'preview must fall back to an empty manifest');
  assert.match(preview, /python3 -I scripts\/release_plan\.py plan --releases-json releases\.json --manifest-json manifest\.json --out plan\.json --summary "\$GITHUB_STEP_SUMMARY"/, 'preview must write the plan to the job summary');
  assert.doesNotMatch(preview, /--require-release/, 'preview must not fail when nothing would be released');

  // Native installers are built only by build-pr-installers.yml (debug) and the publisher; never here.
  for (const forbidden of [
    /build-native-windows\.yml/, /build-native-macos\.yml/, /native-unsigned-(?:windows|macos)/,
    /build-kind:/, /bridgeInstaller(?:Unsigned|Debug|PackageFixture)/, /release-native-installers\.yml/,
    /build-supply-chain\.yml/, /build-toolchain-manifest\.yml/,
  ]) {
    assert.doesNotMatch(executable, forbidden, `${name} must not build installers or other components (${forbidden})`);
  }
  // No reproducibility machinery.
  for (const forbidden of [
    /verify\/verify(?:-web|-rust|-native|-all)?\.sh/, /verify\/aggregate-checksums\.sh/, /docker/i,
    /network-deny-(?:windows\.ps1|macos\.sh)/, /sandbox-exec/, /extract-view\.mjs/, /native-runs/, /unverified-/,
  ]) {
    assert.doesNotMatch(executable, forbidden, `${name} must not contain reproducibility machinery (${forbidden})`);
  }
}

test('release-build builds by label on PRs and by change on master, and previews the release on master', async () => {
  assertReleaseBuildCaller(await workflow(RELEASE_BUILD_FILE));
});

test('release-build contract rejects dispatch, publication, secret, routing and preview regressions', async () => {
  const source = await workflow(RELEASE_BUILD_FILE);
  const androidPr = "contains(github.event.pull_request.labels.*.name, 'build-android')";
  const mutations = {
    'manual dispatch': (text) => text.replace('  pull_request:\n    types:', '  workflow_dispatch:\n  pull_request:\n    types:'),
    'missing unlabeled event': (text) => text.replace('labeled, unlabeled]', 'labeled]'),
    'legacy label': (text) => text.replace(androidPr, "contains(github.event.pull_request.labels.*.name, 'release-build')"),
    'pre-event label shortcut': (text) => text.replace(androidPr, "github.event.label.name == 'build-android'"),
    'android built on every push': (text) => text.replace("needs.changes.outputs.android == 'true'", "true"),
    'rust built on PR': (text) => text.replace(
      "always() && github.event_name == 'push' && needs.changes.outputs.rust == 'true'",
      "always() && needs.changes.outputs.rust == 'true' || github.event_name == 'pull_request'",
    ),
    'write permission': (text) => text.replace('permissions:\n  contents: read', 'permissions:\n  contents: write'),
    'secrets passed': (text) => text.replace(
      '    uses: ./.github/workflows/build-android.yml\n',
      '    uses: ./.github/workflows/build-android.yml\n    secrets: inherit\n',
    ),
    'publication': (text) => text.replace('--summary "$GITHUB_STEP_SUMMARY"', '--summary "$GITHUB_STEP_SUMMARY"\n          gh release create "$TAG"'),
    'injected version': (text) => text.replace('      artifact-name: web-extension\n', '      artifact-name: web-extension\n      version: 1.0.0\n'),
    'native producer': (text) => `${text}\n  windows:\n    needs: [changes]\n    uses: ./.github/workflows/build-native-windows.yml\n    with:\n      commit-sha: \${{ github.sha }}\n      build-kind: debug\n`,
    'supply-chain producer': (text) => `${text}\n  supply-chain:\n    needs: [changes]\n    uses: ./.github/workflows/build-supply-chain.yml\n`,
    'preview on PR': (text) => text.replace(/(  preview:\n(?:.*\n)*?    if: )github\.event_name == 'push'/, "$1always()"),
    'preview without summary': (text) => text.replace(' --summary "$GITHUB_STEP_SUMMARY"', ''),
    'preview requires a release': (text) => text.replace('--summary "$GITHUB_STEP_SUMMARY"', '--summary "$GITHUB_STEP_SUMMARY" --require-release'),
    'shallow preview': (text) => text.replace(/(  preview:\n(?:.*\n)*?          )fetch-depth: 0/, '$1fetch-depth: 1'),
    'cancelled master pushes': (text) => text.replace("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", 'cancel-in-progress: true'),
  };
  for (const [label, mutate] of Object.entries(mutations)) {
    const mutated = mutate(source);
    assert.notEqual(mutated, source, `mutation "${label}" must change the workflow`);
    assert.throws(() => assertReleaseBuildCaller(mutated), `the release-build contract must reject "${label}"`);
  }
});

test('retired build labels appear in no workflow', async () => {
  for (const file of await workflowFiles()) {
    const source = await workflow(file);
    for (const label of LEGACY_BUILD_LABELS) {
      assert.ok(!source.includes(label), `${file} must not mention the retired label ${label}`);
    }
  }
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
  assert.doesNotMatch(source, /verify\/verify\.sh/, 'tag release must not reference the removed reproducibility verifier');
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
  // User-facing attestation instructions remain.
  const notesBlock = source.slice(source.indexOf('- name: Append verification instructions'));
  assert.match(notesBlock, /gh attestation verify veles-\$GITHUB_REF_NAME\.apk --repo \$GITHUB_REPOSITORY/, 'user-facing attestation verification instructions must remain');
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
  const callers = {
    'release-build.yml': ['build-android.yml', 'build-web-extension.yml', 'build-rust.yml'],
    'ci.yml': ['build-supply-chain.yml'],
  };
  for (const [callerFile, files] of Object.entries(callers)) {
    const caller = await workflow(callerFile);
    for (const file of files) {
      assert.match(caller, new RegExp(`uses: \\./\\.github/workflows/${file.replace(/\./g, '\\.')}`), `${callerFile} must reuse ${file}`);
      const reusable = await workflow(file);
      assert.match(reusable, /workflow_call:/, `${file} must remain reusable (workflow_call)`);
    }
  }
  // Every repository-local workflow any caller references stays reusable.
  for (const callerFile of await workflowFiles()) {
    for (const ref of usesReferences(await workflow(callerFile))) {
      const local = ref.match(/^\.\/\.github\/workflows\/([^\s@]+\.yml)$/);
      if (!local) continue;
      assert.match(await workflow(local[1]), /^  workflow_call:/m, `${local[1]} (called by ${callerFile}) must remain reusable (workflow_call)`);
    }
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
  windows: 'build-windows-installer',
  macos: 'build-macos-installer',
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
    'wrong Windows label': (text) => text.replaceAll("'build-windows-installer'", "'windows build'"),
    'retired Windows label': (text) => text.replaceAll("'build-windows-installer'", "'windows-installer-build'"),
    'retired macOS label': (text) => text.replaceAll("'build-macos-installer'", "'mac-installer-build'"),
    'pre-event label shortcut': (text) => text.replace(
      "contains(github.event.pull_request.labels.*.name, 'build-windows-installer')",
      "github.event.label.name == 'build-windows-installer'",
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
    'removed approval-boundary honesty': (text) => text
      // Remove only the honesty comment lines: replacing path separators would corrupt the
      // reusable-workflow paths and fail the contract for an unrelated reason.
      .replace('# - The approve job is a workflow-local convenience gate.\n', '')
      .replace('# - It is not a tamper-resistant security boundary.\n', '')
      .replace('# - A pull request that edits its own `pull_request` workflow can remove this job.\n', ''),
    'claimed tamper resistance': (text) => text.replace('not a tamper-resistant', 'a tamper-resistant'),
  };
  for (const [label, mutate] of Object.entries(mutations)) {
    const mutated = mutate(source);
    assert.notEqual(mutated, source, `mutation "${label}" must change the workflow`);
    assert.throws(() => assertPrInstallerCaller(mutated), `the contract must reject "${label}"`);
  }
  // The honesty mutation only removes comment lines, so it must fail on the specific
  // approval-boundary diagnostic; failing elsewhere would mean an unrelated check fired.
  const dishonest = mutations['removed approval-boundary honesty'](source);
  assert.throws(
    () => assertPrInstallerCaller(dishonest),
    (error) => /approval boundary as a workflow-local convenience gate/.test(error.message),
    'removing the approval-boundary honesty must fail the workflow-local convenience gate diagnostic',
  );
});
