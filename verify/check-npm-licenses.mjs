import { spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { evaluateNpmLicense } from './lib/license-policy.mjs';
import { error, mismatch } from './lib/checksum-manifest.mjs';

// A tool invocation failure (missing binary, nonzero exit, spawn error) is an
// environment/infrastructure failure (exit 2), not a license-policy mismatch.
function run(command, arguments_, options) {
  const result = spawnSync(command, arguments_, { encoding: 'utf8', ...options });
  if (result.error) throw error(`cannot run ${command}: ${result.error.message}`);
  if (result.status !== 0) throw error(`${command} failed: ${result.stderr || result.stdout}`);
  return result.stdout;
}

async function checkNpmProject(repository, project, policy) {
  const checker = join(repository, 'verify', 'node_modules', '.bin', 'license-checker-rseidelsohn');
  const output = run(checker, ['--json', '--start', join(repository, project), '--relativeLicensePath', '--excludePrivatePackages']);
  const licenses = JSON.parse(output);
  const report = [];
  for (const [packageVersion, evidence] of Object.entries(licenses)) {
    const at = packageVersion.lastIndexOf('@');
    const result = evaluateNpmLicense(evidence.licenses, {
      ...policy,
      diagnostic: {
        package: packageVersion.slice(0, at), version: packageVersion.slice(at + 1),
        licenseText: evidence.licenseFile ?? evidence.licenseText,
      },
    });
    if (!result.allowed) throw mismatch(result.diagnostic);
    report.push(`${packageVersion}: ${result.selectedLicense}`);
  }
  return `${project}\n${report.sort().join('\n')}\n`;
}

async function main() {
  const repository = process.cwd();
  const policy = JSON.parse(await readFile(join(repository, '.license-policy.json'), 'utf8'));
  const reports = join(repository, 'build', 'verification', 'supply-chain');
  await mkdir(reports, { recursive: true });
  const npm = await Promise.all(['web-extension', 'native-bridge'].map((project) => checkNpmProject(repository, project, policy)));
  await writeFile(join(reports, 'npm-licenses.txt'), npm.join('\n'));

  const cargoDeny = join(repository, 'build', 'verify-tools', 'cargo-deny', 'bin', 'cargo-deny');
  const cargoReports = [];
  for (const manifest of ['rust/Cargo.toml', 'native-bridge/src-tauri/Cargo.toml']) {
    const output = run(cargoDeny, ['deny', '--manifest-path', join(repository, manifest), '--config', join(repository, 'licenses.toml'), 'check', 'licenses'], {
      cwd: repository, env: { ...process.env, CARGO_NET_OFFLINE: 'true' },
    });
    cargoReports.push(`${manifest}\n${output}`);
  }
  await writeFile(join(reports, 'cargo-licenses.txt'), cargoReports.join('\n'));
}

if (import.meta.main) main().catch((caught) => { console.error(caught.message); process.exitCode = caught.exitCode ?? 2; });
