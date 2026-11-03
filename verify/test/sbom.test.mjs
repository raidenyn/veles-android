import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { validateSboms } from '../verify-sboms.mjs';

const root = {
  'web-extension': '@veles/web-extension@0.1.0',
  protocol: 'veles-protocol-contract-checks@1.0.0',
  rust: 'veles-crypto@0.1.0',
  'native-bridge': 'veles-native-bridge@0.1.0',
};

function sbom(identity, dependency = 'pkg:npm/example@1.0.0') {
  const separator = identity.lastIndexOf('@');
  const componentName = identity.slice(0, separator);
  const slash = componentName.lastIndexOf('/');
  return {
    bomFormat: 'CycloneDX',
    specVersion: '1.6',
    metadata: { component: {
      type: 'application', 'bom-ref': identity, name: slash >= 0 ? componentName.slice(slash + 1) : componentName,
      ...(slash >= 0 ? { group: componentName.slice(0, slash) } : {}), version: identity.slice(separator + 1),
    } },
    components: [{ type: 'library', 'bom-ref': dependency, name: 'example', version: '1.0.0' }],
    dependencies: [
      { ref: identity, dependsOn: [dependency] },
      { ref: dependency, dependsOn: [] },
    ],
  };
}

async function fixture() {
  const directory = await mkdtemp(join(tmpdir(), 'veles-sbom-'));
  await mkdir(join(directory, 'build', 'sbom'), { recursive: true });
  for (const [name, identity] of Object.entries(root)) {
    await writeFile(join(directory, 'build', 'sbom', `${name}.cdx.json`), JSON.stringify(sbom(identity)));
  }
  return directory;
}

test('accepts exactly the four requested SBOMs with resolvable non-empty graphs', async () => {
  const directory = await fixture();
  await assert.doesNotReject(validateSboms(directory, root));
});

test('rejects malformed evidence, a wrong root, unresolved references, and an empty graph', async () => {
  const directory = await fixture();
  const output = join(directory, 'build', 'sbom', 'web-extension.cdx.json');
  await writeFile(output, '{');
  await assert.rejects(validateSboms(directory, root), /invalid JSON/i);

  await writeFile(output, JSON.stringify(sbom('wrong@1.0.0')));
  await assert.rejects(validateSboms(directory, root), /root/i);

  const unresolved = sbom(root['web-extension']);
  unresolved.dependencies[0].dependsOn = ['missing'];
  await writeFile(output, JSON.stringify(unresolved));
  await assert.rejects(validateSboms(directory, root), /unresolved/i);

  const empty = sbom(root['web-extension']);
  empty.dependencies = [];
  await writeFile(output, JSON.stringify(empty));
  await assert.rejects(validateSboms(directory, root), /non-empty dependency graph/i);
});

test('rejects an unrequested fifth SBOM', async () => {
  const directory = await fixture();
  await writeFile(join(directory, 'build', 'sbom', 'unexpected.cdx.json'), '{}');
  await assert.rejects(validateSboms(directory, root), /exactly 4/i);
});

test('rejects an SBOM that omits a component declared by its lockfile', async () => {
  const directory = await fixture();
  await assert.rejects(validateSboms(directory, root, {
    'web-extension': ['pkg:npm/example@1.0.0', 'pkg:npm/missing@2.0.0'], rust: [], 'native-bridge': [],
  }), /lockfile component/i);
});

test('derives npm lock components for production validation', async () => {
  const { deriveLockComponents } = await import('../verify-sboms.mjs');
  const directory = await fixture();
  await writeFile(join(directory, 'web-extension-package-lock.json'), JSON.stringify({ packages: { 'node_modules/example': { version: '1.0.0' } } }));
  assert.deepEqual(await deriveLockComponents(join(directory, 'web-extension-package-lock.json'), 'npm'), ['pkg:npm/example@1.0.0']);
});

test('rejects Rust and bridge SBOMs that omit their derived locked components', async () => {
  const directory = await fixture();
  await assert.rejects(validateSboms(directory, root, { 'web-extension': [], rust: ['pkg:cargo/missing-rust@1.0.0'], 'native-bridge': [] }), /lockfile component/i);
  await assert.rejects(validateSboms(directory, root, { 'web-extension': [], rust: [], 'native-bridge': ['pkg:cargo/missing-bridge@1.0.0'] }), /lockfile component/i);
});

