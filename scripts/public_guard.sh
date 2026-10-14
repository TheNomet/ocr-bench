#!/usr/bin/env bash
# Public-repo guard: refuse commits/pushes/publishes containing identifiers listed in the LOCAL,
# gitignored file `.public-denylist` (one extended regex per line, case-insensitive). Keeps
# org-specific account ids, hostnames, client ids etc. out of this public repository.
#
#   scripts/public_guard.sh staged            # staged added lines, file names and author (pre-commit)
#   scripts/public_guard.sh range A..B        # commits in a range: added lines, file names, messages, author/committer
#   scripts/public_guard.sh range REV --not --remotes=origin   # any `git rev-list` arguments
#   scripts/public_guard.sh tree              # every tracked file (working tree and index) and path
#   scripts/public_guard.sh dir PATH          # a directory (e.g. the site before publishing): content + paths
#   scripts/public_guard.sh text              # stdin (e.g. a commit message)
#
# Denylist: blank lines and lines starting with `#` are skipped; surrounding whitespace and CR are
# stripped. Each line is its own pattern (grep -f), so back-references work.
# PUBLIC_GUARD_DENYLIST=path overrides the denylist location (tests).
#
# Fails closed: a missing or invalid denylist, an unknown revision, a missing directory or any failing
# git/find/grep command is an error. Set PUBLIC_GUARD_ALLOW_MISSING=1 to skip when no denylist exists
# (e.g. a contributor without one).
# Install hooks once per clone:  scripts/install_hooks.sh
set -uo pipefail

fail() { echo "public_guard: FAILED — $*" >&2; exit 1; }

root=$(git rev-parse --show-toplevel) || fail "not inside a git repository"
deny="${PUBLIC_GUARD_DENYLIST:-$root/.public-denylist}"
if [ ! -f "$deny" ]; then
  if [ "${PUBLIC_GUARD_ALLOW_MISSING:-0}" = "1" ]; then echo "public_guard: no denylist, skipped"; exit 0; fi
  fail "$deny not found (set PUBLIC_GUARD_ALLOW_MISSING=1 to skip)"
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/public_guard.XXXXXX") || fail "mktemp"
trap 'rm -rf "$tmp"' EXIT
pf="$tmp/patterns" buf="$tmp/content"
: >"$pf" && : >"$buf" || fail "cannot write temp files"

n=0 lineno=0
while IFS= read -r line || [ -n "$line" ]; do
  lineno=$((lineno + 1))
  line=${line//$'\r'/}
  line=${line#"${line%%[![:space:]]*}"}
  line=${line%"${line##*[![:space:]]}"}
  [ -z "$line" ] && continue
  [ "${line:0:1}" = "#" ] && continue
  grep -E -e "$line" </dev/null >/dev/null 2>&1
  [ $? -gt 1 ] && fail "invalid pattern on denylist line $lineno"
  printf '%s\n' "$line" >>"$pf" || fail "cannot write temp files"
  n=$((n + 1))
done <"$deny"
[ "$n" -gt 0 ] || fail "denylist has no patterns"

# Raw, uncoloured, unquoted output regardless of the user's git config.
g() {
  git -c core.quotePath=false -c color.ui=false -c diff.relative=false -c diff.noprefix=false \
    -c log.showSignature=false -c grep.lineNumber=false -c grep.column=false -c grep.fullName=false "$@"
}
# Append a command's stdout to the buffer; any non-zero exit fails closed.
add() { "$@" >>"$buf" || fail "command failed: $*"; }
# Same for grep-like producers, where exit 1 only means "no lines".
add_grep() { "$@" >>"$buf"; local rc=$?; [ "$rc" -le 1 ] || fail "command failed ($rc): $*"; }
# Only added lines of a patch: removing a leaked string must not be blocked.
added() { "$@" | sed -n '/^+/p' >>"$buf" || fail "command failed: $*"; }

difflags=(--no-color --no-textconv --no-ext-diff --text --no-renames -U0)
mode=${1:-staged}
case "$mode" in
  staged)
    added g diff --cached "${difflags[@]}"
    add g diff --cached --name-only --no-renames
    add g var GIT_AUTHOR_IDENT
    add g var GIT_COMMITTER_IDENT
    ;;
  range)
    [ $# -ge 2 ] || fail "range needs revision arguments"
    revs=("${@:2}")
    g rev-list "${revs[@]}" -- >/dev/null 2>"$tmp/err" || fail "invalid range: $(head -1 "$tmp/err")"
    added g log -p -m --format= "${difflags[@]}" "${revs[@]}" --
    add g log -m --format= --name-only --no-renames "${revs[@]}" --
    add g log --no-mailmap --format='%an <%ae>%n%cn <%ce>%n%B' "${revs[@]}" --
    ;;
  tree)
    cd "$root" || fail "cd $root"
    g ls-files -z >"$tmp/files" || fail "git ls-files"
    tr '\0' '\n' <"$tmp/files" >>"$buf" || fail "tr"
    add_grep g grep -I -h --no-color -e '' -- .          # working tree
    add_grep g grep -I -h --no-color --cached -e '' -- . # index, incl. files deleted locally
    ;;
  dir)
    [ $# -ge 2 ] || fail "dir needs a path"
    [ -d "$2" ] || fail "not a directory: $2"
    add find "$2" -print
    add_grep grep -rIh -e '' -- "$2"
    ;;
  text) add cat ;;
  *) echo "usage: $0 staged|range <rev-list args>|tree|dir PATH|text" >&2; exit 2 ;;
esac

hits=$(grep -a -inE -f "$pf" -- "$buf"); rc=$?
[ "$rc" -gt 1 ] && fail "grep error"
if [ "$rc" -eq 0 ]; then
  echo "public_guard: BLOCKED — denylisted identifiers found ($mode):" >&2
  printf '%s\n' "$hits" | head -20 >&2
  exit 1
fi
echo "public_guard: clean ($mode)"
