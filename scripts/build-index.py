#!/usr/bin/env python3
"""Scan topics/*.html and build index.html - the wiki/graph for the Learning Hub.

Single rendering path: the same UI at n=4 and n=100. Rows render server-side
with data-* attributes; client-side JS only hides/reorders them, so the page
is fully readable without JavaScript (graceful degradation).

Uses only metadata already extracted: title, tags, created, last-updated,
last-quizzed. No new <meta> fields.

The index does not render the blueprint graph; cross-references live in the
blueprints themselves.
"""

import os
import re
import html
from datetime import datetime, timedelta
from html.parser import HTMLParser

TOPICS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "topics")
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")
STALE_DAYS = 14

# Tag rail: only tags shared by this many blueprints are useful filters.
TAG_MIN_COUNT = 3
TAG_CAP = 20


class BlueprintParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metas = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "meta":
            name = attrs_dict.get("name", "")
            content = attrs_dict.get("content", "")
            if name:
                self.metas[name] = content
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def parse_blueprint(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    parser = BlueprintParser()
    parser.feed(content)
    return parser


def is_stale(last_quizzed):
    if not last_quizzed.strip():
        return True
    try:
        quizzed_date = datetime.strptime(last_quizzed.strip(), "%Y-%m-%d")
        return datetime.now() - quizzed_date > timedelta(days=STALE_DAYS)
    except ValueError:
        return True


def display_title(raw):
    """Strip the ' - intuition blueprint' template suffix from a <title>.

    Robust to dash variant (em/en/hyphen). Pages whose <title> has no suffix
    (e.g. reference pages) are returned unchanged.
    """
    marker = "intuition blueprint"
    if marker in raw:
        idx = raw.rfind(marker)
        return raw[:idx].rstrip(" \u2014\u2013-").rstrip()
    return raw


def build_index():
    blueprints = []
    if not os.path.isdir(TOPICS_DIR):
        print(f"No topics/ directory found at {TOPICS_DIR}")
        return

    for filename in sorted(os.listdir(TOPICS_DIR)):
        if not filename.endswith(".html"):
            continue
        filepath = os.path.join(TOPICS_DIR, filename)
        parser = parse_blueprint(filepath)
        blueprints.append({
            "filename": filename,
            "title": display_title(parser.title or filename),
            "metas": parser.metas,
        })

    # Tag frequency
    freq = {}
    for b in blueprints:
        tags = [t.strip() for t in b["metas"].get("tags", "").split(",") if t.strip()]
        for t in tags:
            freq[t] = freq.get(t, 0) + 1
    # Qualifying tags: count >= threshold, sorted by freq desc then alpha, capped
    rail_tags = sorted(
        ((t, c) for t, c in freq.items() if c >= TAG_MIN_COUNT),
        key=lambda kv: (-kv[1], kv[0]),
    )

    stale = [b for b in blueprints if is_stale(b["metas"].get("last-quizzed", ""))]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    e = html.escape

    # --- rows (server-rendered; JS only hides/reorders) ---
    def sort_key(b):
        return b["metas"].get("last-updated", "") or b["metas"].get("created", "")
    sorted_bps = sorted(blueprints, key=sort_key, reverse=True)

    rows = []
    for b in sorted_bps:
        fn = b["filename"]
        title = b["title"]
        tags = [t.strip() for t in b["metas"].get("tags", "").split(",") if t.strip()]
        updated = b["metas"].get("last-updated", "")
        lq = b["metas"].get("last-quizzed", "").strip()
        st = is_stale(b["metas"].get("last-quizzed", ""))

        haystack = (title + " " + " ".join(tags)).lower()
        tags_html = "".join(
            f'<button class="tag" data-tag="{e(t)}">{e(t)}</button>' for t in tags
        )
        qtxt = "never quizzed" if not lq else f"quizzed {lq}"

        due = '<span class="due">due</span>' if st else ''

        rows.append(
            f'<li class="row" data-h="{e(haystack)}" data-tags="{e(" ".join(tags))}" '
            f'data-stale="{"1" if st else "0"}" data-u="{e(updated)}" '
            f'data-title="{e(title.lower())}">'
            f'<div class="r-main"><a class="r-title" href="topics/{e(fn)}">{e(title)}</a>{due}</div>'
            f'<div class="r-tags">{tags_html}</div>'
            f'<div class="r-meta"><span>upd {e(updated)}</span><span class="dot">·</span>'
            f'<span>{e(qtxt)}</span></div>'
            f'</li>'
        )

    rows_html = "\n".join(rows)

    # --- tag rail ---
    if rail_tags:
        # Everything past TAG_CAP is still rendered, just hidden behind "+n more".
        chips = "".join(
            f'<button class="chip{"" if i < TAG_CAP else " extra"}" '
            f'data-tag="{e(t)}">{e(t)}<span class="ct">{c}</span></button>'
            for i, (t, c) in enumerate(rail_tags)
        )
        extra_count = max(0, len(rail_tags) - TAG_CAP)
        more_btn = (
            f'<button class="moretags" id="moretags" data-n="{extra_count}">'
            f'+{extra_count} more</button>'
            if extra_count > 0 else ''
        )
        tagrail_html = (
            f'<div class="tagrail" id="tagrail">'
            f'<span class="rl">topics</span>{chips}{more_btn}'
            f'</div>'
        )
    else:
        tagrail_html = ''

    due_btn = (
        f'<button class="duebtn" id="duebtn">{len(stale)} due for quiz</button>'
        if stale else ''
    )

    # --- assemble page ---
    n_total = len(blueprints)
    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="UTF-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    parts.append("<title>Learning Hub - Index</title>")
    parts.append('<link rel="stylesheet" href="assets/blueprint.css">')
    parts.append("<style>")
    parts.append(CSS)
    parts.append("</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append('<div class="index-page">')
    parts.append("<h1>Learning Hub</h1>")
    parts.append('<p class="subtitle">A growing map of intuition blueprints</p>')
    parts.append(f'<p class="updated">Last rebuilt: {now}</p>')

    parts.append('<div class="controls">')
    parts.append('<div class="searchline">')
    parts.append('<input id="q" type="search" placeholder="Search blueprints and tags…" autocomplete="off">')
    parts.append('<span class="kbd">/</span>')
    parts.append('</div>')  # searchline
    parts.append('<div class="countline">')
    parts.append(f'<span><span class="n" id="shown">{n_total}</span> of {n_total} blueprints</span>')
    parts.append(due_btn)
    parts.append('<span class="seg">')
    parts.append('<span style="color:var(--rule)">sort</span>')
    parts.append('<button class="on" data-sort="u">recent</button>')
    parts.append('<button data-sort="t">a-z</button>')
    parts.append('</span>')  # seg
    parts.append('</div>')  # countline
    parts.append(tagrail_html)
    parts.append('</div>')  # controls

    if blueprints:
        parts.append(f'<ul class="rows" id="rows">{rows_html}</ul>')
        parts.append('<p class="noresults" id="noresults">Nothing matches. Clear the filters to see everything.</p>')
    else:
        parts.append('<p class="empty">No blueprints yet. The first one you file will appear here.</p>')

    parts.append(JS)
    parts.append("</div>")  # index-page
    parts.append("</body>")
    parts.append("</html>")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(p for p in parts if p is not None))
    print(f"Built index.html with {n_total} blueprint(s)")


CSS = """
.index-page { max-width: 900px; margin: 0 auto; padding: 44px 56px 96px; }
.index-page h1 { font-family: Georgia, serif; font-size: 36px; font-weight: 700; margin-bottom: 6px; }
.index-page .subtitle { color: var(--soft); font-style: italic; font-size: 20px; margin-bottom: 4px; }
.updated { font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--soft); letter-spacing: 0.1em; }
.empty { color: var(--soft); font-style: italic; padding: 40px 0; }

/* sticky control bar */
.controls {
  position: sticky; top: 0; z-index: 20;
  background: var(--paper);
  border-bottom: 1px solid var(--ink);
  padding: 14px 0 10px;
  margin: 26px 0 0;
}
.searchline { display: flex; align-items: center; gap: 12px; }
.searchline input {
  flex: 1; font-family: Georgia, serif; font-size: 19px;
  background: transparent; border: none; border-bottom: 1px solid var(--rule);
  padding: 6px 2px; color: var(--ink); outline: none;
}
.searchline input:focus { border-bottom-color: var(--accent); }
.searchline input::placeholder { color: var(--rule); font-style: italic; }
.searchline .kbd {
  font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--soft);
  border: 1px solid var(--rule); padding: 2px 6px; border-radius: 2px;
}
.countline {
  font-family: 'JetBrains Mono', monospace; font-size: 10px; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--soft); margin-top: 10px;
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
}
.countline .n { color: var(--ink); }
.countline .seg { display: flex; gap: 8px; margin-left: auto; }
.countline button {
  font-family: inherit; font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase;
  background: none; border: none; color: var(--soft); cursor: pointer; padding: 2px 0;
  border-bottom: 1px solid transparent;
}
.countline button:hover { color: var(--ink); }
.countline button.on { color: var(--accent); border-bottom-color: var(--accent); }
.countline .duebtn.on { background: var(--highlight); padding: 2px 8px; border-bottom-color: transparent; }

/* tag rail */
.tagrail { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; padding: 14px 0 0; }
.tagrail .rl { font-family: 'JetBrains Mono', monospace; font-size: 9px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--soft); margin-right: 6px; }
.chip {
  font-family: 'JetBrains Mono', monospace; font-size: 10.5px; letter-spacing: 0.04em;
  padding: 3px 9px; border: 1px solid var(--rule); background: none; color: var(--soft);
  cursor: pointer; border-radius: 2px; display: inline-flex; gap: 6px; align-items: baseline;
}
.chip .ct { font-size: 9px; color: var(--rule); }
.chip:hover { border-color: var(--ink); color: var(--ink); }
.chip:hover .ct { color: var(--soft); }
.chip.on { border-color: var(--accent); color: var(--accent); background: rgba(200,16,46,0.06); }
.chip.on .ct { color: var(--accent); }
.chip.extra { display: none; }
.tagrail.open .chip.extra { display: inline-flex; }
/* an active filter must never be invisible, even when the rail is collapsed */
.chip.extra.on { display: inline-flex; }
.moretags {
  font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--accent);
  background: none; border: none; cursor: pointer; padding: 3px 4px; letter-spacing: 0.08em;
}
.moretags:hover { text-decoration: underline; }

/* rows */
.rows { list-style: none; padding: 0; margin: 0; }
.row { padding: 15px 0 14px; border-bottom: 1px dotted var(--rule); }
.row.hide { display: none; }
.r-main { display: flex; align-items: baseline; gap: 10px; }
.r-title { font-family: Georgia, serif; font-size: 18px; color: var(--ink); text-decoration: none; }
.r-title:hover { color: var(--accent); }
.due {
  font-family: 'JetBrains Mono', monospace; font-size: 8.5px; letter-spacing: 0.18em;
  text-transform: uppercase; color: var(--ink); background: var(--highlight);
  padding: 1px 6px; border-radius: 2px;
}
.r-tags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.tag {
  font-family: 'JetBrains Mono', monospace; font-size: 10px; letter-spacing: 0.06em;
  color: var(--accent); background: rgba(200,16,46,0.06); padding: 2px 8px;
  border-radius: 2px; border: none; cursor: pointer;
}
.tag:hover { background: rgba(200,16,46,0.16); }
.r-tags .tag.on { background: var(--accent); color: var(--paper); }
.r-meta { font-family: 'JetBrains Mono', monospace; font-size: 10.5px; color: var(--soft); margin-top: 6px; display: flex; gap: 7px; flex-wrap: wrap; }
.r-meta .dot { color: var(--rule); }
.noresults { color: var(--soft); font-style: italic; padding: 40px 0; display: none; }
.noresults.on { display: block; }

@media (max-width: 700px) {
  .index-page { padding: 30px 22px 70px; }
  .countline .seg { margin-left: 0; width: 100%; }
}
"""

JS = """<script>
var rowsEl = document.getElementById('rows');
var all = rowsEl ? Array.prototype.slice.call(rowsEl.children) : [];
var q = document.getElementById('q');
var shown = document.getElementById('shown');
var noresults = document.getElementById('noresults');
var duebtn = document.getElementById('duebtn');
var active = new Set();
var dueOnly = false;

function apply() {
  var term = q ? q.value.trim().toLowerCase() : '';
  var n = 0;
  all.forEach(function (r) {
    var ok = true;
    if (term && r.dataset.h.indexOf(term) === -1) ok = false;
    if (ok && active.size) {
      var t = r.dataset.tags.split(' ');
      active.forEach(function (a) { if (t.indexOf(a) === -1) ok = false; });
    }
    if (ok && dueOnly && r.dataset.stale !== '1') ok = false;
    r.classList.toggle('hide', !ok);
    if (ok) n++;
  });
  if (shown) shown.textContent = n;
  if (noresults) noresults.classList.toggle('on', n === 0);
  document.querySelectorAll('.chip').forEach(function (c) {
    c.classList.toggle('on', active.has(c.dataset.tag));
  });
  document.querySelectorAll('.r-tags .tag').forEach(function (c) {
    c.classList.toggle('on', active.has(c.dataset.tag));
  });
  if (duebtn) duebtn.classList.toggle('on', dueOnly);
}

function toggleTag(t) {
  if (active.has(t)) active.delete(t); else active.add(t);
  apply();
}

if (q) q.addEventListener('input', apply);
if (duebtn) duebtn.addEventListener('click', function () { dueOnly = !dueOnly; apply(); });
document.addEventListener('click', function (ev) {
  var c = ev.target.closest('.chip, .r-tags .tag');
  if (c) { ev.preventDefault(); toggleTag(c.dataset.tag); }
});
var moretags = document.getElementById('moretags');
if (moretags) {
  moretags.addEventListener('click', function () {
    var r = document.getElementById('tagrail');
    r.classList.toggle('open');
    this.textContent = r.classList.contains('open') ? 'fewer' : '+' + this.dataset.n + ' more';
  });
}
document.querySelectorAll('[data-sort]').forEach(function (b) {
  b.addEventListener('click', function () {
    document.querySelectorAll('[data-sort]').forEach(function (x) { x.classList.remove('on'); });
    b.classList.add('on');
    var k = b.dataset.sort;
    var s = all.slice().sort(function (x, y) {
      if (k === 't') return x.dataset.title < y.dataset.title ? -1 : 1;
      return x.dataset.u < y.dataset.u ? 1 : -1;
    });
    s.forEach(function (r) { rowsEl.appendChild(r); });
  });
});
document.addEventListener('keydown', function (ev) {
  if (ev.key === '/' && document.activeElement !== q) { ev.preventDefault(); q.focus(); }
  if (ev.key === 'Escape') { q.value = ''; active.clear(); dueOnly = false; apply(); q.blur(); }
});
</script>"""


if __name__ == "__main__":
    build_index()