test('production SBOM roots and lockfiles include the OTP-24 host and its vendored bluest patch', async () => {
  const { PRODUCTION_SBOM_ROOTS, PRODUCTION_LOCKFILES, deriveLockComponents } = await import('../verify-sboms.mjs');
  assert.equal(PRODUCTION_SBOM_ROOTS['native-host'], 'veles-ble-host@0.1.0');
  assert.deepEqual(PRODUCTION_LOCKFILES['native-host'], { path: ['native-bridge', 'host', 'Cargo.lock'], ecosystem: 'cargo' });
  assert.deepEqual(Object.keys(PRODUCTION_SBOM_ROOTS).sort(), ['native-bridge', 'native-host', 'protocol', 'rust', 'web-extension']);
  // The patched bluest is a path package in the production lockfile and must be an SBOM obligation.
  const repository = new URL('../../', import.meta.url).pathname;
  const components = await deriveLockComponents(join(repository, ...PRODUCTION_LOCKFILES['native-host'].path), 'cargo');
  assert.ok(components.includes('pkg:cargo/bluest@0.6.9'), 'bluest@0.6.9 must be required in the native-host SBOM');
  assert.ok(components.includes('pkg:cargo/veles-ble-host@0.1.0'));
});

test('rejects a native-host SBOM that omits the vendored bluest component', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'veles-sbom-host-'));
  await mkdir(join(directory, 'build', 'sbom'), { recursive: true });
  const five = { ...root, 'native-host': 'veles-ble-host@0.1.0' };
  for (const [name, identity] of Object.entries(five)) {
    await writeFile(join(directory, 'build', 'sbom', `${name}.cdx.json`), JSON.stringify(sbom(identity)));
  }
  await validateSboms(directory, five, {});
  await assert.rejects(validateSboms(directory, five, { 'native-host': ['pkg:cargo/bluest@0.6.9'] }), /Missing lockfile component pkg:cargo\/bluest@0\.6\.9 in native-host\.cdx\.json/);
  await rm(join(directory, 'build', 'sbom', 'native-host.cdx.json'));
  await assert.rejects(validateSboms(directory, five, {}), /Expected exactly 5 SBOMs/);
});

test('normalizes the checkout path out of Cargo SBOMs and rejects SBOMs that still contain it', async () => {
  const { normalizeSbomPaths, SBOM_ROOT_PLACEHOLDER } = await import('../normalize-sbom-paths.mjs');
  const checkout = '/home/someone/work/veles';
  const raw = JSON.stringify(sbom(root.rust)).replaceAll('pkg:npm/example@1.0.0', `path+file://${checkout}/native-bridge/host/vendor/bluest#0.6.9`);
  assert.ok(raw.includes(checkout));
  const normalized = normalizeSbomPaths(raw, checkout);
  assert.ok(!normalized.includes(checkout));
  assert.ok(normalized.includes(`path+file://${SBOM_ROOT_PLACEHOLDER}/native-bridge/host/vendor/bluest#0.6.9`));
  // Same checkout path in two different checkouts normalizes to identical bytes (portable evidence).
  assert.equal(normalizeSbomPaths(raw.replaceAll(checkout, '/tmp/other/checkout'), '/tmp/other/checkout'), normalized);
  JSON.parse(normalized);

  const directory = await fixture();
  await validateSboms(directory, root, {});
  const leaking = join(directory, 'build', 'sbom', 'rust.cdx.json');
  await writeFile(leaking, JSON.stringify(sbom(root.rust)).replaceAll('pkg:npm/example@1.0.0', `path+file://${directory}/x#1`));
  await assert.rejects(validateSboms(directory, root, {}), /absolute checkout path/i);
});

