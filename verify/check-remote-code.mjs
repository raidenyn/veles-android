import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

function failure(path, reason) {
  const error = new Error(`remote-code policy rejected ${path}: ${reason}`);
  error.exitCode = 1;
  return error;
}

export async function scanRemoteCode(root, paths) {
  const findings = [];
  for (const path of paths) {
    const text = await readFile(join(root, path), 'utf8');
    if (/\bnpx\b/.test(text)) throw failure(path, 'npx is forbidden');
    if (/(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|zsh)\b/.test(text)) throw failure(path, 'shell-pipe download is forbidden');
    if (/content_security_policy[^\n]*https?:\/\//i.test(text) || /script-src[^;"']*https?:\/\//i.test(text)) {
      throw failure(path, 'remote CSP script source is forbidden');
    }
    if (/"updater"\s*:|tauri-plugin-updater|plugins\s*\.\s*updater/i.test(text)) {
      throw failure(path, 'Tauri updater wiring is forbidden');
    }
    if (/(?:curl|wget)\s+https?:\/\/[^\s]+(?:latest|releases\/download\/[^/]+\/[^\s]*\.(?:sh|exe|msi))/i.test(text)) {
      throw failure(path, 'floating executable download is forbidden');
    }
    findings.push(path);
  }
  return findings;
}

async function filesUnder(root, directory) {
  const output = [];
  for (const entry of await readdir(join(root, directory), { withFileTypes: true })) {
    const relative = join(directory, entry.name);
    if (entry.isDirectory()) output.push(...await filesUnder(root, relative));
    else if (entry.isFile()) output.push(relative);
  }
  return output;
}

async function main() {
  const repository = process.cwd();
  const scopes = [
    'web-extension/src', 'web-extension/scripts', 'web-extension/manifest.json',
    'native-bridge/src-tauri', 'native-bridge/scripts', 'native-bridge/package.json',
    'build.gradle.kts', 'verify',
  ];
  const paths = [];
  for (const scope of scopes) {
    try {
      const entries = await filesUnder(repository, scope);
      paths.push(...entries.filter((path) => !path.includes('node_modules/') && !path.includes('verify/test/') && !path.endsWith('package-lock.json') && !['verify/check-cargo-build-scripts.mjs', 'verify/check-remote-code.mjs'].includes(path)));
    } catch {
      try { await readFile(join(repository, scope)); paths.push(scope); } catch { /* absent optional scope */ }
    }
  }
  const findings = await scanRemoteCode(repository, paths);
  const reports = join(repository, 'build', 'verification', 'supply-chain');
  await writeFile(join(reports, 'remote-code.txt'), `${findings.sort().join('\n')}\n`);
}

if (import.meta.main) main().catch((error) => { console.error(error.message); process.exitCode = error.exitCode ?? 2; });
