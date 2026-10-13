"""Pure helpers of ocrbench.images: digest pinning, the generated serve script, reproducible layers."""

from __future__ import annotations

import gzip
import io
import shlex
import tarfile
import time

import pytest

from ocrbench.images import layer_bytes, pinned, serve_script

D = "sha256:" + "ab" * 32


@pytest.mark.parametrize(
    ("ref", "want"),
    [
        ("vllm/vllm-openai", "vllm/vllm-openai"),
        ("vllm/vllm-openai:v0.11.0", "vllm/vllm-openai"),
        (f"vllm/vllm-openai@{'cd' * 32}", "vllm/vllm-openai"),
        (f"vllm/vllm-openai:v0.11.0@sha256:{'cd' * 32}", "vllm/vllm-openai"),
        ("python", "python"),
        ("python:3.12-slim", "python"),
        # a registry port is not a tag
        ("host:5000/repo", "host:5000/repo"),
        ("host:5000/repo:tag", "host:5000/repo"),
        ("host:5000/team/repo:tag", "host:5000/team/repo"),
        (f"host:5000/repo:tag@sha256:{'cd' * 32}", "host:5000/repo"),
    ],
)
def test_pinned(ref, want):
    assert pinned(ref, D) == f"{want}@{D}"


def test_serve_script_quotes_args():
    o = {
        "served_model_name": "my model",
        "serve_args": ["--max-model-len", "8192", "--limit-mm-per-prompt", '{"image": 1}'],
    }
    s = serve_script(o)
    assert s.startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in s
    assert "exec vllm serve /opt/ml/model" in s
    assert f"--served-model-name {shlex.quote('my model')}" in s
    assert shlex.quote('{"image": 1}') in s
    assert "--host 0.0.0.0 --port 8080" in s
    # the script is valid shell: tokenising the exec line gives back the arguments
    exec_line = s[s.index("exec vllm") :].replace("\\\n", " ")
    tokens = shlex.split(exec_line)
    assert tokens[:4] == ["exec", "vllm", "serve", "/opt/ml/model"]
    assert tokens[tokens.index("--served-model-name") + 1] == "my model"
    assert tokens[tokens.index("--limit-mm-per-prompt") + 1] == '{"image": 1}'
    assert tokens[-4:] == ["--host", "0.0.0.0", "--port", "8080"]


def test_serve_script_without_args():
    tokens = shlex.split(serve_script({"served_model_name": "m"}).split("exec ", 1)[1].replace("\\\n", " "))
    assert tokens == [
        "vllm",
        "serve",
        "/opt/ml/model",
        "--served-model-name",
        "m",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
    ]


FILES = {
    "opt/ocrbench/b.py": (b"print('b')\n", 0o644),
    "opt/serve.sh": (b"#!/bin/bash\n", 0o755),
    "opt/ocrbench/a/__init__.py": (b"", 0o644),
}


def test_layer_is_reproducible(monkeypatch):
    first = layer_bytes(FILES)
    # a later clock and a different insertion order must not change a byte
    monkeypatch.setattr(time, "time", lambda: 2_000_000_000.0)
    second = layer_bytes(dict(reversed(FILES.items())))
    assert first == second
    assert first[4:8] == b"\0\0\0\0"  # gzip header mtime


def test_layer_contents():
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(layer_bytes(FILES))), mode="r") as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == sorted(FILES)
        for m in members:
            assert (m.mtime, m.uid, m.gid, m.uname, m.gname) == (0, 0, 0, "", "")
            assert m.mode == FILES[m.name][1]
            f = tar.extractfile(m)
            assert f is not None and f.read() == FILES[m.name][0]
