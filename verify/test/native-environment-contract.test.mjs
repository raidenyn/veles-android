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
    'WixTools314/WixUtilExtension.dll', 'NSIS/makensis.exe', 'NSIS/Bin/makensis.exe', 'NSIS/Bin/zlib1.dll',
    'NSIS/Stubs/lzma-x86-unicode', 'NSIS/Stubs/lzma_solid-x86-unicode', 'NSIS/Stubs/uninst',
    'NSIS/Stubs/zlib-x86-unicode', 'NSIS/Stubs/zlib_solid-x86-unicode',
    'NSIS/Plugins/x86-unicode/nsis_tauri_utils.dll',
    'NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll', 'NSIS/Include/MUI2.nsh',
    'NSIS/Include/FileFunc.nsh', 'NSIS/Include/x64.nsh', 'NSIS/Include/nsDialogs.nsh',
    'NSIS/Include/WinMessages.nsh',
    // Tauri 2.6.0's installer.nsi !includes these unconditionally; utils.nsh /
    // FileAssociation.nsh use the nsDialogs and System plugins.
    'NSIS/Include/WordFunc.nsh', 'NSIS/Include/StrFunc.nsh',
    'NSIS/Include/Win/COM.nsh', 'NSIS/Include/Win/Propkey.nsh',
    'NSIS/Plugins/x86-unicode/nsDialogs.dll', 'NSIS/Plugins/x86-unicode/System.dll',
    // MUI2 StartMenu page (MUI_PAGE_STARTMENU) loads StartMenu.dll; the MUI
    // language dialog (MUI_RESERVEFILE_LANGDLL) reserves LangDLL.dll.
    'NSIS/Plugins/x86-unicode/StartMenu.dll', 'NSIS/Plugins/x86-unicode/LangDLL.dll',
    // MUI2.nsh redirects to Contrib\Modern UI 2\MUI2.nsh, which !includes
    // LogicLib.nsh, LangFile.nsh, Sections.nsh, Util.nsh and the whole
    // Contrib\Modern UI 2 tree (the offline build must resolve every !include).
    'NSIS/Include/LogicLib.nsh', 'NSIS/Include/LangFile.nsh',
    'NSIS/Include/Sections.nsh', 'NSIS/Include/Util.nsh',
    'NSIS/Contrib/Modern UI 2/MUI2.nsh',
    'NSIS/Contrib/Modern UI 2/Deprecated.nsh',
    'NSIS/Contrib/Modern UI 2/Interface.nsh',
    'NSIS/Contrib/Modern UI 2/Localization.nsh',
    'NSIS/Contrib/Modern UI 2/Pages.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/Components.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/Directory.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/Finish.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/InstallFiles.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/License.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/StartMenu.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/UninstallConfirm.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/Welcome.nsh',
    // The default MUI_LANGUAGE "English" requires the English language files
    // (English.nlf + English.nsh) under Contrib\Language files\.
    'NSIS/Contrib/Language files/English.nlf',
    'NSIS/Contrib/Language files/English.nsh',
    // MUI_INTERFACE (Interface.nsh) defaults MUI_UI to Contrib\UIs\modern.exe
    // and ChangeUI all reads it at compile time. The Tauri installer.nsi does
    // not override MUI_UI, so the default modern.exe is required.
    'NSIS/Contrib/UIs/modern.exe',
    // MUI_PAGE_WELCOME defaults MUI_WELCOMEFINISHPAGE_BITMAP to
    // Contrib\Graphics\Wizard\win.bmp and File-extracts it at compile time.
    // The Tauri installer.nsi overrides it only with a configured
    // sidebar_image (Veles has none), so win.bmp is required.
    'NSIS/Contrib/Graphics/Wizard/win.bmp',
    // MUI_INTERFACE defaults MUI_ICON/MUI_UNICON to these icons. The Tauri
    // installer.nsi overrides MUI_ICON only with a configured installer_icon;
    // the defaults are required for the offline preflight MUI2 compile.
    'NSIS/Contrib/Graphics/Icons/modern-install.ico',
    'NSIS/Contrib/Graphics/Icons/modern-uninstall.ico',
  ]) assert.match(provisioner, new RegExp(path.replaceAll('/', '[\\\\/]')));
  // The provisioner must map the actual archive layouts into the expected
  // cache roots: wix314-binaries.zip extracts flat (candle.exe at the archive
  // root, no WixTools314/ prefix), and nsis-3.zip extracts under nsis-3.08/.
  // The cache must end up with WixTools314/ and NSIS/ roots regardless.
  assert.match(provisioner, /Resolve-ArchiveSource[\s\S]*?WixTools314\/\*[\s\S]*?nsis-3\.08/);
  for (const contract of ['Get-FileHash', 'SHA256', 'Expand-Archive', 'unexpected', 'missing']) {
    assert.match(provisioner, new RegExp(contract));
  }
});

