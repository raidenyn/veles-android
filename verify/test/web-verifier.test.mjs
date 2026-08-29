import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { chmod, mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const ZIP = 'veles-extension-0.1.0.zip';
const SIDECAR = `${ZIP}.sha256`;
const REPO_ROOT = join(import.meta.dirname, '..', '..');
const VERIFY_SCRIPT = join(REPO_ROOT, 'verify', 'verify-web.sh');
const sha256 = (value) => createHash('sha256').update(value).digest('hex');

async function withArtifacts(files, run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-web-verifier-'));
  try {
    for (const [path, content] of Object.entries(files)) {
      await mkdir(join(root, path.slice(0, path.lastIndexOf('/'))), { recursive: true });
      await writeFile(join(root, path), content);
    }
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function manifest(zip, sidecar) {
  return `${sha256(zip)}  ${ZIP}\n${sha256(sidecar)}  ${SIDECAR}\n`;
}

async function withFakeTools(tools, run) {
  const bin = await mkdtemp(join(tmpdir(), 'veles-web-tools-'));
  try {
    await Promise.all(Object.entries(tools).map(async ([name, source]) => {
      const path = join(bin, name);
      await writeFile(path, source);
      await chmod(path, 0o755);
    }));
    await run(bin);
  } finally {
    await rm(bin, { recursive: true, force: true });
  }
}

function runVerifier(bin, environment = {}) {
  return spawnSync('/bin/bash', [VERIFY_SCRIPT], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    env: {
      ...process.env,
      ...environment,
      PATH: `${bin}:${process.env.PATH}`,
    },
  });
}

const cleanGit = '#!/usr/bin/env bash\nexit 0\n';
const successfulDocker = '#!/usr/bin/env bash\nexit 0\n';

test('rejects an invalid candidate manifest', async () => {
  const { verifyWebArtifacts } = await import('../verify-web.mjs');
  await withArtifacts({
    [`candidate/${ZIP}`]: 'candidate zip',
    [`candidate/${SIDECAR}`]: 'candidate sidecar',
    'candidate/SHA256SUMS': `${'0'.repeat(64)}  ${ZIP}\n${sha256('candidate sidecar')}  ${SIDECAR}\n`,
    [`reference/${ZIP}`]: 'reference zip',
    [`reference/${SIDECAR}`]: 'reference sidecar',
    'reference/SHA256SUMS': manifest('reference zip', 'reference sidecar'),
  }, async (root) => {
    await assert.rejects(
      () => verifyWebArtifacts(join(root, 'candidate'), join(root, 'reference')),
      (error) => error.exitCode === 1 && /candidate.*checksum mismatch.*\.zip/.test(error.message),
    );
  });
});

test('reports the first byte-different artifact path and both hashes', async () => {
  const { verifyWebArtifacts } = await import('../verify-web.mjs');
  const candidateZip = 'candidate zip';
  const referenceZip = 'reference zip';
  const sidecar = 'sidecar';
  await withArtifacts({
    [`candidate/${ZIP}`]: candidateZip,
    [`candidate/${SIDECAR}`]: sidecar,
    'candidate/SHA256SUMS': manifest(candidateZip, sidecar),
    [`reference/${ZIP}`]: referenceZip,
    [`reference/${SIDECAR}`]: sidecar,
    'reference/SHA256SUMS': manifest(referenceZip, sidecar),
  }, async (root) => {
    await assert.rejects(
      () => verifyWebArtifacts(join(root, 'candidate'), join(root, 'reference')),
      (error) => error.exitCode === 1
        && error.message === `byte mismatch: ${ZIP}\ncandidate: ${sha256(candidateZip)}\nreference: ${sha256(referenceZip)}`,
    );
  });
});

test('maps a raw candidate-validation tool failure to exit 2', async () => {
  await withFakeTools({
    git: cleanGit,
    npm: '#!/usr/bin/env bash\nexit 0\n',
    node: '#!/usr/bin/env bash\nexit 127\n',
    docker: successfulDocker,
  }, async (bin) => {
    const result = runVerifier(bin);
    assert.equal(result.status, 2);
    assert.match(result.stderr, /candidate artifact validation failed/);
  });
});

test('maps a failed git status check to exit 2', async () => {
  await withFakeTools({
    git: '#!/usr/bin/env bash\nexit 7\n',
    npm: '#!/usr/bin/env bash\nexit 0\n',
    node: '#!/usr/bin/env bash\nexit 127\n',
    docker: successfulDocker,
  }, async (bin) => {
    const result = runVerifier(bin);
    assert.equal(result.status, 2);
    assert.match(result.stderr, /cannot determine checkout cleanliness/);
  });
});

test('preserves exit 1 for a real candidate/reference artifact mismatch', async () => {
  const fakeNpm = `#!/usr/bin/env bash
set -euo pipefail
OUT="$(dirname "$PWD")/build/web-extension"
ZIP="veles-extension-0.1.0.zip"
SIDECAR="$ZIP.sha256"
mkdir -p "$OUT"
printf 'candidate zip' > "$OUT/$ZIP"
printf '%s  %s\\n' "$(sha256sum "$OUT/$ZIP" | cut -d' ' -f1)" "$ZIP" > "$OUT/$SIDECAR"
printf '%s  %s\\n' "$(sha256sum "$OUT/$ZIP" | cut -d' ' -f1)" "$ZIP" > "$OUT/SHA256SUMS"
printf '%s  %s\\n' "$(sha256sum "$OUT/$SIDECAR" | cut -d' ' -f1)" "$SIDECAR" >> "$OUT/SHA256SUMS"
`;
  const fakeDocker = `#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = run ] && [ "\${!#}" = package ]; then
  for argument in "$@"; do
    case "$argument" in
      *:/out) OUT="\${argument%:/out}" ;;
    esac
  done
  CANDIDATE="$(dirname "$PWD")/build/web-extension"
  cp "$CANDIDATE"/* "$OUT/"
  ZIP="veles-extension-0.1.0.zip"
  SIDECAR="$ZIP.sha256"
  printf 'reference zip' > "$OUT/$ZIP"
  printf '%s  %s\\n' "$(sha256sum "$OUT/$ZIP" | cut -d' ' -f1)" "$ZIP" > "$OUT/$SIDECAR"
  printf '%s  %s\\n' "$(sha256sum "$OUT/$ZIP" | cut -d' ' -f1)" "$ZIP" > "$OUT/SHA256SUMS"
  printf '%s  %s\\n' "$(sha256sum "$OUT/$SIDECAR" | cut -d' ' -f1)" "$SIDECAR" >> "$OUT/SHA256SUMS"
fi
`;
  await withFakeTools({ git: cleanGit, npm: fakeNpm, docker: fakeDocker }, async (bin) => {
    const result = runVerifier(bin, { DOCKER_BIN: join(bin, 'docker') });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /byte mismatch: veles-extension-0\.1\.0\.zip/);
  });
});
