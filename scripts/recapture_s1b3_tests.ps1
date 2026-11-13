<#
.SYNOPSIS
    PowerShell script to recapture live pytest stdout/stderr with JUnit XML and exit codes.
.PARAMETER PythonExe
    Path to python executable. Dynamically discovered if omitted.
.PARAMETER RepoRoot
    Path to repository root. Defaults to parent directory of this script.
#>
[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$RepoRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 1. Dynamically resolve RepoRoot
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    if ($env:HYDRODISTURB_BENCH_ROOT -and (Test-Path -LiteralPath $env:HYDRODISTURB_BENCH_ROOT)) {
        $RepoRoot = $env:HYDRODISTURB_BENCH_ROOT
    } elseif ($PSScriptRoot) {
        $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    } else {
        $RepoRoot = (Get-Location).Path
    }
}

# 2. Dynamically resolve Python executable
$py = $PythonExe
$legacyAnaconda = "D:\Program Files\Anaconda\envs\seagent\python.exe"
if ([string]::IsNullOrWhiteSpace($py)) {
    if ($env:PYTHON_EXE -and (Test-Path -LiteralPath $env:PYTHON_EXE)) {
        $py = $env:PYTHON_EXE
    } elseif ($env:PYTHON -and (Test-Path -LiteralPath $env:PYTHON)) {
        $py = $env:PYTHON
    } elseif ($env:VIRTUAL_ENV -and (Test-Path -LiteralPath (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"))) {
        $py = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    } elseif ($env:CONDA_PREFIX -and (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX "python.exe"))) {
        $py = Join-Path $env:CONDA_PREFIX "python.exe"
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    } elseif (Test-Path -LiteralPath $legacyAnaconda) {
        $py = $legacyAnaconda
    } else {
        throw "Python executable not found. Provide -PythonExe or ensure python is on your PATH."
    }
}

Write-Host "Using Python: $py"
Write-Host "Repo Root:    $RepoRoot"

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ev = Join-Path $RepoRoot ("docs/evidence/s1b3_tests_recaptured_" + $timestamp)
New-Item -ItemType Directory -Path $ev -Force | Out-Null

$commit = try { (git -C $RepoRoot rev-parse HEAD 2>$null) } catch { "UNKNOWN_COMMIT" }
if (-not $commit) { $commit = "UNKNOWN_COMMIT" }
$commit | Set-Content (Join-Path $ev "commit.txt")

function Invoke-LoggedPytest {
    param(
        [string]$Name,
        [string[]]$TestArgs
    )

    $log = Join-Path $ev "$Name.txt"
    $xml = Join-Path $ev "$Name.xml"

    Write-Host "Running $Name with args: $TestArgs..."
    Push-Location $RepoRoot
    try {
        & $py -m pytest @TestArgs "--junitxml=$xml" 2>&1 |
            Tee-Object -FilePath $log

        $code = $LASTEXITCODE
        $code | Set-Content (Join-Path $ev "$Name.exitcode.txt")

        if ($code -ne 0) {
            throw "pytest failed: $Name, exit code $code"
        }
        Write-Host "PASSED: $Name (exit code $code)"
    } finally {
        Pop-Location
    }
}

# 1. Targeted: M3 time unit tests
Invoke-LoggedPytest -Name "m3_time" -TestArgs @(
    "-v", "tests/test_re100_s1b3_m3_time.py"
)

# 2. Related modules: mesh and s1b1 time convergence
Invoke-LoggedPytest -Name "related_modules" -TestArgs @(
    "-q",
    "tests/test_re100_s1b2_mesh.py",
    "tests/test_re100_s1b1_time_convergence.py"
)

# 3. Full regression
Invoke-LoggedPytest -Name "full_regression" -TestArgs @("-q")

Write-Host "All logged pytest runs completed successfully in $ev"
