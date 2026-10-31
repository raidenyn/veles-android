$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Capture = Join-Path $Root 'capture'
$Adb = @((Get-Process -Name adb).Path | Sort-Object -Unique)[0]
$Sdk = Split-Path (Split-Path $Adb)
$Jar = Get-ChildItem (Join-Path $Sdk 'build-tools') -Filter apksigner.jar -Recurse | Sort-Object FullName -Descending | Select-Object -First 1
$Java = (Get-Command java).Source
$Fingerprint = '2081d17da5ec1a400e710720eba4a16939db774da54cdce936be30f4a848506e'
$MainHash = '0e32c572bbf1d72249a888b7401cce91097b9a459deac6352a2583019157649b'
$TestHash = '5a8e9e30e798e33c7577dccf542dfbbd2ba4058a8b4dc8f2b82c31ef17bd1b41'
foreach ($Serial in @('2be02e41','864a3db0')) {
    $Path = ((& $Adb -s $Serial shell pm path me.nagaev.veles.otp23) -replace '^package:', '').Trim()
    $Backup = Join-Path $Capture "installed-$Serial-main.apk"
    & $Adb -s $Serial pull $Path $Backup
    if ($LASTEXITCODE -ne 0 -or (Get-FileHash $Backup).Hash.ToLowerInvariant() -ne '063ac7ea719b8b2625690a5be5a9d57a7b3a24bf64242c34a1772b510f677a5e') { throw 'Unexpected installed main fixture' }
    $TestPath = ((& $Adb -s $Serial shell pm path me.nagaev.veles.otp23.test) -replace '^package:', '').Trim()
    $TestBackup = Join-Path $Capture "before-scan-$Serial-test.apk"
    & $Adb -s $Serial pull $TestPath $TestBackup
    if ($LASTEXITCODE -ne 0 -or (Get-FileHash $TestBackup).Hash.ToLowerInvariant() -ne '8fe3a37a53df7478b1875642fda37dbc57c5b1712e779f692a77d568c2255550') { throw 'Unexpected installed first test fixture' }
}
& python (Join-Path $Root 'transfer-main\apply-apk-delta.py') (Join-Path $Capture 'installed-2be02e41-main.apk') (Join-Path $Capture 'a01-scan-main.apk')
if ($LASTEXITCODE -ne 0) { throw 'Main reconstruction failed' }
& python (Join-Path $Root 'transfer-test\apply-apk-delta.py') (Join-Path $Capture 'installed-2be02e41-test.apk') (Join-Path $Capture 'a01-scan-test.apk')
if ($LASTEXITCODE -ne 0) { throw 'Test reconstruction failed' }
$Records = @()
foreach ($Serial in @('2be02e41','864a3db0')) {
    foreach ($Kind in @('main','test')) {
        $New = Join-Path $Capture "a01-scan-$Kind.apk"
        $Old = if ($Kind -eq 'main') { Join-Path $Capture "installed-$Serial-main.apk" } else { Join-Path $Capture "before-scan-$Serial-test.apk" }
        $OldCert = (& $Java -jar $Jar.FullName verify --print-certs $Old) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $OldCert -notmatch "certificate SHA-256 digest: $Fingerprint") { throw 'Installed certificate mismatch' }
        $NewCert = (& $Java -jar $Jar.FullName verify --print-certs $New) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $NewCert -notmatch "certificate SHA-256 digest: $Fingerprint") { throw 'New certificate mismatch' }
        $Hash = (Get-FileHash $New).Hash.ToLowerInvariant()
        if (($Kind -eq 'main' -and $Hash -ne $MainHash) -or ($Kind -eq 'test' -and $Hash -ne $TestHash)) { throw 'Wrong reconstructed APK' }
        $Install = (& $Adb -s $Serial install -r -t $New) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $Install -notmatch 'Success') { throw 'Same-key fixture update failed' }
        $Records += [ordered]@{ serial=$Serial; kind=$Kind; sha256=$Hash; signer_sha256=$Fingerprint;
            installed_certificate=$OldCert; new_certificate=$NewCert; install_output=$Install;
            scope='Only the existing isolated .otp23 fixture and test package; same key and update -r, no uninstall/data clearing' }
    }
}
$Json = $Records | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $Capture 'a01-scan-install-record.json'), $Json)
$Json