test('derives the dev-only Cargo closure from resolve edge kinds and keeps shipped crates mandatory', async () => {
  const { cargoDevOnlyPackages } = await import('../verify-sboms.mjs');
  const pkg = (name) => ({ id: name, name, version: '1.0.0' });
  const metadata = {
    workspace_members: ['root'],
    packages: ['root', 'shipped', 'both', 'devonly', 'devchild', 'buildonly'].map(pkg),
    resolve: {
      nodes: [
        { id: 'root', deps: [
          { pkg: 'shipped', dep_kinds: [{ kind: null }] },
          { pkg: 'both', dep_kinds: [{ kind: 'dev' }, { kind: null, target: 'cfg(unix)' }] },
          { pkg: 'devonly', dep_kinds: [{ kind: 'dev' }] },
          { pkg: 'buildonly', dep_kinds: [{ kind: 'build' }] },
        ] },
        { id: 'shipped', deps: [] },
        { id: 'both', deps: [] },
        { id: 'devonly', deps: [{ pkg: 'devchild', dep_kinds: [{ kind: null }] }, { pkg: 'shipped', dep_kinds: [{ kind: null }] }] },
        { id: 'devchild', deps: [] },
        { id: 'buildonly', deps: [] },
      ],
    },
  };
  assert.deepEqual(cargoDevOnlyPackages(metadata).map((p) => p.name).sort(), ['devchild', 'devonly']);
});

test('the real rust/ lock exempts only the serde_json dev closure and still requires every shipped crate', async () => {
  const { PRODUCTION_LOCKFILES, deriveLockComponents, deriveShippedCargoComponents } = await import('../verify-sboms.mjs');
  const repository = join(import.meta.dirname, '..', '..');
  const all = await deriveLockComponents(join(repository, 'rust', 'Cargo.lock'), 'cargo');
  const shipped = await deriveShippedCargoComponents(join(repository, 'rust', 'Cargo.toml'), join(repository, 'rust', 'Cargo.lock'));
  assert.deepEqual(all.filter((component) => !shipped.includes(component)).sort(), [
    'pkg:cargo/itoa@1.0.18', 'pkg:cargo/serde@1.0.229', 'pkg:cargo/serde_core@1.0.229',
    'pkg:cargo/serde_derive@1.0.229', 'pkg:cargo/serde_json@1.0.154', 'pkg:cargo/zmij@1.0.23',
  ]);
  for (const name of ['native-bridge', 'native-host']) {
    const lock = join(repository, ...PRODUCTION_LOCKFILES[name].path);
    const derived = await deriveShippedCargoComponents(join(lock, '..', 'Cargo.toml'), lock);
    assert.ok(derived.length > 0 && derived.length <= (await deriveLockComponents(lock, 'cargo')).length);
  }
});

const CRATES = 'registry+https://github.com/rust-lang/crates.io-index';
const GIT = 'git+https://example.invalid/dup.git?rev=abc#abc';

test('Cargo source identity: a dev-only package never exempts a shipped one sharing its name and version', async () => {
  const { cargoDevOnlyPackages, selectShippedCargoComponents, parseCargoLock } = await import('../verify-sboms.mjs');
  // `dup 1.0.0` exists twice: the crates.io one is shipped, the git one is dev-only (and the reverse for dup2).
  const metadata = {
    workspace_members: ['root'],
    packages: [
      { id: 'root', name: 'root', version: '1.0.0', source: null },
      { id: 'dup-crates', name: 'dup', version: '1.0.0', source: CRATES },
      { id: 'dup-git', name: 'dup', version: '1.0.0', source: GIT },
      { id: 'dup2-crates', name: 'dup2', version: '1.0.0', source: CRATES },
      { id: 'dup2-git', name: 'dup2', version: '1.0.0', source: GIT },
    ],
    resolve: { nodes: [
      { id: 'root', deps: [
        { pkg: 'dup-crates', dep_kinds: [{ kind: null }] }, { pkg: 'dup-git', dep_kinds: [{ kind: 'dev' }] },
        { pkg: 'dup2-git', dep_kinds: [{ kind: null }] }, { pkg: 'dup2-crates', dep_kinds: [{ kind: 'dev' }] },
      ] },
      { id: 'dup-crates', deps: [] }, { id: 'dup-git', deps: [] }, { id: 'dup2-crates', deps: [] }, { id: 'dup2-git', deps: [] },
    ] },
  };
  const devOnly = cargoDevOnlyPackages(metadata);
  assert.deepEqual(devOnly.map((p) => `${p.name}:${p.source}`).sort(), [`dup2:${CRATES}`, `dup:${GIT}`]);
  const lock = parseCargoLock(`
[[package]]
name = "dup"
version = "1.0.0"
source = "${CRATES}"
checksum = "x"

[[package]]
name = "dup"
version = "1.0.0"
source = "${GIT}"

[[package]]
name = "dup2"
version = "1.0.0"
source = "${CRATES}"

[[package]]
name = "dup2"
version = "1.0.0"
source = "${GIT}"

[[package]]
name = "root"
version = "1.0.0"
dependencies = [
 "dup",
]
`);
  assert.equal(lock.length, 5);
  const selected = selectShippedCargoComponents(lock, devOnly);
  assert.deepEqual(selected.map((e) => (typeof e === 'string' ? e : `${e.purl}:${e.kind}`)), [
    'pkg:cargo/dup@1.0.0:crates-io', 'pkg:cargo/dup2@1.0.0:git', 'pkg:cargo/root@1.0.0',
  ]);
  assert.equal(selected[1].expected, 'git+https://example.invalid/dup.git@abc');
});

