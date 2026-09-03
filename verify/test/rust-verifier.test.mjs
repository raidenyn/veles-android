import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { chmod, mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const REPO_ROOT = join(import.meta.dirname, '..', '..');
const VERIFY_SCRIPT = join(REPO_ROOT, 'verify', 'verify-rust.sh');
const RUST_INNER = join(REPO_ROOT, 'verify', 'rust-inner.sh');
const JNI_PATHS = ['jni/arm64-v8a/libveles_crypto.so', 'jni/armeabi-v7a/libveles_crypto.so', 'jni/x86_64/libveles_crypto.so'];
const WASM_PATHS = ['wasm/veles_crypto.js'];

async function withPackage(files, run) {
  const root = await mkdtemp(join(tmpdir(), 'veles-rust-verifier-'));
  try {
    for (const [path, content] of Object.entries(files)) {
      const separator = path.lastIndexOf('/');
      const parent = separator < 0 ? '' : path.slice(0, separator);
      if (parent) await mkdir(join(root, parent), { recursive: true });
      await writeFile(join(root, path), content);
    }
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function manifest(files) {
  return Object.entries(files).sort(([left], [right]) => left.localeCompare(right))
    .map(([path, content]) => `${createHash('sha256').update(content).digest('hex')}  ${path}`).join('\n') + '\n';
}

function validPackage(prefix) {
  const files = Object.fromEntries([...JNI_PATHS, ...WASM_PATHS].map((path) => [path, `${prefix}:${path}`]));
  return { ...files, SHA256SUMS: manifest(files) };
}

async function withFakeTools(tools, run) {
  const bin = await mkdtemp(join(tmpdir(), 'veles-rust-tools-'));
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
    env: { ...process.env, ...environment, PATH: `${bin}:${process.env.PATH}` },
  });
}

const cleanGit = '#!/usr/bin/env bash\nexit 0\n';
const successfulGradle = `#!/usr/bin/env bash
set -euo pipefail
OUT="$RUST_PACKAGE_DIR"
rm -rf "$OUT"
mkdir -p "$OUT/jni/arm64-v8a" "$OUT/jni/armeabi-v7a" "$OUT/jni/x86_64" "$OUT/wasm"
for path in jni/arm64-v8a/libveles_crypto.so jni/armeabi-v7a/libveles_crypto.so jni/x86_64/libveles_crypto.so wasm/veles_crypto.js; do
  printf candidate > "$OUT/$path"
done
(cd "$OUT" && sha256sum jni/arm64-v8a/libveles_crypto.so jni/armeabi-v7a/libveles_crypto.so jni/x86_64/libveles_crypto.so wasm/veles_crypto.js) > "$OUT/SHA256SUMS"
`;

test('rejects invalid candidate paths before starting Docker', async () => {
  await withFakeTools({
    git: cleanGit,
    gradle: '#!/usr/bin/env bash\nexit 0\n',
    docker: '#!/usr/bin/env bash\ntouch "$DOCKER_MARKER"\n',
  }, async (bin) => {
    const marker = join(bin, 'docker-started');
    const candidate = await mkdtemp(join(tmpdir(), 'veles-rust-candidate-'));
    const result = runVerifier(bin, { GRADLE_BIN: join(bin, 'gradle'), RUST_PACKAGE_DIR: candidate, DOCKER_BIN: join(bin, 'docker'), DOCKER_MARKER: marker });
    await rm(candidate, { recursive: true, force: true });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /candidate: artifact path set is invalid/);
  });
});

test('maps a Docker build failure to exit 2', async () => {
  await withFakeTools({ git: cleanGit, gradle: successfulGradle, docker: '#!/usr/bin/env bash\n[ "$1" = build ] && exit 42\n' }, async (bin) => {
    const candidate = await mkdtemp(join(tmpdir(), 'veles-rust-candidate-'));
    const result = runVerifier(bin, { GRADLE_BIN: join(bin, 'gradle'), RUST_PACKAGE_DIR: candidate, DOCKER_BIN: join(bin, 'docker') });
    await rm(candidate, { recursive: true, force: true });
    assert.equal(result.status, 2);
    assert.match(result.stderr, /failed to build Rust reference image/);
    assert.doesNotMatch(result.stdout, /VERIFIED:/);
  });
});

