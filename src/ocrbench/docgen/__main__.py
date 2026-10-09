"""python -m ocrbench.docgen --out docs-out --per-type 6"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from . import TYPES, generate


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m ocrbench.docgen", description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("docs-out"))
    ap.add_argument("--per-type", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--scale", type=float, default=2.0, help="pdfium render scale (2.0 = 144 dpi, A4 ~1190x1684)")
    ap.add_argument("--types", nargs="+", choices=TYPES, help="subset of document types (default: all)")
    ap.add_argument("--workers", type=int, default=4, help="parallel Chrome renders")
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    manifest = generate(
        args.out, per_type=args.per_type, seed=args.seed, scale=args.scale, types=args.types, workers=args.workers
    )
    for m in manifest:
        sizes = ", ".join(f"{p['width']}x{p['height']}" for p in m["pages"])
        print(f"  {m['id']:<20} {len(m['pages'])} page(s) [{sizes}]  {m['title']}")
    n_pages = sum(len(m["pages"]) for m in manifest)
    print(f"wrote {len(manifest)} documents / {n_pages} pages to {args.out}/ in {time.monotonic() - t0:.1f}s")


if __name__ == "__main__":
    main()
