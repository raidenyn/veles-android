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
  ]) assert.match(provisioner, new RegExp(path.replaceAll('/', '[\\\\/]')));
  for (const contract of ['Get-FileHash', 'SHA256', 'Expand-Archive', 'unexpected', 'missing']) {
    assert.match(provisioner, new RegExp(contract));
  }
});

test('Windows target wrapper validates its exact runner and safely proves offline packaging', async () => {
  const script = await native('network-deny-windows.ps1');
  for (const contract of [
    "ImageOS -ne 'windows-2025'", "ImageVersion", "RUNNER_ARCH", "node --version", "v26.8.1",
    "npm --version", "11.19.0", 'https://github.com/', 'Invoke-WebRequest', 'New-NetFirewallRule',
    'Get-NetFirewallRule', 'CARGO_NET_OFFLINE', 'finally', 'Remove-NetFirewallRule', 'bridgePackage',
  ]) assert.match(script, literal(contract));
  assert.match(script, /bridgeBuild/);
  assert.ok(script.indexOf('bridgeBuild') < script.indexOf('Invoke-WebRequest'));
  assert.ok(script.indexOf('Invoke-WebRequest') < script.indexOf('New-NetFirewallRule'));
  assert.ok(script.indexOf('New-NetFirewallRule') < script.lastIndexOf('Invoke-WebRequest'));
});

test('macOS target wrapper validates its exact runner and safely proves offline packaging', async () => {
  const script = await native('network-deny-macos.sh');
  for (const contract of [
    'macos-26', 'ImageVersion', 'RUNNER_ARCH', 'node --version', 'v26.8.1', 'npm --version', '11.19.0',
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
});

test('Gradle forwards only an isolated Tauri cache contract to native bundle builds', async () => {
  const gradle = await readFile(join(ROOT, 'build.gradle.kts'), 'utf8');
  for (const contract of ['TAURI_WIX_PATH', 'TAURI_NSIS_PATH']) assert.match(gradle, new RegExp(contract));
});
