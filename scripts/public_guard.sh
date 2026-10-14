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
# PUBLIC_GUARD_DENYLIST=path overrides the denylist location (CI, tests).
#
# On a match it prints only where (file:line, commit, file name, message line) and which denylist
# line matched, never the matched text or the pattern: CI logs are public. Denylisted text inside a
# reported location (e.g. a file name) is masked.
#
# Fails closed: a missing or invalid denylist, an unknown revision, a missing directory or any failing
# git/find/grep command is an error. Set PUBLIC_GUARD_ALLOW_MISSING=1 to skip when no denylist exists
# (e.g. a contributor without one); every skip prints a WARNING on stderr.
# Install hooks once per clone:  scripts/install_hooks.sh
set -uo pipefail

fail() { echo "public_guard: FAILED — $*" >&2; exit 1; }

root=$(git rev-parse --show-toplevel) || fail "not inside a git repository"
deny="${PUBLIC_GUARD_DENYLIST:-$root/.public-denylist}"
if [ ! -f "$deny" ]; then
  if [ "${PUBLIC_GUARD_ALLOW_MISSING:-0}" = "1" ]; then
    echo "WARNING: public_guard: $deny not found and PUBLIC_GUARD_ALLOW_MISSING=1: check SKIPPED, nothing was scanned" >&2
    exit 0
  fi
  fail "$deny not found (set PUBLIC_GUARD_ALLOW_MISSING=1 to skip)"
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/public_guard.XXXXXX") || fail "mktemp"
trap 'rm -rf "$tmp"' EXIT
# buf: the text to scan; where: line for line, where each buf line came from.
# pf: the patterns; pn: line for line, each pattern's line number in the denylist.
pf="$tmp/patterns" pn="$tmp/pattern-lines" buf="$tmp/content" where="$tmp/where"
{ : >"$pf" && : >"$pn" && : >"$buf" && : >"$where"; } || fail "cannot write temp files"

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
  { printf '%s\n' "$line" >>"$pf" && printf '%s\n' "$lineno" >>"$pn"; } || fail "cannot write temp files"
  n=$((n + 1))
done <"$deny"
[ "$n" -gt 0 ] || fail "denylist has no patterns"

# Raw, uncoloured, unquoted output regardless of the user's git config.
g() {
  git -c core.quotePath=false -c color.ui=false -c diff.relative=false -c diff.noprefix=false \
    -c diff.mnemonicPrefix=false -c log.showSignature=false -c grep.lineNumber=false -c grep.column=false \
    -c grep.fullName=false "$@"
}

