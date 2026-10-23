import assert from 'node:assert/strict';
import { chmod, mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const REPOSITORY = join(import.meta.dirname, '..', '..');

async function withVerifier({ released, environment = {} }, run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-android-verifier-'));
  const source = join(root, 'source');
  const output = join(root, 'out');
  const fakeBin = join(root, 'bin');
  const releasedApk = join(root, 'released.apk');
  try {
    await mkdir(fakeBin, { recursive: true });
    if (released) await writeFile(releasedApk, 'released');
    await mkdir(output, { recursive: true });
    const inner = (await readFile(join(REPOSITORY, 'verify', 'verify-inner.sh'), 'utf8'))
      .replaceAll('/build/src', source)
      .replaceAll('/apk/released.apk', releasedApk)
      .replace(/(?<![A-Za-z])\/out\b/g, output);
    const script = join(root, 'verify-inner.sh');
    await writeFile(script, inner, { mode: 0o755 });
    await Promise.all([
      writeFile(join(fakeBin, 'git'), `#!/usr/bin/env bash
if [ "$1" = clone ]; then
  destination="${'${!#}'}"
  mkdir -p "$destination/app/build/outputs/apk/release" "$destination/rust/scripts"
  printf rebuilt > "$destination/app/build/outputs/apk/release/app-release-unsigned.apk"
  printf '#!/usr/bin/env bash\\nexit 0\\n' > "$destination/gradlew"
  printf '#!/usr/bin/env bash\\nexit 0\\n' > "$destination/rust/scripts/verify-apk-jni.sh"
  chmod +x "$destination/gradlew" "$destination/rust/scripts/verify-apk-jni.sh"
fi
`),
      writeFile(join(fakeBin, 'apksigcopier'), '#!/usr/bin/env bash\nexit 1\n'),
      writeFile(join(fakeBin, 'chown'), '#!/usr/bin/env bash\nexit 1\n'),
    ]);
    await Promise.all(['git', 'apksigcopier', 'chown'].map((name) => chmod(join(fakeBin, name), 0o755)));
    await run({
      output,
      result: spawnSync('bash', [script, 'test-ref'], {
        encoding: 'utf8',
        env: { ...process.env, ...environment, PATH: `${fakeBin}:${process.env.PATH}` },
      }),
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

test('reports an APK mismatch before an ownership handoff failure', async () => {
  await withVerifier({
    released: true,
    environment: { VELES_OUTPUT_UID: '1001', VELES_OUTPUT_GID: '1001' },
  }, ({ result }) => {
    assert.equal(result.status, 1, result.stderr);
    assert.match(result.stderr, /MISMATCH: released APK does NOT correspond/);
    assert.doesNotMatch(result.stderr, /cannot return rebuilt APK with host ownership/);
  });
});

test('allows audit output without an ownership handoff', async () => {
  await withVerifier({ released: false }, async ({ output, result }) => {
    assert.equal(result.status, 0, result.stderr);
    assert.equal(await readFile(join(output, 'app-release-unsigned.apk'), 'utf8'), 'rebuilt');
  });
});
