$ErrorActionPreference = 'Stop'
$AdbPaths = @((Get-Process -Name adb).Path | Sort-Object -Unique)
if ($AdbPaths.Count -ne 1) { throw 'Cannot identify the existing adb server executable' }
$Adb = $AdbPaths[0]
foreach ($Serial in @('2be02e41','864a3db0')) {
    $Raw = & $Adb -s $Serial shell pm path me.nagaev.veles.otp23.test
    if ($LASTEXITCODE -ne 0) { throw 'Installed package lookup failed' }
    $Path = ($Raw -replace '^package:', '').Trim()
    $Out = Join-Path $PSScriptRoot "capture\installed-$Serial-test.apk"
    & $Adb -s $Serial pull $Path $Out
    if ($LASTEXITCODE -ne 0) { throw 'Installed APK pull failed' }
    Get-FileHash -Algorithm SHA256 -LiteralPath $Out
}
