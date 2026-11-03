import { readFile, writeFile } from 'node:fs/promises';

// cargo-cyclonedx writes path-package bom-refs as `path+file://<absolute checkout>/...` (e.g. the vendored
// bluest). Replacing the checkout with a fixed placeholder keeps the SBOM free of local paths and makes its
// bytes independent of where the repository was checked out. Applied to every generated Cargo SBOM; the
// SBOM validator rejects any SBOM that still contains the checkout path.
export const SBOM_ROOT_PLACEHOLDER = '/repo-root';

export function normalizeSbomPaths(text, root) {
  const trimmed = root.replace(/\/+$/, '');
  return trimmed ? text.replaceAll(trimmed, SBOM_ROOT_PLACEHOLDER) : text;
}

if (import.meta.main) {
  const [root, file] = process.argv.slice(2);
  if (!root || !file) { console.error('usage: normalize-sbom-paths.mjs <repository-root> <sbom-file>'); process.exit(2); }
  const text = await readFile(file, 'utf8');
  await writeFile(file, normalizeSbomPaths(text, root));
}
