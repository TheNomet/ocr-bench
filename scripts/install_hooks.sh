#!/usr/bin/env bash
# Copy the repo's hooks into .git/hooks (does not modify git config).
set -euo pipefail
root=$(git rev-parse --show-toplevel)
hooks=$(git rev-parse --git-path hooks) # also right in a linked worktree, where .git is a file
mkdir -p "$hooks"
for h in pre-commit pre-push commit-msg; do
  install -m 755 "$root/.githooks/$h" "$hooks/$h"
done
echo "hooks installed: pre-commit, pre-push, commit-msg"
[ -f "$root/.public-denylist" ] || echo "note: create .public-denylist (gitignored) to enable the public guard"