test('purlMatchesSource compares the exact source and rejects disguised or near-matching components', async () => {
  const { purlMatchesSource } = await import('../verify-sboms.mjs');
  const crates = { purl: 'pkg:cargo/dup@1.0.0', kind: 'crates-io', expected: CRATES, manifestDir: '/w' };
  const git = { purl: 'pkg:cargo/dup@1.0.0', kind: 'git', expected: 'git+https://example.invalid/dup.git@abc123', manifestDir: '/w' };
  const path = { purl: 'pkg:cargo/dup@1.0.0', kind: 'path', expected: '/w/vendor/dup', manifestDir: '/w' };
  const gitPurl = (value) => `pkg:cargo/dup@1.0.0?vcs_url=${encodeURIComponent(value)}`;
  // crates.io: only the qualifier-less purl; an https download_url / vcs_url / unknown qualifier is another source.
  assert.ok(purlMatchesSource('pkg:cargo/dup@1.0.0', crates));
  assert.ok(!purlMatchesSource('pkg:cargo/dup@1.0.0?download_url=https://github.com/x/dup/archive/abc.tar.gz', crates), 'https git download disguised as a registry');
  assert.ok(!purlMatchesSource(gitPurl('git+https://example.invalid/dup.git@abc123'), crates));
  assert.ok(!purlMatchesSource('pkg:cargo/dup@1.0.0?other=1', crates));
  assert.ok(!purlMatchesSource('pkg:cargo/dup2@1.0.0', crates));
  // git: exact URL AND commit.
  assert.ok(purlMatchesSource(gitPurl('git+https://example.invalid/dup.git@abc123'), git));
  assert.ok(!purlMatchesSource(gitPurl('git+https://example.invalid/dup.git@abc124'), git), 'different revision');
  assert.ok(!purlMatchesSource(gitPurl('git+https://example.invalid/dup.git.evil@abc123'), git), 'near-match URL');
  assert.ok(!purlMatchesSource(gitPurl('git+https://example.invalid/other/dup.git@abc123'), git), 'URL merely containing the repository');
  assert.ok(!purlMatchesSource(gitPurl('git+https://example.invalid/dup.git'), git), 'no commit');
  assert.ok(!purlMatchesSource('pkg:cargo/dup@1.0.0', git));
  assert.ok(!purlMatchesSource('pkg:cargo/dup@1.0.0?download_url=https://example.invalid/dup.git', git));
  // path: resolved directory equality (relative to the manifest dir, absolute, or the /repo-root form).
  assert.ok(purlMatchesSource('pkg:cargo/dup@1.0.0?download_url=file://vendor/dup', path));
  assert.ok(purlMatchesSource('pkg:cargo/dup@1.0.0?download_url=file:///w/vendor/dup', path));
  assert.ok(purlMatchesSource('pkg:cargo/dup@1.0.0?download_url=file:///repo-root/vendor/dup', { ...path, expected: '/r/vendor/dup', manifestDir: '/r' }, '/r'));
  assert.ok(!purlMatchesSource('pkg:cargo/dup@1.0.0?download_url=file://vendor/dup2', path), 'sibling directory');
  assert.ok(!purlMatchesSource('pkg:cargo/dup@1.0.0?download_url=file://vendor/dup/sub', path));
  assert.ok(!purlMatchesSource('pkg:cargo/dup@1.0.0?download_url=https://example.invalid/vendor/dup', path));
  assert.ok(!purlMatchesSource('pkg:cargo/dup@1.0.0', path));
});

