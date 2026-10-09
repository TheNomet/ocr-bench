"""Doc builder: every call emits HTML *and* the matching ground-truth line, so they never drift.

A document is a list of explicit pages. Each page is rendered as its own
``<section class="page">`` with a forced page break after it, and the renderer
verifies the PDF page count equals ``len(doc.pages)``, so per-page ground truth is
exact.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

BASE_CSS = """
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;font-size:10.5pt;color:#111;line-height:1.35}
.page{padding:16mm 18mm;page-break-after:always;break-after:page}
.page:last-child{page-break-after:auto;break-after:auto}
h1{font-size:17pt;margin:0 0 4mm}
h2{font-size:12pt;margin:5mm 0 2mm}
h3{font-size:10.5pt;margin:3mm 0 1mm}
p{margin:0 0 2.5mm}
.line{margin:0}
table{border-collapse:collapse;width:100%;font-size:9.5pt;margin:2mm 0 3mm}
td,th{border:1px solid #999;padding:2px 5px;text-align:left;vertical-align:top}
th{background:#eee}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.small{font-size:8.5pt;color:#333}
.muted{color:#555}
.box{border:1px solid #444;padding:3mm 4mm;margin:3mm 0}
.row{display:flex;gap:4mm;margin:0.6mm 0}
.row.spread{justify-content:space-between}
.row.grid > span{flex:1}
.row b{font-weight:600}
.right{text-align:right}
.flex{display:flex;justify-content:space-between;gap:10mm}
.flex > div{flex:1}
.strong{font-weight:700}
.big{font-size:12.5pt}
.sig{font-family:'Snell Roundhand','Apple Chancery','Brush Script MT',cursive;font-size:17pt;color:#1a2a6b;margin:2mm 0 0}
.sigline{border-top:1px solid #222;width:70mm;margin:0 0 1mm;padding-top:1mm;font-size:8.5pt;color:#333}
.footer{font-size:8pt;color:#555;border-top:1px solid #bbb;padding-top:1.5mm;margin-top:6mm}
"""


@dataclass
class Page:
    html: list[str] = field(default_factory=list)
    gt: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)


def _e(s: str) -> str:
    return html.escape(s, quote=True)


def _cls(cls: str) -> str:
    return f' class="{cls}"' if cls else ""


class Doc:
    def __init__(self, doc_type: str, title: str = "", css: str = "", page_css: str = "size:A4;margin:0"):
        self.type = doc_type
        self.title = title
        self.css = css
        self.page_css = page_css
        self.pages: list[Page] = [Page()]
        self.fields: dict[str, str | list[str]] = {}

    # -- bookkeeping -------------------------------------------------------
    @property
    def cur(self) -> Page:
        return self.pages[-1]

    def new_page(self) -> None:
        self.pages.append(Page())

    def num(self, s: str) -> str:
        """Register a numeric string printed on the current page; returns it unchanged."""
        if s not in self.cur.numbers:
            self.cur.numbers.append(s)
        return s

    def field(self, key: str, value: str | list[str]) -> None:
        self.fields[key] = value

    def _emit(self, markup: str, text: str | None) -> None:
        self.cur.html.append(markup)
        if text is not None:
            text = " ".join(text.split())
            if text:
                self.cur.gt.append(text)

    # -- blocks ------------------------------------------------------------
    def raw(self, markup: str) -> None:
        """HTML without ground truth (wrappers, rules, spacing)."""
        self._emit(markup, None)

    def open(self, cls: str = "", tag: str = "div") -> None:
        self.raw(f"<{tag}{_cls(cls)}>")

    def close(self, tag: str = "div") -> None:
        self.raw(f"</{tag}>")

    def h1(self, t: str, cls: str = "") -> None:
        self._emit(f"<h1{_cls(cls)}>{_e(t)}</h1>", t)

    def h2(self, t: str, cls: str = "") -> None:
        self._emit(f"<h2{_cls(cls)}>{_e(t)}</h2>", t)

    def h3(self, t: str, cls: str = "") -> None:
        self._emit(f"<h3{_cls(cls)}>{_e(t)}</h3>", t)

    def p(self, t: str, cls: str = "", lead: str = "") -> None:
        """Paragraph; optional bold `lead` text rendered before it on the same line."""
        b = f"<b>{_e(lead)}</b> " if lead else ""
        self._emit(f"<p{_cls(cls)}>{b}{_e(t)}</p>", f"{lead} {t}" if lead else t)

    def lines(self, items: list[str], cls: str = "") -> None:
        """Address-style block: one ground-truth line per visual line."""
        c = f"line {cls}".strip()
        for t in items:
            self._emit(f'<div class="{c}">{_e(t)}</div>', t)

    def row(self, *cells: str, cls: str = "spread", bold: tuple[int, ...] = ()) -> None:
        """One visual line made of several spans (e.g. label ... value); gt joins with one space."""
        parts = [c for c in cells if c]
        spans = "".join(f"<b>{_e(c)}</b>" if i in bold else f"<span>{_e(c)}</span>" for i, c in enumerate(cells) if c)
        self._emit(f'<div class="row {cls}">{spans}</div>', " ".join(parts))

    def sig(self, t: str) -> None:
        """Handwritten-looking signature (cursive font)."""
        self._emit(f'<div class="sig">{_e(t)}</div>', t)

    def kv(self, k: str, v: str, cls: str = "spread") -> None:
        self.row(k, v, cls=cls)

    def table(
        self,
        header: list[str],
        rows: list[list[str]],
        numeric: set[int] | frozenset[int] = frozenset(),
        cls: str = "",
        widths: list[str] | None = None,
        bold_rows: set[int] | frozenset[int] = frozenset(),
    ) -> None:
        def cell(tag: str, i: int, c: str, b: bool = False) -> str:
            inner = f"<b>{_e(c)}</b>" if b and c else _e(c)
            return f"<{tag}{_cls('n' if i in numeric else '')}>{inner}</{tag}>"

        cols = ""
        if widths:
            cols = "<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>"
        head = "".join(cell("th", i, c) for i, c in enumerate(header))
        self._emit(f"<table{_cls(cls)}>{cols}<thead><tr>{head}</tr></thead><tbody>", " ".join(header))
        for ri, row in enumerate(rows):
            tds = "".join(cell("td", i, c, ri in bold_rows) for i, c in enumerate(row))
            self._emit(f"<tr>{tds}</tr>", " ".join(c for c in row if c))
        self.raw("</tbody></table>")

    # -- output ------------------------------------------------------------
    @property
    def gt_text(self) -> str:
        return "\n".join(line for pg in self.pages for line in pg.gt)

    def render(self) -> str:
        pages = "".join(f'<section class="page">{"".join(pg.html)}</section>' for pg in self.pages)
        title = _e(self.title)
        return (
            "<!doctype html><html lang='nb'><head><meta charset='utf-8'>"
            f"<title>{title}</title><style>@page{{{self.page_css}}}{BASE_CSS}{self.css}</style></head>"
            f"<body>{pages}</body></html>"
        )
