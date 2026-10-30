// Live pages (dashboard/live.py): one event stream per page, subscribed to the topics the page shows
// (<body data-live="jobs,job:…">, plus "attention" for the banner on every page). When one changes,
// the page fetches itself once and replaces each [data-live-region] whose content changed — never a
// region the user is typing in or has edited, and keeping the <details> they have open. Nothing here
// polls: without a change, nothing is fetched.
(function () {
  'use strict';
  var body = document.body;
  var pageTopics = (body.getAttribute('data-live') || '').split(',').filter(Boolean);
  var topics = pageTopics.indexOf('attention') < 0 ? pageTopics.concat(['attention']) : pageTopics;
  var SETTLE = 300;  // let a burst of changes settle into one fetch
  var GAP = Number(body.getAttribute('data-live-gap') || 1500);  // and never fetch more often than this
  var timer = null, busy = false, again = false, last = 0, dropped = false;

  function hasRegions() { return document.querySelector('[data-live-region]') !== null; }

  function schedule() {
    if (!hasRegions()) return;
    clearTimeout(timer);
    timer = setTimeout(refresh, Math.max(SETTLE, last + GAP - Date.now()));
  }

  function typingIn(region) {
    var active = document.activeElement;
    return region.dataset.dirty === '1' ||
      (active && region.contains(active) && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName) && active.type !== 'submit');
  }

  function openKeys(region) {
    return Array.prototype.map.call(region.querySelectorAll('details[open]'), keyOf);
  }
  function keyOf(details) {
    if (details.id) return '#' + details.id;
    if (details.dataset.key) return 'k:' + details.dataset.key;
    var all = details.closest('[data-live-region]').querySelectorAll('details');
    return 'i:' + Array.prototype.indexOf.call(all, details);
  }

  function swap(doc) {
    var deferred = false;
    document.querySelectorAll('[data-live-region]').forEach(function (region) {
      var fresh = region.id && doc.getElementById(region.id);
      if (!fresh) return;
      var html = fresh.innerHTML;
      if (html === region.dataset.liveHtml) return;  // nothing new since the last look
      if (typingIn(region)) { deferred = true; return; }  // the user's own input wins: look again later
      var open = openKeys(region);
      Array.prototype.forEach.call(fresh.attributes, function (a) { if (a.name.indexOf('data-') === 0 && a.name !== 'data-live-html') region.setAttribute(a.name, a.value); });
      region.innerHTML = html;
      region.dataset.liveHtml = html;
      region.querySelectorAll('details').forEach(function (d) { if (open.indexOf(keyOf(d)) >= 0) d.open = true; });
      region.dispatchEvent(new CustomEvent('live:replaced', { bubbles: true }));
    });
    if (deferred) { again = true; }
  }

  function refresh() {
    if (busy) { again = true; return; }
    busy = true;
    last = Date.now();
    fetch(location.href, { headers: { 'X-Live': '1' }, cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) { if (html) swap(new DOMParser().parseFromString(html, 'text/html')); })
      .catch(function () {})
      .then(function () { busy = false; if (again) { again = false; setTimeout(schedule, GAP); } });
  }

  // What the user types in a region is theirs until they send it: the region is not replaced meanwhile.
  document.addEventListener('input', function (e) {
    var region = e.target.closest && e.target.closest('[data-live-region]');
    if (region && e.target.matches('input:not([type=search]), textarea')) region.dataset.dirty = '1';
  });

  // A page whose content moves with the clock (the status page's last-2-hours windows) asks for a look itself.
  window.liveRefresh = schedule;

  if (!window.EventSource) return;
  var source = null;

  function onUpdate(e) {
    var message;
    try { message = JSON.parse(e.data); } catch (err) { return; }
    document.dispatchEvent(new CustomEvent('live:update', { detail: message }));
    if (message.topic === 'attention' && window.refreshAttention) window.refreshAttention();
    if (message.topic.indexOf('peek:') === 0) return;  // a peek card appends what it is sent (static/peek.js)
    if (pageTopics.indexOf(message.topic) >= 0) schedule();  // "attention" alone only moves the banner
  }
  function catchUp() {  // what changed while the stream was down was missed: look once
    schedule();
    if (window.refreshAttention) window.refreshAttention();
    document.dispatchEvent(new CustomEvent('live:reconnected'));
  }
  function connect() {
    source = new EventSource('/live/events?topics=' + encodeURIComponent(topics.join(',')));
    source.addEventListener('update', onUpdate);
    source.addEventListener('error', function () { dropped = true; });
    source.addEventListener('open', function () { if (dropped) { dropped = false; catchUp(); } });
  }
  function disconnect() { if (source) { source.close(); source = null; } }

  // A tab out of sight lets its stream go (a browser keeps few connections per site open), and picks it
  // up again, catching up once, when it is looked at.
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { disconnect(); dropped = true; return; }
    if (!source) connect();
  });
  window.addEventListener('pagehide', disconnect);
  if (!document.hidden) connect(); else dropped = true;
})();
