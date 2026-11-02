# Publish the repository to GitHub

**Target account**: `general-chen` (not `Pitohuie-AIversion`). **Target name**: `HydroDisturb-Bench`. The scripts create a **private** GitHub repository by default. You can change its visibility after reviewing the code and documentation.

## Option A: one command with GitHub CLI

1. Install `git` and [`gh`](https://cli.github.com/).
2. Run `gh auth login` and make sure `gh api user --jq .login` prints `general-chen`.
3. Extract the ZIP and enter the project directory.
4. On Windows PowerShell, run `powershell -ExecutionPolicy Bypass -File scripts/publish.ps1`; on Bash run `bash scripts/publish.sh`.
5. Confirm when the script asks. The script will initialize a local git repository, commit the checked scaffold and create/push to GitHub. It will **refuse** to run on the wrong account or into an existing repo.

> The code has been tested only in the provided Python development environment; the publication scripts themselves cannot be exercised without a GitHub CLI session and user credentials.

## Option B: create the repository in the browser

1. Sign into GitHub as `general-chen` and open <https://github.com/new>.
2. Enter `HydroDisturb-Bench`. Select **Private** initially, with **no** auto-generated README, license, or .gitignore.
3. Extract this ZIP and run in the extracted project directory:

```bash
git init -b main
git add .
git diff --cached --check
git commit -m "chore: initialize HydroDisturb-Bench research scaffold"
git remote add origin https://github.com/general-chen/HydroDisturb-Bench.git
git push -u origin main
```

No secrets, licenses, `.mph` model files, simulations, or bulk data are supplied in this package. Don't check those into Git.
