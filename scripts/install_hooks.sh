#!/usr/bin/env bash
# Copy the repo's hooks into .git/hooks (does not modify git config).
set -euo pipefail
root=$(git rev-parse --show-toplevel)
for h in pre-commit pre-push; do
  install -m 755 "$root/.githooks/$h" "$root/.git/hooks/$h"
done
echo "hooks installed: pre-commit, pre-push"
[ -f "$root/.public-denylist" ] || echo "note: create .public-denylist (gitignored) to enable the public guard"
