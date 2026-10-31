$out = 'C:\Users\yuriy\otp23-probe\desk.txt'
Add-Type @'
using System; using System.Runtime.InteropServices;
public class D { [DllImport("user32.dll")] public static extern IntPtr OpenInputDesktop(uint f, bool i, uint a);
  [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }
'@
$h = [D]::OpenInputDesktop(0, $false, 0x0100)
"input_desktop_accessible=" + ($h -ne [IntPtr]::Zero) | Out-File $out
if ($h -ne [IntPtr]::Zero) { [void][D]::CloseDesktop($h) }
"foreground_window_handle=" + [D]::GetForegroundWindow() | Out-File $out -Append
"session_id=" + (Get-Process -Id $PID).SessionId | Out-File $out -Append
"whoami=" + (whoami) | Out-File $out -Append
"time=" + (Get-Date -Format o) | Out-File $out -Append
"integrity:" | Out-File $out -Append
whoami /groups | Select-String "Mandatory Label" | Out-File $out -Append
"done" | Out-File 'C:\Users\yuriy\otp23-probe\desk.done'
