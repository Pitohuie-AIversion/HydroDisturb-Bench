# Safe first publication after extracting the project zip, Windows PowerShell.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub CLI gh is required.' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git is required.' }
$account = (gh api user --jq .login).Trim()
if ($LASTEXITCODE -ne 0) { throw 'GitHub authentication not available.' }
if ($account -ne 'general-chen') { throw "Wrong GitHub account '$account' (expected general-chen)." }
gh repo view general-chen/HydroDisturb-Bench *> $null
if ($LASTEXITCODE -eq 0) { throw 'Repository already exists; refusing to modify it.' }
if (Test-Path .git) { throw 'Existing .git directory detected; inspect manually.' }
$answer = Read-Host 'Create PRIVATE repository general-chen/HydroDisturb-Bench? (y/N)'
if ($answer -ne 'y' -and $answer -ne 'Y') { return }
git init -b main
if ($LASTEXITCODE -ne 0) { throw 'git init failed' }
git add README.md .gitignore .github pyproject.toml configs docs models scripts src tests
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'git diff check failed' }
git commit -m 'chore: initialize HydroDisturb-Bench research scaffold'
if ($LASTEXITCODE -ne 0) { throw 'git commit failed (configure git user.name and user.email)' }
gh repo create general-chen/HydroDisturb-Bench --private --source . --remote origin --push
if ($LASTEXITCODE -ne 0) { throw 'Repository creation or push failed; inspect GitHub and git state before retrying' }
Write-Host 'Published: https://github.com/general-chen/HydroDisturb-Bench'
