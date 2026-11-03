import { readdir, readFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { dirname, join, resolve as resolvePath } from 'node:path';
import { promisify } from 'node:util';

const run = promisify(execFile);

function failure(message) {
  const error = new Error(message);
  error.exitCode = 1;
  return error;
}

function collectRefs(components, refs) {
  for (const component of components ?? []) {
    if (component['bom-ref']) refs.add(component['bom-ref']);
    if (component.purl) refs.add(component.purl);
    collectRefs(component.components, refs);
  }
}

export async function deriveLockComponents(lockfile, ecosystem) {
  const text = await readFile(lockfile, 'utf8');
  if (ecosystem === 'npm') {
    const lock = JSON.parse(text);
    return Object.entries(lock.packages ?? {}).filter(([path, entry]) => path.startsWith('node_modules/') && entry.version)
      .map(([path, entry]) => `pkg:npm/${path.slice(path.lastIndexOf('node_modules/') + 'node_modules/'.length).replace('@', '%40')}@${entry.version}`);
  }
  return parseCargoLock(text).map(cargoPurl);
}

// ---- Cargo source identity -------------------------------------------------------------------
// Cargo permits several packages with the same name and version from different sources (crates.io,
// a git repository, a path). The unit of identity throughout is `(name, version, source)`; the
// source-less `pkg:cargo/<name>@<version>` purl is only RENDERED for the final SBOM comparison.
// `source` is Cargo's own string (`registry+...`, `git+...`), or null for a path package.
function collectPurls(components, purls) {
  for (const component of components ?? []) {
    if (component.purl) purls.push(component.purl);
    collectPurls(component.components, purls);
  }
}

export function parseCargoLock(text) {
  const packages = [];
  for (const block of text.split(/^\[\[package\]\]\s*$/m).slice(1)) {
    const head = block.split(/^\[/m)[0];
    const field = (key) => head.match(new RegExp(`^${key} = "([^"]*)"`, 'm'))?.[1] ?? null;
    const name = field('name');
    const version = field('version');
    if (name && version) packages.push({ name, version, source: field('source') });
  }
  return packages;
}

export const cargoPurl = ({ name, version }) => `pkg:cargo/${name}@${version}`;
const cargoKey = ({ name, version, source }) => `${name}\u0000${version}\u0000${source ?? ''}`;

// What a cargo-cyclonedx purl can PROVE about a package's source (observed tool output, see
// verify/test/sbom.test.mjs which regenerates it with the real tool):
//   crates.io crate  -> `pkg:cargo/<n>@<v>`                  (no qualifier at all)
//   git dependency   -> `...?vcs_url=git%2B<url>%40<commit>` (the lock's URL and precise commit)
//   path package     -> `...?download_url=file://<dir>`      (relative to the manifest dir when inside it)
// A source is compared EXACTLY (full URL + commit, resolved directory) or not at all: substrings,
// arbitrary https download_urls and unknown qualifiers never match. Sources the purl cannot
// distinguish (other registries, or a registry that shares name@version with another registry,
// or a path package without one unique metadata directory) are an explicit unsupported ambiguity.
export const CRATES_IO_SOURCES = new Set([
  'registry+https://github.com/rust-lang/crates.io-index',
  'sparse+https://index.crates.io/',
]);

function unsupported(message) {
  return failure(`Unsupported Cargo source ambiguity: ${message}; cargo-cyclonedx purls cannot prove this source, so lock coverage fails closed`);
}

function splitPurl(purl) {
  const hash = purl.indexOf('#');
  const withoutSubpath = hash >= 0 ? purl.slice(0, hash) : purl;
  const question = withoutSubpath.indexOf('?');
  const base = question >= 0 ? withoutSubpath.slice(0, question) : withoutSubpath;
  const params = new Map();
  if (question >= 0) {
    for (const pair of withoutSubpath.slice(question + 1).split('&')) {
      const equals = pair.indexOf('=');
      let key = equals >= 0 ? pair.slice(0, equals) : pair;
      let value = equals >= 0 ? pair.slice(equals + 1) : '';
      try { value = decodeURIComponent(value); key = decodeURIComponent(key); } catch { return { base, params: null }; }
      params.set(key, value);
    }
  }
  return { base, params };
}

function resolveFileUrl(value, manifestDir, root) {
  if (!value.startsWith('file://')) return null;
  let path = value.slice('file://'.length);
  if (path === '/repo-root' || path.startsWith('/repo-root/')) path = join(root, path.slice('/repo-root'.length));
  return resolvePath(manifestDir, path);
}

// `entry` = { purl, kind: 'crates-io' | 'git' | 'path', expected, manifestDir } from
// `selectShippedCargoComponents`; `purl` is one SBOM component purl.
export function purlMatchesSource(purl, entry, root = '/') {
  const { base, params } = splitPurl(purl);
  if (base !== entry.purl || params === null) return false;
  if (entry.kind === 'crates-io') return params.size === 0;
  if (params.size !== 1) return false;
  if (entry.kind === 'git') return params.get('vcs_url') === entry.expected;
  if (entry.kind === 'path') {
    return params.has('download_url') && resolveFileUrl(params.get('download_url'), entry.manifestDir, root) === resolvePath(entry.expected);
  }
  return false;
}

// cargo-cyclonedx documents the shipped (normal + build) dependency graph and omits crates that are
// reachable only through `[dev-dependencies]`, yet Cargo.lock lists them. `cargo metadata` resolves
// every edge with its kind, so the dev-only closure is derived exactly (never hand-listed): a locked
// package is exempt from SBOM lock coverage only if NO workspace member reaches it through a
// normal or build edge. Identity is the full package ID, so a shipped package that merely shares
// a name/version with a dev-only one from another source is never exempted.
export function cargoDevOnlyPackages(metadata) {
  const nodes = new Map((metadata.resolve?.nodes ?? []).map((node) => [node.id, node]));
  const shipped = new Set(metadata.workspace_members ?? []);
  const pending = [...shipped];
  while (pending.length > 0) {
    const node = nodes.get(pending.pop());
    for (const dep of node?.deps ?? []) {
      const kinds = dep.dep_kinds?.length ? dep.dep_kinds : [{ kind: null }];
      if (kinds.every((kind) => kind.kind === 'dev')) continue;
      if (!shipped.has(dep.pkg)) {
        shipped.add(dep.pkg);
        pending.push(dep.pkg);
      }
    }
  }
  const byId = new Map((metadata.packages ?? []).map((pkg) => [pkg.id, pkg]));
  return [...nodes.keys()].filter((id) => !shipped.has(id)).map((id) => byId.get(id)).filter(Boolean)
    .map((pkg) => ({
      name: pkg.name, version: pkg.version, source: pkg.source ?? null,
      // Full metadata identity retained for path packages, whose lock entries carry no directory.
      id: pkg.id, ...(pkg.source || !pkg.manifest_path ? {} : { dir: dirname(pkg.manifest_path) }),
    }));
}

// Required lock components for lock coverage: every locked package except the dev-only ones, matched
// by full `(name, version, source)`. Unambiguous packages stay plain purl strings. A shipped package
// whose name/version is shared with another locked package of a different source becomes
// `{ purl, kind, expected, manifestDir }` so the SBOM must contain a component of EXACTLY that
// source; where the purl cannot express that source, derivation fails with an explicit error.
export function selectShippedCargoComponents(lockPackages, devOnly, metadataPackages = [], manifestDir = '.') {
  // Cargo.lock lists a path package without its directory, so two path packages sharing
  // name/version are indistinguishable there. Before ANY exemption each such lock entry must map to
  // exactly one metadata package (one lock entry, at most one metadata path package); otherwise a
  // dev-only path package could exempt a shipped one with a different directory.
  const pathLocks = new Map();
  for (const pkg of lockPackages) if (pkg.source === null) pathLocks.set(cargoPurl(pkg), (pathLocks.get(cargoPurl(pkg)) ?? 0) + 1);
  const pathMeta = new Map();
  for (const m of metadataPackages) if ((m.source ?? null) === null) pathMeta.set(cargoPurl(m), (pathMeta.get(cargoPurl(m)) ?? new Set()).add(m.id ?? m.manifest_path));
  for (const [purl, count] of pathLocks) {
    const meta = pathMeta.get(purl)?.size ?? 0;
    if (count > 1 || meta > 1) {
      throw unsupported(`${purl} (path) has ${count} Cargo.lock entries and ${meta} metadata packages; Cargo.lock does not record path directories, so a dev-only path package cannot be told apart from a shipped one`);
    }
  }
  const exempt = new Set(devOnly.map(cargoKey));
  const sharing = new Map();
  for (const pkg of lockPackages) sharing.set(cargoPurl(pkg), [...(sharing.get(cargoPurl(pkg)) ?? []), pkg]);
  return lockPackages.filter((pkg) => !exempt.has(cargoKey(pkg))).map((pkg) => {
    const group = sharing.get(cargoPurl(pkg));
    if (group.length === 1) return cargoPurl(pkg);
    const purl = cargoPurl(pkg);
    const label = `${purl} (${pkg.source ?? 'path'})`;
    if (pkg.source === null) {
      const dirs = new Set(metadataPackages.filter((m) => m.name === pkg.name && m.version === pkg.version && (m.source ?? null) === null)
        .map((m) => dirname(m.manifest_path)));
      if (dirs.size !== 1) throw unsupported(`${label} has no single path directory in cargo metadata`);
      return { purl, kind: 'path', expected: [...dirs][0], manifestDir, source: null };
    }
    if (pkg.source.startsWith('git+')) {
      const hash = pkg.source.indexOf('#');
      const url = pkg.source.slice(0, Math.min(...[pkg.source.indexOf('?'), hash].filter((i) => i >= 0), pkg.source.length));
      if (hash < 0 || hash === pkg.source.length - 1) throw unsupported(`${label} has no pinned commit`);
      return { purl, kind: 'git', expected: `${url}@${pkg.source.slice(hash + 1)}`, manifestDir, source: pkg.source };
    }
    if (CRATES_IO_SOURCES.has(pkg.source)) {
      const otherRegistries = group.filter((other) => other !== pkg && other.source !== null && !other.source.startsWith('git+'));
      if (otherRegistries.length > 0) throw unsupported(`${label} shares name/version with another registry package (${otherRegistries.map((o) => o.source).join(', ')})`);
      return { purl, kind: 'crates-io', expected: pkg.source, manifestDir, source: pkg.source };
    }
    throw unsupported(`${label} comes from a registry other than crates.io`);
  });
}

export async function deriveShippedCargoComponents(manifest, lockfile) {
  const { stdout } = await run('cargo', ['metadata', '--locked', '--format-version', '1', '--manifest-path', manifest], { maxBuffer: 256 * 1024 * 1024 });
  const metadata = JSON.parse(stdout);
  return selectShippedCargoComponents(parseCargoLock(await readFile(lockfile, 'utf8')), cargoDevOnlyPackages(metadata), metadata.packages ?? [], dirname(manifest));
}

export async function validateSboms(root, expectedRoots, lockComponents = {}) {
  const directory = join(root, 'build', 'sbom');
  let files;
  try {
    files = (await readdir(directory)).filter((file) => file.endsWith('.cdx.json')).sort();
  } catch {
    throw failure(`Missing SBOM directory: ${directory}`);
  }
  const expected = Object.keys(expectedRoots).map((name) => `${name}.cdx.json`).sort();
  if (files.length !== expected.length || files.join('\n') !== expected.join('\n')) {
    throw failure(`Expected exactly ${expected.length} SBOMs (${expected.join(', ')}), found ${files.join(', ') || 'none'}`);
  }
  for (const file of files) {
    let bom;
    const text = await readFile(join(directory, file), 'utf8').catch(() => '');
    try {
      bom = JSON.parse(text);
    } catch {
      throw failure(`Invalid JSON in SBOM ${file}`);
    }
    const name = file.replace('.cdx.json', '');
    if (text.includes(root.replace(/\/+$/, ''))) throw failure(`SBOM ${file} contains the absolute checkout path ${root}; normalize it with verify/normalize-sbom-paths.mjs`);
    const rootComponent = bom?.metadata?.component;
    const rootRef = rootComponent?.['bom-ref'];
    const componentName = rootComponent?.group ? `${rootComponent.group}/${rootComponent.name}` : rootComponent?.name;
    const identity = `${componentName}@${rootComponent?.version}`;
    if (bom?.bomFormat !== 'CycloneDX' || identity !== expectedRoots[name] || !rootRef) {
      throw failure(`Wrong root identity in ${file}: expected ${expectedRoots[name]}`);
    }
    if (!Array.isArray(bom.dependencies) || bom.dependencies.length === 0) {
      throw failure(`SBOM ${file} must contain a non-empty dependency graph`);
    }
    const refs = new Set([rootRef, rootComponent.purl]);
    collectRefs(bom.components, refs);
    const purls = [];
    collectPurls(bom.components, purls);
    for (const entry of lockComponents[name] ?? []) {
      const component = typeof entry === 'string' ? entry : entry.purl;
      if (typeof entry !== 'string' && !purls.some((purl) => (purl === component || purl.startsWith(`${component}?`)) && purlMatchesSource(purl, entry, root))) {
        throw failure(`Missing lockfile component ${component} from source ${entry.source ?? 'path'} (exact ${entry.kind} identity ${entry.expected}) in ${file}`);
      }
      if (![...refs].some((ref) => ref === component || (ref && ref.startsWith(`${component}?`)))) throw failure(`Missing lockfile component ${component} in ${file}`);
    }
    for (const dependency of bom.dependencies) {
      if (!refs.has(dependency.ref)) throw failure(`Unresolved dependency ref ${dependency.ref} in ${file}`);
      for (const child of dependency.dependsOn ?? []) {
        if (!refs.has(child)) throw failure(`Unresolved dependsOn ref ${child} in ${file}`);
      }
    }
  }
}

// OTP-24: `native-host` is the production Native Messaging host (native-bridge/host, plain Rust, no
// Tauri). Its lockfile carries the vendored, patched bluest 0.6.9 as a path package, so the SBOM must
// list it (see check-vendored-patches.mjs for the patch provenance obligations).
export const PRODUCTION_SBOM_ROOTS = {
  'web-extension': '@veles/web-extension@0.1.0',
  protocol: 'veles-protocol-contract-checks@1.0.0',
  rust: 'veles-crypto@0.1.0',
  'native-bridge': 'veles-native-bridge@0.1.0',
  'native-host': 'veles-ble-host@0.1.0',
};

export const PRODUCTION_LOCKFILES = {
  'web-extension': { path: ['web-extension', 'package-lock.json'], ecosystem: 'npm' },
  protocol: { path: ['protocol', 'package-lock.json'], ecosystem: 'npm' },
  rust: { path: ['rust', 'Cargo.lock'], ecosystem: 'cargo' },
  'native-bridge': { path: ['native-bridge', 'src-tauri', 'Cargo.lock'], ecosystem: 'cargo' },
  'native-host': { path: ['native-bridge', 'host', 'Cargo.lock'], ecosystem: 'cargo' },
};

if (import.meta.main) {
  const root = process.cwd();
  Promise.all(Object.entries(PRODUCTION_LOCKFILES).map(async ([name, { path, ecosystem }]) => {
    const lockfile = join(root, ...path);
    if (ecosystem !== 'cargo') return [name, await deriveLockComponents(lockfile, ecosystem)];
    return [name, await deriveShippedCargoComponents(join(root, ...path.slice(0, -1), 'Cargo.toml'), lockfile)];
  }))
    .then((entries) => validateSboms(root, PRODUCTION_SBOM_ROOTS, Object.fromEntries(entries))).catch((error) => {
      console.error(error.message);
      process.exitCode = error.exitCode ?? 2;
    });
}
