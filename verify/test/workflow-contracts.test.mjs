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

test('supply-chain workflow enforces reports before uploading exactly four SBOMs', async () => {
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
    'build/verification/supply-chain/npm-licenses.txt',
    'build/verification/supply-chain/cargo-licenses.txt',
    'build/verification/supply-chain/npm-install-scripts.txt',
    'build/verification/supply-chain/cargo-build-scripts.txt',
    'build/verification/supply-chain/remote-code.txt',
  ]);
  assert.doesNotMatch(source, /secrets:/, 'Supply-chain workflow must not accept signing secrets');
});

function assertSingleBuildNativeWorkflow(source, name, platform, packageCommand, artifactDefault, uploadPaths_) {
  assert.match(source, /workflow_call:/, `${name} must be reusable`);
  assert.match(source, /commit-sha:[\s\S]*?required:\s*true/, `${name} must require the source commit`);
  assert.match(source, /artifact-name:[\s\S]*?required:\s*true/, `${name} must require an artifact name`);
  assert.match(source, new RegExp(`artifact-name:[\\s\\S]*?default:\\s*${artifactDefault}`), `${name} must default to the neutral ${artifactDefault} artifact name`);
  assert.doesNotMatch(source, /-latest/, `${name} must use pinned target runner labels`);
  assert.match(source, new RegExp(`runs-on:\\s*${platform.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`), `${name} must target ${platform}`);
  assert.equal((source.match(/runs-on:/g) ?? []).length, 1, `${name} must build and package in exactly one job`);
  assert.deepEqual(Object.keys(callerJobKeys(source)), ['build'], `${name} must define exactly one native job`);
  assert.doesNotMatch(source, /secrets:/, `${name} must not accept signing secrets`);
  assertNoSecretRunInterpolation(source, name);
  assert.equal((source.match(/actions\/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38[\s\S]*?node-version:\s*['"]26\.8\.1['"]/g) ?? []).length, 1, `${name} must pin Node 26.8.1 in its single job`);
  assert.match(source, /ref:\s*\$\{\{ inputs\.commit-sha \}\}/, `${name} must build the requested commit`);
  // Normal packaging only: no offline/firewall/sandbox wrapper, no extracted
  // verification view, no A/B run transports, no cross-run byte comparison,
  // and no metadata-preserving re-staging of the product tree.
  for (const forbidden of [
    /network-deny-windows\.ps1/, /network-deny-macos\.sh/, /sandbox-exec/, /NetFirewallRule/, /pfctl/,
    /extract-view\.mjs/, /prepare-run\.(?:sh|ps1)/, /verify\/verify-native\.sh/, /verified-artifact\.mjs/,
    /native-runs/, /unverified-/, /needs:/, /actions\/download-artifact@/,
  ]) {
    assert.doesNotMatch(source, forbidden, `${name} must not reproduce the A/B verification machinery`);
  }
  assert.equal(
    (source.match(new RegExp(packageCommand.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) ?? []).length,
    1,
    `${name} must invoke the normal packaging command exactly once`,
  );
  assert.match(source, /name:\s*\$\{\{ inputs\.artifact-name \}\}/, `${name} must publish the caller-selected artifact`);
  assertExactUploadPaths(source, name, uploadPaths_);
}

test('Windows native workflow packages its installer in one job with provisioned tools', async () => {
  const source = await workflow('build-native-windows.yml');
  assertSingleBuildNativeWorkflow(
    source,
    'build-native-windows.yml',
    'windows-2025',
    String.raw`.\gradlew.bat bridgePackage`,
    'native-windows',
    [
      'build/native-bridge/windows/veles-native-bridge-*.zip',
      'build/native-bridge/windows/veles-native-bridge-*.zip.sha256',
      'build/native-bridge/windows/SHA256SUMS',
    ],
  );
  const provisionIndex = source.indexOf('- name: Provision pinned Windows installer tools');
  const packageIndex = source.indexOf('- name: Package native bridge');
  assert.ok(provisionIndex !== -1, 'Windows workflow must provision installer tools');
  assert.ok(packageIndex !== -1 && provisionIndex < packageIndex, 'Windows workflow must provision installer tools before packaging');
  const provisionStep = source.slice(provisionIndex, packageIndex);
  assert.match(provisionStep, /\$cache = \[IO\.Path\]::GetFullPath\('native-bridge\/src-tauri\/target\/\.tauri'\)/, 'Windows provisioning must resolve the isolated Tauri cache path');
  assert.match(provisionStep, /pwsh -NoProfile -File verify\/native\/provision-windows-tools\.ps1 -TauriCachePath \$cache/, 'Windows workflow must provision the pinned WiX/NSIS toolset');
  assert.match(provisionStep, /if \(\$LASTEXITCODE -ne 0\) \{ exit \$LASTEXITCODE \}/, 'provisioning failure must propagate explicitly');
  assert.match(provisionStep, /"NSIS_PATH=\$\(Join-Path \$cache 'NSIS'\)" >> \$env:GITHUB_ENV/, 'Windows workflow must export the absolute NSIS_PATH the packaging build reads');
  const provisionInvoke = provisionStep.indexOf('pwsh -NoProfile -File');
  const exitCheck = provisionStep.indexOf('if ($LASTEXITCODE -ne 0)');
  const nsisPersist = provisionStep.indexOf('"NSIS_PATH=');
  assert.ok(provisionInvoke !== -1 && provisionInvoke < exitCheck && exitCheck < nsisPersist, 'the exit-code check must sit between provisioning and persisting NSIS_PATH');
  const packageStep = source.slice(packageIndex);
  assert.match(packageStep, /run: \.\\gradlew\.bat bridgePackage/, 'Windows packaging must invoke Gradle directly');
});

test('macOS native workflow packages its installer in one job under the pinned Xcode', async () => {
  const source = await workflow('build-native-macos.yml');
  assertSingleBuildNativeWorkflow(
    source,
    'build-native-macos.yml',
    'macos-26',
    String.raw`./gradlew bridgePackage`,
    'native-macos',
    [
      'build/native-bridge/macos/veles-native-bridge-*.tar.gz',
      'build/native-bridge/macos/veles-native-bridge-*.tar.gz.sha256',
      'build/native-bridge/macos/SHA256SUMS',
    ],
  );
  const jobBlock = source.slice(source.indexOf('  build:'));
  assert.match(jobBlock, /^    env:\n      DEVELOPER_DIR: \/Applications\/Xcode_26\.6\.app$/m, 'macOS workflow must set the pinned Xcode in job environment so it reaches every build step');
  for (const contract of ['17F113', 'macosx26.5']) {
    assert.match(jobBlock, new RegExp(contract.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), `macOS workflow must enforce the pinned ${contract}`);
  }
  assert.match(jobBlock, /run: \.\/gradlew bridgePackage/, 'macOS packaging must invoke Gradle directly');
  assert.doesNotMatch(jobBlock, /export DEVELOPER_DIR/, 'macOS Xcode selection must not depend on a per-step shell export');
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
    'native-windows': 'native-windows',
    'native-macos': 'native-macos',
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
