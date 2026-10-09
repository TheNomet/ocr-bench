#!/usr/bin/env bash
# Public-repo guard: refuse commits/pushes containing identifiers listed in the
# LOCAL, gitignored file `.public-denylist` (one extended regex per line,
# case-insensitive). Keeps org-specific account IDs, hostnames, client IDs etc.
# out of this public repository.
#
#   scripts/public_guard.sh staged        # scan the staged diff (pre-commit)
#   scripts/public_guard.sh range A..B    # scan commits in a range (pre-push)
#   scripts/public_guard.sh tree          # scan every tracked file
#
# Install hooks once per clone:  scripts/install_hooks.sh
set -euo pipefail
root=$(git rev-parse --show-toplevel)
deny="$root/.public-denylist"
[ -f "$deny" ] || { echo "public_guard: no .public-denylist, skipping"; exit 0; }
patterns=$(grep -vE '^\s*(#|$)' "$deny" | paste -sd'|' -)
[ -n "$patterns" ] || exit 0

case "${1:-staged}" in
  staged) content=$(git diff --cached -U0 --no-color | grep -E '^\+' | grep -vE '^\+\+\+ ' || true) ;;
  range)  content=$(git log -p --no-color "$2" | grep -E '^(\+|Author:|Commit:)' | grep -vE '^\+\+\+ ' || true) ;;
  tree)   content=$(git ls-files -z | xargs -0 cat 2>/dev/null || true) ;;
  *) echo "usage: $0 staged|range A..B|tree"; exit 2 ;;
esac

hits=$(printf '%s\n' "$content" | grep -inE "$patterns" || true)
if [ -n "$hits" ]; then
  echo "public_guard: BLOCKED — denylisted identifiers found:" >&2
  printf '%s\n' "$hits" | head -20 >&2
  exit 1
fi
echo "public_guard: clean ($1)"