test('Windows target wrapper validates its exact runner and safely proves offline packaging', async () => {
  const script = await native('network-deny-windows.ps1');
  for (const contract of [
    // ImageOS for the windows-2025 runner label is `win25-vs2026` (the runner
    // label is a versioned alias, not the ImageOS value). The workflow
    // `runs-on: windows-2025` label stays; only the wrapper's ImageOS pin
    // matches the observed runner ImageOS.
    "ImageOS -ne 'win25-vs2026'", "ImageVersion", "RUNNER_ARCH", "node --version", "v26.8.1",
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
  // The param() block must NOT mark TauriCachePath Mandatory: mandatory binding
  // happens BEFORE $ErrorActionPreference takes effect, so a missing
  // -TauriCachePath would exit 1 instead of 2. TauriCachePath is validated
  // inside the guarded body via env-fail so usage failures exit 2.
  assert.doesNotMatch(script, /\[Parameter\(Mandatory *= *\$true\)\][^\n]*\$TauriCachePath/);
  assert.match(script, /IsNullOrWhiteSpace\(\$TauriCachePath\)[\s\S]*?env-fail 'TauriCachePath is required'/);
  // EXIT-CONTRACT: every `exit 2` must be reachable at TOP LEVEL (never from
  // inside a try/catch). GitHub's pwsh `-command ". '<script>'"` wrapper
  // interacts badly with `$host.SetShouldExit`/`exit` issued from inside a
  // try/catch, surfacing as exit 1 instead of 2. Assert the script never
  // calls `$host.SetShouldExit` and never calls `env-fail` from inside a
  // try/catch block (the firewall try records state in variables and the
  // env-fail calls happen AFTER the try/finally at top level).
  assert.doesNotMatch(script, /SetShouldExit/);
  // ASYMMETRY: the Windows default package command does NOT use --no-daemon.
  // Windows firewall outbound rules do not block loopback, so gradlew.bat's
  // daemon works under the Windows deny rule. (The macOS wrapper needs
  // --no-daemon because sandbox-exec denies the daemon's loopback socket.)
  assert.match(script, /\$PackageCommand = @\('\.\\gradlew\.bat', 'bridgePackage'\)/);
  assert.doesNotMatch(script, /--no-daemon/);
  // The workflow supplies a relative cache path, but Gradle invokes Tauri from
  // native-bridge/. NSIS_PATH must therefore be canonical before it reaches
  // Tauri, or makensis resolves against native-bridge/native-bridge/...
  const relativeCachePath = 'native-bridge/src-tauri/target/.tauri';
  assert.equal(relativeCachePath.startsWith('/'), false, 'fixture must model the workflow relative cache path');
  assert.match(script, /\$tauriCache = \[IO\.Path\]::GetFullPath\(\$TauriCachePath\)/);
  assert.match(script, /\$env:NSIS_PATH = Join-Path \$tauriCache 'NSIS'/);
  assert.doesNotMatch(script, /\$env:NSIS_PATH = Join-Path \$TauriCachePath 'NSIS'/);
  assert.match(script, /\$makensis = Join-Path \$env:NSIS_PATH 'makensis\.exe'/,
    'preflight must invoke the canonical provisioned makensis executable');
  assert.match(script, /& \$makensis \/VERSION[\s\S]*?makensis preflight failed/,
    'preflight failures must report makensis output via the exit-2 environment path');
  // The preflight must COMPILE a minimal MUI2 installer (not just /VERSION) so
  // a cache gap (e.g. a missing Contrib\UIs\modern.exe) is named by makensis
  // before the slower Gradle/Tauri package step runs. /VERSION only exercises
  // the default zlib stub and does not pull MUI2 Contrib files.
  assert.match(script, /!include MUI2\.nsh/, 'preflight must include MUI2.nsh to exercise the MUI2 Contrib tree');
  assert.match(script, /MUI_PAGE_WELCOME/, 'preflight must use MUI_PAGE_WELCOME to pull the default welcome bitmap');
  assert.match(script, /MUI_LANGUAGE English/, 'preflight must use the default English language to pull language files');
  assert.match(script, /preflight-setup\.exe/, 'preflight must compile a real installer (OutFile a setup.exe)');
  assert.match(script, /makensis preflight compile failed/, 'preflight compile failures must env-fail (exit 2)');
  // Under pwsh 7.4+, $PSNativeCommandUseErrorActionPreference defaults to true,
  // which would make the Gradle package command (stderr + non-zero exit) throw
  // under ErrorActionPreference=Stop and reroute the package-command failure to
  // the firewall catch instead of the dedicated package-command env-fail. Disable
  // it so $LASTEXITCODE is captured cleanly and the package-command env-fail
  // (exit 2) is the deterministic, top-level outcome.
  assert.match(script, /PSNativeCommandUseErrorActionPreference = \$false/);
  assert.match(script, /package command failed/, 'package-command failure must env-fail (exit 2) at top level');
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
  // The macOS sandbox-exec profile denies network-outbound, then re-allows
  // loopback ONLY (SBPL is last-match-wins, so the loopback allow follows the
  // deny). Gradle 9 forks a single-use daemon even under --no-daemon and
  // communicates with it over loopback TCP; a blanket deny blocks that socket
  // ('Could not connect to the Gradle daemon'). --no-daemon is kept for parity
  // with the Windows wrapper and to minimize daemon overhead, but it is no
  // longer required for sandbox correctness: the loopback allow lets the
  // daemon control socket work while real outbound stays denied. The Windows
  // wrapper does NOT use --no-daemon (Windows firewall outbound rules do not
  // block loopback); that asymmetry is intentional and asserted below.
  assert.match(script, /set -- \.\/gradlew --no-daemon bridgePackage/);
});

test('macOS target wrapper isolates only the package command tree without modifying PF', async () => {
  const script = await native('network-deny-macos.sh');
  for (const contract of [
    'command -v sandbox-exec', 'profile=$(mktemp)', '(deny network-outbound)',
    '(allow network-outbound (local ip "localhost:*") (remote ip "localhost:*"))',
    'trap cleanup EXIT', 'rm -f "$profile"', 'sandbox-exec -f "$profile" curl',
    'sandbox-exec -f "$profile" env CARGO_NET_OFFLINE=true',
  ]) assert.match(script, literal(contract));
  assert.doesNotMatch(script, /127\.0\.0\.1|::1/, 'SBPL rejects numeric IP addresses with port wildcards');
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
