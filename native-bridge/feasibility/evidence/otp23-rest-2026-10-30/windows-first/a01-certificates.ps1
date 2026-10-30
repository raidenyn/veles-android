$ErrorActionPreference = 'Stop'
$Adb = @((Get-Process -Name adb).Path | Sort-Object -Unique)[0]
$Sdk = Split-Path (Split-Path $Adb)
$Jar = Get-ChildItem (Join-Path $Sdk 'build-tools') -Filter apksigner.jar -Recurse | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $Jar) { throw 'No existing Android SDK apksigner.jar' }
$Java = (Get-Command java -ErrorAction SilentlyContinue).Source
if (-not $Java) { $Java = 'C:\Program Files\Android\Android Studio\jbr\bin\java.exe' }
if (-not (Test-Path $Java)) { throw 'No existing Java runtime for certificate readback' }
foreach ($Name in @('installed-2be02e41-test.apk','installed-864a3db0-test.apk','a01-new-test.apk')) {
    $File = Join-Path $PSScriptRoot "capture\$Name"
    if (Test-Path $File) {
        "APK=$Name"
        & $Java -jar $Jar.FullName verify --print-certs $File
        if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed' }
    }
}