test('prepares dependencies online and produces the reference package offline', async () => {
  const [wrapper, inner] = await Promise.all([
    readFile(VERIFY_SCRIPT, 'utf8'),
    readFile(RUST_INNER, 'utf8'),
  ]);
  assert.match(inner, /prepare\(\)[\s\S]*\.\/gradlew --refresh-dependencies rustInstall/);
  assert.match(inner, /package_reference\(\)[\s\S]*CARGO_NET_OFFLINE=true \.\/gradlew --offline rustPackage/);
  assert.match(wrapper, /verify-rust\.mjs" --validate "\$RUST_PACKAGE_DIR"/);
  assert.doesNotMatch(wrapper, /verify-rust\.mjs" "\$RUST_PACKAGE_DIR" "\$RUST_PACKAGE_DIR"/);
});

test('persists the locked workspace Cargo registry before the offline package container starts', async () => {
  const inner = await readFile(RUST_INNER, 'utf8');
  const prepare = inner.match(/prepare\(\) \{([\s\S]*?)\n\}/)?.[1] ?? '';

  // The two containers share /work only. Fetching into the image-local
  // /opt/cargo home leaves the offline container without the crates.io index.
  assert.match(inner, /export CARGO_HOME=\/work\/cargo-home/);
  assert.match(prepare, /\(cd rust && cargo fetch --locked\)/);
  assert.match(prepare, /\.\/gradlew --refresh-dependencies rustInstall/);
  assert.ok(
    prepare.indexOf('(cd rust && cargo fetch --locked)') < prepare.indexOf('./gradlew --refresh-dependencies rustInstall'),
    'prepare must fetch the locked workspace graph before installing build tools',
  );
  assert.match(inner, /CARGO_NET_OFFLINE=true \.\/gradlew --offline rustPackage/);
});

test('pre-caches the pinned Gradle wrapper distribution for the offline package run', async () => {
  // The offline `package` container (docker run --network=none) cannot reach
  // services.gradle.org. The image must pre-cache gradle-9.5.0-bin.zip during
  // the online build, and rust-inner.sh must use a volume-backed
  // GRADLE_USER_HOME seeded from that pre-cache so the wrapper resolves the
  // distribution locally in both the prepare and package containers.
  const [dockerfile, inner, wrapperProperties] = await Promise.all([
    readFile(join(REPO_ROOT, 'verify', 'Dockerfile.rust'), 'utf8'),
    readFile(RUST_INNER, 'utf8'),
    readFile(join(REPO_ROOT, 'gradle', 'wrapper', 'gradle-wrapper.properties'), 'utf8'),
  ]);
  assert.match(dockerfile, /ENV GRADLE_USER_HOME=\/opt\/gradle-home/);
  assert.match(dockerfile, /ARG GRADLE_DISTRIBUTION_SHA256=553c78f50dafcd54d65b9a444649057857469edf836431389695608536d6b746/);
  // Gradle 9.5.0 retains no ZIP after unpacking. The canonical distribution pin
  // lives in gradle/wrapper/gradle-wrapper.properties (distributionSha256Sum —
  // the wrapper verifies it at download time), and the Dockerfile asserts the
  // nested .ok marker plus the unpacked distribution the offline seed consumes.
  assert.match(wrapperProperties, /distributionSha256Sum=553c78f50dafcd54d65b9a444649057857469edf836431389695608536d6b746/);
  assert.match(dockerfile, /COPY gradle\/wrapper\/gradle-wrapper\.properties gradle\/wrapper\/gradle-wrapper\.jar \/tmp\/veles-gradle\/gradle\/wrapper\//);
  assert.match(dockerfile, /\.\/gradlew --version/);
  // The .ok marker is one level below the distribution's hash directory. The
  // Dockerfile must derive that directory from the actual marker, then assert
  // the extracted Gradle launcher. Searching the parent `gradle-9.5.0-bin`
  // directory puts the marker lookup at the wrong level.
  assert.match(dockerfile, /find \$\{GRADLE_USER_HOME\}\/wrapper\/dists -type f -name 'gradle-9\.5\.0-bin\.zip\.ok'/);
  assert.match(dockerfile, /dist_dir="\$\(dirname "\$dist_marker"\)"/);
  assert.match(dockerfile, /gradle-9\.5\.0-bin\.zip\.ok/);
  assert.match(dockerfile, /gradle-9\.5\.0\/bin\/gradle/);
  assert.doesNotMatch(dockerfile, /find \$\{GRADLE_USER_HOME\}\/wrapper\/dists -name 'gradle-9\.5\.0-bin\.zip'/);
  assert.match(inner, /export GRADLE_USER_HOME=\/work\/gradle-home/);
  assert.match(inner, /seed_gradle_distribution/);
  assert.match(inner, /\/opt\/gradle-home\/wrapper\/dists/);
});

test('pins and verifies all Rust-image Android downloads and apt packages', async () => {
  const dockerfile = await readFile(join(REPO_ROOT, 'verify', 'Dockerfile.rust'), 'utf8');
  assert.match(dockerfile, /ARG PLATFORM_TOOLS_VERSION=37\.0\.1/);
  assert.match(dockerfile, /platform-tools_r\$\{PLATFORM_TOOLS_VERSION\}-linux\.zip/);
  assert.match(dockerfile, /d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1/);
  assert.match(dockerfile, /2d2d50857e4eb553af5a6dc3ad507a17adf43d115264b1afc116f95c92e5e258/);
  // Base apt packages are installed unversioned, matching the reviewed pattern
  // in verify/Dockerfile; the supply-chain pin discipline is on the build tools
  // (Rust/Node/NDK), not apt plumbing whose patch versions churn.
  assert.match(dockerfile, /apt-get install -y --no-install-recommends[\s\S]*?curl unzip ca-certificates build-essential/);
  assert.doesNotMatch(dockerfile, /curl=[0-9]/);
  assert.doesNotMatch(dockerfile, /unzip=[0-9]/);
  assert.doesNotMatch(dockerfile, /ca-certificates=[0-9]/);
  assert.doesNotMatch(dockerfile, /build-essential=[0-9]/);
  assert.doesNotMatch(dockerfile, /sdkmanager "platform-tools"/);
  // Each installed package's Pkg.Revision pin must print the actual
  // source.properties on mismatch before exiting 1, so the next CI run
  // pinpoints drift instead of failing silently. The three pins are
  // android-35, build-tools/36.0.0, and ndk/<NDK_VERSION>.
  for (const pin of [
    "platforms/android-35/source.properties",
    "build-tools/36.0.0/source.properties",
    "ndk/${NDK_VERSION}/source.properties",
  ]) {
    assert.match(dockerfile, new RegExp(`${pin.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[\\s\\S]*?cat \\$\\{ANDROID_HOME\\}/${pin.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`), `Dockerfile.rust must print the actual ${pin} on pin mismatch`);
  }
});

test('pins the JDK, NDK, Rust, Node, npm, and Android helper in the reference files', async () => {
  const [dockerfile, inner] = await Promise.all([
    readFile(join(REPO_ROOT, 'verify', 'Dockerfile.rust'), 'utf8'),
    readFile(RUST_INNER, 'utf8'),
  ]);
  assert.match(dockerfile, /eclipse-temurin:21\.0\.12_8-jdk-noble@sha256:1ca5e470/);
  assert.match(dockerfile, /NDK_VERSION=29\.0\.14206865/);
  assert.match(dockerfile, /node-v26\.8\.1-linux-x64\.tar\.xz/);
  assert.match(dockerfile, /3e301118d7df53d563b7e96c1617545f26e2f76f9724be668d6cab65c15dda5d/);
  for (const pin of ['JDK version drift', 'NDK version drift', 'Rust version drift', 'Node version drift', 'npm version drift', 'Android SDK helper version drift']) {
    assert.match(inner, new RegExp(pin));
  }
});

test('sets the exact temporary Rust toolchain as the image default before source is copied', async () => {
  const dockerfile = await readFile(join(REPO_ROOT, 'verify', 'Dockerfile.rust'), 'utf8');
  assert.match(dockerfile, /COPY rust\/rust-toolchain\.toml rust\/toolchain-tools\.toml \/tmp\/veles-rust\//);
  assert.match(
    dockerfile,
    /cd \/tmp\/veles-rust\s*\\?\s*&& rustup show active-toolchain\s*\\?\s*&& rustup default 1\.98\.0-x86_64-unknown-linux-gnu/,
    'the temporary rust-toolchain.toml override must become the global default for /work/src',
  );
});

test('uses a shell-safe NDK Pkg.Revision regex that accepts spacing but rejects drift', async () => {
  const [dockerfile, inner] = await Promise.all([
    readFile(join(REPO_ROOT, 'verify', 'Dockerfile.rust'), 'utf8'),
    readFile(RUST_INNER, 'utf8'),
  ]);
  assert.match(dockerfile, /grep -Eq 'Pkg\\\.Revision \*= \*'"\$\{NDK_VERSION\}"'\$'/,
    'the NDK pin must use one ERE escape level and a shell-quoted end anchor');
  assert.doesNotMatch(dockerfile, /Pkg\\\\\.Revision \*= \*\$\{NDK_VERSION\}\\\\\$/,
    'double escaping makes grep look for a literal backslash and dollar sign');
  assert.match(inner, /Pkg\\\.Revision\[\[:space:\]\]\*=\[\[:space:\]\]\*/,
    'the outer verifier must accept source.properties spaces around =');
  assert.match(inner, /29\.0\.14206865/, 'the outer verifier must retain the exact NDK pin');
});

test('preserves exit 1 for a byte mismatch after valid package paths', async () => {
  const { verifyRustPackages } = await import('../verify-rust.mjs');
  await withPackage({
    ...Object.fromEntries(Object.entries(validPackage('candidate')).map(([path, value]) => [path.startsWith('SHA') ? `candidate/${path}` : `candidate/${path}`, value])),
    ...Object.fromEntries(Object.entries(validPackage('reference')).map(([path, value]) => [path.startsWith('SHA') ? `reference/${path}` : `reference/${path}`, value])),
  }, async (root) => {
    await assert.rejects(() => verifyRustPackages(join(root, 'candidate'), join(root, 'reference')), (error) => error.exitCode === 1 && /byte mismatch/.test(error.message));
  });
});
