import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const sha256 = (contents) => createHash('sha256').update(contents).digest('hex');
const dangerous = /(?:reqwest|ureq|curl|wget|Command::new\s*\(\s*["'](?:curl|wget|sh|bash)|https?:\/\/)/;

function failure(policy, message) {
  const error = new Error(`${message}; policy: ${policy.policyPath}`);
  error.exitCode = 1;
  return error;
}

export async function verifyCargoBuildScripts({ sources, policy, lockedPackages = new Map() }) {
  const findings = [];
  for (const source of sources) {
    let checksum;
    try { checksum = JSON.parse(await readFile(join(source, '.cargo-checksum.json'), 'utf8')); } catch { continue; }
    const packageName = source.split('/').pop().replace(/-(\d+\.\d+\.\d+(?:[-+].*)?)$/, '');
    const packageVersion = source.split('/').pop().match(/-(\d+\.\d+\.\d+(?:[-+].*)?)$/)?.[1];
    const expected = lockedPackages.get(`${packageName}@${packageVersion}`);
    if (expected && expected !== checksum.package) throw failure(policy, `Cargo locked checksum mismatch for ${packageName}@${packageVersion}`);
    let entries;
    try { entries = await readdir(source, { recursive: true }); } catch { continue; }
    for (const entry of entries.filter((path) => path === 'build.rs' || path.endsWith('/build.rs'))) {
      const bytes = await readFile(join(source, entry));
      if (checksum.files?.[entry] !== sha256(bytes)) throw failure(policy, `Cargo checksum mismatch for ${source}/${entry}`);
      const text = bytes.toString('utf8');
      if (!dangerous.test(text)) continue;
      const exception = policy.exceptions.find((item) => item.packageChecksum === checksum.package && item.file === entry && item.sha256 === sha256(bytes));
      if (!exception) throw failure(policy, `Suspicious Cargo build script ${source}/${entry}: ${text.trim()}`);
      findings.push(`${source}/${entry}`);
    }
  }
  return findings;
}

async function main() {
  const repository = process.cwd();
  const policy = JSON.parse(await readFile(join(repository, 'verify', 'cargo-build-script-policy.json'), 'utf8'));
  const cargoHome = process.env.CARGO_HOME ?? join(process.env.HOME ?? '', '.cargo');
  const manifests = ['rust/Cargo.toml', 'native-bridge/src-tauri/Cargo.toml'];
  for (const manifest of manifests) {
    const fetched = spawnSync('cargo', ['fetch', '--locked', '--manifest-path', join(repository, manifest)], { cwd: repository, encoding: 'utf8' });
    if (fetched.status !== 0) throw new Error(`Locked Cargo fetch failed for ${manifest}: ${fetched.stderr}`);
  }
  const lockedPackages = new Map();
  for (const manifest of manifests) {
    const lock = await readFile(join(repository, manifest.replace(/Cargo\.toml$/, 'Cargo.lock')), 'utf8');
    for (const block of lock.split('[[package]]').slice(1)) {
      const name = block.match(/^\s*name = "([^"]+)"/m)?.[1];
      const version = block.match(/^\s*version = "([^"]+)"/m)?.[1];
      const checksum = block.match(/^\s*checksum = "([^"]+)"/m)?.[1];
      if (name && version && checksum) lockedPackages.set(`${name}@${version}`, checksum);
    }
  }
  const registry = join(cargoHome, 'registry', 'src');
  const cache = join(cargoHome, 'registry', 'cache');
  const indexes = await readdir(registry).catch(() => []);
  const sources = (await Promise.all(indexes.map(async (index) => {
    const base = join(registry, index);
    return (await readdir(base).catch(() => [])).map((entry) => join(base, entry));
  }))).flat();
  const findings = await verifyCargoBuildScripts({ sources, policy, lockedPackages });
  for (const [identity, checksum] of lockedPackages) {
    const source = sources.find((path) => path.endsWith(`/${identity.replace('@', '-')}`));
    const archives = (await readdir(cache, { recursive: true }).catch(() => [])).filter((path) => path.endsWith(`/${identity.replace('@', '-')}.crate`));
    const archive = archives[0] && await readFile(join(cache, archives[0]));
    if (!source || !archive || sha256(archive) !== checksum) throw failure(policy, `Missing or mismatched locked Cargo source ${identity}`);
  }
  for (const manifest of manifests) {
    const analyzed = spawnSync('cargo', ['metadata', '--locked', '--offline', '--manifest-path', join(repository, manifest)], { cwd: repository, encoding: 'utf8' });
    if (analyzed.status !== 0) throw new Error(`Offline Cargo analysis failed for ${manifest}: ${analyzed.stderr}`);
  }
  await writeFile(join(repository, 'build', 'verification', 'supply-chain', 'cargo-build-scripts.txt'), `${findings.join('\n')}\n`);
}

if (import.meta.main) main().catch((error) => { console.error(error.message); process.exitCode = error.exitCode ?? 2; });
