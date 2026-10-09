"""A tiny, safe Markdown subset renderer for the report summary.

Supports: ``#``/``##``/``###`` headings, paragraphs, ``- `` / ``* `` bullet lists,
``**bold**``, ```code```, and pipe tables (``| a | b |`` with an optional
``|---|---|`` separator row). All text is HTML-escaped before markup is applied.
"""

from __future__ import annotations

import html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")


def inline(text: str) -> str:
    """Render inline markup (code spans, bold) with everything else escaped."""
    parts = text.split("`")
    out = []
    for i, part in enumerate(parts):
        # Odd segments are inside backticks — but only if the backtick was closed.
        if i % 2 == 1 and i < len(parts) - 1:
            out.append(f"<code>{html.escape(part)}</code>")
        else:
            seg = html.escape(part if i % 2 == 0 else "`" + part)
            out.append(_BOLD.sub(r"<strong>\1</strong>", seg))
    return "".join(out)


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def render(md: str) -> str:
    """Render the supported Markdown subset to an HTML fragment."""
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
            level = min(len(m.group(1)) + 1, 6)  # page already has an <h1>; shift down one level
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
            if header is not None:
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
