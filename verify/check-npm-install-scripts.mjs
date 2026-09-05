import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { gunzipSync } from 'node:zlib';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const sha256 = (contents) => createHash('sha256').update(contents).digest('hex');

function tarFiles(buffer) {
  const files = new Map();
  for (let offset = 0; offset + 512 <= buffer.length;) {
    const header = buffer.subarray(offset, offset + 512);
    const name = header.subarray(0, 100).toString('utf8').replace(/\0.*$/, '');
    if (!name) break;
    const size = Number.parseInt(header.subarray(124, 136).toString('utf8').replace(/\0.*$/, '').trim(), 8);
    const start = offset + 512;
    files.set(name, buffer.subarray(start, start + size));
    offset = start + Math.ceil(size / 512) * 512;
  }
  return files;
}

async function optionalTarball(packageName, version, integrity) {
  const directory = await mkdtemp(join(tmpdir(), 'veles-npm-tarball-'));
  try {
    // Acquisition phase: fetch the locked optional tarball (e.g. the macOS-only
    // fsevents optional dep) so the supply-chain verifier does not require it
    // to already be in the local npm cache. On a Linux CI host, `npm ci` does
    // not download fsevents at all, so an `--offline` pack would fail with
    // ENOTCACHED. `--ignore-scripts` keeps the acquisition safe (no lifecycle
    // scripts run); the sha512 integrity check below is the security gate that
    // binds the fetched bytes to the lockfile, so allowing the network fetch
    // here is consistent with the plan's "offline product packaging after
    // acquisition" boundary.
    const packed = spawnSync('npm', ['pack', '--ignore-scripts', '--pack-destination', directory, `${packageName}@${version}`], { encoding: 'utf8' });
    if (packed.status !== 0) throw new Error(`Cannot acquire locked optional package ${packageName}@${version}: ${packed.stderr}`);
    const tarball = join(directory, (await readdir(directory))[0]);
    const bytes = await readFile(tarball);
    const [algorithm, expected] = integrity.split('-', 2);
    if (algorithm !== 'sha512' || createHash(algorithm).update(bytes).digest('base64') !== expected) {
      throw new Error(`Integrity mismatch for optional package ${packageName}@${version}`);
    }
    return tarFiles(gunzipSync(bytes));
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

function failure(policy, message) {
  const error = new Error(`${message}; policy: ${policy.policyPath}`);
  error.exitCode = 1;
  return error;
}

function lifecycleCommand(pkg) {
  const scripts = pkg.scripts ?? {};
  return scripts.preinstall ?? scripts.install ?? scripts.postinstall ?? (pkg.gypfile ? 'node-gyp rebuild' : null);
}

export async function verifyNpmInstallScripts({ root, project, policy }) {
  const lock = JSON.parse(await readFile(join(root, 'package-lock.json'), 'utf8'));
  const reviewed = policy.exceptions.filter((entry) => entry.project.split(',').includes(project));
  const reviewedKeys = new Set(reviewed.map((entry) => `${entry.package}@${entry.version}`));
  const findings = [];
  for (const [path, entry] of Object.entries(lock.packages ?? {})) {
    if (!entry.hasInstallScript) continue;
    const packagePath = join(root, path);
    let pkg;
    let fileBytes;
    try {
      pkg = JSON.parse(await readFile(join(packagePath, 'package.json'), 'utf8'));
      fileBytes = async (file) => readFile(join(packagePath, file));
    } catch {
      if (!entry.optional) throw failure(policy, `Cannot inspect lifecycle package ${path}@${entry.version}`);
      const files = await optionalTarball(path.replace(/^node_modules\//, ''), entry.version, entry.integrity);
      pkg = JSON.parse((files.get('package/package.json') ?? files.get('package.json'))?.toString('utf8') ?? '');
      fileBytes = async (file) => files.get(`package/${file}`) ?? files.get(file) ?? Buffer.alloc(0);
    }
    // npm records `hasInstallScript` for native optional packages even when the
    // tarball relies on npm's implicit node-gyp lifecycle command.
    const command = lifecycleCommand(pkg) ?? 'node-gyp rebuild';
    const exception = reviewed.find((item) => item.package === pkg.name && item.version === entry.version);
    if (!exception || exception.integrity !== entry.integrity || exception.command !== command) {
      throw failure(policy, `Unreviewed or changed lifecycle script ${pkg.name}@${entry.version}: ${command}`);
    }
    const bytes = await fileBytes(exception.referencedFile);
    if (sha256(bytes) !== exception.referencedFileSha256) {
      throw failure(policy, `Changed lifecycle file ${pkg.name}@${entry.version}:${exception.referencedFile}`);
    }
    findings.push(`${pkg.name}@${entry.version} ${command} ${exception.referencedFile}`);
    reviewedKeys.delete(`${pkg.name}@${entry.version}`);
  }
  if (reviewedKeys.size > 0) throw failure(policy, `Reviewed lifecycle package missing from lockfile: ${[...reviewedKeys].join(', ')}`);
  return findings;
}

async function main() {
  const repository = process.cwd();
  const policy = JSON.parse(await readFile(join(repository, 'verify', 'install-script-policy.json'), 'utf8'));
  const reports = join(repository, 'build', 'verification', 'supply-chain');
  const results = [];
  for (const project of ['web-extension', 'native-bridge']) {
    const findings = await verifyNpmInstallScripts({ root: join(repository, project), project, policy });
    results.push(`${project}\n${findings.join('\n')}\n`);
  }
  await writeFile(join(reports, 'npm-install-scripts.txt'), results.join('\n'));
}

if (import.meta.main) main().catch((error) => { console.error(error.message); process.exitCode = error.exitCode ?? 2; });
