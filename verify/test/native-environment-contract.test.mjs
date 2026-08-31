import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';

const ROOT = join(import.meta.dirname, '..', '..');
const native = (name) => readFile(join(ROOT, 'verify', 'native', name), 'utf8');
const literal = (value) => new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));

test('pins the reviewed Windows Tauri tool inputs and required cache files', async () => {
  const manifest = JSON.parse(await native('windows-tools.json'));
  assert.deepEqual(manifest, {
    wix: {
      url: 'https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip',
      sha256: '6ac824e1642d6f7277d0ed7ea09411a508f6116ba6fae0aa5f2c7daa2ff43d31',
    },
    nsis: {
      url: 'https://github.com/tauri-apps/binary-releases/releases/download/nsis-3/nsis-3.zip',
      sha256: '1bb9fc85ee5b220d3869325dbb9d191dfe6537070f641c30fbb275c97051fd0c',
    },
    nsisTauriUtils: {
      url: 'https://github.com/tauri-apps/nsis-tauri-utils/releases/download/nsis_tauri_utils-v0.5.1/nsis_tauri_utils.dll',
      sha256: '3697d11bdbe1e34daa26b1e89d84276d9ff28148906943d0fe888354c3b13620',
    },
  });
  const provisioner = await native('provision-windows-tools.ps1');
  for (const path of [
    'WixTools314/candle.exe', 'WixTools314/candle.exe.config', 'WixTools314/darice.cub',
    'WixTools314/light.exe', 'WixTools314/light.exe.config', 'WixTools314/wconsole.dll',
    'WixTools314/winterop.dll', 'WixTools314/wix.dll', 'WixTools314/WixUIExtension.dll',
    'WixTools314/WixUtilExtension.dll', 'NSIS/makensis.exe', 'NSIS/Bin/makensis.exe',
    'NSIS/Stubs/lzma-x86-unicode', 'NSIS/Stubs/lzma_solid-x86-unicode',
    'NSIS/Plugins/x86-unicode/nsis_tauri_utils.dll',
    'NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll', 'NSIS/Include/MUI2.nsh',
    'NSIS/Include/FileFunc.nsh', 'NSIS/Include/x64.nsh', 'NSIS/Include/nsDialogs.nsh',
    'NSIS/Include/WinMessages.nsh',
    // Tauri 2.6.0's installer.nsi !includes these unconditionally; utils.nsh /
    // FileAssociation.nsh use the nsDialogs and System plugins.
    'NSIS/Include/WordFunc.nsh', 'NSIS/Include/StrFunc.nsh',
    'NSIS/Include/Win/COM.nsh', 'NSIS/Include/Win/Propkey.nsh',
    'NSIS/Plugins/x86-unicode/nsDialogs.dll', 'NSIS/Plugins/x86-unicode/System.dll',
  ]) assert.match(provisioner, new RegExp(path.replaceAll('/', '[\\\\/]')));
  for (const contract of ['Get-FileHash', 'SHA256', 'Expand-Archive', 'unexpected', 'missing']) {
    assert.match(provisioner, new RegExp(contract));
  }
});

test('Windows target wrapper validates its exact runner and safely proves offline packaging', async () => {
  const script = await native('network-deny-windows.ps1');
  for (const contract of [
    "ImageOS -ne 'win25'", "ImageVersion", "RUNNER_ARCH", "node --version", "v26.8.1",
    "npm --version", "11.19.0", 'https://github.com/', 'Invoke-WebRequest', 'New-NetFirewallRule',
    'Get-NetFirewallRule', 'CARGO_NET_OFFLINE', 'finally', 'Remove-NetFirewallRule', 'bridgePackage',
  ]) assert.match(script, literal(contract));
  assert.match(script, /bridgeBuild/);
  assert.ok(script.indexOf('bridgeBuild') < script.indexOf('Invoke-WebRequest'));
  assert.ok(script.indexOf('Invoke-WebRequest') < script.indexOf('New-NetFirewallRule'));
  assert.ok(script.indexOf('New-NetFirewallRule') < script.lastIndexOf('Invoke-WebRequest'));
  // Environment/usage/identity failures normalize to exit 2 (never 1) per the
  // 0/1/2 contract; a bare `throw` under ErrorActionPreference=Stop would
  // otherwise surface as exit 1.
  assert.match(script, /function env-fail[\s\S]*?exit 2/);
});