# collect KIND [LABEL] < input: append input lines to $buf and their locations to $where.
# NULs become \001 first (awk would cut a line at a NUL); \001 also separates fields and marks
# commits (--format=%x01%H). Byte semantics, so any encoding passes through unchanged.
#   label    every line is LABEL
#   lines    LABEL line N
#   names    file names, optionally after commit markers
#   message  per commit: author, committer, message lines
#   patch    only added lines of a patch (removing a leaked string must not be blocked), as file:line
#   gitgrep  `git grep -z -n` output (path NUL line NUL text), location path:line LABEL
#   grep     `grep -n --null` output (path NUL line:text)
read -r -d '' collect_awk <<'AWK'
function out(s, loc) { print s >> B; print loc >> W }
function cnt(s, a) { return split(s, a, ",") > 1 ? a[2] + 0 : 1 }
function start(s, a) { split(s, a, ","); return substr(a[1], 2) + 0 }
function marker() { if (substr($0, 1, 1) != "\001") return 0; c = substr($0, 2, 12) " "; k = 0; return 1 }
kind == "label" { out($0, label); next }
kind == "lines" { out($0, label " line " NR); next }
kind == "names" { if (marker() || $0 == "") next; out($0, c "file name " $0); next }
kind == "message" {
  if (marker()) next
  k++
  out($0, c (k == 1 ? "author" : (k == 2 ? "committer" : "message line " (k - 2)))); next
}
kind == "patch" {
  if (ro > 0 || rn > 0) {
    t = substr($0, 1, 1)
    if (t == "+") { out(substr($0, 2), c f ":" ln); ln++; rn--; next }
    if (t == "-") { ro--; next }
    if (t == " ") { ro--; rn--; ln++; next }
    if (t == "\\") next
    ro = rn = 0
  }
  if (marker()) next
  if (substr($0, 1, 4) == "+++ ") {
    f = substr($0, 5); sub(/\t$/, "", f)
    if (f ~ /^".*"$/) f = substr(f, 2, length(f) - 2)
    sub(/^b\//, "", f); next
  }
  if (substr($0, 1, 3) == "@@ ") { split($0, h, " "); ro = cnt(h[2]); rn = cnt(h[3]); ln = start(h[3]) }
  next
}
kind == "gitgrep" {
  p = index($0, "\001"); r = substr($0, p + 1); q = index(r, "\001")
  out(substr(r, q + 1), substr($0, 1, p - 1) ":" substr(r, 1, q - 1) label); next
}
kind == "grep" {
  p = index($0, "\001"); r = substr($0, p + 1); q = index(r, ":")
  out(substr(r, q + 1), substr($0, 1, p - 1) ":" substr(r, 1, q - 1)); next
}
AWK
collect() { LC_ALL=C tr '\0' '\001' | LC_ALL=C awk -v B="$buf" -v W="$where" -v kind="$1" -v label="${2:-}" "$collect_awk"; }

# from KIND LABEL cmd...: collect a command's stdout; any non-zero exit fails closed.
from() {
  local kind=$1 label=$2
  shift 2
  "$@" | collect "$kind" "$label"
  st=("${PIPESTATUS[@]}")
  [ "${st[0]}" -eq 0 ] && [ "${st[1]}" -eq 0 ] || fail "command failed: $*"
}
# Same for grep-like producers, where exit 1 only means "no lines".
from_grep() {
  local kind=$1 label=$2
  shift 2
  "$@" | collect "$kind" "$label"
  st=("${PIPESTATUS[@]}")
  [ "${st[0]}" -le 1 ] && [ "${st[1]}" -eq 0 ] || fail "command failed (${st[0]}): $*"
}

difflags=(--no-color --no-textconv --no-ext-diff --text --no-renames -U0 --src-prefix=a/ --dst-prefix=b/)
mode=${1:-staged}
case "$mode" in
  staged)
    from patch "" g diff --cached "${difflags[@]}"
    from names "" g diff --cached --name-only --no-renames
    from label "author identity" g var GIT_AUTHOR_IDENT
    from label "committer identity" g var GIT_COMMITTER_IDENT
    ;;
  range)
    [ $# -ge 2 ] || fail "range needs revision arguments"
    revs=("${@:2}")
    g rev-list "${revs[@]}" -- >/dev/null 2>"$tmp/err" || fail "invalid range: $(head -1 "$tmp/err")"
    from patch "" g log -p -m --format=%x01%H "${difflags[@]}" "${revs[@]}" --
    from names "" g log -m --format=%x01%H --name-only --no-renames "${revs[@]}" --
    from message "" g log --no-mailmap --format='%x01%H%n%an <%ae>%n%cn <%ce>%n%B' "${revs[@]}" --
    ;;
  tree)
    cd "$root" || fail "cd $root"
    g ls-files -z >"$tmp/files" || fail "git ls-files"
    LC_ALL=C tr '\0' '\n' <"$tmp/files" | collect names || fail "collect"
    from_grep gitgrep "" g grep -I -z -n --no-color -e '' -- .                    # working tree
    from_grep gitgrep " (index)" g grep -I -z -n --no-color --cached -e '' -- . # index, incl. files deleted locally
    ;;
  dir)
    [ $# -ge 2 ] || fail "dir needs a path"
    [ -d "$2" ] || fail "not a directory: $2"
    from names "" find "$2" -print
    from_grep grep "" grep -rIn --null -e '' -- "$2"
    ;;
  text) from lines "stdin" cat ;;
  *) echo "usage: $0 staged|range <rev-list args>|tree|dir PATH|text" >&2; exit 2 ;;
esac

# Replace denylisted text inside a location (e.g. a leaked file name) before printing it.
mask() {
  local s=$1 m _
  for _ in 1 2 3 4 5 6 7 8; do
    m=$(printf '%s\n' "$s" | grep -a -o -i -E -f "$pf" 2>/dev/null | head -1)
    [ -n "$m" ] || { printf '%s' "$s"; return; }
    s=${s//"$m"/<denylisted>}
  done
  printf '%s' "<location withheld>"
}

grep -a -q -i -E -f "$pf" -- "$buf" 2>/dev/null # stderr: GNU grep warnings quote bits of patterns
rc=$?
[ "$rc" -gt 1 ] && fail "grep error"
if [ "$rc" -eq 0 ]; then
  hits="$tmp/hits"
  : >"$hits"
  # Which pattern matched where: one pass per pattern, printing only buffer line numbers.
  while IFS= read -r p <&3 && IFS= read -r d <&4; do
    grep -a -n -i -E -e "$p" -- "$buf" 2>/dev/null | cut -d: -f1 >"$tmp/nums"
    st=("${PIPESTATUS[@]}")
    [ "${st[0]}" -le 1 ] && [ "${st[1]}" -eq 0 ] || fail "grep error (denylist line $d)"
    LC_ALL=C awk -v d="$d" 'NR == FNR { want[$1] = 1; next } FNR in want { print d " " $0 }' "$tmp/nums" "$where" \
      >>"$hits" || fail "awk"
  done 3<"$pf" 4<"$pn"
  total=$(wc -l <"$hits" | tr -d ' ')
  {
    echo "public_guard: BLOCKED — denylisted identifiers found ($mode); matched text not shown:"
    head -20 "$hits" | while IFS= read -r row; do
      printf '  %s  (denylist line %s)\n' "$(mask "${row#* }")" "${row%% *}"
    done
    [ "$total" -gt 20 ] && echo "  ... $((total - 20)) more"
  } >&2
  exit 1
fi
echo "public_guard: clean ($mode)"
