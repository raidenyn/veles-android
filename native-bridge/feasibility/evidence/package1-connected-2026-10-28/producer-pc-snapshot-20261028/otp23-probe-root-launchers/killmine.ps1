# Terminate ONLY Chrome processes whose command line carries one of OUR probe profile paths (never any other Chrome).
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -match 'otp23-probe' -or $_.CommandLine -match 'veles-otp23-profile' } |
  ForEach-Object { "killing " + $_.ProcessId; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
