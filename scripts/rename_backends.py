"""Rename backend keys in a config and in run folders (keys appear in records, paths and prose).

    uv run python scripts/rename_backends.py --config config/x.yaml --runs runs/* \
        --map old=new [--map old2=new2] [--redact-endpoint]

--redact-endpoint turns 'model@sagemaker:<endpoint-name>' into 'model@sagemaker' in records.
Longest keys are replaced first, so 'x-55' is handled before 'x'. The run's config.yaml snapshot
is renamed too (it stays gitignored).
"""

import argparse
import re
from pathlib import Path

TEXT = {".json", ".jsonl", ".md", ".yaml", ".yml"}


def rewrite(text: str, mapping: list[tuple[str, str]], redact: bool) -> str:
    for old, new in mapping:
        text = re.sub(rf"(?<![A-Za-z0-9._])(?<![A-Za-z0-9.]-){re.escape(old)}(?![A-Za-z0-9._]|-(?!>))", new, text)
    if redact:
        text = re.sub(r"@sagemaker:[A-Za-z0-9-]+", "@sagemaker", text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--runs", nargs="*", default=[])
    ap.add_argument("--map", action="append", required=True)
    ap.add_argument("--redact-endpoint", action="store_true")
    a = ap.parse_args()
    mapping = sorted((tuple(m.split("=", 1)) for m in a.map), key=lambda x: -len(x[0]))
    files = [Path(a.config)] if a.config else []
    for r in a.runs:
        d = Path(r)
        # rename directories/files first (outputs/<backend>/, outputs/extract/<pipe slug>/)
        for p in sorted(d.rglob("*"), key=lambda p: -len(p.parts)):
            if p.name.startswith("docs") and p.parent == d:
                continue
            new = rewrite(p.name, mapping, False)
            if new != p.name:
                p.rename(p.with_name(new))
        files += [
            p for p in d.rglob("*") if p.is_file() and p.suffix in TEXT and "docs" not in p.relative_to(d).parts[:1]
        ]
    changed = 0
    for f in files:
        t = f.read_text(encoding="utf-8")
        n = rewrite(t, mapping, a.redact_endpoint)
        if n != t:
            f.write_text(n, encoding="utf-8")
            changed += 1
    print(f"rewrote {changed} files")


if __name__ == "__main__":
    main()
