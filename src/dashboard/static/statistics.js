// The Statistics page: one shared time period (picked on the calendar) and six plots, each
// filtered on its own. Everything is drawn from the per-day rows the page embeds; when the page's
// live stream brings new rows (static/live.js), the plots and the calendar are drawn again from them,
// keeping the period, project or pillar, and the jobs and policies chosen.
(function () {
  'use strict';
  var data = JSON.parse(document.getElementById('stats-data').textContent);
  var ROWS = { 'job-count': 'jobs', 'job-success': 'jobs', 'policy-fail': 'policies', 'job-duration': 'jobs', incidents: 'incidents', 'test-success': 'tests' };
  var pillarNames = JSON.parse(document.getElementById('stats-pillars').textContent);
  var SVG = 'http://www.w3.org/2000/svg';
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

  // -- days ---------------------------------------------------------------------------------
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function parse(day) { var p = day.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function addDays(day, n) { var d = parse(day); d.setDate(d.getDate() + n); return iso(d); }
  function daysBetween(from, to) { var out = []; for (var d = from; d <= to; d = addDays(d, 1)) out.push(d); return out; }
  function shortDay(day) { var d = parse(day); return d.getDate() + ' ' + MONTHS[d.getMonth()].slice(0, 3); }
  function longDay(day) { var d = parse(day); return d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear(); }

  var today = iso(new Date());
  var allDays = [].concat(data.jobs, data.policies, data.incidents, data.tests).map(function (r) { return r.day; }).sort();
  var firstDay = allDays.length ? allDays[0] : today;
  var period = { from: addDays(today, -29) < firstDay ? firstDay : addDays(today, -29), to: today };

  // -- formatting ---------------------------------------------------------------------------
  function percent(v) { return (Math.round(v * 1000) / 10) + '%'; }
  function seconds(v) {
    if (v < 10) return (Math.round(v * 10) / 10) + 's';
    if (v < 60) return Math.round(v) + 's';
    if (v < 3600) return Math.floor(v / 60) + 'm ' + pad(Math.round(v % 60)) + 's';
    return Math.floor(v / 3600) + 'h ' + pad(Math.floor(v % 3600 / 60)) + 'm';
  }
  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    for (var k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (text != null) node.textContent = text;
    return node;
  }
  function svg(tag, attrs) {
    var node = document.createElementNS(SVG, tag);
    for (var k in attrs || {}) node.setAttribute(k, attrs[k]);
    return node;
  }

  // -- the plots ----------------------------------------------------------------------------
  // `point(sum)` turns one day's summed columns into {value, lines}; a null value is a day with nothing to plot.
  function ratioPoint(good, bad, of, goodWord, badWord) {
    return function (sum) {
      var total = (sum[good] || 0) + (sum[bad] || 0);
      if (!total) return { value: null };
      return { value: (sum[of] || 0) / total, lines: [(sum[good] || 0) + ' ' + goodWord, (sum[bad] || 0) + ' ' + badWord] };
    };
  }
  var plots = [
    { id: 'job-count', rows: data.jobs, item: 'job', itemWord: 'Jobs', mark: 'bar', format: String,
      point: function (sum) { return { value: sum.triggered || 0, lines: [] }; }, breakdown: 'triggered' },
    { id: 'job-success', rows: data.jobs, item: 'job', itemWord: 'Jobs', mark: 'line', format: percent, max: 1,
      point: function (sum) {
        var p = ratioPoint('succeeded', 'failed', 'succeeded', 'succeeded', 'failed')(sum);
        if (p.lines) p.lines.push((sum.triggered || 0) + ' triggered');
        return p;
      } },
    { id: 'policy-fail', rows: data.policies, item: 'policy', itemWord: 'Policies', mark: 'line', format: percent, max: 1,
      point: function (sum) {
        var p = ratioPoint('passed', 'failed', 'failed', 'passed', 'failed')(sum);
        if (p.lines) { p.lines.reverse(); if (sum.not_applicable) p.lines.push(sum.not_applicable + ' not applicable (left out)'); }
        return p;
      } },
    { id: 'job-duration', rows: data.jobs, item: 'job', itemWord: 'Jobs', mark: 'line', format: seconds,
      point: function (sum) {
        if (!sum.duration_count) return { value: null };
        return { value: sum.duration_seconds / sum.duration_count, lines: ['average of ' + plural(sum.duration_count, 'finished run')] };
      } },
    { id: 'incidents', rows: data.incidents, mark: 'bar', format: String,
      point: function (sum) { return { value: sum.incidents || 0, lines: [] }; } },
    { id: 'test-success', rows: data.tests, mark: 'line', format: percent, max: 1,
      point: ratioPoint('succeeded', 'failed', 'succeeded', 'succeeded', 'failed') },
  ];

  // -- project or pillar: one choice for every plot ---------------------------------------------
  // Either a project (everything about it, whichever pillar did it) or a pillar (everything it did,
  // whichever project it was for), never both: picking in one list sets the other back to "all".
  var chosen = { kind: null, value: '' };
  function inScope(row) { return chosen.kind === null || row[chosen.kind] === chosen.value; }
  function buildScopeSelects() {
    var rows = [].concat(data.jobs, data.policies, data.incidents, data.tests);
    var selects = {};
    [['project', String], ['pillar', function (id) { return pillarNames[id] || (id === 'unknown' ? 'Unknown' : id); }]].forEach(function (pair) {
      var kind = pair[0], select = selects[kind] = document.getElementById('stats-' + kind);
      select.appendChild(el('option', { value: '' }, 'All ' + kind + 's'));
      unique(rows, kind).forEach(function (v) { if (v) select.appendChild(el('option', { value: v }, pair[1](v))); });
      select.addEventListener('change', function () {
        chosen = select.value ? { kind: kind, value: select.value } : { kind: null, value: '' };
        selects[kind === 'project' ? 'pillar' : 'project'].value = '';
        drawAll();
      });
    });
  }

  function unique(rows, key) {
    var seen = {};
    rows.forEach(function (r) { seen[r[key]] = true; });
    return Object.keys(seen).sort();
  }

  function buildControls(plot, card) {
    var box = card.querySelector('.stats-controls');
    if (plot.item && plot.rows.length) {
      var items = unique(plot.rows, plot.item);
      plot.off = {};
      var details = el('details', { 'class': 'stats-items' });
      var summary = el('summary');
      details.appendChild(summary);
      var label = function () {
        var on = items.filter(function (i) { return !plot.off[i]; }).length;
        summary.textContent = plot.itemWord + ': ' + (on === items.length ? 'all ' + on : on + ' of ' + items.length);
      };
      var bar = el('div', { 'class': 'stats-items-bar' });
      var chips = el('div', { 'class': 'daypick' });
      var setAll = function (checked) {
        chips.querySelectorAll('input').forEach(function (input) { input.checked = checked; plot.off[input.value] = !checked; });
        label(); draw(plot);
      };
      [['Select all', true], ['Deselect all', false]].forEach(function (pair) {
        var button = el('button', { type: 'button' }, pair[0]);
        button.addEventListener('click', function () { setAll(pair[1]); });
        bar.appendChild(button);
      });
      items.forEach(function (item) {
        var wrap = el('label', { 'class': 'chip-check' });
        var input = el('input', { type: 'checkbox', value: item });
        input.checked = true;
        input.addEventListener('change', function () { plot.off[item] = !input.checked; label(); draw(plot); });
        wrap.appendChild(input);
        wrap.appendChild(el('span', {}, item));
        chips.appendChild(wrap);
      });
      details.appendChild(bar);
      details.appendChild(chips);
      box.appendChild(details);
      label();
    }
  }

  function series(plot) {
    var sums = {}, parts = {};
    plot.rows.forEach(function (row) {
      if (row.day < period.from || row.day > period.to) return;
      if (!inScope(row)) return;
      if (plot.item && plot.off[row[plot.item]]) return;
      var sum = sums[row.day] = sums[row.day] || {};
      for (var k in row) if (typeof row[k] === 'number') sum[k] = (sum[k] || 0) + row[k];
      if (plot.breakdown && row[plot.breakdown]) {
        var part = parts[row.day] = parts[row.day] || {};
        part[row[plot.item]] = (part[row[plot.item]] || 0) + row[plot.breakdown];
      }
    });
    return daysBetween(period.from, period.to).map(function (day) {
      var point = plot.point(sums[day] || {});
      point.day = day;
      if (parts[day]) {
        var names = Object.keys(parts[day]).sort(function (a, b) { return parts[day][b] - parts[day][a]; });
        point.lines = names.slice(0, 6).map(function (n) { return parts[day][n] + ' × ' + n; });
        if (names.length > 6) point.lines.push('… and ' + (names.length - 6) + ' more');
      }
      return point;
    });
  }

  function niceMax(v) {
    if (v <= 0) return 1;
    var mag = Math.pow(10, Math.floor(Math.log10(v)));
    var steps = [1, 2, 4, 5, 10];
    for (var i = 0; i < steps.length; i++) if (v <= steps[i] * mag) return steps[i] * mag;
    return 10 * mag;
  }

  function draw(plot) {
    var card = document.querySelector('[data-plot="' + plot.id + '"]');
    var holder = card.querySelector('.stats-chart');
    var points = series(plot);
    var shown = points.filter(function (p) { return p.value != null; });
    holder.textContent = '';
    var table = card.querySelector('.stats-table tbody');
    table.textContent = '';
    shown.forEach(function (p) {
      var tr = el('tr');
      tr.appendChild(el('td', {}, longDay(p.day)));
      tr.appendChild(el('td', {}, plot.format(p.value)));
      tr.appendChild(el('td', {}, (p.lines || []).join(', ')));
      table.appendChild(tr);
    });
    if (!shown.length || (plot.mark === 'bar' && !shown.some(function (p) { return p.value > 0; }))) {
      holder.appendChild(el('p', { 'class': 'muted stats-empty' }, 'Nothing in this period.'));
      return;
    }
    var width = Math.max(holder.clientWidth, 280), height = 200;
    var left = 46, right = 8, top = 10, bottom = 22;
    var innerW = width - left - right, innerH = height - top - bottom;
    var max = plot.max || niceMax(Math.max.apply(null, shown.map(function (p) { return p.value; })));
    if (plot.mark === 'bar') max = Math.max(max, 2);  // counts: keep the middle gridline a whole number
    var slot = innerW / points.length;
    var x = function (i) { return left + slot * (i + 0.5); };
    var y = function (v) { return top + innerH * (1 - v / max); };
    var root = svg('svg', { viewBox: '0 0 ' + width + ' ' + height, width: width, height: height, role: 'img',
      'aria-label': card.querySelector('h2').textContent + ', ' + longDay(period.from) + ' to ' + longDay(period.to) });
    [0, 0.5, 1].forEach(function (f) {
      var gy = y(max * f);
      root.appendChild(svg('line', { x1: left, x2: width - right, y1: gy, y2: gy, 'class': 'stats-grid' }));
      var text = svg('text', { x: left - 6, y: gy + 4, 'text-anchor': 'end', 'class': 'stats-axis' });
      text.textContent = plot.format(max * f);
      root.appendChild(text);
    });
    var every = Math.ceil(points.length / Math.max(Math.floor(innerW / 64), 1));
    points.forEach(function (p, i) {
      if (i % every) return;
      var text = svg('text', { x: x(i), y: height - 6, 'text-anchor': 'middle', 'class': 'stats-axis' });
      text.textContent = shortDay(p.day);
      root.appendChild(text);
    });
    if (plot.mark === 'bar') {
      var barW = Math.max(Math.min(slot - 2, 28), 1);
      points.forEach(function (p, i) {
        if (!p.value) return;
        var h = Math.max(innerH * p.value / max, 1), r = Math.min(4, barW / 2, h);
        var bx = x(i) - barW / 2, by = top + innerH - h;
        // Rounded at the data end only, square on the baseline.
        root.appendChild(svg('path', { 'class': 'stats-bar', d: 'M' + bx + ' ' + (by + h) + 'V' + (by + r) + 'Q' + bx + ' ' + by + ' ' + (bx + r) + ' ' + by +
          'H' + (bx + barW - r) + 'Q' + (bx + barW) + ' ' + by + ' ' + (bx + barW) + ' ' + (by + r) + 'V' + (by + h) + 'Z' }));
      });
    } else {
      // A line joins consecutive days only: a day with nothing to measure is a gap, not a zero.
      var d = '';
      points.forEach(function (p, i) {
        if (p.value == null) return;
        d += (i && points[i - 1].value != null ? 'L' : 'M') + x(i) + ' ' + y(p.value);
      });
      root.appendChild(svg('path', { 'class': 'stats-line', d: d }));
      var radius = slot < 10 ? 2 : 4;
      points.forEach(function (p, i) {
        if (p.value != null) root.appendChild(svg('circle', { 'class': 'stats-dot', cx: x(i), cy: y(p.value), r: radius }));
      });
    }
    var cursor = svg('line', { y1: top, y2: top + innerH, 'class': 'stats-cursor', visibility: 'hidden' });
    root.appendChild(cursor);
    var tip = el('div', { 'class': 'stats-tip', hidden: '' });
    var show = function (event) {
      var box = root.getBoundingClientRect();
      var i = Math.min(Math.max(Math.floor((event.clientX - box.left - left) / slot), 0), points.length - 1);
      var p = points[i];
      cursor.setAttribute('x1', x(i)); cursor.setAttribute('x2', x(i)); cursor.setAttribute('visibility', 'visible');
      tip.textContent = '';
      tip.appendChild(el('span', { 'class': 'stats-tip-day' }, longDay(p.day)));
      tip.appendChild(el('strong', {}, p.value == null ? 'Nothing measured' : plot.format(p.value)));
      (p.value == null ? [] : p.lines || []).forEach(function (line) { tip.appendChild(el('span', {}, line)); });
      tip.hidden = false;
      var tx = Math.min(Math.max(x(i) - tip.offsetWidth / 2, 0), width - tip.offsetWidth);
      tip.style.left = tx + 'px';
    };
    root.addEventListener('pointermove', show);
    root.addEventListener('pointerdown', show);
    root.addEventListener('pointerleave', function () { tip.hidden = true; cursor.setAttribute('visibility', 'hidden'); });
    holder.appendChild(root);
    holder.appendChild(tip);
  }

  function drawAll() { plots.forEach(draw); }

  // -- the calendar: pick two days; a day is brighter the more jobs it had, within the month shown ----
  var activity = {};
  function countActivity() {
    activity = {};
    data.jobs.forEach(function (r) { activity[r.day] = (activity[r.day] || 0) + r.triggered; });
  }
  countActivity();
  var cal = document.getElementById('stats-calendar');
  var shownMonth = parse(period.to); shownMonth.setDate(1);
  var pending = null;  // the first of the two days, once picked

  function drawCalendar() {
    cal.textContent = '';
    var head = el('div', { 'class': 'stats-cal-head' });
    var prev = el('button', { type: 'button', 'aria-label': 'Previous month' }, '‹');
    var next = el('button', { type: 'button', 'aria-label': 'Next month' }, '›');
    prev.addEventListener('click', function () { shownMonth.setMonth(shownMonth.getMonth() - 1); drawCalendar(); });
    next.addEventListener('click', function () { shownMonth.setMonth(shownMonth.getMonth() + 1); drawCalendar(); });
    head.appendChild(prev);
    head.appendChild(el('strong', {}, MONTHS[shownMonth.getMonth()] + ' ' + shownMonth.getFullYear()));
    head.appendChild(next);
    cal.appendChild(head);
    var grid = el('div', { 'class': 'stats-cal-grid' });
    ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].forEach(function (d) { grid.appendChild(el('span', { 'class': 'stats-cal-dow' }, d)); });
    var days = [], d = new Date(shownMonth);
    while (d.getMonth() === shownMonth.getMonth()) { days.push(iso(d)); d.setDate(d.getDate() + 1); }
    var peak = Math.max.apply(null, days.map(function (day) { return activity[day] || 0; }));
    for (var blank = (shownMonth.getDay() + 6) % 7; blank > 0; blank--) grid.appendChild(el('span'));
    days.forEach(function (day) {
      var count = activity[day] || 0;
      var button = el('button', { type: 'button', 'class': 'stats-cal-day', title: longDay(day) + ': ' + plural(count, 'job') }, String(parse(day).getDate()));
      if (count) button.style.setProperty('--heat', String(0.15 + 0.85 * count / peak));
      var from = pending || period.from, to = pending || period.to;
      if (day >= from && day <= to) button.classList.add('in-period');
      if (day === from || day === to) button.classList.add('period-end');
      if (day === today) button.classList.add('is-today');
      button.addEventListener('click', function () {
        if (pending === null) { pending = day; }
        else { period = pending <= day ? { from: pending, to: day } : { from: day, to: pending }; pending = null; drawAll(); }
        drawCalendar();
      });
      grid.appendChild(button);
    });
    cal.appendChild(grid);
    document.getElementById('stats-period-label').textContent = pending
      ? 'From ' + longDay(pending) + ' — now pick the last day'
      : longDay(period.from) + ' – ' + longDay(period.to) + ' (' + plural(daysBetween(period.from, period.to).length, 'day') + ')';
  }

  document.querySelectorAll('[data-period]').forEach(function (button) {
    button.addEventListener('click', function () {
      var span = button.dataset.period;
      period = { from: span === 'all' ? firstDay : addDays(today, 1 - span), to: today };
      pending = null; shownMonth = parse(today); shownMonth.setDate(1);
      drawCalendar(); drawAll();
    });
  });

  buildScopeSelects();
  plots.forEach(function (plot) { buildControls(plot, document.querySelector('[data-plot="' + plot.id + '"]')); });
  drawCalendar();
  drawAll();
  var resizing;
  window.addEventListener('resize', function () { clearTimeout(resizing); resizing = setTimeout(drawAll, 150); });
  document.addEventListener('live:replaced', function (e) {
    if (!e.target || e.target.id !== 'stats-live') return;
    data = JSON.parse(document.getElementById('stats-data').textContent);
    plots.forEach(function (plot) { plot.rows = data[ROWS[plot.id]]; });
    countActivity();
    drawCalendar();
    drawAll();
  });
})();
