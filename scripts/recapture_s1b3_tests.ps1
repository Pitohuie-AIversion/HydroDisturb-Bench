# PowerShell script to recapture live pytest stdout/stderr with JUnit XML and exit codes

$ErrorActionPreference = "Stop"
$py = "D:\Program Files\Anaconda\envs\seagent\python.exe"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ev = Join-Path "docs/evidence" ("s1b3_tests_recaptured_" + $timestamp)
New-Item -ItemType Directory -Path $ev -ErrorAction Stop | Out-Null

git rev-parse HEAD | Set-Content (Join-Path $ev "commit.txt")

function Invoke-LoggedPytest {
    param(
        [string]$Name,
        [string[]]$TestArgs
    )

    $log = Join-Path $ev "$Name.txt"
    $xml = Join-Path $ev "$Name.xml"

    Write-Host "Running $Name with args: $TestArgs..."
    & $py -m pytest @TestArgs "--junitxml=$xml" 2>&1 |
        Tee-Object -FilePath $log

    $code = $LASTEXITCODE
    $code | Set-Content (Join-Path $ev "$Name.exitcode.txt")

    if ($code -ne 0) {
        throw "pytest failed: $Name, exit code $code"
    }
    Write-Host "PASSED: $Name (exit code $code)"
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
