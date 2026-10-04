// The Claude usage page: one week of per-day plots, with the weeks it is compared to drawn as
// dashed lines on the same graph. Everything is drawn from the summary the page embeds.
(function () {
  'use strict';
  var data = JSON.parse(document.getElementById('usage-data').textContent);
  var SVG = 'http://www.w3.org/2000/svg';
  var DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var W = 720, H = 220, LEFT = 56, RIGHT = 12, TOP = 12, BOTTOM = 26;
  var COLORS = ['var(--accent, #4b8)', '#c84', '#59c', '#b6b'];
  var compare = {};

  function svg(tag, attrs, text) {
    var node = document.createElementNS(SVG, tag);
    for (var k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (text != null) node.textContent = text;
    return node;
  }
  function dollars(v) { return '$' + (v >= 100 ? Math.round(v) : v.toFixed(2)); }
  function percent(v) { return (Math.round(v * 1000) / 10) + '%'; }
  function tokens(v) { return v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? Math.round(v / 1e3) + 'k' : String(Math.round(v)); }
  function weighted(day) { return day.weighted; }

  // Each plot: series of {name, values[]} over `labels`, drawn as lines; `stack` draws stacked bars instead.
  var plots = {
    weighted: { format: dollars, stack: true, labels: DAYS, series: function () {
      return [['Cache read', 'weighted_cache_read'], ['Cache write', 'weighted_cache_write'], ['Output', 'weighted_output'], ['Input', 'weighted_input']]
        .map(function (p) { return { name: p[0], values: data.days.map(function (d) { return d[p[1]]; }) }; });
    }, compare: weighted },
    context: { format: tokens, labels: DAYS, series: function () { return [{ name: 'This week', values: data.days.map(function (d) { return d.average_context; }) }]; },
      compare: function (d) { return d.average_context; } },
    hit: { format: percent, max: 1, labels: DAYS, series: function () { return [{ name: 'This week', values: data.days.map(function (d) { return d.cache_hit_ratio; }) }]; },
      compare: function (d) { return d.cache_hit_ratio; } },
    long: { format: percent, max: 1, labels: DAYS, series: function () { return [{ name: 'This week', values: data.days.map(function (d) { return d.long_context_share; }) }]; },
      compare: function (d) { return d.long_context_share; } },
    pressure: { format: function (v) { return Math.round(v) + '%'; }, max: 100, labels: DAYS, series: function () {
      var byDay = {};
      data.plan_pressure.forEach(function (p) { byDay[p.day] = p; });
      return [['5-hour', 'five_hour'], ['7-day', 'seven_day']].map(function (p) {
        return { name: p[0], values: data.days.map(function (d) { return byDay[d.day] ? byDay[d.day][p[1]] : null; }) };
      });
    } },
    pays: { format: dollars, labels: data.pays_for_itself.map(function (w) { return w.week.slice(5); }), series: function () {
      return [['little-workshop', 'workshop'], ['Other projects', 'others']].map(function (p) {
        return { name: p[0], values: data.pays_for_itself.map(function (w) { return w[p[1]]; }) };
      });
    } }
  };
  var COMPARE_NAMES = { previous_week: 'Previous week', next_week: 'Next week', four_week_average: 'Last 4 weeks average' };

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
        dashed.push({ name: COMPARE_NAMES[key], values: data[key].map(function (d) { return plot.compare(d); }) });
      });
    }
    var n = plot.labels.length;
    var totals = plot.stack ? plot.labels.map(function (_, i) { return series.reduce(function (sum, s) { return sum + (s.values[i] || 0); }, 0); }) : [];
    var all = (plot.stack ? totals : [].concat.apply([], series.map(function (s) { return s.values; })))
      .concat([].concat.apply([], dashed.map(function (s) { return s.values; }))).filter(function (v) { return v != null; });
    if (!all.length || !n) { card.appendChild(document.createTextNode('No data for this week.')); return; }
    var max = plot.max || Math.max.apply(null, all) || 1;
    var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, width: '100%', role: 'img' });
    var x = function (i) { return LEFT + (W - LEFT - RIGHT) * (n === 1 ? 0.5 : (i + 0.5) / n); };
    var y = function (v) { return TOP + (H - TOP - BOTTOM) * (1 - Math.min(v, max) / max); };
    [0, 0.5, 1].forEach(function (f) {
      root.appendChild(svg('line', { x1: LEFT, x2: W - RIGHT, y1: y(max * f), y2: y(max * f), stroke: 'currentColor', 'stroke-opacity': 0.15 }));
      root.appendChild(svg('text', { x: LEFT - 6, y: y(max * f) + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'currentColor' }, plot.format(max * f)));
    });
    plot.labels.forEach(function (label, i) {
      root.appendChild(svg('text', { x: x(i), y: H - 8, 'text-anchor': 'middle', 'font-size': 11, fill: 'currentColor' }, label));
    });
    if (plot.stack) {
      var width = (W - LEFT - RIGHT) / n * 0.6;
      plot.labels.forEach(function (_, i) {
        var base = 0;
        series.forEach(function (s, k) {
          var v = s.values[i] || 0;
          if (!v) return;
          var rect = svg('rect', { x: x(i) - width / 2, y: y(base + v), width: width, height: Math.max(y(base) - y(base + v), 0.5), fill: COLORS[k % COLORS.length] });
          rect.appendChild(svg('title', {}, s.name + ': ' + plot.format(v)));
          root.appendChild(rect);
          base += v;
        });
      });
    } else {
      series.forEach(function (s, k) { root.appendChild(line(s, COLORS[k % COLORS.length], false)); });
    }
    dashed.forEach(function (s, k) { root.appendChild(line(s, COLORS[(k + 1) % COLORS.length], true)); });
    function line(s, color, dash) {
      var group = svg('g');
      var path = '';
      s.values.forEach(function (v, i) {
        if (v == null) return;
        path += (path ? 'L' : 'M') + x(i) + ' ' + y(v);
        var dot = svg('circle', { cx: x(i), cy: y(v), r: 3, fill: color });
        dot.appendChild(svg('title', {}, s.name + ': ' + plot.format(v)));
        group.appendChild(dot);
      });
      var attrs = { d: path, fill: 'none', stroke: color, 'stroke-width': 2 };
      if (dash) attrs['stroke-dasharray'] = '5 4';
      group.insertBefore(svg('path', attrs), group.firstChild);
      return group;
    }
    card.appendChild(root);
    var legend = document.createElement('p');
    legend.className = 'muted';
    legend.textContent = series.map(function (s) { return s.name; }).concat(dashed.map(function (s) { return s.name + ' (dashed)'; })).join(' · ');
    card.appendChild(legend);
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
  drawAll();
})();
