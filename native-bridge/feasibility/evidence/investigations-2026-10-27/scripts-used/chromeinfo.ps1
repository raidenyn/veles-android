$paths = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
foreach ($p in $paths) { "{0} exists={1}" -f $p, (Test-Path $p); if (Test-Path $p) { (Get-Item $p).VersionInfo.ProductVersion } }
"--- chrome processes (count, user data dirs)"
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -notmatch '--type=' } | ForEach-Object { $_.ProcessId.ToString() + ' ' + $_.CommandLine.Substring(0, [Math]::Min(200, $_.CommandLine.Length)) }
"--- lock state"
(Get-Process LogonUI -ErrorAction SilentlyContinue | Measure-Object).Count
