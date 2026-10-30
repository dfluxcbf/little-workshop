/* Documentation review viewer (/docs/{project}/view).

   Two pages side by side — the rendered document and its comments — switched by
   swiping (left: comments, right: documentation) or the toggle button. Each page
   scrolls on its own; just before a switch the other page is scrolled so the
   same highlight/comment sits at the same height on screen, mapping positions
   piecewise between highlight i and comment slot i (text between highlights maps
   onto the separators between slots).

   A comment is anchored by its source line range plus the exact highlighted text
   and a little text around it, located again on every render. Marks always take
   the height of what is on the current page: on the documentation page a white
   mark on the right spans the highlighted section; on the comments page the
   yellow (open) or blue (done) strip on the left spans the comment.

   Comments autosave 2 seconds after typing stops, and at once when the page is
   hidden (keepalive), so switching away from the installed app loses nothing. */
(function () {
  "use strict";

  var SAVE_DELAY = 2000;
  var CONTEXT = 32;
  var root = document.getElementById("rv");
  if (!root) return;
  var project = root.dataset.project, path = root.dataset.path, branch = root.dataset.branch;
  var data = JSON.parse(document.getElementById("rv-data").textContent);
  var comments = data.comments || [];

  var viewport = root.querySelector(".rv-viewport");
  var track = root.querySelector(".rv-track");
  var docPane = root.querySelector(".rv-docs");
  var comPane = root.querySelector(".rv-comments");
  var docEl = root.querySelector(".rv-md");
  var docWrap = root.querySelector(".rv-doc");
  var marksEl = root.querySelector(".rv-marks");
  var slotsEl = root.querySelector(".rv-slots");
  var emptyEl = root.querySelector(".rv-empty");
  var fab = root.querySelector('.rv-fab[data-rv="highlight"]');
  var certFab = root.querySelector('.rv-fab[data-rv="certify"]');
  var savingEl = root.querySelector(".rv-saving");
  var toastEl = root.querySelector(".rv-toast");

  var page = "docs";
  var geo = {};      // comment id -> {top, bottom, lost}
  var ranges = {};   // comment id -> {s, e}: text offsets of a yellow/blue text highlight
  var anchorEls = {}; // comment id -> elements making up its highlight
  var editing = null;

  function byId(id) { for (var i = 0; i < comments.length; i++) if (comments[i].id === id) return comments[i]; return null; }
  // A certified comment always reads "Certified."; its own text is kept but never shown.
  function hasBody(c) { return !!c.certified || !!(c.body && c.body.trim()); }
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    var b = new Uint8Array(16); crypto.getRandomValues(b);
    b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
    var h = Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join("");
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
  }
  function escapeHtml(s) { return s.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  var toastTimer;
  function toast(msg) {
    toastEl.textContent = msg; toastEl.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { toastEl.hidden = true; }, 3500);
  }

  // ---- the document's text, as one string over its text nodes --------------

  function textIndex() {
    var nodes = [], starts = [], text = "";
    var walker = document.createTreeWalker(docEl, NodeFilter.SHOW_TEXT);
    var n;
    while ((n = walker.nextNode())) { nodes.push(n); starts.push(text.length); text += n.nodeValue; }
    return { nodes: nodes, starts: starts, text: text };
  }

  function offsetOf(node, offset) {
    var r = document.createRange();
    r.setStart(docEl, 0);
    r.setEnd(node, offset);
    return r.toString().length;
  }

  function nodeAt(idx, offset) {
    for (var i = idx.nodes.length - 1; i >= 0; i--) if (idx.starts[i] <= offset) return idx.nodes[i];
    return idx.nodes[0] || null;
  }

  function lineOf(node, attr) {
    var el = node && (node.nodeType === 1 ? node : node.parentNode);
    el = el && el.closest ? el.closest("[data-ls]") : null;
    return el ? parseInt(el.getAttribute(attr), 10) : 1;
  }

  function anchorFor(idx, s, e) {
    return {
      start_line: lineOf(nodeAt(idx, s), "data-ls"),
      end_line: Math.max(lineOf(nodeAt(idx, s), "data-ls"), lineOf(nodeAt(idx, Math.max(s, e - 1)), "data-le")),
      exact: idx.text.slice(s, e),
      prefix: idx.text.slice(Math.max(0, s - CONTEXT), s),
      suffix: idx.text.slice(e, e + CONTEXT),
    };
  }

  function commonTail(a, b) { var n = 0; while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n++; return n; }
  function commonHead(a, b) { var n = 0; while (n < a.length && n < b.length && a[n] === b[n]) n++; return n; }

  function findAnchor(idx, anchor) {
    var exact = anchor && anchor.exact;
    if (!exact) return null;
    var best = null, bestScore = -Infinity, from = 0, guard = 0;
    while (guard++ < 500) {
      var at = idx.text.indexOf(exact, from);
      if (at < 0) break;
      var score = commonTail(idx.text.slice(Math.max(0, at - CONTEXT), at), anchor.prefix || "")
        + commonHead(idx.text.slice(at + exact.length, at + exact.length + CONTEXT), anchor.suffix || "")
        - Math.min(Math.abs(lineOf(nodeAt(idx, at), "data-ls") - (anchor.start_line || 1)), 1000) * 0.05;
      if (score > bestScore) { bestScore = score; best = { s: at, e: at + exact.length }; }
      from = at + 1;
    }
    return best;
  }

  var NO_WRAP = /^(TABLE|THEAD|TBODY|TFOOT|TR|UL|OL)$/;

  function wrap(idx, s, e, cls, id) {
    var made = [];
    for (var i = idx.nodes.length - 1; i >= 0; i--) {
      var node = idx.nodes[i], ns = idx.starts[i], ne = ns + node.nodeValue.length;
      if (ne <= s || ns >= e || NO_WRAP.test(node.parentNode.nodeName)) continue;
      var a = Math.max(s - ns, 0), b = Math.min(e - ns, node.nodeValue.length);
      if (b <= a) continue;
      if (b < node.nodeValue.length) node.splitText(b);
      var mid = a > 0 ? node.splitText(a) : node;
      var mark = document.createElement("mark");
      mark.className = cls;
      mark.dataset.id = id;
      mid.parentNode.replaceChild(mark, mid);
      mark.appendChild(mid);
      made.unshift(mark);
    }
    return made;
  }

  function blocksForLines(s, e) {
    var all = Array.prototype.slice.call(docEl.querySelectorAll("[data-ls]"));
    var inside = all.filter(function (el) { return +el.dataset.ls >= s && +el.dataset.le <= e; });
    var top = inside.filter(function (el) { var p = el.parentNode.closest("[data-ls]"); return !p || inside.indexOf(p) < 0; });
    if (top.length) return top;
    var around = all.filter(function (el) { return +el.dataset.ls <= s && +el.dataset.le >= e; });
    return around.length ? [around[around.length - 1]] : [];
  }

  function blockAtLine(line) {
    var all = docEl.children;
    for (var i = 0; i < all.length; i++) if (all[i].dataset && +all[i].dataset.ls >= line) return all[i];
    return null;
  }

  // ---- placing every comment on the document -------------------------------

  function clearAnchors() {
    docEl.querySelectorAll("mark.rv-hl").forEach(function (m) {
      var p = m.parentNode; while (m.firstChild) p.insertBefore(m.firstChild, m); p.removeChild(m);
    });
    docEl.querySelectorAll(".rv-removed").forEach(function (m) { m.remove(); });
    docEl.normalize();
  }

  // Text offsets of the blocks a job edited: a done section is highlighted (blue) like any other.
  function blockSpan(blocks) {
    if (!blocks.length) return null;
    var r = document.createRange();
    r.setStartBefore(blocks[0]); r.setEndAfter(blocks[blocks.length - 1]);
    var text = textIndex().text;
    var s = offsetOf(r.startContainer, r.startOffset), e = offsetOf(r.endContainer, r.endOffset);
    while (s < e && /\s/.test(text[s])) s++;
    while (e > s && /\s/.test(text[e - 1])) e--;
    return e > s ? { s: s, e: e } : null;
  }

  function applyAnchors() {
    clearAnchors();
    ranges = {}; anchorEls = {};
    comments.forEach(function (c) {
      var ch = c.change;
      var samePath = ch && (!ch.path || ch.path === path);
      if (!c.certified && c.status === "done" && ch && ch.kind === "edited") {
        if (samePath && ch.found !== false) {
          var span = blockSpan(blocksForLines(ch.start_line, ch.end_line));
          if (span) {
            ranges[c.id] = span;
            anchorEls[c.id] = wrap(textIndex(), span.s, span.e, "rv-hl is-done", c.id);
          }
        }
        return;
      }
      if (ch && ch.kind === "removed") {
        if (!samePath) return;
        var marker = document.createElement("div");
        marker.className = "rv-removed" + (c.status === "done" ? "" : " is-open");
        marker.dataset.id = c.id;
        marker.title = "A section was removed here";
        var before = blockAtLine(ch.start_line);
        if (before) docEl.insertBefore(marker, before); else docEl.appendChild(marker);
        anchorEls[c.id] = [marker];
        return;
      }
      var idx = textIndex();
      var found = findAnchor(idx, c.anchor);
      if (!found) return;
      ranges[c.id] = found;
      anchorEls[c.id] = wrap(idx, found.s, found.e, "rv-hl " + (c.certified ? "is-certified" : c.status === "done" ? "is-done" : "is-open"), c.id);
    });
  }

  function posIn(pane, el) {
    return el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop;
  }

  function measure() {
    geo = {};
    comments.forEach(function (c) {
      var els = anchorEls[c.id] || [];
      var top = Infinity, bottom = -Infinity;
      els.forEach(function (el) {
        Array.prototype.forEach.call(el.getClientRects(), function (r) {
          if (!r.height) return;
          var t = r.top - docPane.getBoundingClientRect().top + docPane.scrollTop;
          top = Math.min(top, t); bottom = Math.max(bottom, t + r.height);
        });
      });
      if (top === Infinity) {
        var line = (c.change && c.change.start_line) || (c.anchor && c.anchor.start_line) || 1;
        var el = blockAtLine(line);
        top = el ? posIn(docPane, el) : docPane.scrollHeight;
        geo[c.id] = { top: top, bottom: top + 4, lost: true };
      } else {
        geo[c.id] = { top: top, bottom: bottom, lost: false };
      }
    });
  }

  function ordered() {
    return comments.slice().sort(function (a, b) {
      return (geo[a.id].top - geo[b.id].top) || ((a.created_at || "") < (b.created_at || "") ? -1 : 1);
    });
  }

  function renderMarks() {
    marksEl.innerHTML = "";
    var wrapTop = posIn(docPane, docWrap);
    comments.forEach(function (c) {
      var g = geo[c.id];
      if (!hasBody(c) || !g || g.lost) return;
      var m = document.createElement("div");
      m.className = "rv-mark" + (c.certified ? " is-certified" : "");
      m.style.top = (g.top - wrapTop) + "px";
      m.style.height = Math.max(6, g.bottom - g.top) + "px";
      marksEl.appendChild(m);
    });
  }

  // ---- the comments page ----------------------------------------------------

  function slotBody(c) {
    if (editing === c.id) return null;
    if (c.certified) return "<p>Certified.</p>";
    if (!hasBody(c)) return '<p class="rv-placeholder">Tap to comment</p>';
    return c.html != null ? c.html : "<p>" + escapeHtml(c.body) + "</p>";
  }

  function renderSlot(c) {
    var el = document.createElement("div");
    el.className = "rv-slot " + (c.certified ? "is-certified" : c.status === "done" ? "is-done" : "is-open") + (hasBody(c) ? "" : " is-empty") + (c.job_id || c.certified ? " is-locked" : "");
    el.dataset.id = c.id;
    if (selectedSlot === c.id) el.classList.add("is-selected");
    var body = document.createElement("div");
    body.className = "rv-slot-body";
    var html = slotBody(c);
    if (html !== null) body.innerHTML = html;
    el.appendChild(body);
    var foot = document.createElement("div");
    foot.className = "rv-slot-foot";
    if (c.job_id) foot.innerHTML = '<span class="rv-chip">Being updated</span>';
    if (c.status === "done" && !c.certified) {
      foot.innerHTML = '<span class="rv-chip rv-chip-done">Done</span>' + (c.note ? '<span class="rv-note-text">' + escapeHtml(c.note) + "</span>" : "")
        + '<button type="button" class="rv-finish" data-finish="' + c.id + '">Finish</button>';
    }
    if (geo[c.id] && geo[c.id].lost) foot.insertAdjacentHTML("afterbegin", '<span class="rv-chip">Section not found</span>');
    if (foot.innerHTML) el.appendChild(foot);
    return el;
  }

  function renderSlots() {
    if (editing) return; // never pull a textarea out from under the user
    slotsEl.innerHTML = "";
    ordered().forEach(function (c) { slotsEl.appendChild(renderSlot(c)); });
    emptyEl.hidden = comments.length > 0;
  }

  // Re-render one slot inside its existing element, so a tap already on its way
  // to it (blurring another slot's editor) still lands.
  function refreshSlot(id) {
    var c = byId(id), el = slotsEl.querySelector('.rv-slot[data-id="' + id + '"]');
    if (!c || !el || editing === id) return;
    var fresh = renderSlot(c);
    el.className = fresh.className;
    el.innerHTML = fresh.innerHTML;
  }

  function relayout() {
    applyAnchors();
    measure();
    renderMarks();
    renderSlots();
  }

  // ---- saving -----------------------------------------------------------------

  var timers = {}, inFlight = 0, dirty = {}, savingSince = 0;

  function showSaving(on) {
    if (on) { savingSince = Date.now(); savingEl.hidden = false; return; }
    setTimeout(function () { if (!inFlight) savingEl.hidden = true; }, Math.max(0, 500 - (Date.now() - savingSince)));
  }

  function payload(c) {
    return JSON.stringify({ branch: c.branch, path: c.path, anchor: c.anchor, body: c.body || "", status: c.status, change: c.change || null, certified: !!c.certified });
  }

  function save(id, keepalive) {
    clearTimeout(timers[id]); delete timers[id];
    var c = byId(id);
    if (!c) return Promise.resolve();
    delete dirty[id];
    inFlight++; showSaving(true);
    return fetch("/docs/" + encodeURIComponent(project) + "/comments/" + id, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: payload(c), keepalive: !!keepalive,
    }).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok) throw new Error(body.detail || ("save failed (" + r.status + ")"));
        var cur = byId(id);
        if (cur) { cur.html = body.html; cur.updated_at = body.updated_at; cur.created_at = body.created_at; }
      });
    }).catch(function (err) {
      dirty[id] = true;
      toast("Not saved: " + err.message);
    }).then(function () {
      inFlight--; if (!inFlight) showSaving(false);
    });
  }

  function saveSoon(id) {
    dirty[id] = true;
    clearTimeout(timers[id]);
    timers[id] = setTimeout(function () { save(id); }, SAVE_DELAY);
  }

  function flushAll(keepalive) {
    Object.keys(dirty).forEach(function (id) { save(id, keepalive); });
  }

  function remove(id) {
    clearTimeout(timers[id]); delete timers[id]; delete dirty[id];
    comments = comments.filter(function (c) { return c.id !== id; });
    inFlight++; showSaving(true);
    return fetch("/docs/" + encodeURIComponent(project) + "/comments/" + id, { method: "DELETE" }).then(function (r) {
      if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || "delete failed"); });
    }).catch(function (err) { toast("Not removed: " + err.message); }).then(function () {
      inFlight--; if (!inFlight) showSaving(false);
    });
  }

  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flushAll(true); });
  window.addEventListener("pagehide", function () { flushAll(true); });

  // ---- editing a comment --------------------------------------------------------

  function reopen(c) {
    // Editing a done comment makes it open again: its highlight becomes the section the job changed.
    if (c.status !== "done") return;
    c.status = "open";
    c.note = "";
    if (c.change && c.change.kind === "edited") {
      var els = anchorEls[c.id] || [];
      if (els.length) {
        var r = document.createRange();
        r.setStartBefore(els[0]); r.setEndAfter(els[els.length - 1]);
        var idx = textIndex();
        var s = offsetOf(r.startContainer, r.startOffset), e = offsetOf(r.endContainer, r.endOffset);
        while (s < e && /\s/.test(idx.text[s])) s++;
        while (e > s && /\s/.test(idx.text[e - 1])) e--;
        c.anchor = anchorFor(idx, s, e);
      }
      c.change = null;
    }
  }

  function startEdit(id) {
    var c = byId(id);
    if (c && c.certified) { toast("A certified section's comment cannot be edited. Ctrl+I lifts the certification."); return; }
    if (!c || c.job_id) { if (c && c.job_id) toast("The running update job is working on this comment."); return; }
    editing = id;
    selectSlot(null);
    var slot = slotsEl.querySelector('.rv-slot[data-id="' + id + '"]');
    var body = slot.querySelector(".rv-slot-body");
    body.innerHTML = "";
    var ta = document.createElement("textarea");
    ta.className = "rv-editor";
    ta.value = c.body || "";
    ta.placeholder = "Write a comment (markdown)…";
    body.appendChild(ta);
    slot.classList.remove("is-empty");
    var grow = function () { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; };
    ta.addEventListener("input", function () {
      if (c.status === "done") {
        reopen(c);
        slot.classList.remove("is-done"); slot.classList.add("is-open");
        var foot = slot.querySelector(".rv-slot-foot"); if (foot) foot.remove();
        applyAnchors(); measure(); renderMarks();
      }
      var had = hasBody(c);
      c.body = ta.value;
      c.html = null;
      if (had !== hasBody(c)) renderMarks();
      grow();
      saveSoon(id);
    });
    ta.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || !(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      if (e.shiftKey) showHighlightOf(id); else ta.blur();
    });
    ta.addEventListener("blur", function () {
      if (editing === id) editing = null;
      var pending = dirty[id] ? save(id) : Promise.resolve();
      refreshSlot(id);
      pending.then(function () { refreshSlot(id); });
    });
    grow();
    ta.focus({ preventScroll: true });
  }

  // The slot a tap starts on: blurring an open editor shrinks it and moves the
  // slots below before the tap completes, so the click may land elsewhere.
  var tappedSlot = null;
  slotsEl.addEventListener("pointerdown", function (e) {
    var slot = e.target.closest(".rv-slot");
    tappedSlot = slot && !e.target.closest("a, button, textarea") ? slot.dataset.id : null;
  });
  comPane.addEventListener("click", function (e) {
    // A comment's link to a section of this document: over to the documentation, at that section.
    var link = e.target.closest("a[href]");
    if (link) {
      var target = sectionFor(link);
      if (target) {
        e.preventDefault();
        setPage("docs");
        docPane.scrollTo({ top: Math.max(0, posIn(docPane, target) - 8), behavior: "smooth" });
        history.replaceState(null, "", "#" + target.id);
      }
      tappedSlot = null;
      return;
    }
    var finish = e.target.closest("[data-finish]");
    if (finish) {
      remove(finish.dataset.finish).then(relayout);
      relayout();
      return;
    }
    var id = tappedSlot;
    tappedSlot = null;
    if (!id || e.target.closest("a, button, textarea")) return;
    if (editing !== id) startEdit(id);
  });

  // ---- highlighting: the floating button ----------------------------------------

  var lastSel = null, selTimer;

  function currentSelection() {
    var sel = window.getSelection();
    if (!sel || !sel.rangeCount || sel.isCollapsed) return null;
    var r = sel.getRangeAt(0);
    if (!docEl.contains(r.commonAncestorContainer)) return null;
    var s = offsetOf(r.startContainer, r.startOffset), e = offsetOf(r.endContainer, r.endOffset);
    var text = textIndex().text;
    while (s < e && /\s/.test(text[s])) s++;
    while (e > s && /\s/.test(text[e - 1])) e--;
    return e > s ? { s: s, e: e } : null;
  }

  document.addEventListener("selectionchange", function () {
    var cur = page === "docs" ? currentSelection() : null;
    clearTimeout(selTimer);
    if (cur) { lastSel = cur; fab.disabled = false; return; }
    // Tapping the button can collapse the selection first: keep the last one a moment.
    selTimer = setTimeout(function () { lastSel = null; fab.disabled = true; }, 1200);
  });
  fab.addEventListener("pointerdown", function (e) { e.preventDefault(); });
  fab.addEventListener("mousedown", function (e) { e.preventDefault(); });
  certFab.addEventListener("pointerdown", function (e) { e.preventDefault(); });
  certFab.addEventListener("mousedown", function (e) { e.preventDefault(); });

  function openRangesOverlapping(s, e) {
    return comments.filter(function (c) {
      var r = ranges[c.id];
      return r && !c.certified && r.s < e && r.e > s;
    });
  }

  function certifiedOverlapping(s, e) {
    return comments.some(function (c) { var r = ranges[c.id]; return c.certified && r && r.s < e && r.e > s; });
  }

  function covered(list, s, e) {
    var spans = list.map(function (c) { return ranges[c.id]; }).sort(function (a, b) { return a.s - b.s; });
    var at = s;
    for (var i = 0; i < spans.length; i++) { if (spans[i].s > at) return false; at = Math.max(at, spans[i].e); }
    return at >= e;
  }

  // "toggle" (the floating button): unhighlight a fully highlighted selection,
  // otherwise highlight all of it. "remove" (Del): unhighlight whatever part of
  // the selection is highlighted, never highlight. "add" (Ctrl+Enter): highlight,
  // never unhighlight.
  function selectOffsets(s, e) {
    var idx = textIndex(), sel = window.getSelection();
    var find = function (o, end) {
      for (var i = 0; i < idx.nodes.length; i++) {
        var len = idx.nodes[i].nodeValue.length;
        if (o < idx.starts[i] + len || (end && o === idx.starts[i] + len)) return [idx.nodes[i], o - idx.starts[i]];
      }
      return null;
    };
    var a = find(s, false), b = find(e, true);
    if (!a || !b || !sel) return;
    var r = document.createRange();
    r.setStart(a[0], a[1]); r.setEnd(b[0], b[1]);
    sel.removeAllRanges();
    sel.addRange(r);
  }

  // `keep` (Enter): afterwards the section stays selected — the highlight just made,
  // or the text just unhighlighted — instead of clearing the selection.
  function highlightSelection(mode, keep) {
    var sel = currentSelection() || lastSel;
    if (!sel) { toast("Select some text first."); return; }
    var s = sel.s, e = sel.e;
    if (certifiedOverlapping(s, e)) { toast("That is certified. Ctrl+I lifts the certification first."); return; }
    var hits = openRangesOverlapping(s, e);
    if (mode === "remove" && !hits.length) { toast("Nothing highlighted there."); return; }
    if (hits.some(function (c) { return c.job_id; })) { toast("The running update job is working on that highlight."); return; }
    hits.forEach(reopen); // a done section touched again is open again
    var idx = textIndex();
    var touched = [], removed = [], made = null;
    if (hits.length && mode !== "add" && (mode === "remove" || covered(hits, s, e))) {
      // Unhighlight the selected part.
      hits.forEach(function (c) {
        var r = ranges[c.id];
        var pieces = [];
        if (r.s < s) pieces.push([r.s, s]);
        if (r.e > e) pieces.push([e, r.e]);
        if (!pieces.length) {
          if (hasBody(c) && !window.confirm("Remove this highlight and its comment?")) { touched.push(c.id); return; }
          removed.push(c.id);
          return;
        }
        c.anchor = anchorFor(idx, pieces[0][0], pieces[0][1]);
        touched.push(c.id);
        if (pieces[1]) {
          var extra = { id: uuid(), branch: branch, path: path, body: "", status: "open", change: null, job_id: null,
                        anchor: anchorFor(idx, pieces[1][0], pieces[1][1]), created_at: new Date().toISOString() };
          comments.push(extra); touched.push(extra.id);
        }
      });
    } else if (hits.length) {
      // Partly highlighted: highlight all of it, merging what it touches into one.
      var from = s, to = e;
      hits.forEach(function (c) { from = Math.min(from, ranges[c.id].s); to = Math.max(to, ranges[c.id].e); });
      var keep = hits.filter(hasBody)[0] || hits[0];
      keep.body = hits.map(function (c) { return (c.body || "").trim(); }).filter(Boolean).join("\n\n");
      keep.html = null;
      keep.anchor = anchorFor(idx, from, to);
      touched.push(keep.id);
      made = keep.id;
      hits.forEach(function (c) { if (c !== keep) removed.push(c.id); });
    } else {
      var c = { id: uuid(), branch: branch, path: path, body: "", status: "open", change: null, job_id: null,
                anchor: anchorFor(idx, s, e), created_at: new Date().toISOString() };
      comments.push(c); touched.push(c.id);
      made = c.id;
    }
    var sel2 = window.getSelection(); if (sel2) sel2.removeAllRanges();
    lastSel = null; fab.disabled = true;
    removed.forEach(remove);
    touched.forEach(function (id) { save(id); });
    relayout();
    if (keep) { if (made) selectHighlight(made); else selectOffsets(s, e); }
    return made;
  }

  // ---- certifying -----------------------------------------------------------------

  // Toggles "Certified" on the selected highlight (highlighting selected plain text first),
  // or, on the comments page, on the selected comment. Ctrl+I and the green button.
  function toggleCertify() {
    var id;
    if (page === "docs") {
      id = selectedHighlight();
      if ((!id || !byId(id)) && (currentSelection() || lastSel)) id = highlightSelection("add", false);
    } else {
      id = selectedSlot;
    }
    var c = id && byId(id);
    if (!c) { if (toastEl.hidden) toast(page === "docs" ? "Select a highlight or some text first." : "Select a comment first."); return; }
    if (c.job_id) { toast("The running update job is working on that comment."); return; }
    if (c.certified) {
      c.certified = false;
    } else {
      if (c.status === "done" && c.change && c.change.kind === "removed") { toast("A removed section cannot be certified."); return; }
      reopen(c);
      c.certified = true;
    }
    save(id);
    relayout();
    if (page === "docs") selectHighlight(id); else selectSlot(id);
  }

  // ---- switching pages, with aligned scroll -------------------------------------

  function points() {
    var D = [0], C = [0];
    ordered().forEach(function (c) {
      var g = geo[c.id], slot = slotsEl.querySelector('.rv-slot[data-id="' + c.id + '"]');
      if (!g || !slot) return;
      var ct = posIn(comPane, slot), cb = ct + slot.offsetHeight;
      [[g.top, ct], [g.bottom, cb]].forEach(function (p) {
        D.push(Math.max(p[0], D[D.length - 1]));
        C.push(Math.max(p[1], C[C.length - 1]));
      });
    });
    D.push(Math.max(docPane.scrollHeight, D[D.length - 1]));
    C.push(Math.max(comPane.scrollHeight, C[C.length - 1]));
    return { D: D, C: C };
  }

  function mapY(y, A, B) {
    for (var i = 1; i < A.length; i++) {
      if (y <= A[i]) {
        if (A[i] === A[i - 1]) return B[i];
        return B[i - 1] + (y - A[i - 1]) / (A[i] - A[i - 1]) * (B[i] - B[i - 1]);
      }
    }
    return B[B.length - 1];
  }

  function syncOther() {
    var p = points();
    var from = page === "docs" ? docPane : comPane, to = page === "docs" ? comPane : docPane;
    var ref = from.clientHeight * 0.3;
    var y = page === "docs" ? mapY(from.scrollTop + ref, p.D, p.C) : mapY(from.scrollTop + ref, p.C, p.D);
    to.scrollTop = Math.max(0, y - ref);
  }

  function setPage(next, alreadySynced) {
    if (next === page) { track.style.transform = page === "docs" ? "translateX(0)" : "translateX(-50%)"; return; }
    if (editing) { var ta = slotsEl.querySelector("textarea"); if (ta) ta.blur(); }
    if (!alreadySynced) syncOther();
    page = next;
    track.style.transform = page === "docs" ? "translateX(0)" : "translateX(-50%)";
    root.classList.toggle("on-comments", page === "comments");
    root.querySelector("[data-label-docs]").hidden = page !== "docs";
    root.querySelector("[data-label-comments]").hidden = page === "docs";
    fab.hidden = page !== "docs";
    certFab.hidden = page !== "docs";
    root.querySelector('[data-keys="docs"]').hidden = page !== "docs";
    root.querySelector('[data-keys="comments"]').hidden = page === "docs";
    setInert();
  }

  // The page off screen takes no focus: Tab would otherwise walk into it and the
  // browser would scroll the viewport sideways to show it, breaking the layout.
  function setInert() {
    docPane.inert = page !== "docs";
    comPane.inert = page === "docs";
  }
  viewport.addEventListener("scroll", function () { viewport.scrollLeft = 0; viewport.scrollTop = 0; });

  function scrollsSideways(el) {
    for (; el && el !== viewport; el = el.parentNode) {
      if (el.scrollWidth > el.clientWidth + 1 && /(auto|scroll)/.test(getComputedStyle(el).overflowX)) return true;
    }
    return false;
  }

  var sx, sy, st, mode, width;
  viewport.addEventListener("touchstart", function (e) {
    mode = null;
    if (e.touches.length !== 1) { mode = "none"; return; }
    var t = e.touches[0];
    sx = t.clientX; sy = t.clientY; st = Date.now(); width = viewport.clientWidth;
    var sel = window.getSelection();
    if ((sel && !sel.isCollapsed) || e.target.closest("textarea") || scrollsSideways(e.target)) mode = "none";
  }, { passive: true });

  viewport.addEventListener("touchmove", function (e) {
    if (mode === "none" || e.touches.length !== 1) return;
    var t = e.touches[0], dx = t.clientX - sx, dy = t.clientY - sy;
    if (mode === null) {
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
      mode = Math.abs(dx) > Math.abs(dy) * 1.2 ? "h" : "v";
      if (mode === "h") { syncOther(); track.style.transition = "none"; }
    }
    if (mode !== "h") return;
    e.preventDefault();
    var base = page === "docs" ? 0 : -width;
    track.style.transform = "translateX(" + Math.max(-width, Math.min(0, base + dx)) + "px)";
  }, { passive: false });

  function endSwipe(e) {
    if (mode !== "h") { mode = null; return; }
    mode = null;
    var t = e.changedTouches[0], dx = t.clientX - sx, v = dx / Math.max(1, Date.now() - st);
    track.style.transition = "";
    var go = page === "docs" ? (dx < -width * 0.2 || v < -0.5) : (dx > width * 0.2 || v > 0.5);
    setPage(go ? (page === "docs" ? "comments" : "docs") : page, true);
  }
  viewport.addEventListener("touchend", endSwipe);
  viewport.addEventListener("touchcancel", endSwipe);

  // ---- previous / next -------------------------------------------------------------

  // Selects a highlight's whole text on the documentation page (then Del removes it).
  function selectHighlight(id) {
    var els = anchorEls[id] || [];
    var sel = window.getSelection();
    if (!els.length || !sel) return;
    var r = document.createRange();
    r.setStartBefore(els[0]);
    r.setEndAfter(els[els.length - 1]);
    sel.removeAllRanges();
    sel.addRange(r);
  }

  // The selected comment on the comments page (Enter edits it).
  var selectedSlot = null;
  function selectSlot(id) {
    selectedSlot = id;
    slotsEl.querySelectorAll(".rv-slot").forEach(function (el) { el.classList.toggle("is-selected", el.dataset.id === id); });
  }

  // The highlight the current text selection starts in, if any.
  function selectedHighlight() {
    var sel = window.getSelection();
    if (!sel || !sel.rangeCount || sel.isCollapsed) return null;
    var r = sel.getRangeAt(0), node = r.startContainer;
    if (node.nodeType !== 1) node = node.parentNode;
    else if (node.childNodes[r.startOffset]) node = node.childNodes[r.startOffset];
    var hit = node && node.closest && (node.closest("[data-id]") || node.querySelector && node.querySelector("[data-id]"));
    if (hit && docEl.contains(hit)) return hit.dataset.id;
    return null;
  }

  // Next/previous highlight (documentation) or comment (comments), scrolled to and selected.
  // It steps from the selected one when there is one, else from the scroll position.
  function jump(dir) {
    var pane = page === "docs" ? docPane : comPane, offset = 60;
    var items = page === "docs"
      ? ordered().filter(function (c) { return !geo[c.id].lost; }).map(function (c) { return { id: c.id, top: geo[c.id].top }; })
      : Array.prototype.filter.call(slotsEl.children, function (s) { return !s.classList.contains("is-empty"); })
          .map(function (s) { return { id: s.dataset.id, top: posIn(comPane, s) }; });
    var current = page === "docs" ? selectedHighlight() : selectedSlot, target = null;
    var at = items.map(function (it) { return it.id; }).indexOf(current);
    if (at >= 0) {
      target = items[at + dir] || null;
    } else {
      var ref = pane.scrollTop + offset;
      if (dir > 0) { for (var i = 0; i < items.length; i++) if (items[i].top > ref + 1) { target = items[i]; break; } }
      else { for (var j = items.length - 1; j >= 0; j--) if (items[j].top < ref - 1) { target = items[j]; break; } }
    }
    if (!target) { toast(dir > 0 ? "No more below." : "No more above."); return; }
    pane.scrollTo({ top: Math.max(0, target.top - offset), behavior: "smooth" });
    if (page === "docs") selectHighlight(target.id); else selectSlot(target.id);
  }

  root.addEventListener("click", function (e) {
    var b = e.target.closest("[data-rv]");
    if (!b) return;
    var what = b.dataset.rv;
    if (what === "toggle") setPage(page === "docs" ? "comments" : "docs");
    else if (what === "next") jump(1);
    else if (what === "prev") jump(-1);
    else if (what === "highlight") highlightSelection("toggle");
    else if (what === "certify") toggleCertify();
  });

  // Paragraph-like blocks of the document, in order: what Ctrl+Shift+↑/↓ steps through.
  function paragraphs() {
    return Array.prototype.filter.call(docEl.querySelectorAll("p, h1, h2, h3, h4, h5, h6, pre, tr, li"), function (el) {
      return el.tagName !== "LI" || !el.querySelector(":scope > p");
    });
  }

  function jumpParagraph(dir) {
    var blocks = paragraphs(), sel = window.getSelection(), at = -1;
    if (sel && sel.rangeCount && !sel.isCollapsed && docEl.contains(sel.anchorNode)) {
      var r = sel.getRangeAt(0);
      // The block the selection is in: the last one starting at or before its start.
      for (var i = 0; i < blocks.length; i++) {
        var b = document.createRange(); b.selectNodeContents(blocks[i]);
        if (b.compareBoundaryPoints(Range.START_TO_START, r) <= 0) at = i;
      }
    }
    var target;
    if (at >= 0) target = blocks[at + dir];
    else {
      var ref = docPane.scrollTop + 60;
      var tops = blocks.map(function (el) { return posIn(docPane, el); });
      if (dir > 0) target = blocks.filter(function (el, i) { return tops[i] >= ref - 1; })[0];
      else target = blocks.filter(function (el, i) { return tops[i] < ref - 1; }).pop();
    }
    if (!target) { toast(dir > 0 ? "No more below." : "No more above."); return; }
    var range = document.createRange();
    range.selectNodeContents(target);
    sel.removeAllRanges();
    sel.addRange(range);
    var top = posIn(docPane, target), bottom = top + target.offsetHeight;
    if (top < docPane.scrollTop + 8 || bottom > docPane.scrollTop + docPane.clientHeight - 8) {
      docPane.scrollTo({ top: Math.max(0, top - 60), behavior: "smooth" });
    }
  }

  // Ctrl+Shift+Enter on a comment: over to the documentation, its highlight selected.
  function showHighlightOf(id) {
    if (!id) { toast("Select a comment first."); return; }
    setPage("docs"); // stops any editing first
    var els = anchorEls[id] || [];
    if (!els.length) { toast("Its section was not found in this document."); return; }
    var g = geo[id];
    if (g && (g.top < docPane.scrollTop || g.bottom > docPane.scrollTop + docPane.clientHeight)) {
      docPane.scrollTop = Math.max(0, g.top - docPane.clientHeight * 0.3);
    }
    selectHighlight(id);
  }

  // Ctrl+Enter on a highlight: over to its comment, editing it. On text that is
  // not highlighted yet, it is highlighted first — highlight and comment in one go.
  function commentOnSelectedHighlight() {
    var id = selectedHighlight();
    if ((!id || !byId(id)) && currentSelection()) id = highlightSelection("add", true);
    if (!id || !byId(id)) { toast("Select some text first."); return; }
    setPage("comments");
    var slot = slotsEl.querySelector('.rv-slot[data-id="' + id + '"]');
    if (slot) {
      var top = posIn(comPane, slot);
      if (top < comPane.scrollTop || top + slot.offsetHeight > comPane.scrollTop + comPane.clientHeight) {
        comPane.scrollTop = Math.max(0, top - comPane.clientHeight * 0.3);
      }
    }
    startEdit(id);
  }

  document.addEventListener("keydown", function (e) {
    if (e.target.closest && e.target.closest("textarea, input, select")) return;
    var onControl = e.target.closest && e.target.closest("a, button");
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === "i" || e.key === "I")) {
      e.preventDefault();
      toggleCertify();
      return;
    }
    if (page === "docs" && e.ctrlKey && e.shiftKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      jumpParagraph(e.key === "ArrowDown" ? 1 : -1);
    } else if (page === "docs" && e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      commentOnSelectedHighlight();
    } else if (page === "docs" && e.key === "Enter" && !e.shiftKey && !e.altKey && !onControl && currentSelection()) {
      e.preventDefault();
      highlightSelection("toggle", true);
    } else if (e.ctrlKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      // Next/previous highlighted section (documentation) or comment (comments).
      e.preventDefault();
      jump(e.key === "ArrowDown" ? 1 : -1);
    } else if (page === "comments" && e.key === "Enter" && e.shiftKey && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      showHighlightOf(selectedSlot);
    } else if (e.key === "Enter" && page === "comments" && selectedSlot && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      startEdit(selectedSlot);
    } else if (e.key === "Delete" && page === "docs" && currentSelection()) {
      e.preventDefault();
      highlightSelection("remove");
    } else if (e.ctrlKey || e.altKey || e.metaKey) {
      return;
    } else if (e.key === "ArrowRight") setPage("comments");
    else if (e.key === "ArrowLeft") setPage("docs");
  });

  // A single click on a highlight selects all of its text (then Del removes it).
  docEl.addEventListener("click", function (e) {
    var mark = e.target.closest("mark.rv-hl");
    if (!mark || e.detail > 1 || e.target.closest("a")) return;
    var sel = window.getSelection();
    if (!sel || !sel.isCollapsed) return; // the end of a drag-selection, not a click
    selectHighlight(mark.dataset.id);
  });

  // The heading a link points to when it is a section of this same document
  // (`#anchor`, or this document's own viewer URL with an anchor), else null.
  function sectionFor(a) {
    var url = new URL(a.getAttribute("href"), location.href);
    if (!url.hash || url.pathname !== location.pathname || url.searchParams.get("path") !== path) return null;
    var el = document.getElementById(decodeURIComponent(url.hash.slice(1)));
    return el && docEl.contains(el) ? el : null;
  }

  // In-page anchor links scroll the documentation pane.
  docEl.addEventListener("click", function (e) {
    var a = e.target.closest("a[href]");
    var target = a && sectionFor(a);
    if (!target) return;
    e.preventDefault();
    docPane.scrollTo({ top: Math.max(0, posIn(docPane, target) - 8), behavior: "smooth" });
    history.replaceState(null, "", "#" + target.id);
  });

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { measure(); renderMarks(); if (page === "comments") track.style.transform = "translateX(-50%)"; }, 150);
  });
  docEl.querySelectorAll("img").forEach(function (img) { img.addEventListener("load", function () { measure(); renderMarks(); }); });

  relayout();
  setInert();
  fab.disabled = true;
  if (location.hash) {
    var target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target) docPane.scrollTop = Math.max(0, posIn(docPane, target) - 8);
  }
})();
