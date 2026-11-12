<#
.SYNOPSIS
    E:\ 3D 圆柱数据存储空间初始化与双路至强 (32C/64T, 128 GB RAM) 性能调优脚本。

.DESCRIPTION
    默认安全运行（仅创建缺失目录与高性能 .ini 配置，不删除或移动现存文件）。
    传入显式开关可执行系统级优化（电源计划、TEMP/TMP 重定向 E 盘、Defender 排除、COMSOL 偏好更新或历史文件归档）。

.EXAMPLE
    # 1. 安全查看当前状态并补全 E:\ 规划目录
    .\setup_e_workspace.ps1

    # 2. 执行完整性能优化（推荐以管理员身份运行）
    .\setup_e_workspace.ps1 -ApplyPowerPlan -RedirectTempEnv -AddDefenderExclusions -UpdateComsolPrefs

    # 3. 验证后迁移现存 E:\cylinder_3d 资产并删除重复压缩包与过期 .lock
    .\setup_e_workspace.ps1 -MigrateLegacyAssets -CleanupLegacyLockAndZip
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$ApplyPowerPlan,
    [switch]$RedirectTempEnv,
    [switch]$AddDefenderExclusions,
    [switch]$UpdateComsolPrefs,
    [switch]$LinkRepoJunctions,
    [switch]$MigrateLegacyAssets,
    [switch]$CleanupLegacyLockAndZip,
    [string]$ComsolRoot = "D:\Program Files\COMSOL\COMSOL64\Multiphysics",
    [string]$RepoRoot = "D:\HydroDisturb-Bench"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host ("=== " + $Title + " ===") -ForegroundColor Cyan
}

