"""scripts/public_guard.sh and the pre-push hook: they must scan diffs, names, messages and authors, and fail closed."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "public_guard.sh"
PRE_PUSH = ROOT / ".githooks" / "pre-push"
SECRET = "zqx-forbidden-token"

pytestmark = pytest.mark.skipif(not shutil.which("git") or not shutil.which("bash"), reason="needs git and bash")


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", **(env or {})}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "Dev")
    git(r, "config", "user.email", "dev@example.invalid")
    git(r, "config", "commit.gpgsign", "false")
    (r / "a.txt").write_text("hello\n")
    git(r, "add", "a.txt")
    git(r, "commit", "-qm", "init")
    return r


@pytest.fixture
def deny(tmp_path: Path) -> Path:
    p = tmp_path / "deny"
    p.write_text(f"# a comment\n\n{SECRET}  \r\n")  # trailing whitespace + CR must not stop a match
    return p


def guard(repo: Path, deny: Path, *args: str, stdin: str = "", script: Path = GUARD):
    env = {**os.environ, "PUBLIC_GUARD_DENYLIST": str(deny), "GIT_CONFIG_GLOBAL": "/dev/null"}
    env.pop("PUBLIC_GUARD_ALLOW_MISSING", None)
    return subprocess.run(
        ["bash", str(script), *args], cwd=repo, env=env, input=stdin, capture_output=True, text=True, check=False
    )


def commit(repo: Path, name: str, text: str, msg: str = "change", **env: str) -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg, env=env)
    return git(repo, "rev-parse", "HEAD").strip()


def test_range_scans_added_diff_lines(repo, deny):
    commit(repo, "a.txt", f"hello\nleak {SECRET}\n")
    r = guard(repo, deny, "range", "HEAD~1..HEAD")
    assert r.returncode == 1 and "BLOCKED" in r.stderr
    sha = git(repo, "rev-parse", "--short=12", "HEAD").strip()
    assert f"{sha} a.txt:2  (denylist line 3)" in r.stderr


def test_binary_content_is_not_scanned_but_its_name_is(repo, deny):
    """Like `tree`/`dir` (grep -I): bytes of rendered PDFs/PNGs can match short patterns by chance."""
    data = b"\x89PNG\r\n\x1a\n\x00\x01" + SECRET.encode() + b"\x00\xff"
    (repo / "img.png").write_bytes(data)
    git(repo, "add", "img.png")
    assert guard(repo, deny, "staged").returncode == 0
    git(repo, "commit", "-qm", "binary")
    assert guard(repo, deny, "range", "HEAD~1..HEAD").returncode == 0
    (repo / f"{SECRET}.png").write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "named")
    assert guard(repo, deny, "range", "HEAD~1..HEAD").returncode == 1


def test_report_never_shows_matched_text_or_pattern(repo, tmp_path):
    """CI logs are public: only locations and denylist line numbers, with denylisted names masked."""
    p = tmp_path / "deny-re"
    p.write_text("zqx-[a-z]+-token\n")
    commit(repo, f"docs/{SECRET}.md", f"x\n{SECRET}\n", f"msg\n\nsee {SECRET}")
    r = guard(repo, p, "range", "HEAD~1..HEAD")
    assert r.returncode == 1 and SECRET not in r.stderr + r.stdout and "zqx-[a-z]" not in r.stderr
    assert "docs/<denylisted>.md:2  (denylist line 1)" in r.stderr
    assert "file name docs/<denylisted>.md" in r.stderr and "message line 3" in r.stderr


def test_report_locations_per_mode(repo, deny, tmp_path):
    (repo / "b:c.txt").write_text(f"one\ntwo {SECRET}\n")
    git(repo, "add", "b:c.txt")
    assert "  b:c.txt:2  (denylist line 3)" in guard(repo, deny, "staged").stderr
    assert "b:c.txt:2 (index)" in guard(repo, deny, "tree").stderr
    assert "stdin line 2" in guard(repo, deny, "text", stdin=f"ok\n{SECRET}\n").stderr
    site = tmp_path / "site"
    site.mkdir()
    (site / "i.html").write_text(f"\n\n{SECRET}\n")
    assert f"{site}/i.html:3" in guard(repo, deny, "dir", str(site)).stderr
    git(repo, "commit", "-qm", "x", env={"GIT_AUTHOR_NAME": SECRET})
    r = guard(repo, deny, "range", "HEAD~1..HEAD")
    assert " author  (denylist line 3)" in r.stderr and SECRET not in r.stderr


@pytest.mark.parametrize(
    ("name", "msg", "env"),
    [
        (f"docs/{SECRET}.md", "change", {}),
        ("b.txt", f"mention {SECRET}", {}),
        ("b.txt", "change", {"GIT_AUTHOR_NAME": SECRET}),
        ("b.txt", "change", {"GIT_COMMITTER_EMAIL": f"{SECRET}@example.invalid"}),
    ],
)
def test_range_scans_names_messages_and_idents(repo, deny, name, msg, env):
    commit(repo, name, "clean\n", msg, **env)
    assert guard(repo, deny, "range", "HEAD~1..HEAD").returncode == 1


def test_range_allows_removing_a_leak(repo, deny):
    commit(repo, "a.txt", f"{SECRET}\n")
    commit(repo, "a.txt", "clean\n")
    assert guard(repo, deny, "range", "HEAD~1..HEAD").returncode == 0
    assert guard(repo, deny, "range", "HEAD~2..HEAD").returncode == 1


def test_range_ignores_textconv_and_color_config(repo, deny):
    git(repo, "config", "color.ui", "always")
    git(repo, "config", "diff.txt.textconv", "true")  # a textconv that prints nothing
    (repo / ".gitattributes").write_text("*.txt diff=txt\n")
    commit(repo, "a.txt", f"{SECRET}\n")
    assert guard(repo, deny, "range", "HEAD~1..HEAD").returncode == 1


def test_unknown_revision_fails_closed(repo, deny):
    r = guard(repo, deny, "range", "0123456789abcdef0123456789abcdef01234567..HEAD")
    assert r.returncode == 1 and "FAILED" in r.stderr and "clean" not in r.stdout


def test_missing_dir_fails_closed(repo, deny, tmp_path):
    r = guard(repo, deny, "dir", str(tmp_path / "nope"))
    assert r.returncode == 1 and "clean" not in r.stdout


def test_dir_scans_content_and_paths(repo, deny, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("clean")
    assert guard(repo, deny, "dir", str(site)).returncode == 0
    (site / f"{SECRET}.html").write_text("clean")
    assert guard(repo, deny, "dir", str(site)).returncode == 1


def test_staged_and_tree(repo, deny):
    assert guard(repo, deny, "tree").returncode == 0
    (repo / "b.txt").write_text(f"{SECRET}\n")
    git(repo, "add", "b.txt")
    assert guard(repo, deny, "staged").returncode == 1
    assert guard(repo, deny, "tree").returncode == 1
    (repo / "b.txt").unlink()  # still in the index
    assert guard(repo, deny, "tree").returncode == 1


def test_text(repo, deny):
    assert guard(repo, deny, "text", stdin="fine\n").returncode == 0
    assert guard(repo, deny, "text", stdin=f"x {SECRET.upper()}\n").returncode == 1  # case-insensitive


def test_back_references_work(repo, tmp_path):
    p = tmp_path / "deny2"
    p.write_text("(ab)\\1\nunrelated\n")
    assert guard(repo, p, "text", stdin="xabx\n").returncode == 0
    assert guard(repo, p, "text", stdin="xababx\n").returncode == 1


@pytest.mark.parametrize("content", ["# only comments\n\n   \n", "ok\n(unclosed\n"])
def test_bad_denylist_fails_closed(repo, tmp_path, content):
    p = tmp_path / "deny3"
    p.write_text(content)
    r = guard(repo, p, "text", stdin="x\n")
    assert r.returncode == 1 and "FAILED" in r.stderr


def test_missing_denylist(repo, tmp_path):
    assert guard(repo, tmp_path / "none", "text", stdin="x").returncode == 1
    env = {**os.environ, "PUBLIC_GUARD_DENYLIST": str(tmp_path / "none"), "PUBLIC_GUARD_ALLOW_MISSING": "1"}
    r = subprocess.run(
        ["bash", str(GUARD), "text"], cwd=repo, env=env, input="x", capture_output=True, text=True, check=False
    )
    assert r.returncode == 0 and r.stderr.startswith("WARNING:") and "SKIPPED" in r.stderr


# ---------------------------------------------------------------------------- pre-push hook
@pytest.fixture
def hooked(repo):
    """The hook calls <toplevel>/scripts/public_guard.sh, so put the script there (untracked)."""
    (repo / "scripts").mkdir()
    shutil.copy(GUARD, repo / "scripts" / "public_guard.sh")
    (repo / ".git" / "info" / "exclude").write_text("scripts/\n")
    return repo


ZERO = "0" * 40


def push(repo, deny, local_sha, remote_sha, remote="origin", ref="refs/heads/main"):
    stdin = f"{ref} {local_sha} {ref} {remote_sha}\n"
    return guard(repo, deny, remote, "https://example.invalid/r.git", stdin=stdin, script=PRE_PUSH)


def test_pre_push_known_remote_sha(hooked, deny):
    base = git(hooked, "rev-parse", "HEAD").strip()
    head = commit(hooked, "a.txt", f"{SECRET}\n")
    assert push(hooked, deny, head, base).returncode == 1
    clean = commit(hooked, "a.txt", "clean\n")
    assert push(hooked, deny, clean, head).returncode == 0


def test_pre_push_unknown_remote_sha_scans_unpushed_commits(hooked, deny):
    """After a force-push elsewhere the remote tip isn't local: fall back to commits not on the remote."""
    base = git(hooked, "rev-parse", "HEAD").strip()
    git(hooked, "update-ref", "refs/remotes/origin/main", base)
    head = commit(hooked, "a.txt", f"{SECRET}\n")
    r = push(hooked, deny, head, "0123456789abcdef0123456789abcdef01234567")
    assert r.returncode == 1 and "BLOCKED" in r.stderr


