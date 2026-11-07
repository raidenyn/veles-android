<#
.SYNOPSIS
Embed the policy carrier document into the UNSIGNED Windows engine image (OTP-25 Task 14).

.DESCRIPTION
Writes -Carrier (carrier.bin from assemble_unsigned.py, which ends in the zeroed 32-byte sealed-digest
slot Task 13's release_gate.py fills in place) into -Image as the RT_RCDATA resource
VELES_POLICY_CARRIER the engine reads (src/platform/windows.rs), then reads the image back and
requires exactly one carrier document equal to the one embedded. Nothing is signed or downloaded.

STATUS: NOT RUN on the development host (no Windows runner, no pwsh). The resource is written with the
documented Win32 UpdateResourceW; whether the pinned toolchain's output stays byte-identical across two
clean builds after this step is Windows-runner evidence (the producer's A/B comparison decides).

.PARAMETER Image
The unsigned carrier.exe to modify in place.

.PARAMETER Carrier
The carrier document to embed.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$Image,
    [Parameter(Mandatory = $true)] [string]$Carrier
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7 -or -not $IsWindows) {
    throw 'embed-carrier.ps1 is Windows-only and requires PowerShell 7+ (pwsh)'
}
foreach ($path in @($Image, $Carrier)) {
    if (-not [IO.Path]::IsPathRooted($path)) { throw "absolute path required: $path" }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "missing file: $path" }
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class VelesResource {
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr BeginUpdateResourceW(string fileName, bool deleteExisting);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool UpdateResourceW(IntPtr update, IntPtr type, string name, ushort language, byte[] data, uint size);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool EndUpdateResourceW(IntPtr update, bool discard);
}
'@

$carrierBytes = [IO.File]::ReadAllBytes($Carrier)
if ($carrierBytes.Length -lt 64) { throw 'the carrier document is implausibly small' }
$RT_RCDATA = [IntPtr]10
$handle = [VelesResource]::BeginUpdateResourceW($Image, $false)
if ($handle -eq [IntPtr]::Zero) { throw "BeginUpdateResourceW failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
$written = [VelesResource]::UpdateResourceW($handle, $RT_RCDATA, 'VELES_POLICY_CARRIER', 0, $carrierBytes, [uint32]$carrierBytes.Length)
$error1 = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
if (-not $written) {
    [void][VelesResource]::EndUpdateResourceW($handle, $true)
    throw "UpdateResourceW failed: $error1"
}
if (-not [VelesResource]::EndUpdateResourceW($handle, $false)) {
    throw "EndUpdateResourceW failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
}

# Read the image back. The engine's own code also contains the 8-byte magic as a constant, so a
# carrier DOCUMENT is recognised by magic plus the fixed version/flags/header-length/offset words
# (version 1, flags 1, header length 32, descriptor offset 32). Exactly one such document must exist
# and it must equal the one embedded.
$image = [IO.File]::ReadAllBytes($Image)
$prefix = [byte[]]@(0x56,0x45,0x4C,0x45,0x53,0x50,0x43,0x31, 0x01,0x00, 0x01,0x00, 0x20,0x00,0x00,0x00, 0x20,0x00,0x00,0x00,0x00,0x00,0x00,0x00)
$found = New-Object System.Collections.Generic.List[int]
for ($i = 0; $i -le $image.Length - $prefix.Length; $i++) {
    if ($image[$i] -ne $prefix[0]) { continue }
    $match = $true
    for ($j = 1; $j -lt $prefix.Length; $j++) {
        if ($image[$i + $j] -ne $prefix[$j]) { $match = $false; break }
    }
    if ($match) { $found.Add($i) }
}
if ($found.Count -ne 1) { throw "expected exactly one embedded carrier document, found $($found.Count)" }
$start = $found[0]
for ($i = 0; $i -lt $carrierBytes.Length; $i++) {
    if ($image[$start + $i] -ne $carrierBytes[$i]) { throw 'the embedded carrier differs from the requested document' }
}