test('colliding sources the purl cannot prove fail closed with an explicit unsupported-ambiguity error', async () => {
  const { selectShippedCargoComponents } = await import('../verify-sboms.mjs');
  const ALT = 'registry+https://alt.invalid/index';
  const ALT2 = 'registry+https://alt2.invalid/index';
  const pkg = (source) => ({ name: 'dup', version: '1.0.0', source });
  // registry vs registry (crates.io shipped, alternative dev-only): a plain purl cannot tell them apart.
  assert.throws(() => selectShippedCargoComponents([pkg(CRATES), pkg(ALT)], [pkg(ALT)]), /Unsupported Cargo source ambiguity.*another registry/);
  // two alternative registries / a shipped non-crates.io registry in a collision.
  assert.throws(() => selectShippedCargoComponents([pkg(ALT), pkg(ALT2)], [pkg(ALT2)]), /Unsupported Cargo source ambiguity.*registry other than crates\.io/);
  // git without a pinned commit, and a path package without a unique metadata directory.
  assert.throws(() => selectShippedCargoComponents([pkg('git+https://example.invalid/dup.git'), pkg(CRATES)], []), /no pinned commit/);
  assert.throws(() => selectShippedCargoComponents([pkg(null), pkg(CRATES)], [pkg(CRATES)], []), /no single path directory/);
  // A shipped git/path package colliding with a dev-only registry package is provable and accepted.
  const meta = [{ name: 'dup', version: '1.0.0', source: null, manifest_path: '/w/vendor/dup/Cargo.toml' }];
  const chosen = selectShippedCargoComponents([pkg(null), pkg(CRATES)], [pkg(CRATES)], meta, '/w');
  assert.deepEqual(chosen, [{ purl: 'pkg:cargo/dup@1.0.0', kind: 'path', expected: '/w/vendor/dup', manifestDir: '/w', source: null }]);
  // No collision: unchanged plain purl, shipped coverage not weakened.
  assert.deepEqual(selectShippedCargoComponents([pkg(CRATES)], []), ['pkg:cargo/dup@1.0.0']);
});

test('validateSboms rejects a colliding requirement satisfied only by a wrong-source component', async () => {
  const { validateSboms } = await import('../verify-sboms.mjs');
  const directory = await mkdtemp(join(tmpdir(), 'veles-sbom-'));
  await mkdir(join(directory, 'build', 'sbom'), { recursive: true });
  const write = async (purl) => {
    const document = sbom('veles-crypto@0.1.0', purl);
    document.components[0].purl = purl;
    await writeFile(join(directory, 'build', 'sbom', 'rust.cdx.json'), JSON.stringify(document));
  };
  const git = { purl: 'pkg:cargo/dup@1.0.0', kind: 'git', expected: 'git+https://example.invalid/dup.git@abc123', manifestDir: directory, source: GIT };
  const roots = { rust: 'veles-crypto@0.1.0' };
  await write('pkg:cargo/dup@1.0.0');
  await assert.rejects(validateSboms(directory, roots, { rust: [git] }), /exact git identity/);
  await write('pkg:cargo/dup@1.0.0?vcs_url=git%2Bhttps://example.invalid/dup.git%40abc999');
  await assert.rejects(validateSboms(directory, roots, { rust: [git] }), /exact git identity/);
  await write('pkg:cargo/dup@1.0.0?vcs_url=git%2Bhttps://example.invalid/dup.git%40abc123');
  await assert.doesNotReject(validateSboms(directory, roots, { rust: [git] }));
});

