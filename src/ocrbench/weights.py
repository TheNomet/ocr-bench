"""Stream a pinned HuggingFace snapshot straight into S3, verifying SHA-256 against the HF index.

No local copy is needed. If the HF CDN is unreachable from this machine (corporate proxies
sometimes block it), download the large files in a browser and pass --from-dir: local files
whose size matches are used instead, and are still hash-verified.
"""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import sys
from pathlib import Path

import boto3
import httpx
from boto3.s3.transfer import TransferConfig

from .config import Config

try:  # trust the OS store too (corporate TLS inspection)
    import truststore  # pyright: ignore[reportMissingImports]

    VERIFY: ssl.SSLContext | bool = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:
    VERIFY = True


class _HashingReader:
    """Streams bytes to S3 while hashing them: SHA-256 (LFS files) and git's blob SHA-1 (all other files)."""

    def __init__(self, chunks, total: int):
        self.it, self.buf, self.sha, self.n, self.total = chunks, b"", hashlib.sha256(), 0, total
        self.blob = hashlib.sha1(f"blob {total}\0".encode())  # noqa: S324 — git object id, as HF reports it

    def read(self, size: int = -1) -> bytes:
        while size < 0 or len(self.buf) < size:
            try:
                c = next(self.it)
            except StopIteration:
                break
            self.sha.update(c)
            self.blob.update(c)
            self.buf += c
        out, self.buf = (self.buf, b"") if size < 0 else (self.buf[:size], self.buf[size:])
        self.n += len(out)
        if self.total > 500_000_000:
            sys.stdout.write(f"\r    {self.n / 1e9:.2f} / {self.total / 1e9:.2f} GB")
            sys.stdout.flush()
        return out


def _list_tree(repo: str, rev: str) -> list[dict]:
    """All files at `rev`, following the API's pagination (Link: rel="next")."""
    url = f"https://huggingface.co/api/models/{repo}/tree/{rev}"
    params: dict | None = {"recursive": "1"}
    out: list[dict] = []
    while url:
        r = httpx.get(url, params=params, timeout=30, verify=VERIFY)
        r.raise_for_status()
        out += r.json()
        url, params = r.links.get("next", {}).get("url"), None
    return out


def upload(cfg: Config, name: str, from_dir: Path | None = None) -> None:
    o = cfg.backend(name)
    repo, rev, prefix, bucket = o["hf_repo"], o["hf_revision"], cfg.model_prefix(name), cfg.bucket
    skip = tuple(o.get("hf_skip_prefixes") or [])
    s3 = boto3.client("s3")
    tcfg = TransferConfig(multipart_chunksize=64 * 1024 * 1024, max_concurrency=4)
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        raise SystemExit(f"backends.{name}.hf_revision must be a full 40-character commit hash, got {rev!r}")
    tree = _list_tree(repo, rev)
    files = [f for f in tree if f["type"] == "file" and not f["path"].startswith(skip)]
    manifest = {"repo": repo, "revision": rev, "files": []}
    with httpx.Client(follow_redirects=True, verify=VERIFY, timeout=httpx.Timeout(60.0, read=300.0)) as http:
        for f in files:
            path, size = f["path"], f.get("lfs", {}).get("size", f["size"])
            expected = f.get("lfs", {}).get("oid")  # sha256 for LFS files; otherwise f["oid"] is the git blob sha1
            key = f"{prefix}/{path}"
            local = None
            if from_dir:  # keep the repo layout if present, else accept a flat download folder
                local = next((c for c in (from_dir / path, from_dir / Path(path).name) if c.exists()), None)
            if local and local.exists():
                if local.stat().st_size != size:
                    raise SystemExit(f"{local}: size {local.stat().st_size} != {size} (incomplete download?)")
                print(f"  {path} ({size / 1e6:.1f} MB, from {local})")
                with local.open("rb") as fh:
                    reader = _HashingReader(iter(lambda: fh.read(8 << 20), b""), size)
                    s3.upload_fileobj(reader, bucket, key, Config=tcfg)
            else:
                print(f"  {path} ({size / 1e6:.1f} MB)")
                with http.stream("GET", f"https://huggingface.co/{repo}/resolve/{rev}/{path}") as resp:
                    if resp.status_code >= 400:
                        raise SystemExit(
                            f"{path}: HTTP {resp.status_code}. If a proxy blocks the HF CDN, open the "
                            f"URL in a browser once (accept any warning page) or use --from-dir."
                        )
                    reader = _HashingReader(resp.iter_bytes(8 << 20), size)
                    s3.upload_fileobj(reader, bucket, key, Config=tcfg)
            if size > 500_000_000:
                print()
            digest = reader.sha.hexdigest()
            if reader.n != size:
                s3.delete_object(Bucket=bucket, Key=key)
                raise SystemExit(f"size mismatch for {path}: got {reader.n} bytes, expected {size}")
            if expected and digest != expected:
                s3.delete_object(Bucket=bucket, Key=key)
                raise SystemExit(f"SHA-256 mismatch for {path}: {digest} != {expected}")
            if not expected and f.get("oid") and reader.blob.hexdigest() != f["oid"]:
                s3.delete_object(Bucket=bucket, Key=key)
                raise SystemExit(
                    f"git blob hash mismatch for {path} (this includes model code run with trust_remote_code)"
                )
            manifest["files"].append({"path": path, "size": size, "sha256": digest})
    s3.put_object(Bucket=bucket, Key=f"{prefix}.manifest.json", Body=json.dumps(manifest, indent=1).encode())
    print(f"done: {len(files)} files -> s3://{bucket}/{prefix}/ (manifest {prefix}.manifest.json)")
