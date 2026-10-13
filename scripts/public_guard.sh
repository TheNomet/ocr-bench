#!/usr/bin/env bash
# Public-repo guard: refuse commits/pushes/publishes containing identifiers listed in the LOCAL,
# gitignored file `.public-denylist` (one extended regex per line, case-insensitive). Keeps
# org-specific account ids, hostnames, client ids etc. out of this public repository.
#
#   scripts/public_guard.sh staged        # staged diff, file names and author (pre-commit)
#   scripts/public_guard.sh range A..B    # commits in a range: diffs, file names, messages, author/committer
#   scripts/public_guard.sh tree          # every tracked file and path
#   scripts/public_guard.sh dir PATH      # a directory (e.g. the site before publishing): content + paths
#   scripts/public_guard.sh text          # stdin (e.g. a commit message)
#
# Fails closed: a missing or invalid denylist is an error. Set PUBLIC_GUARD_ALLOW_MISSING=1 to
# skip when no denylist exists (e.g. a contributor without one).
# Install hooks once per clone:  scripts/install_hooks.sh
set -uo pipefail
root=$(git rev-parse --show-toplevel)
deny="$root/.public-denylist"
if [ ! -f "$deny" ]; then
  if [ "${PUBLIC_GUARD_ALLOW_MISSING:-0}" = "1" ]; then echo "public_guard: no .public-denylist, skipped"; exit 0; fi
  echo "public_guard: FAILED — $deny not found (set PUBLIC_GUARD_ALLOW_MISSING=1 to skip)" >&2; exit 1
fi
patterns=()
while IFS= read -r line || [ -n "$line" ]; do
  line=${line%$'\r'}
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  echo | grep -E -- "$line" >/dev/null 2>&1; rc=$?
  if [ "$rc" -gt 1 ]; then echo "public_guard: FAILED — invalid pattern in denylist: $line" >&2; exit 1; fi
  patterns+=("$line")
done < "$deny"
[ "${#patterns[@]}" -gt 0 ] || { echo "public_guard: FAILED — denylist has no patterns" >&2; exit 1; }
joined=$(IFS='|'; echo "${patterns[*]}")

mode=${1:-staged}
case "$mode" in
  staged) content=$(git diff --cached -U0 --no-color --text; git diff --cached --name-only;
                    git var GIT_AUTHOR_IDENT; git var GIT_COMMITTER_IDENT) ;;
  range)  content=$(git log -p -m --text --format=fuller --name-only "$2") ;;
  tree)   content=$(git ls-files; git ls-files -z | xargs -0 grep -I -h "" 2>/dev/null) ;;
  dir)    content=$(find "$2" -print; grep -rIh "" "$2" 2>/dev/null) ;;
  text)   content=$(cat) ;;
  *) echo "usage: $0 staged|range A..B|tree|dir PATH|text"; exit 2 ;;
esac

hits=$(printf '%s\n' "$content" | grep -inE -- "$joined"); rc=$?
if [ "$rc" -gt 1 ]; then echo "public_guard: FAILED — grep error" >&2; exit 1; fi
if [ -n "$hits" ]; then
  echo "public_guard: BLOCKED — denylisted identifiers found ($mode):" >&2
  printf '%s\n' "$hits" | head -20 >&2
  exit 1
fi
echo "public_guard: clean ($mode)"
