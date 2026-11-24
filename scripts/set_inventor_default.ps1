# Associate 3D CAD model extensions directly with Autodesk Inventor 2025 (Inventor.PartDocument)
$inventorExe = "C:\Program Files\Autodesk\Inventor 2025\Bin\Inventor.exe"
$progId = "Inventor.PartDocument"

if (-not (Test-Path $inventorExe)) {
    Write-Error "Inventor 2025 not found at $inventorExe"
    exit 1
}

$extensions = @('.x_t', '.step', '.stp', '.stl', '.iges', '.igs')

foreach ($ext in $extensions) {
    Write-Host "Setting default application for $ext -> $progId ($inventorExe)..."
    
    # 1. HKCU:\Software\Classes\<ext>
    $extKey = "HKCU:\Software\Classes\$ext"
    New-Item -Path $extKey -Force | Out-Null
    Set-ItemProperty -Path $extKey -Name "(default)" -Value $progId
    
    # 2. HKCR\<ext>
    try {
        Set-ItemProperty -Path "Registry::HKEY_CLASSES_ROOT\$ext" -Name "(default)" -Value $progId -ErrorAction SilentlyContinue
    } catch {}
    
    # 3. Explorer FileExts
    $fileExtsKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$ext"
    New-Item -Path "$fileExtsKey\OpenWithList" -Force | Out-Null
    Set-ItemProperty -Path "$fileExtsKey\OpenWithList" -Name "a" -Value "Inventor.exe"
    Set-ItemProperty -Path "$fileExtsKey\OpenWithList" -Name "MRUList" -Value "a"
    
    New-Item -Path "$fileExtsKey\OpenWithProgids" -Force | Out-Null
    Set-ItemProperty -Path "$fileExtsKey\OpenWithProgids" -Name $progId -Value ([byte[]]@()) -Type Binary
}

# 4. Notify Windows Shell of Association Changes
try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class ShellNotify {
    [DllImport("shell32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern void SHChangeNotify(uint wEventId, uint uFlags, IntPtr dwItem1, IntPtr dwItem2);
}
"@
    # SHCNE_ASSOCCHANGED = 0x08000000, SHCNF_IDLIST = 0x0000
    [ShellNotify]::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)
    Write-Host "Sent SHCNE_ASSOCCHANGED shell notification."
} catch {
    Write-Warning "Shell notification skipped: $_"
}

Write-Host "=== Successfully set Autodesk Inventor 2025 as the default open app for CAD models (.x_t, .step, .stl, .iges) ==="