test('macOS target wrapper validates its exact runner and safely proves offline packaging', async () => {
  const script = await native('network-deny-macos.sh');
  for (const contract of [
    'macos26', 'ImageVersion', 'RUNNER_ARCH', 'node --version', 'v26.8.1', 'npm --version', '11.19.0',
    'DEVELOPER_DIR=/Applications/Xcode_26.6.app', '17F113', 'macosx26.5', 'https://github.com/',
    'curl --fail --silent --show-error', 'sandbox-exec', 'CARGO_NET_OFFLINE=true', 'trap', 'bridgePackage',
  ]) assert.match(script, literal(contract));
  assert.match(script, /bridgeBuild/);
  assert.ok(script.indexOf('bridgeBuild') < script.indexOf('curl --fail'));
  assert.ok(script.indexOf('curl --fail') < script.indexOf('sandbox-exec'));
  assert.ok(script.indexOf('sandbox-exec') < script.lastIndexOf('curl --fail'));
});

test('macOS target wrapper isolates only the package command tree without modifying PF', async () => {
  const script = await native('network-deny-macos.sh');
  for (const contract of [
    'command -v sandbox-exec', 'profile=$(mktemp)', '(deny network-outbound)',
    'trap cleanup EXIT', 'rm -f "$profile"', 'sandbox-exec -f "$profile" curl',
    'sandbox-exec -f "$profile" env CARGO_NET_OFFLINE=true',
  ]) assert.match(script, literal(contract));
  assert.doesNotMatch(script, /pfctl/);
  assert.ok(script.indexOf('curl --fail') < script.indexOf('sandbox-exec -f "$profile" curl'));
  assert.ok(script.indexOf('sandbox-exec -f "$profile" curl') < script.indexOf('sandbox-exec -f "$profile" env CARGO_NET_OFFLINE=true'));
  // Environment/build/identity failures normalize to exit 2 (never 1) per the
  // 0/1/2 contract; a raw nonzero exit under `set -e` would otherwise
  // propagate the underlying command's status.
  assert.match(script, /env_fail\(\)[\s\S]*?exit 2/);
});

test('Tauri config and Gradle forward the isolated Tauri cache contract to native bundle builds', async () => {
  const gradle = await readFile(join(ROOT, 'build.gradle.kts'), 'utf8');
  // Tauri 2.6.0's NSIS bundler reads NSIS_PATH (not TAURI_NSIS_PATH); WiX has
  // no env override and is located via the local-tools directory. Gradle
  // forwards NSIS_PATH from the provisioned cache as a belt-and-suspenders
  // override; the authoritative mechanism is useLocalToolsDir in tauri.conf.
  // The Gradle bridgeBundle doFirst must not environment()-forward the inert
  // TAURI_WIX_PATH / TAURI_NSIS_PATH names (a comment may reference them for
  // historical context, but the code path must use NSIS_PATH).
  assert.match(gradle, /NSIS_PATH/);
  const bridgeBundleBlock = gradle.slice(gradle.indexOf('val bridgeBundle'), gradle.indexOf('val bridgeManifests'));
  // The doFirst env-forwarding listOf(...) must use NSIS_PATH only; the inert
  // TAURI_WIX_PATH / TAURI_NSIS_PATH names must not appear in the active
  // environment() forwarding listOf(...) (a comment may reference them).
  assert.match(bridgeBundleBlock, /listOf\("NSIS_PATH"\)\.forEach/);
  assert.doesNotMatch(bridgeBundleBlock, /listOf\("TAURI_WIX_PATH",\s*"TAURI_NSIS_PATH"\)/);
  assert.doesNotMatch(bridgeBundleBlock, /listOf\("TAURI_NSIS_PATH",\s*"TAURI_WIX_PATH"\)/);
  const tauriConf = JSON.parse(await readFile(join(ROOT, 'native-bridge', 'src-tauri', 'tauri.conf.json'), 'utf8'));
  assert.equal(tauriConf.bundle.useLocalToolsDir, true, 'tauri.conf.json bundle.useLocalToolsDir must be true so Tauri reads the provisioned isolated cache');
});
