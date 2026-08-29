import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { validateSboms } from '../verify-sboms.mjs';

const root = {
  'web-extension': '@veles/web-extension@0.1.0',
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

test('accepts exactly the three requested SBOMs with resolvable non-empty graphs', async () => {
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

test('rejects an unrequested fourth SBOM', async () => {
  const directory = await fixture();
  await writeFile(join(directory, 'build', 'sbom', 'unexpected.cdx.json'), '{}');
  await assert.rejects(validateSboms(directory, root), /exactly three/i);
});
