$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Capture = Join-Path $Root 'capture'
$Apk = Join-Path $Capture 'a01-final-test.apk'
$TargetHash = '8fe3a37a53df7478b1875642fda37dbc57c5b1712e779f692a77d568c2255550'
if ((Get-FileHash $Apk).Hash.ToLowerInvariant() -ne $TargetHash) { throw 'Wrong candidate APK' }
$Adb = @((Get-Process -Name adb).Path | Sort-Object -Unique)[0]
$Sdk = Split-Path (Split-Path $Adb)
$Jar = Get-ChildItem (Join-Path $Sdk 'build-tools') -Filter apksigner.jar -Recurse | Sort-Object FullName -Descending | Select-Object -First 1
$Aapt = Get-ChildItem (Join-Path $Sdk 'build-tools') -Filter aapt.exe -Recurse | Sort-Object FullName -Descending | Select-Object -First 1
$Java = (Get-Command java).Source
$Badging = (& $Aapt.FullName dump badging $Apk) -join "`n"
if ($Badging -notmatch "package: name='me.nagaev.veles.otp23.test'") { throw 'Wrong test application ID' }
$Manifest = (& $Aapt.FullName dump xmltree $Apk AndroidManifest.xml) -join "`n"
if ($Manifest -notmatch 'targetPackage[^\r\n]*"me.nagaev.veles.otp23"') { throw 'Wrong instrumentation target package' }
$NewCert = (& $Java -jar $Jar.FullName verify --print-certs $Apk) -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'Candidate signature invalid' }
$Fingerprint = '2081d17da5ec1a400e710720eba4a16939db774da54cdce936be30f4a848506e'
if ($NewCert -notmatch "certificate SHA-256 digest: $Fingerprint") { throw 'Unapproved new signer' }
$Records = @()
foreach ($Serial in @('2be02e41','864a3db0')) {
    $CurrentPath = ((& $Adb -s $Serial shell pm path me.nagaev.veles.otp23.test) -replace '^package:','').Trim()
    $CurrentHash = (& $Adb -s $Serial shell sha256sum $CurrentPath) -join ''
    if ($CurrentHash -notmatch '^8ca89381dcc8573aba500e65ba4746e698ebdfa0bf3793345ddc4645712ede1c ') { throw 'Installed test APK changed since pull; stop' }
    $Old = Join-Path $Capture "installed-$Serial-test.apk"
    $OldCert = (& $Java -jar $Jar.FullName verify --print-certs $Old) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $OldCert -notmatch "certificate SHA-256 digest: $Fingerprint") { throw 'Installed signer mismatch' }
    $MainPath = ((& $Adb -s $Serial shell pm path me.nagaev.veles.otp23) -replace '^package:','').Trim()
    $MainBefore = (& $Adb -s $Serial shell sha256sum $MainPath) -join ''
    if ($MainBefore -notmatch '^063ac7ea719b8b2625690a5be5a9d57a7b3a24bf64242c34a1772b510f677a5e ') { throw 'Unexpected target fixture APK' }
    $Install = (& $Adb -s $Serial install -r -t $Apk) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $Install -notmatch 'Success') { throw 'Test-only package update failed' }
    $MainAfter = (& $Adb -s $Serial shell sha256sum $MainPath) -join ''
    if ($MainBefore -ne $MainAfter) { throw 'Main fixture changed' }
    $Records += [ordered]@{ serial=$Serial; old_test_sha256='8ca89381dcc8573aba500e65ba4746e698ebdfa0bf3793345ddc4645712ede1c';
        new_test_sha256=$TargetHash; signer_sha256=$Fingerprint; installed_certificate_readback=$OldCert;
        new_certificate_readback=$NewCert; target_apk_unchanged=$true; install_output=$Install }
}
$Json = $Records | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $Capture 'a01-install-record.json'), $Json)
$Json
