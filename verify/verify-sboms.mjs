import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

function failure(message) {
  const error = new Error(message);
  error.exitCode = 1;
  return error;
}

function collectRefs(components, refs) {
  for (const component of components ?? []) {
    if (component['bom-ref']) refs.add(component['bom-ref']);
    collectRefs(component.components, refs);
  }
}

export async function validateSboms(root, expectedRoots) {
  const directory = join(root, 'build', 'sbom');
  let files;
  try {
    files = (await readdir(directory)).filter((file) => file.endsWith('.cdx.json')).sort();
  } catch {
    throw failure(`Missing SBOM directory: ${directory}`);
  }
  const expected = Object.keys(expectedRoots).map((name) => `${name}.cdx.json`).sort();
  if (files.length !== 3 || files.join('\n') !== expected.join('\n')) {
    throw failure(`Expected exactly three SBOMs (${expected.join(', ')}), found ${files.join(', ') || 'none'}`);
  }
  for (const file of files) {
    let bom;
    try {
      bom = JSON.parse(await readFile(join(directory, file), 'utf8'));
    } catch {
      throw failure(`Invalid JSON in SBOM ${file}`);
    }
    const name = file.replace('.cdx.json', '');
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
    const refs = new Set([rootRef]);
    collectRefs(bom.components, refs);
    for (const dependency of bom.dependencies) {
      if (!refs.has(dependency.ref)) throw failure(`Unresolved dependency ref ${dependency.ref} in ${file}`);
      for (const child of dependency.dependsOn ?? []) {
        if (!refs.has(child)) throw failure(`Unresolved dependsOn ref ${child} in ${file}`);
      }
    }
  }
}

if (import.meta.main) {
  const root = process.cwd();
  validateSboms(root, {
    'web-extension': '@veles/web-extension@0.1.0',
    rust: 'veles-crypto@0.1.0',
    'native-bridge': 'veles-native-bridge@0.1.0',
  }).catch((error) => {
    console.error(error.message);
    process.exitCode = error.exitCode ?? 2;
  });
}
