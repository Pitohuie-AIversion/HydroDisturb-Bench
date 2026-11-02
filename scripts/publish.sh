#!/usr/bin/env bash
# Safe first publication after extracting the project zip.
set -euo pipefail
cd "$(dirname "$0")/.."
for exe in gh git; do
  command -v "$exe" >/dev/null || { echo "Missing $exe" >&2; exit 1; }
done
account=$(gh api user --jq .login)
if [[ "$account" != "general-chen" ]]; then
  echo "Wrong GitHub account: $account (expected general-chen). Aborting." >&2
  exit 1
fi
if gh repo view general-chen/HydroDisturb-Bench >/dev/null 2>&1; then
  echo "Repository already exists; this first-time publishing script will not modify it." >&2
  exit 1
fi
if [[ -e .git ]]; then
  echo "Existing .git directory detected; manual inspection required." >&2
  exit 1
fi
printf 'Creating PRIVATE repository general-chen/HydroDisturb-Bench. Continue? [y/N] '
read -r response
[[ "$response" == "y" || "$response" == "Y" ]] || exit 0
git init -b main
git add README.md .gitignore .github pyproject.toml configs docs models scripts src tests
if ! git diff --cached --check; then exit 1; fi
git commit -m "chore: initialize HydroDisturb-Bench research scaffold"
gh repo create general-chen/HydroDisturb-Bench --private --source . --remote origin --push
printf 'Published: https://github.com/general-chen/HydroDisturb-Bench\n'
