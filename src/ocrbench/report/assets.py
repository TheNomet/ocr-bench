"""Inline CSS and JS for the static report site (no external resources)."""

CSS = """
:root {
  --fg: #1f2328; --muted: #656d76; --bg: #ffffff; --panel: #f6f8fa; --border: #d0d7de;
  --accent: #0969da; --ok: #1a7f37; --ok-bg: #dafbe1; --bad: #cf222e; --bad-bg: #ffebe9;
  --best: #fff8c5;
}
* { box-sizing: border-box; }
body {
  margin: 0; color: var(--fg); background: var(--bg); line-height: 1.5;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 15px;
}
main { max-width: 1400px; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.7rem; margin: 0 0 .25rem; }
h2 { font-size: 1.3rem; margin: 2rem 0 .75rem; border-bottom: 1px solid var(--border); padding-bottom: .3rem; }
h3 { font-size: 1.1rem; margin: 1.25rem 0 .5rem; }
.muted { color: var(--muted); }
.small { font-size: .85rem; }
code, pre, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; font-size: .85rem; }
code { background: var(--panel); padding: .1em .3em; border-radius: 4px; }
pre { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: .75rem;
      white-space: pre-wrap; word-break: break-word; margin: .5rem 0; max-height: 70vh; overflow: auto; }
.toc { margin: .5rem 0 1rem; }
.warn { background: #fff4e5; border-left: 4px solid #d97706; padding: .4rem .8rem; }
.facts { border-left: 4px solid #2f6feb; padding-left: 1rem; }
section { margin: 1.5rem 0; }
section table, .summary table { border-collapse: collapse; margin: .5rem 0; font-size: .9rem; }
section th, section td, .summary th, .summary td { border: 1px solid var(--border); padding: .3rem .55rem; text-align: left; vertical-align: top; }
.run-card { display: block; border: 1px solid var(--border); border-radius: 12px; padding: 1.25rem 1.5rem;
  margin: 1.25rem 0; color: inherit; text-decoration: none; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
.run-card:hover { border-color: #8aa4c8; box-shadow: 0 2px 10px rgba(0,0,0,.08); }
.run-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
.run-id { font-size: 1.35rem; font-weight: 650; letter-spacing: -.01em; }
.chips { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .4rem; }
.chip { font-size: .78rem; background: #eef2f7; color: #445; border-radius: 999px; padding: .15rem .65rem; }
.open { font-size: .9rem; color: #2f6feb; white-space: nowrap; }
.bottom-line { margin: 1rem 0 .25rem; padding: .75rem 1rem; background: #f3f7ff; border-left: 4px solid #2f6feb;
  border-radius: 0 8px 8px 0; font-size: .95rem; line-height: 1.55; }
.bottom-line p { margin: .2rem 0; }
.bottom-line .label, .block h4 { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: #667;
  margin: 0 0 .5rem; font-weight: 600; }
.run-grid { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 1.25rem 2.5rem; margin-top: 1.25rem; }
.run-grid .block.wide { grid-row: span 2; }
@media (max-width: 900px) { .run-grid { grid-template-columns: 1fr; } .run-grid .block.wide { grid-row: auto; } }
table.models { border-collapse: collapse; width: 100%; font-size: .88rem; }
table.models td { padding: .5rem .75rem .5rem 0; border-top: 1px solid #eef0f3; vertical-align: top; }
table.models tr:first-child td { border-top: 0; }
table.models td.role { color: #667; width: 10rem; }
.mname { font-weight: 600; }
.mid { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .75rem; color: #8a8f99; word-break: break-all; }
.mnote { font-size: .8rem; color: #556; margin-top: .25rem; line-height: 1.4; }
.kv { display: grid; grid-template-columns: 5rem 1fr; gap: .5rem; font-size: .88rem; padding: .25rem 0; }
.kv > span:first-child { color: #667; }
table.mini { border-collapse: collapse; width: 100%; font-size: .85rem; }
table.mini th { text-align: left; font-weight: 600; color: #667; font-size: .74rem; padding: .25rem .6rem .3rem 0;
  border-bottom: 1px solid var(--border); white-space: nowrap; }
table.mini td { padding: .35rem .6rem .35rem 0; border-bottom: 1px solid #eef0f3; white-space: nowrap; }
table.mini td.win { font-weight: 700; color: #0a6b2d; }
.summary { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: .25rem 1rem; margin: 1rem 0; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; font-size: .9rem; }
th, td { border: 1px solid var(--border); padding: .35rem .6rem; text-align: left; vertical-align: top; }
th { background: var(--panel); font-weight: 600; white-space: nowrap; }
table.sortable th { cursor: pointer; user-select: none; }
table.sortable th::after { content: " \\2195"; color: var(--muted); font-size: .8em; }
table.sortable th.asc::after { content: " \\2191"; }
table.sortable th.desc::after { content: " \\2193"; }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td.best { background: var(--best); font-weight: 600; }
.empty { color: var(--muted); font-style: italic; }
.toolbar { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin: .5rem 0 1rem; }
button { font: inherit; font-size: .85rem; padding: .3rem .7rem; border: 1px solid var(--border);
         background: var(--bg); border-radius: 6px; cursor: pointer; color: var(--fg); }
button:hover { background: var(--panel); }
button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
input[type=search] { font: inherit; padding: .3rem .6rem; border: 1px solid var(--border); border-radius: 6px; min-width: 16rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 1rem; }
.card { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: var(--bg);
        display: flex; flex-direction: column; color: inherit; }
.card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.12); text-decoration: none; }
.card .thumb { height: 220px; background: var(--panel); display: flex; align-items: flex-start; justify-content: center; overflow: hidden; }
.card .thumb img { width: 100%; object-fit: cover; object-position: top; }
.card .body { padding: .6rem .75rem; font-size: .85rem; }
.card .title { font-weight: 600; margin: .1rem 0 .3rem; }
.badges { display: flex; flex-wrap: wrap; gap: .25rem; margin-top: .4rem; }
.badge { display: inline-block; font-size: .75rem; padding: .05rem .4rem; border-radius: 10px;
         background: var(--panel); border: 1px solid var(--border); white-space: nowrap; }
.badge.type { background: #ddf4ff; border-color: #54aeff66; }
.badge.ext { background: #fbefff; border-color: #c297ff66; }
header.doc { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 1rem; align-items: baseline; }
nav.docnav a { margin-left: 1rem; }
section.page { border-top: 1px solid var(--border); padding-top: .5rem; }
.page-grid { display: grid; grid-template-columns: 1fr; gap: 1rem; }
@media (min-width: 1000px) { .page-grid { grid-template-columns: minmax(300px, 2fr) 3fr; } }
.img-col img { max-width: 100%; border: 1px solid var(--border); border-radius: 4px; display: block; }
.img-col .sticky { position: sticky; top: .5rem; }
.tabs { display: flex; flex-wrap: wrap; gap: .25rem; border-bottom: 1px solid var(--border); margin-bottom: .5rem; }
.tabs button { border-bottom: none; border-radius: 6px 6px 0 0; }
.metrics { font-size: .85rem; background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: .4rem .6rem; }
.metrics span { margin-right: 1rem; white-space: nowrap; }
.diff { line-height: 1.7; }
.diff del { color: var(--bad); background: var(--bad-bg); text-decoration: line-through; }
.diff ins { color: var(--ok); background: var(--ok-bg); text-decoration: none; }
details { margin: .5rem 0; }
summary { cursor: pointer; color: var(--accent); font-size: .9rem; }
td.ok { background: var(--ok-bg); }
td.bad { background: var(--bad-bg); }
td .mark { font-weight: 700; margin-right: .3rem; }
td.ok .mark { color: var(--ok); }
td.bad .mark { color: var(--bad); }
th[title], td[title] { text-decoration: underline dotted var(--muted); }
[hidden] { display: none !important; }
"""