test('against REAL cargo-cyclonedx output: git and path purls match their exact source and nothing else', async (t) => {
  const { execFileSync } = await import('node:child_process');
  const { existsSync } = await import('node:fs');
  const { readFile } = await import('node:fs/promises');
  const { parseCargoLock, cargoDevOnlyPackages, selectShippedCargoComponents, purlMatchesSource } = await import('../verify-sboms.mjs');
  const repository = join(import.meta.dirname, '..', '..');
  const tool = join(repository, 'build', 'verify-tools', 'cargo-cyclonedx', 'bin', 'cargo-cyclonedx');
  if (!existsSync(tool)) return t.skip('cargo-cyclonedx not provisioned (./gradlew verifyCargoCyclonedx)');
  const work = await mkdtemp(join(tmpdir(), 'veles-realsbom-'));
  const sh = (cwd, command, args) => execFileSync(command, args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  try {
    const git = join(work, 'dupgit');
    await mkdir(join(git, 'src'), { recursive: true });
    await writeFile(join(git, 'Cargo.toml'), '[package]\nname="dup"\nversion="1.0.0"\nedition="2021"\n');
    await writeFile(join(git, 'src', 'lib.rs'), 'pub fn f(){}\n');
    sh(git, 'git', ['init', '-q', '-b', 'main']);
    sh(git, 'git', ['-c', 'user.email=a@b', '-c', 'user.name=n', 'add', '-A']);
    sh(git, 'git', ['-c', 'user.email=a@b', '-c', 'user.name=n', 'commit', '-qm', 'one']);
    const rev = sh(git, 'git', ['rev-parse', 'HEAD']).trim();
    const dup2 = join(work, 'app', 'vendor', 'dup2');
    await mkdir(join(dup2, 'src'), { recursive: true });
    await writeFile(join(dup2, 'Cargo.toml'), '[package]\nname="dup2"\nversion="1.0.0"\nedition="2021"\n');
    await writeFile(join(dup2, 'src', 'lib.rs'), 'pub fn f(){}\n');
    const app = join(work, 'app');
    await mkdir(join(app, 'src'), { recursive: true });
    await writeFile(join(app, 'src', 'main.rs'), 'fn main(){}\n');
    await writeFile(join(app, 'Cargo.toml'), `[package]\nname="app"\nversion="0.1.0"\nedition="2021"\n[workspace]\n[dependencies]\ndup={git="file://${git}", rev="${rev}"}\ndup2={path="vendor/dup2"}\n`);
    sh(app, 'cargo', ['generate-lockfile']);
    sh(app, tool, ['cyclonedx', '--format', 'json', '--target', 'all', '--override-filename', 'out']);
    const bom = JSON.parse(await readFile(join(app, 'out.json'), 'utf8'));
    const purls = [bom.metadata.component, ...bom.components].map((c) => c.purl).filter(Boolean);
    const metadata = JSON.parse(sh(app, 'cargo', ['metadata', '--locked', '--format-version', '1']));
    const lock = parseCargoLock(await readFile(join(app, 'Cargo.lock'), 'utf8'));
    const shipped = (name) => lock.find((p) => p.name === name);
    // Force the collision path for both the git and the path package with a registry twin of the same name/version.
    const twin = (p) => ({ name: p.name, version: p.version, source: CRATES });
    const entries = selectShippedCargoComponents([...lock, twin(shipped('dup')), twin(shipped('dup2'))],
      cargoDevOnlyPackages(metadata).concat([twin(shipped('dup')), twin(shipped('dup2'))]), metadata.packages, app);
    const entry = (name) => entries.find((e) => typeof e === 'object' && e.purl.includes(`/${name}@`));
    for (const name of ['dup', 'dup2']) {
      assert.ok(purls.some((purl) => purlMatchesSource(purl, entry(name), work)), `real ${name} purl matches its exact source; purls: ${purls}`);
      assert.ok(!purls.some((purl) => purlMatchesSource(purl, { ...entry(name), kind: 'crates-io' }, work)), `${name} is not a crates.io component`);
    }
    assert.ok(!purls.some((purl) => purlMatchesSource(purl, { ...entry('dup'), expected: entry('dup').expected.replace(rev, 'f'.repeat(40)) }, work)), 'a different revision is rejected');
    assert.ok(!purls.some((purl) => purlMatchesSource(purl, { ...entry('dup2'), expected: `${entry('dup2').expected}-other` }, work)), 'a different path is rejected');
  } finally {
    await rm(work, { recursive: true, force: true });
  }
});

test('path-vs-path same name/version: a dev-only path package never exempts the shipped one (fails closed)', async () => {
  const { cargoDevOnlyPackages, selectShippedCargoComponents } = await import('../verify-sboms.mjs');
  const metadata = {
    workspace_members: ['root'],
    packages: [
      { id: 'root', name: 'root', version: '1.0.0', source: null, manifest_path: '/w/Cargo.toml' },
      { id: 'path+file:///w/shipped/dup#dup@1.0.0', name: 'dup', version: '1.0.0', source: null, manifest_path: '/w/shipped/dup/Cargo.toml' },
      { id: 'path+file:///w/dev/dup#dup@1.0.0', name: 'dup', version: '1.0.0', source: null, manifest_path: '/w/dev/dup/Cargo.toml' },
    ],
    resolve: { nodes: [
      { id: 'root', deps: [
        { pkg: 'path+file:///w/shipped/dup#dup@1.0.0', dep_kinds: [{ kind: null }] },
        { pkg: 'path+file:///w/dev/dup#dup@1.0.0', dep_kinds: [{ kind: 'dev' }] },
      ] },
      { id: 'path+file:///w/shipped/dup#dup@1.0.0', deps: [] },
      { id: 'path+file:///w/dev/dup#dup@1.0.0', deps: [] },
    ] },
  };
  const devOnly = cargoDevOnlyPackages(metadata);
  assert.deepEqual(devOnly.map((p) => p.dir), ['/w/dev/dup'], 'dev-only identity keeps its directory and package id');
  assert.equal(devOnly[0].id, 'path+file:///w/dev/dup#dup@1.0.0');
  const lock = [
    { name: 'root', version: '1.0.0', source: null },
    { name: 'dup', version: '1.0.0', source: null },
    { name: 'dup', version: '1.0.0', source: null },
  ];
  // The lock cannot associate its two source-less `dup` entries with directories: explicit failure,
  // never an exemption that silently drops the shipped path package.
  assert.throws(() => selectShippedCargoComponents(lock, devOnly, metadata.packages, '/w'), /Unsupported Cargo source ambiguity.*dup@1\.0\.0 \(path\).*2 Cargo\.lock entries and 2 metadata packages/);
  // A single lock entry but two metadata path packages is equally unprovable.
  assert.throws(() => selectShippedCargoComponents(lock.slice(0, 2), devOnly, metadata.packages, '/w'), /1 Cargo\.lock entries and 2 metadata packages/);
  // Unique association still works: only the shipped path package, exact directory required if it collides with a registry twin.
  const single = metadata.packages.filter((p) => p.id !== 'path+file:///w/dev/dup#dup@1.0.0');
  assert.deepEqual(selectShippedCargoComponents(lock.slice(0, 2), [], single, '/w'), ['pkg:cargo/root@1.0.0', 'pkg:cargo/dup@1.0.0']);
  const CR = 'registry+https://github.com/rust-lang/crates.io-index';
  const chosen = selectShippedCargoComponents([...lock.slice(0, 2), { name: 'dup', version: '1.0.0', source: CR }], [{ name: 'dup', version: '1.0.0', source: CR }], single, '/w');
  assert.deepEqual(chosen.find((e) => typeof e === 'object'), { purl: 'pkg:cargo/dup@1.0.0', kind: 'path', expected: '/w/shipped/dup', manifestDir: '/w', source: null });
  // The only dev-only path package (unique association) is still exempt.
  assert.deepEqual(selectShippedCargoComponents(lock.slice(0, 2), [{ name: 'dup', version: '1.0.0', source: null }], [single[0], single[1]].map((p, i) => (i ? { ...p, id: 'x' } : p)), '/w'), ['pkg:cargo/root@1.0.0']);
});
