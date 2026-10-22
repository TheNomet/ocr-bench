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
        document.querySelectorAll('button.type-filter').forEach(function (b) {
          b.classList.toggle('active', b === btn);
          b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
        });
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
        sec.querySelectorAll('button.vbtn').forEach(function (b) {
          b.classList.toggle('active', b === btn);
          b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
        });
        sec.querySelectorAll('.vpane').forEach(function (el) { el.hidden = el.getAttribute('data-variant') !== v; });
      });
    });
  });

  // Tabs (ground truth / backends): WAI-ARIA tabs, click or arrow keys.
  document.querySelectorAll('.tabset').forEach(function (set) {
    var buttons = set.querySelectorAll(':scope > .tabs > button');
    var panels = set.querySelectorAll(':scope > .tabpanel');
    var select = function (i) {
      buttons.forEach(function (b, j) {
        b.classList.toggle('active', i === j);
        b.setAttribute('aria-selected', i === j ? 'true' : 'false');
        b.tabIndex = i === j ? 0 : -1;
      });
      panels.forEach(function (p, j) { p.hidden = i !== j; });
    };
    buttons.forEach(function (btn, i) {
      btn.addEventListener('click', function () { select(i); });
      btn.addEventListener('keydown', function (ev) {
        var n = buttons.length, k = -1;
        if (ev.key === 'ArrowRight') k = (i + 1) % n;
        else if (ev.key === 'ArrowLeft') k = (i - 1 + n) % n;
        else if (ev.key === 'Home') k = 0;
        else if (ev.key === 'End') k = n - 1;
        if (k < 0) return;
        ev.preventDefault();
        select(k);
        buttons[k].focus();
      });
    });
  });
})();
