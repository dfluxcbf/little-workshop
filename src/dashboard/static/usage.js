// The Claude usage page: one week of per-day plots, with the weeks it is compared to drawn as
// dashed lines on the same graph. Everything is drawn from the summary the page embeds.
// Each plot is drawn at the width it has on screen (so its text stays readable on a phone),
// with dates on the x axis and a legend saying what each colour is.
(function () {
  'use strict';
  var data = JSON.parse(document.getElementById('usage-data').textContent);
  var SVG = 'http://www.w3.org/2000/svg';
  var WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var HEIGHT = 220, LEFT = 50, RIGHT = 10, TOP = 12, BOTTOM = 40;
  // Series colours, set as `style` (a presentation attribute cannot take a CSS variable): readable on
  // both the dark and the light theme. A comparison week is the same colour, dashed.
  var COLORS = ['#4f9ee8', '#e8a23d', '#3fbf86', '#c77ae0'];
  var compare = {};

  function svg(tag, attrs, text) {
    var node = document.createElementNS(SVG, tag);
    for (var k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (text != null) node.textContent = text;
    return node;
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function parse(day) { var p = day.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function dayMonth(day) { var d = parse(day); return d.getDate() + ' ' + MONTHS[d.getMonth()]; }
  function dollars(v) { return '$' + (v >= 100 ? Math.round(v) : v.toFixed(2)); }
  function percent(v) { return (Math.round(v * 1000) / 10) + '%'; }
  function tokens(v) { return v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? Math.round(v / 1e3) + 'k' : String(Math.round(v)); }

  // The x axis of the week's plots: each day's weekday over its date ("Mon" / "28 Sep").
  var dayLabels = data.days.map(function (d) { return [WEEKDAYS[(parse(d.day).getDay() + 6) % 7], dayMonth(d.day)]; });
  // The x axis of the week-by-week plot: the Monday each week starts on.
  var weekLabels = data.pays_for_itself.map(function (w) { return ['Week of', dayMonth(w.week)]; });

  var plots = {
    weighted: { format: dollars, stack: true, labels: dayLabels, series: function () {
      return [['Cache read', 'weighted_cache_read'], ['Cache write', 'weighted_cache_write'], ['Output', 'weighted_output'], ['Uncached input', 'weighted_input']]
        .map(function (p) { return { name: p[0], values: data.days.map(function (d) { return d[p[1]]; }) }; });
    }, compare: function (d) { return d.weighted; }, compareName: 'total' },
    context: { format: tokens, labels: dayLabels, series: function () { return [{ name: 'This week', values: data.days.map(function (d) { return d.average_context; }) }]; },
      compare: function (d) { return d.average_context; } },
    hit: { format: percent, max: 1, labels: dayLabels, series: function () { return [{ name: 'This week', values: data.days.map(function (d) { return d.cache_hit_ratio; }) }]; },
      compare: function (d) { return d.cache_hit_ratio; } },
    long: { format: percent, max: 1, labels: dayLabels, series: function () { return [{ name: 'This week', values: data.days.map(function (d) { return d.long_context_share; }) }]; },
      compare: function (d) { return d.long_context_share; } },
    pressure: { format: function (v) { return Math.round(v) + '%'; }, max: 100, labels: dayLabels, series: function () {
      var byDay = {};
      data.plan_pressure.forEach(function (p) { byDay[p.day] = p; });
      return [['5-hour window, highest', 'five_hour'], ['7-day window, highest', 'seven_day']].map(function (p) {
        return { name: p[0], values: data.days.map(function (d) { return byDay[d.day] ? byDay[d.day][p[1]] : null; }) };
      });
    } },
    pays: { format: dollars, labels: weekLabels, series: function () {
      return [['little-workshop', 'workshop'], ['Every other project', 'others']].map(function (p) {
        return { name: p[0], values: data.pays_for_itself.map(function (w) { return w[p[1]]; }) };
      });
    } }
  };
  var COMPARE_NAMES = { previous_week: 'Previous week', next_week: 'Next week', four_week_average: 'Last 4 weeks average' };

  function legend(card, entries) {
    var list = el('ul', 'usage-legend');
    entries.forEach(function (entry) {
      var item = el('li');
      var swatch = el('span', 'usage-swatch' + (entry.dashed ? ' dashed' : '') + (entry.bar ? ' bar' : ''));
      swatch.style.setProperty('--swatch', entry.color);
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(entry.name));
      list.appendChild(item);
    });
    card.appendChild(list);
  }

  function draw(id) {
    var plot = plots[id];
    var card = document.querySelector('[data-usage-plot="' + id + '"] .stats-chart');
    if (!card) return;
    card.textContent = '';
    var series = plot.series();
    var dashed = [];
    if (plot.compare) {
      Object.keys(compare).forEach(function (key) {
        if (!compare[key] || !data[key]) return;
        dashed.push({ name: COMPARE_NAMES[key] + (plot.compareName ? ' (' + plot.compareName + ')' : ''),
                      values: data[key].map(function (d) { return plot.compare(d); }) });
      });
    }
    var n = plot.labels.length;
    var totals = plot.stack ? plot.labels.map(function (_, i) { return series.reduce(function (sum, s) { return sum + (s.values[i] || 0); }, 0); }) : [];
    var all = (plot.stack ? totals : [].concat.apply([], series.map(function (s) { return s.values; })))
      .concat([].concat.apply([], dashed.map(function (s) { return s.values; }))).filter(function (v) { return v != null; });
    if (!all.length || !n) { card.appendChild(el('p', 'muted', 'No data for this week.')); return; }

    var width = Math.max(card.clientWidth, 280);
    var max = plot.max || Math.max.apply(null, all) || 1;
    var root = svg('svg', { viewBox: '0 0 ' + width + ' ' + HEIGHT, width: width, height: HEIGHT, role: 'img',
                            'aria-label': card.parentNode.querySelector('h2').textContent });
    var x = function (i) { return LEFT + (width - LEFT - RIGHT) * (n === 1 ? 0.5 : (i + 0.5) / n); };
    var y = function (v) { return TOP + (HEIGHT - TOP - BOTTOM) * (1 - Math.min(v, max) / max); };
    [0, 0.5, 1].forEach(function (f) {
      root.appendChild(svg('line', { x1: LEFT, x2: width - RIGHT, y1: y(max * f), y2: y(max * f), 'class': 'stats-grid' }));
      root.appendChild(svg('text', { x: LEFT - 6, y: y(max * f) + 4, 'text-anchor': 'end', 'class': 'stats-axis' }, plot.format(max * f)));
    });
    // Two lines per tick: the weekday (or "Week of") over the date; when there is no room for every
    // tick, every other one is shown.
    var every = Math.ceil(n / Math.max(Math.floor((width - LEFT - RIGHT) / 48), 1));
    plot.labels.forEach(function (label, i) {
      if (i % every) return;
      var text = svg('text', { x: x(i), y: HEIGHT - BOTTOM + 16, 'text-anchor': 'middle', 'class': 'stats-axis' });
      text.appendChild(svg('tspan', { x: x(i), dy: 0 }, label[0]));
      text.appendChild(svg('tspan', { x: x(i), dy: 14 }, label[1]));
      root.appendChild(text);
    });

    var entries = [];
    if (plot.stack) {
      var barWidth = Math.min((width - LEFT - RIGHT) / n * 0.6, 48);
      plot.labels.forEach(function (label, i) {
        var base = 0;
        series.forEach(function (s, k) {
          var v = s.values[i] || 0;
          if (!v) return;
          var rect = svg('rect', { x: x(i) - barWidth / 2, y: y(base + v), width: barWidth, height: Math.max(y(base) - y(base + v), 0.5),
                                   style: 'fill:' + COLORS[k % COLORS.length] });
          rect.appendChild(svg('title', {}, label.join(' ') + ', ' + s.name + ': ' + plot.format(v)));
          root.appendChild(rect);
          base += v;
        });
      });
      series.forEach(function (s, k) { entries.push({ name: s.name, color: COLORS[k % COLORS.length], bar: true }); });
    } else {
      series.forEach(function (s, k) {
        root.appendChild(line(s, COLORS[k % COLORS.length], false));
        entries.push({ name: s.name, color: COLORS[k % COLORS.length] });
      });
    }
    dashed.forEach(function (s, k) {
      var color = plot.stack ? 'var(--ink-soft)' : COLORS[(k + 1) % COLORS.length];
      root.appendChild(line(s, color, true));
      entries.push({ name: s.name, color: color, dashed: true });
    });
    function line(s, color, dash) {
      var group = svg('g');
      var path = '';
      s.values.forEach(function (v, i) {
        if (v == null) return;
        path += (path ? 'L' : 'M') + x(i) + ' ' + y(v);
        var dot = svg('circle', { cx: x(i), cy: y(v), r: 3, style: 'fill:' + color });
        dot.appendChild(svg('title', {}, plot.labels[i].join(' ') + ', ' + s.name + ': ' + plot.format(v)));
        group.appendChild(dot);
      });
      group.insertBefore(svg('path', { d: path, style: 'fill:none;stroke:' + color + ';stroke-width:2' + (dash ? ';stroke-dasharray:5 4' : '') }),
                         group.firstChild);
      return group;
    }
    card.appendChild(root);
    legend(card, entries);
  }

  function drawAll() { Object.keys(plots).forEach(draw); }
  document.querySelectorAll('#usage-compare button').forEach(function (button) {
    button.addEventListener('click', function () {
      var key = button.getAttribute('data-compare');
      compare[key] = !compare[key];
      button.classList.toggle('primary', compare[key]);
      drawAll();
    });
  });
  // Each cell carries its column's name, which a phone shows beside the value when a row is stacked.
  document.querySelectorAll('.table-scroll.stack table').forEach(function (table) {
    var names = Array.prototype.map.call(table.querySelectorAll('thead th'), function (th) { return th.textContent; });
    table.querySelectorAll('tbody tr').forEach(function (row) {
      Array.prototype.forEach.call(row.children, function (cell, i) {
        if (!cell.hasAttribute('colspan') && names[i]) cell.setAttribute('data-label', names[i]);
      });
    });
  });
  var resizing;
  window.addEventListener('resize', function () { clearTimeout(resizing); resizing = setTimeout(drawAll, 150); });
  drawAll();
})();