def test_pre_push_new_branch(hooked, deny):
    head = commit(hooked, "a.txt", f"{SECRET}\n")
    assert push(hooked, deny, head, ZERO).returncode == 1
    git(hooked, "update-ref", "refs/remotes/origin/main", head)  # already on this remote: nothing new
    assert push(hooked, deny, head, ZERO).returncode == 0
    # ...but being on a *different* remote doesn't count
    assert push(hooked, deny, head, ZERO, remote="public").returncode == 1


def test_pre_push_branch_deletion_skipped(hooked, deny):
    assert push(hooked, deny, ZERO, git(hooked, "rev-parse", "HEAD").strip()).returncode == 0


@pytest.mark.parametrize(
    ("args", "env", "blocked"),
    [
        (["-a", "-m", "release notes"], {}, False),
        (["-a", "-m", f"release {SECRET}"], {}, True),
        (["-a", "-m", "release"], {"GIT_COMMITTER_EMAIL": f"{SECRET}@example.invalid"}, True),  # tagger
        ([], {}, False),  # lightweight tag: only the ref name
    ],
)
def test_pre_push_scans_annotated_tags(hooked, deny, args, env, blocked):
    head = git(hooked, "rev-parse", "HEAD").strip()
    git(hooked, "update-ref", "refs/remotes/origin/main", head)  # the commit itself is already pushed
    git(hooked, "tag", *args, "v1", env=env)
    tag = git(hooked, "rev-parse", "refs/tags/v1").strip()
    r = push(hooked, deny, tag, ZERO, ref="refs/tags/v1")
    assert r.returncode == int(blocked), r.stderr
    assert SECRET not in r.stderr


def test_pre_push_scans_nested_tags_and_ref_names(hooked, deny):
    head = git(hooked, "rev-parse", "HEAD").strip()
    git(hooked, "update-ref", "refs/remotes/origin/main", head)
    git(hooked, "tag", "-a", "-m", f"inner {SECRET}", "inner")
    git(hooked, "tag", "-a", "-m", "outer", "outer", "inner")
    assert push(hooked, deny, git(hooked, "rev-parse", "outer").strip(), ZERO, ref="refs/tags/outer").returncode == 1
    assert push(hooked, deny, head, ZERO, ref=f"refs/heads/{SECRET}").returncode == 1
