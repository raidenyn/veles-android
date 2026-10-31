$ErrorActionPreference = 'Stop'
$Device = 'USB\VID_0489&PID_E10A\8&FAD588D&0&16'
$Capture = Join-Path $PSScriptRoot 'capture'
$Keys = @('DEVPKEY_Device_ProblemCode','DEVPKEY_Device_DevNodeStatus','DEVPKEY_Device_ConfigFlags','DEVPKEY_Device_InstallState')
$Before = @(Get-PnpDeviceProperty -InstanceId $Device -KeyName $Keys | Select-Object KeyName,Data)
$Output = & pnputil.exe /enable-device $Device 2>&1
$Code = $LASTEXITCODE
$After = @(Get-PnpDeviceProperty -InstanceId $Device -KeyName $Keys | Select-Object KeyName,Data)
$Record = [ordered]@{ utc=[DateTime]::UtcNow.ToString('o'); before=$Before;
    enable_exit_code=$Code; enable_output=($Output -join "`n"); after=$After;
    device=(Get-PnpDevice -InstanceId $Device | Select-Object Status,FriendlyName) }
$Json = $Record | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText((Join-Path $Capture 'confirmed-enabled.json'), $Json)
$Json
