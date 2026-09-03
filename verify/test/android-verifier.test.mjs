import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';

const REPOSITORY = join(import.meta.dirname, '..', '..');

test('returns the Docker-rebuilt APK to the host with the invoking user ownership', async () => {
  const [outer, inner] = await Promise.all([
    readFile(join(REPOSITORY, 'verify', 'verify.sh'), 'utf8'),
    readFile(join(REPOSITORY, 'verify', 'verify-inner.sh'), 'utf8'),
  ]);

  // /out is a host bind mount. The Docker verifier runs as root to build the
  // reference APK, so ownership must cross this boundary explicitly before the
  // workflow can stage the canonical unsigned artifact on the host.
  assert.match(outer, /-e VELES_OUTPUT_UID="\$\(id -u\)"/);
  assert.match(outer, /-e VELES_OUTPUT_GID="\$\(id -g\)"/);
  assert.match(inner, /VELES_OUTPUT_UID.*VELES_OUTPUT_GID/);
  assert.match(inner, /chown "\$VELES_OUTPUT_UID:\$VELES_OUTPUT_GID" \/out\/app-release-unsigned\.apk/);
  // Signed inputs must remain on the signature-stripped comparison path.
  assert.match(inner, /apksigcopier compare "\$RELEASED" --unsigned "\$REBUILT"/);
});
