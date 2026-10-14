"""A tiny, safe Markdown subset renderer for the report summary.

Supports: ``#``/``##``/``###`` headings, paragraphs, ``- `` / ``* `` bullet lists,
``**bold**``, ``*italic*``, ```code```, and pipe tables (``| a | b |`` with an optional
``|---|---|`` separator row). All text is HTML-escaped before markup is applied.
"""

from __future__ import annotations

import html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")


def inline(text: str) -> str:
    """Render inline markup (code spans, bold, italic) with everything else escaped.

    Code spans are swapped for NUL-delimited placeholders first, so bold may wrap them (**`x`**); NULs in
    the input are dropped so they can't forge a placeholder.
    """
    text = text.replace("\x00", "")
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    seg = html.escape(re.sub(r"`([^`]+)`", stash, text), quote=False)
    seg = _BOLD.sub(r"<strong>\1</strong>", seg)
    seg = _ITALIC.sub(r"<em>\1</em>", seg)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], seg)


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def render(md: str, shift: int = 1) -> str:
    """Render the supported Markdown subset to an HTML fragment.

    Headings move down `shift` levels (default 1: the page already has an <h1>, so `#` becomes <h2>).
    Use shift=0 for Markdown whose `##` sections are top-level sections of the page.
    """
    if not md or not md.strip():
        return ""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    i = 0

    def flush_para() -> None:
        if para:
            out.append("<p>" + " ".join(inline(p.strip()) for p in para) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            i += 1
            continue
        m = _HEADING.match(stripped)
        if m:
            flush_para()
            level = max(1, min(len(m.group(1)) + shift, 6))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if _BULLET.match(line):
            flush_para()
            items = []
            while i < len(lines) and _BULLET.match(lines[i]):
                items.append(_BULLET.match(lines[i]).group(1))  # type: ignore[union-attr]
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(it)}</li>" for it in items) + "</ul>")
            continue
        if _is_table_line(line):
            flush_para()
            rows = []
            while i < len(lines) and _is_table_line(lines[i]):
                rows.append(lines[i])
                i += 1
            header: list[str] | None = None
            body_rows = rows
            if len(rows) >= 2 and _TABLE_SEP.match(rows[1].strip()):
                header = _split_row(rows[0])
                body_rows = rows[2:]
            parts = ['<div class="table-wrap"><table>']
            if header is not None and any(c.strip() for c in header):  # `| | |` = headerless table
                parts.append("<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in header) + "</tr></thead>")
            parts.append("<tbody>")
            for r in body_rows:
                if _TABLE_SEP.match(r.strip()):
                    continue
                parts.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in _split_row(r)) + "</tr>")
            parts.append("</tbody></table></div>")
            out.append("".join(parts))
            continue
        para.append(line)
        i += 1
    flush_para()
    return "\n".join(out)
