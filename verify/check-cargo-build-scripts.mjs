import { createHash } from 'node:crypto';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const sha256 = (contents) => createHash('sha256').update(contents).digest('hex');
const dangerous = /(?:reqwest|ureq|curl|wget|Command::new\s*\(\s*["'](?:curl|wget|sh|bash)|https?:\/\/)/;

function failure(policy, message) {
  const error = new Error(`${message}; policy: ${policy.policyPath}`);
  error.exitCode = 1;
  return error;
}

export async function verifyCargoBuildScripts({ sources, policy }) {
  const findings = [];
  for (const source of sources) {
    let checksum;
    try { checksum = JSON.parse(await readFile(join(source, '.cargo-checksum.json'), 'utf8')); } catch { continue; }
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
  const registry = join(cargoHome, 'registry', 'src');
  const indexes = await readdir(registry).catch(() => []);
  const sources = (await Promise.all(indexes.map(async (index) => {
    const base = join(registry, index);
    return (await readdir(base).catch(() => [])).map((entry) => join(base, entry));
  }))).flat();
  const findings = await verifyCargoBuildScripts({ sources, policy });
  await writeFile(join(repository, 'build', 'verification', 'supply-chain', 'cargo-build-scripts.txt'), `${findings.join('\n')}\n`);
}

if (import.meta.main) main().catch((error) => { console.error(error.message); process.exitCode = error.exitCode ?? 2; });