# 1. 创建完整 E:\ 规划目录树
Write-Section "1. 检查并初始化 E:\ 目录结构"
$dirs = @(
    "E:\comsol_scratch\tmp",
    "E:\comsol_scratch\recoveries",
    "E:\comsol_scratch\osgi_config",
    "E:\comsol_scratch\osgi_workspace",
    "E:\comsol_scratch\batch",
    "E:\comsol_scratch\logs",
    "E:\cfd_3d\00_templates_readonly",
    "E:\cfd_3d\01_calibration\c04_re126k",
    "E:\cfd_3d\01_calibration\probe_csv",
    "E:\cfd_3d\02_cases_mph",
    "E:\cfd_3d\03_disturbance_mph\C04_pulses",
    "E:\cfd_3d\04_datasets_npz\slices_2d_192x96",
    "E:\cfd_3d\04_datasets_npz\volumes_3d_192x96x64",
    "E:\cfd_3d\04_datasets_npz\manifests",
    "E:\cfd_3d\05_reports_viz",
    "E:\cfd_3d\90_staging",
    "E:\cfd_3d\99_archive"
)
1..9 | ForEach-Object { $dirs += ("E:\cfd_3d\02_cases_mph\C{0:D2}" -f $_) }
foreach ($d in $dirs) {
    if (-not (Test-Path -LiteralPath $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
        Write-Host "  [CREATED] $d"
    }
}

# 2. 生成/校验 COMSOL 高性能启动配置 comsolbatch_perf.ini
Write-Section "2. 校验 E:\comsol_scratch\comsolbatch_perf.ini"
$iniPath = "E:\comsol_scratch\comsolbatch_perf.ini"
$iniContent = @"
# High-performance COMSOL 6.4 batch settings tuned for Dual Xeon Platinum 8260 (32C/64T, 128 GB RAM)
batch
-startup
$ComsolRoot\plugins\org.eclipse.equinox.launcher_1.7.0.v20250519-0528.jar
--launcher.library
$ComsolRoot\bin\win64
-vm
$ComsolRoot\java\win64\jre\bin\server\jvm.dll
-vmargs
-Djdk.lang.Process.allowAmbiguousCommands=false
-Declipse.product=com.comsol.util.product
-Dosgi.bundles=org.eclipse.update.configurator@1:start,org.eclipse.equinox.common@2:start,org.apache.felix.scr@1:start,org.eclipse.equinox.event@3:start,org.eclipse.core.runtime@start
-Dosgi.bundles.defaultStartLevel=4
-Dosgi.startLevel=6
-Dosgi.clean=true
-Dorg.osgi.service.log.admin.loglevel=INFO
-Dosgi.locking=none
-Dosgi.configuration.area=E:/comsol_scratch/osgi_config/comsol_@process.id
-Dosgi.instance.area=E:/comsol_scratch/osgi_workspace/comsol_@process.id
-Dosgi.checkConfiguration=true
-Djava.io.tmpdir=E:/comsol_scratch/tmp
-Dcs.logoutput=file
-Dcs.logfileprefix=batch
-Dcs.uniqueworkspace=on
-Dcs.processuid=@process.id
-Xss4m
-Xms512m
-Xmx8g
-XX:MaxMetaspaceSize=1g
-Dlog4j2.formatMsgNoLookups=true
-Djavax.net.ssl.trustStoreType=Windows-ROOT
"@
Set-Content -LiteralPath $iniPath -Value $iniContent -Encoding ASCII
Write-Host "  [READY] $iniPath"

# 3. 硬件与盘符健康诊断
Write-Section "3. 硬件与磁盘实时状态"
Get-PSDrive C, D, E | Select-Object Name,
    @{N = "FreeGB"; E = { [math]::Round($_.Free / 1GB, 2) } },
    @{N = "UsedGB"; E = { [math]::Round($_.Used / 1GB, 2) } } | Format-Table -AutoSize
$activeScheme = (powercfg /getactivescheme)
Write-Host "  当前电源计划: $activeScheme"
$pf = Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue
if ($pf) {
    $pf | ForEach-Object { Write-Host ("  页面文件: {0} ({1} MB)" -f $_.Name, $_.AllocatedBaseSize) }
}
$cFreeGB = (Get-PSDrive C).Free / 1GB
if ($cFreeGB -lt 15) {
    Write-Warning ("C 盘剩余空间仅 {0:N2} GB！强烈建议开启 -RedirectTempEnv -UpdateComsolPrefs，并在系统高级设置中将 pagefile.sys 迁移到 E:\ (例如 16384~32768 MB)。" -f $cFreeGB)
}

# 4. 可选系统优化步骤
if ($ApplyPowerPlan) {
    Write-Section "4A. 切换 Windows 电源计划至 [高性能]"
    $highPerf = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
    powercfg /setactive $highPerf
    Write-Host "  已切换: $(powercfg /getactivescheme)" -ForegroundColor Green
}

if ($RedirectTempEnv) {
    Write-Section "4B. 重定向用户与系统 TEMP/TMP 至 E:\comsol_scratch\tmp"
    [Environment]::SetEnvironmentVariable("TEMP", "E:\comsol_scratch\tmp", "User")
    [Environment]::SetEnvironmentVariable("TMP", "E:\comsol_scratch\tmp", "User")
    try {
        [Environment]::SetEnvironmentVariable("TEMP", "E:\comsol_scratch\tmp", "Machine")
        [Environment]::SetEnvironmentVariable("TMP", "E:\comsol_scratch\tmp", "Machine")
    } catch {
        Write-Warning "修改 Machine 级环境变量需要管理员权限（User 级已更新）。"
    }
    Write-Host "  已设置 TEMP/TMP = E:\comsol_scratch\tmp（新开终端/COMSOL 进程生效）。" -ForegroundColor Green
}

if ($AddDefenderExclusions) {
    Write-Section "4C. 添加 Windows Defender 实时扫描排除项"
    foreach ($p in @("E:\comsol_scratch", "E:\cfd_3d", $RepoRoot, $ComsolRoot)) {
        Add-MpPreference -ExclusionPath $p
        Write-Host "  [ExclusionPath] $p"
    }
    foreach ($proc in @("comsolbatch.exe", "comsol.exe", "comsolmphserver.exe", "java.exe")) {
        Add-MpPreference -ExclusionProcess $proc
        Write-Host "  [ExclusionProcess] $proc"
    }
}

if ($UpdateComsolPrefs) {
    Write-Section "4D. 迁移 COMSOL 用户偏好恢复/批处理路径并解除电源节流"
    $prefsFile = "C:\Users\Administrator\.comsol\v64\comsol.prefs"
    if (Test-Path -LiteralPath $prefsFile) {
        $bak = "$prefsFile.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Copy-Item -LiteralPath $prefsFile -Destination $bak -Force
        Write-Host "  已备份原配置: $bak"
        $lines = Get-Content -LiteralPath $prefsFile -Encoding UTF8
        $updates = @{
            "tempfiles.recovery.recoverydir"         = "E\:\\comsol_scratch\\recoveries"
            "cluster.settings.batchdir"              = "E\:\\comsol_scratch\\batch"
            "cluster.settings.rundir"                = "E\:\\comsol_scratch\\batch"
            "cluster.settings.serverdir"             = "E\:\\comsol_scratch\\batch"
            "cluster.processor.numberofprocessors"   = "32"
            "cluster.processor.numberofprocessorsactive" = "on"
            "cluster.processor.numberofsockets"      = "2"
            "cluster.processor.numberofsocketsactive" = "on"
            "cluster.processor.alloc"                = "scalable"
            "cluster.processor.allocactive"          = "on"
            "cluster.processor.powerthrottling"      = "off"
            "cluster.processor.powerthrottlingactive" = "on"
        }
        $seen = @{}
        $outLines = foreach ($line in $lines) {
            if ($line -match '^([^#=]+)=') {
                $k = $Matches[1]
                if ($updates.ContainsKey($k)) {
                    $seen[$k] = $true
                    "$k=$($updates[$k])"
                    continue
                }
            }
            $line
        }
        foreach ($kv in $updates.GetEnumerator()) {
            if (-not $seen.ContainsKey($kv.Key)) {
                $outLines += "$($kv.Key)=$($kv.Value)"
            }
        }
        Set-Content -LiteralPath $prefsFile -Value $outLines -Encoding ASCII
        Write-Host "  已更新 $prefsFile（recoverydir/batchdir -> E:\，32核/2插槽，关闭 powerthrottling）。" -ForegroundColor Green
    } else {
        Write-Warning "未找到 $prefsFile"
    }
}

if ($MigrateLegacyAssets) {
    Write-Section "4E. 分层迁移 E:\cylinder_3d 现存资产至 E:\cfd_3d"
    $legacyDir = "E:\cylinder_3d"
    if (Test-Path -LiteralPath $legacyDir) {
        $moves = @(
            @{ Src = "cylinder_flow_turbulent.mph"; Dst = "E:\cfd_3d\00_templates_readonly\cylinder_flow_turbulent.mph" },
            @{ Src = "c04_re126k_benchmark.mph"; Dst = "E:\cfd_3d\01_calibration\c04_re126k\c04_re126k_benchmark.mph" },
            @{ Src = "c04_re126k_s0_solved.mph"; Dst = "E:\cfd_3d\01_calibration\c04_re126k\c04_re126k_s0_solved.mph" },
            @{ Src = "c04_re126k_s0_mesh2.mph"; Dst = "E:\cfd_3d\01_calibration\c04_re126k\c04_re126k_s0_mesh2.mph" },
            @{ Src = "c04_re126k_s1_trial.mph"; Dst = "E:\cfd_3d\99_archive\c04_re126k_s1_trial.mph" }
        )
        foreach ($m in $moves) {
            $s = Join-Path $legacyDir $m.Src
            if ((Test-Path -LiteralPath $s) -and -not (Test-Path -LiteralPath $m.Dst)) {
                $h1 = (Get-FileHash -LiteralPath $s -Algorithm SHA256).Hash
                Move-Item -LiteralPath $s -Destination $m.Dst
                $h2 = (Get-FileHash -LiteralPath $m.Dst -Algorithm SHA256).Hash
                if ($h1 -ne $h2) { throw "SHA256 mismatch moving $s" }
                Write-Host "  [MOVED + VERIFIED] $($m.Src) -> $($m.Dst)"
            }
        }
    }
}

if ($CleanupLegacyLockAndZip) {
    Write-Section "4F. 清理残留 .lock 与重复压缩包 E:\cylinder_3d.zip"
    $lockFile = "E:\cylinder_3d\c04_re126k_s1_trial.mph.lock"
    if (Test-Path -LiteralPath $lockFile) {
        Remove-Item -LiteralPath $lockFile -Force
        Write-Host "  [REMOVED] $lockFile"
    }
    $zipFile = "E:\cylinder_3d.zip"
    if (Test-Path -LiteralPath $zipFile) {
        Remove-Item -LiteralPath $zipFile -Force
        Write-Host "  [REMOVED] $zipFile (释放 ~1.56 GiB)"
    }
}

if ($LinkRepoJunctions) {
    Write-Section "4G. 创建 D:\HydroDisturb-Bench\runs_3d 目录联接 (Junction -> E:\cfd_3d\04_datasets_npz)"
    $linkPath = Join-Path $RepoRoot "runs_3d"
    if (-not (Test-Path -LiteralPath $linkPath)) {
        cmd /c "mklink /J `"$linkPath`" `"E:\cfd_3d\04_datasets_npz`""
    }
}

Write-Section "完成 - 生成当前运行环境变量示例"
Write-Host @"
在 PowerShell 运行 HydroDisturb-Bench 三维算例前，可加载以下环境变量自动启用 E 盘加速与 NUMA 调度：
  `$env:TEMP = 'E:\comsol_scratch\tmp'
  `$env:TMP = 'E:\comsol_scratch\tmp'
  `$env:HYDROBENCH_COMSOL_INIFILE = 'E:\comsol_scratch\comsolbatch_perf.ini'
  `$env:HYDROBENCH_COMSOL_TMPDIR = 'E:\comsol_scratch\tmp'
  `$env:HYDROBENCH_COMSOL_RECOVERYDIR = 'E:\comsol_scratch\recoveries'
  `$env:HYDROBENCH_COMSOL_NP = '32'
  `$env:HYDROBENCH_COMSOL_NUMASETS = '2'
  `$env:HYDROBENCH_COMSOL_MPMODE = 'turnaround'
  `$env:HYDROBENCH_COMSOL_ALLOC = 'scalable'
  `$env:HYDROBENCH_COMSOL_BLAS = 'mkl'
  `$env:HYDROBENCH_COMSOL_AUTOSAVE = 'off'
"@
