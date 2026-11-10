import { readdir, readFile, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

function failure(path, reason) {
  const error = new Error(`remote-code policy rejected ${path}: ${reason}`);
  error.exitCode = 1;
  return error;
}

function codeWithoutStringsAndComments(text) {
  let output = '';
  let quote = null;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    const next = text[index + 1];
    if (quote) {
      if (character === '\\') index += 1;
      else if (character === quote) quote = null;
      output += ' ';
    } else if (character === '"' || character === "'" || character === '`') {
      quote = character; output += ' ';
    } else if (character === '/' && next === '/') {
      index = text.indexOf('\n', index); if (index < 0) break; output += '\n';
    } else if (character === '/' && next === '*') {
      index = text.indexOf('*/', index + 2); if (index < 0) break; index += 1; output += ' ';
    } else output += character;
  }
  return output;
}

function codeWithoutComments(text) {
  let output = '';
  let quote = null;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    const next = text[index + 1];
    if (quote) {
      if (character === '\\') {
        output += character + (next ?? '');
        index += 1;
      } else {
        output += character;
        if (character === quote) quote = null;
      }
    } else if (character === '"' || character === "'" || character === '`') {
      quote = character;
      output += character;
    } else if (character === '/' && next === '/') {
      index = text.indexOf('\n', index);
      if (index < 0) break;
      output += '\n';
    } else if (character === '/' && next === '*') {
      index = text.indexOf('*/', index + 2);
      if (index < 0) break;
      index += 1;
    } else output += character;
  }
  return output;
}

export async function scanRemoteCode(root, paths) {
  const findings = [];
  for (const path of paths) {
    const text = await readFile(join(root, path), 'utf8');
    const executable = path.endsWith('.json') ? text : path.endsWith('.sh') ? text.replace(/#[^\n]*/g, '') : codeWithoutStringsAndComments(text);
    const source = codeWithoutComments(text);
    const executedStrings = [...source.matchAll(/(?:exec|execSync|execFile|execFileSync|spawn|spawnSync)\s*\(\s*(["'`])([\s\S]*?)\1/g)].map((match) => match[2]).join('\n');
    const shellCommands = [...source.matchAll(/(?:exec|execSync|execFile|execFileSync|spawn|spawnSync)\s*\(\s*(["'`])(?:\/(?:usr\/)?bin\/)?(?:sh|bash|zsh)\1\s*,\s*\[\s*(["'`])-c\2\s*,\s*(["'`])([\s\S]*?)\3/g)].map((match) => match[4]).join('\n');
    if (/\bnpx\b/.test(executable)) throw failure(path, 'npx is forbidden');
    if (/(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|zsh)\b/.test(executable) || /(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|zsh)\b/.test(executedStrings) || /(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|zsh)\b/.test(shellCommands)) throw failure(path, 'shell-pipe download is forbidden');
    if (/content_security_policy[^\n]*https?:\/\//i.test(executable) || /script-src[^;"']*https?:\/\//i.test(executable)) {
      throw failure(path, 'remote CSP script source is forbidden');
    }
    if (/"updater"\s*:|tauri-plugin-updater|plugins\s*\.\s*updater/i.test(executable)) {
      throw failure(path, 'Tauri updater wiring is forbidden');
    }
    if (/(?:curl|wget)\s+https?:\/\/[^\s]+(?:latest|releases\/download\/[^/]+\/[^\s]*\.(?:sh|exe|msi))/i.test(executable)) {
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

// Every scope must exist: a renamed or deleted file must be retargeted here, never silently skipped.
export const REMOTE_CODE_SCOPES = [
  'web-extension/src', 'web-extension/scripts', 'web-extension/vite.config.ts',
  'native-bridge/src-tauri', 'native-bridge/scripts', 'native-bridge/package.json',
  'native-bridge/host/src', 'native-bridge/host/scripts', 'native-bridge/host/Cargo.toml',
  // OTP-25 standalone installers: engine, scripts, NSIS and Swift frontends, producer workflows and the publisher.
  'native-bridge/installer/src', 'native-bridge/installer/scripts', 'native-bridge/installer/windows',
  'native-bridge/installer/macos/Package.swift', 'native-bridge/installer/macos/Sources', 'native-bridge/installer/Cargo.toml',
  '.github/workflows/build-native-windows.yml', '.github/workflows/build-native-macos.yml',
  '.github/workflows/publish-release.yml',
  // Component-tree CI, the PR title check, the release/commit tooling and the commit hook.
  '.github/workflows/ci.yml', '.github/workflows/pr-title.yml',
  '.github/workflows/check-native-host.yml', '.github/workflows/check-installer.yml',
  'scripts', '.githooks',
  'build.gradle.kts', 'verify',
];

export async function missingScopes(repository, scopes = REMOTE_CODE_SCOPES) {
  const missing = [];
  for (const scope of scopes) {
    try {
      await stat(join(repository, scope));
    } catch {
      missing.push(scope);
    }
  }
  return missing;
}

async function main() {
  const repository = process.cwd();
  const missing = await missingScopes(repository);
  if (missing.length > 0) {
    const error = new Error(`remote-code scan scopes do not exist: ${missing.join(', ')}`);
    error.exitCode = 1;
    throw error;
  }
  const scopes = REMOTE_CODE_SCOPES;
  const paths = [];
  for (const scope of scopes) {
    try {
      const entries = await filesUnder(repository, scope);
      paths.push(...entries.filter((path) => !path.includes('node_modules/') && !path.includes('verify/test/') && !path.includes('installer/scripts/fixtures/') && !path.includes('__pycache__/') && !/installer\/scripts\/test_[^/]*\.py$/.test(path) && !path.endsWith('package-lock.json') && !['verify/check-cargo-build-scripts.mjs', 'verify/check-remote-code.mjs'].includes(path)));
    } catch {
      await readFile(join(repository, scope));
      paths.push(scope);
    }
  }
  const findings = await scanRemoteCode(repository, paths);
  const reports = join(repository, 'build', 'verification', 'supply-chain');
  await writeFile(join(reports, 'remote-code.txt'), `${findings.sort().join('\n')}\n`);
}

if (import.meta.main) main().catch((error) => { console.error(error.message); process.exitCode = error.exitCode ?? 2; });
