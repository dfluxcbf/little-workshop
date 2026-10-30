// The Claude peek card (templates/_peek.html): what a task's Claude has done so far, from its
// transcript. The page embeds the newest events; while Claude works, the page's live stream
// (static/live.js) sends each new batch with where it starts, and a batch that does not follow on
// from what is shown is caught up from /jobs/<id>/peek.json instead.
(function () {
  'use strict';
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function tag(event) {
    if (event.kind === 'tool') return event.tool || 'tool';
    if (event.kind === 'error') return (event.tool || 'tool') + ' failed';
    if (event.kind === 'text') return 'Claude';
    if (event.kind === 'prompt') return 'Prompt';
    return '·';
  }
  function time(at) {
    var d = at ? new Date(at) : null;
    return d && !isNaN(d.getTime()) ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
  }

  document.querySelectorAll('[data-peek-job]').forEach(function (card) {
    var data = JSON.parse(card.querySelector('.peek-data').textContent);
    var list = card.querySelector('.peek-feed');
    var state = card.querySelector('.peek-state');
    var empty = card.querySelector('.peek-empty');
    var jobId = card.getAttribute('data-peek-job');
    var next = data.next || 0, catching = false;

    function add(events) {
      var atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
      (events || []).forEach(function (event) {
        var item = el('li', 'peek-item peek-kind-' + event.kind);
        item.appendChild(el('span', 'peek-time', time(event.at)));
        item.appendChild(el('span', 'peek-tag', tag(event)));
        item.appendChild(el('span', 'peek-text', event.text));
        list.appendChild(item);
      });
      if (empty && list.children.length) empty.hidden = true;
      if (atBottom) list.scrollTop = list.scrollHeight;
    }
    function running(yes) {
      card.setAttribute('data-peek-running', yes ? '1' : '0');
      if (state) state.textContent = yes ? 'live' : 'finished';
    }
    function catchUp() {
      if (catching) return;
      catching = true;
      fetch('/jobs/' + jobId + '/peek.json?after=' + next, { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (peek) { if (peek) { add(peek.events); next = peek.next; running(peek.running); } })
        .catch(function () {})
        .then(function () { catching = false; });
    }

    if (data.skipped) list.appendChild(el('li', 'peek-skipped', data.skipped < 0 ? 'Earlier events are not shown.' : data.skipped + ' earlier events are not shown.'));
    add(data.events);
    list.scrollTop = list.scrollHeight;
    document.addEventListener('live:update', function (e) {
      var message = e.detail;
      if (!message || message.topic !== 'peek:' + jobId || !message.data) return;
      if (message.data.from !== next) { catchUp(); return; }
      add(message.data.events);
      next = message.data.next;
      running(message.data.running);
    });
    document.addEventListener('live:reconnected', catchUp);
  });
})();