JS = """
(function () {
  // Sortable tables: click a header to sort by that column (numbers use data-v).
  document.querySelectorAll('table.sortable').forEach(function (table) {
    var ths = table.querySelectorAll('thead th');
    ths.forEach(function (th, idx) {
      th.addEventListener('click', function () {
        var asc = !th.classList.contains('asc');
        ths.forEach(function (o) { o.classList.remove('asc', 'desc'); });
        th.classList.add(asc ? 'asc' : 'desc');
        var tbody = table.tBodies[0];
        var rows = Array.prototype.slice.call(tbody.rows);
        rows.sort(function (a, b) {
          var ca = a.cells[idx], cb = b.cells[idx];
          var va = ca ? ca.getAttribute('data-v') : null, vb = cb ? cb.getAttribute('data-v') : null;
          var na = va === null || va === '' ? NaN : parseFloat(va);
          var nb = vb === null || vb === '' ? NaN : parseFloat(vb);
          var r;
          if (!isNaN(na) || !isNaN(nb)) {
            if (isNaN(na)) return 1; if (isNaN(nb)) return -1;  // blanks last
            r = na - nb;
          } else {
            r = (ca ? ca.textContent : '').localeCompare(cb ? cb.textContent : '');
          }
          return asc ? r : -r;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  });

  // Document grid: type filter buttons + text search.
  var grid = document.getElementById('doc-grid');
  if (grid) {
    var search = document.getElementById('doc-search');
    var currentType = '';
    var apply = function () {
      var q = (search.value || '').toLowerCase().trim();
      grid.querySelectorAll('.card').forEach(function (card) {
        var okType = !currentType || card.getAttribute('data-type') === currentType;
        var okText = !q || card.getAttribute('data-search').indexOf(q) !== -1;
        card.hidden = !(okType && okText);
      });
    };
    document.querySelectorAll('button.type-filter').forEach(function (btn) {
      btn.addEventListener('click', function () {
        document.querySelectorAll('button.type-filter').forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        currentType = btn.getAttribute('data-type');
        apply();
      });
    });
    search.addEventListener('input', apply);
  }

  // Per-page variant toggle (clean / degraded): switches image + transcript pane.
  document.querySelectorAll('section.page').forEach(function (sec) {
    sec.querySelectorAll('button.vbtn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var v = btn.getAttribute('data-variant');
        sec.querySelectorAll('button.vbtn').forEach(function (b) { b.classList.toggle('active', b === btn); });
        sec.querySelectorAll('.vpane').forEach(function (el) { el.hidden = el.getAttribute('data-variant') !== v; });
      });
    });
  });

  // Tabs (ground truth / backends).
  document.querySelectorAll('.tabset').forEach(function (set) {
    var buttons = set.querySelectorAll(':scope > .tabs > button');
    var panels = set.querySelectorAll(':scope > .tabpanel');
    buttons.forEach(function (btn, i) {
      btn.addEventListener('click', function () {
        buttons.forEach(function (b, j) { b.classList.toggle('active', i === j); });
        panels.forEach(function (p, j) { p.hidden = i !== j; });
      });
    });
  });
})();
"""
