Write-Host "=== CPU Hardware & Topology Audit ==="
Get-CimInstance Win32_Processor | Select-Object DeviceID, Name, NumberOfCores, NumberOfLogicalProcessors, LoadPercentage | Format-Table -AutoSize

Write-Host "=== Memory (RAM) Audit ==="
$os = Get-CimInstance Win32_OperatingSystem
$totalRam = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
$freeRam = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
$ramPass = if ($freeRam -ge 30.0) { "PASS (>= 30GB)" } else { "WARN (< 30GB)" }
[PSCustomObject]@{
    Total_RAM_GB = $totalRam
    Free_RAM_GB  = $freeRam
    Threshold_GB = 30.0
    Verdict      = $ramPass
} | Format-Table -AutoSize

Write-Host "=== Disk E: Space Audit ==="
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='E:'"
$totalDisk = [math]::Round($disk.Size / 1GB, 2)
$freeDisk = [math]::Round($disk.FreeSpace / 1GB, 2)
$diskPass = if ($freeDisk -ge 80.0) { "PASS (>= 80GB)" } else { "WARN (< 80GB)" }
[PSCustomObject]@{
    Drive        = "E:"
    Total_GB     = $totalDisk
    Free_GB      = $freeDisk
    Threshold_GB = 80.0
    Verdict      = $diskPass
} | Format-Table -AutoSize

Write-Host "=== COMSOL Solver Processes (comsolbatch.exe) ==="
Get-Process comsolbatch -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, CPU, @{Name='WorkingSet_GB';Expression={[math]::Round($_.WorkingSet64/1GB, 2)}} | Format-Table -AutoSize
