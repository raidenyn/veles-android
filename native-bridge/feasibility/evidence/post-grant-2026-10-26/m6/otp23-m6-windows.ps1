$ErrorActionPreference = 'Stop'
$r = 'C:\Users\yuriy\repos\otp23-scenarios-3dbe3db\native-bridge\feasibility'
Set-Location $r
New-Item -ItemType Directory -Force evidence\m6-pair-20261027-a | Out-Null
$code=1
try {
    & .\scripts\register-host.ps1 -Flavour plain -ExePath (Join-Path $r 'target\release\otp23-host.exe')
    cmd /d /c "python scripts/scenario_matrix.py m6 --role windows --pair-run-id m6-pair-20261027-a --phone-label OnePlus6T --sole-advertiser --barrier-file evidence/m6-pair-20261027-a/release.json --barrier-timeout-s 300 --exchanges 100 --isolation-action only-known-6T-fixture-advertising-other-phone-stopped --out evidence/m6-pair-20261027-a/windows.json > evidence/m6-pair-20261027-a/windows-driver.log 2>&1"
    $code=$LASTEXITCODE
} finally {
    & .\scripts\unregister-host.ps1 -Flavour plain
    [IO.File]::WriteAllText((Join-Path $r 'evidence\m6-pair-20261027-a\windows-exit.txt'), "$code`n", (New-Object Text.UTF8Encoding($false)))
}
exit $code
