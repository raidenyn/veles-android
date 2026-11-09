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
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr LoadLibraryExW(string fileName, IntPtr file, uint flags);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr FindResourceW(IntPtr module, string name, IntPtr type);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint SizeofResource(IntPtr module, IntPtr resource);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr LoadResource(IntPtr module, IntPtr resource);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr LockResource(IntPtr data);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool FreeLibrary(IntPtr module);
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
# Read the resource back through the PE resource table. Scanning the file for the
# magic is a heuristic (the engine also carries the magic as a code constant); the
# named resource is the authoritative object.
$module = [VelesResource]::LoadLibraryExW($Image, [IntPtr]::Zero, 0x2)  # LOAD_LIBRARY_AS_DATAFILE
if ($module -eq [IntPtr]::Zero) { throw "LoadLibraryExW failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
try {
    $resource = [VelesResource]::FindResourceW($module, 'VELES_POLICY_CARRIER', [IntPtr]10)
    if ($resource -eq [IntPtr]::Zero) { throw "the VELES_POLICY_CARRIER resource is missing from $Image" }
    $size = [VelesResource]::SizeofResource($module, $resource)
    if ($size -ne $carrierBytes.Length) { throw "the embedded resource has $size bytes, expected $($carrierBytes.Length)" }
    $data = [VelesResource]::LoadResource($module, $resource)
    if ($data -eq [IntPtr]::Zero) { throw "LoadResource failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
    $pointer = [VelesResource]::LockResource($data)
    if ($pointer -eq [IntPtr]::Zero) { throw "LockResource failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
    $embedded = New-Object byte[] $size
    [Runtime.InteropServices.Marshal]::Copy($pointer, $embedded, 0, [int]$size)
} finally {
    [void][VelesResource]::FreeLibrary($module)
}
for ($i = 0; $i -lt $carrierBytes.Length; $i++) {
    if ($embedded[$i] -ne $carrierBytes[$i]) { throw "the embedded carrier differs from the requested document at byte $i" }
}
