$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Capture = Join-Path $Root 'capture'
$Adb = @((Get-Process -Name adb).Path | Sort-Object -Unique)[0]
$Sdk = Split-Path (Split-Path $Adb)
$Jar = Get-ChildItem (Join-Path $Sdk 'build-tools') -Filter apksigner.jar -Recurse | Sort-Object FullName -Descending | Select-Object -First 1
$Java = (Get-Command java).Source
$Fingerprint = '2081d17da5ec1a400e710720eba4a16939db774da54cdce936be30f4a848506e'
$Records = @()
foreach ($Serial in @('2be02e41','864a3db0')) {
    foreach ($Kind in @('main','test')) {
        $Package = if ($Kind -eq 'main') { 'me.nagaev.veles.otp23' } else { 'me.nagaev.veles.otp23.test' }
        $ExpectedCurrent = if ($Kind -eq 'main') { '0e32c572bbf1d72249a888b7401cce91097b9a459deac6352a2583019157649b' } else { '5a8e9e30e798e33c7577dccf542dfbbd2ba4058a8b4dc8f2b82c31ef17bd1b41' }
        $ExpectedOriginal = if ($Kind -eq 'main') { '063ac7ea719b8b2625690a5be5a9d57a7b3a24bf64242c34a1772b510f677a5e' } else { '8ca89381dcc8573aba500e65ba4746e698ebdfa0bf3793345ddc4645712ede1c' }
        $CurrentPath = ((& $Adb -s $Serial shell pm path $Package) -replace '^package:', '').Trim()
        $CurrentHash = (& $Adb -s $Serial shell sha256sum $CurrentPath) -join ''
        if ($CurrentHash -notmatch "^$ExpectedCurrent ") { throw 'Current APK changed; do not overwrite user work' }
        $Original = Join-Path $Capture "installed-$Serial-$Kind.apk"
        if ((Get-FileHash $Original).Hash.ToLowerInvariant() -ne $ExpectedOriginal) { throw 'Original backup hash mismatch' }
        $OriginalCert = (& $Java -jar $Jar.FullName verify --print-certs $Original) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $OriginalCert -notmatch "certificate SHA-256 digest: $Fingerprint") { throw 'Original signer mismatch' }
        $Current = Join-Path $Capture "a01-scan-$Kind.apk"
        $CurrentCert = (& $Java -jar $Jar.FullName verify --print-certs $Current) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $CurrentCert -notmatch "certificate SHA-256 digest: $Fingerprint") { throw 'Current signer mismatch' }
        $Install = (& $Adb -s $Serial install -r -t $Original) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $Install -notmatch 'Success') { throw 'Same-key restoration failed; no uninstall/data clearing fallback' }
        $FinalPath = ((& $Adb -s $Serial shell pm path $Package) -replace '^package:', '').Trim()
        $FinalHash = (& $Adb -s $Serial shell sha256sum $FinalPath) -join ''
        if ($FinalHash -notmatch "^$ExpectedOriginal ") { throw 'Restored APK hash mismatch' }
        $Records += [ordered]@{ serial=$Serial; package=$Package; restored_sha256=$ExpectedOriginal;
            signer_sha256=$Fingerprint; current_certificate=$CurrentCert; restored_certificate=$OriginalCert;
            install_output=$Install; method='same-key install -r -t; no uninstall or data clearing' }
    }
}
$Json = $Records | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $Capture 'a01-restoration.json'), $Json)
$Json
