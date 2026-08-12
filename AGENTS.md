# Learning Hub — Conventions

The single source of truth for working in this repo. Self-contained: **do not
fetch anything.** The hub renders from disk with the network off, and that rule
applies to its instructions too.

`CLAUDE.md` is a symlink to this file.

## Who the user is

> **Owner: rewrite this section first.** Everything else in this file is
> machinery and applies to any hub. This section is the only part that is about
> *you*, and it is what makes the teaching land. What follows is a placeholder.

Someone building understanding with AI assistance. The goal is *understanding*,
not artifacts. AI makes it too easy to skip the learning; this system keeps
learning in the loop.

Teach intuition-up: basic concepts → examples → intuition → precise terms. Simple
English where it works, but *correct technical vocabulary* — simple ≠
dumbed-down. Growing a vocabulary of precise terms is part of the point.

Worth stating here: whether diagrams help you or distract you, which fields you
already know cold, and what "too basic" and "too dense" each look like for you.
An agent reads this before writing anything.

## The hub

`topics/*.html` is the knowledge and the only irreplaceable part. `index.html` is
a generated view over their metas, rebuilt in ~1s. `assets/` holds the look.
`log.md` is the curated narrative. `skills/learn/` is the `/learn` skill, so the
hub and the way agents reach it stay versioned together.

**Local-first.** Opening a file from disk is the primary interface. Three rules
protect it:

1. **No runtime network calls.** Stylesheet, fonts, and math renderer all ship in
   `assets/`. `validate.py` rejects any `http` stylesheet or script.
2. **Relative paths only.** Blueprints link `../assets/blueprint.css`; the index
   links `assets/blueprint.css`. Root-absolute paths break over `file://`.
3. **Generated files are committed.** A fresh clone must work with no tooling.

There is no CI. Nothing rebuilds on push. `README.md` covers the layout;
`deploy/` covers reading the hub from a phone, which is optional and which most
owners never need.

## How to build a blueprint

A blueprint is a self-contained HTML file at `topics/<name>.html` teaching one
topic. The substance is HTML — visuals, diagrams, worked examples — not markdown
notes.

### Intuition-up ordering

1. **The intuition first** — plain English, the metaphor, no jargon. What problem
   does the concept solve?
2. **Examples** — concrete before abstract. Most familiar case first.
3. **The precise terms** — technical vocabulary, anchored to the intuition above.
4. **Why it's shaped this way** — the structural insight, the trade-off the shape
   embodies.

Not every section needs all four. The order is a default, not a law.

### Diagrams

For a visual learner diagrams carry real weight — but only where they earn it. If
**Who the user is** says diagrams don't help this owner, skip this section.

**Use one for:** how parts relate, spatial structures, transformations,
comparisons where shape matters.

**Don't use one for:** definitions (a sentence is sharper), linear prose ("step 1
→ step 2" is decoration), anything one sentence explains equally well.

One per section unless the section is fundamentally visual. Colors must be
consistent and meaningful across every diagram on the page — `treated` vs
`control`, `observed` vs `counterfactual` — never arbitrary decoration.

**SVG text must fit its box.** JetBrains Mono advance width is ~0.6em — ~7.5px
per char at 12.5px, ~6.3px at 10.5px. Leave ~8px padding each side. If a label
doesn't fit, shorten it or drop a size; don't widen the box without checking
downstream layout.

### The four template patterns

Load-bearing structure. Every blueprint follows them.

**1. Modular sections** — each opens with a chapter label and a title that is a
*claim*, not a label:

- ✅ "Why the average difference between groups lies"
- ❌ "Introduction to matching"

The title tells the reader what they'll understand by the end. Sections are
modular — no fixed order.

**2. Formula + "reading it"** — every formula is immediately followed by a
plain-English breakdown defining **every symbol, every time**. Never assume
vocabulary from an earlier section.

```html
<div class="formula">
  <span class="lab">{{label}}</span>
  <div class="eq">{{$$ formula $$}}</div>
</div>
<div class="where">
  <span class="wlab">reading it</span>
  <ul>
    <li><b>{{symbol}}</b> — {{plain-English definition, one sentence}}</li>
    <li><b>{{whole expression}}</b> — {{what it says in words}}</li>
  </ul>
</div>
```

The highest-value pattern — the anti-false-aha mechanism. A reader who skips the
formula still understands; a reader who reads both actually understands it.

**3. Per-section "Remember"** — a one-sentence distillation of the load-bearing
insight, at the end of each section. "I thought X, but actually Y" lives here.

```html
<div class="key">
  <span class="lab">Remember</span>
  <p>{{the thing you'd whisper to a friend.}}</p>
</div>
```

**4. "In one breath" closing** — one paragraph synthesizing the whole topic, each
section's insight strung together.

```html
<section class="closing">
  <div class="chno">In one breath</div>
  <p class="breath">{{the whole topic, synthesized.}}</p>
</section>
```

This is the "do I actually understand this?" test. If you can't write it, the
blueprint isn't done.

### The skeleton

Head, and the shape of the body. For the full structure copy any file in
`topics/` — they all follow it.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="source-chat" content="">
<meta name="last-quizzed" content="">
<meta name="prerequisites" content="">
<meta name="tags" content="">
<meta name="created" content="">
<meta name="last-updated" content="">
<title>{{TOPIC}} — intuition blueprint</title>
<link rel="stylesheet" href="../assets/blueprint.css">
</head>
<body>
<div class="page">
  <nav class="crumb"><a href="../index.html">Learning Hub</a><span>/</span>{{slug}}</nav>

  <header class="title">
    <div class="eyebrow">Intuition Blueprint</div>
    <h1>{{central question}}<span class="subtitle">{{topic}}</span></h1>
    <div class="meta">
      <span>created: {{DATE}}</span>
      <span>last-updated: {{DATE}}</span>
      <span class="tags">{{tags}}</span>
    </div>
  </header>

  <section>
    <div class="chno">{{01 — framing}}</div>
    <h2>{{claim-title}}</h2>
    <p>{{intuition}}</p>
    <div class="diagram">
      <svg viewBox="0 0 540 120">…</svg>
      <div class="caption">{{Fig. N — what to look at}}</div>
    </div>
    <!-- formula + where, then key — see the four patterns above -->
  </section>

  <section class="closing">…</section>

  <div class="cross-refs">
    <span class="label">prereqs</span>
    <a href="{{prerequisite.html}}">{{prerequisite}}</a>
  </div>

  <footer>
    <span>blueprint v1 · last-quizzed: {{date or "—"}}</span>
    <span>source: <a href="{{chat-url}}">{{chat}}</a></span>
  </footer>
</div>
</body>
</html>
```

Inline cross-references within a page: `<a class="xref" href="#secNN">§NN</a>`.

### Math

Only blueprints containing math get this, directly after the stylesheet link:

```html
<link rel="stylesheet" href="../assets/katex/katex.min.css">
<script defer src="../assets/katex/katex.min.js"></script>
<script defer src="../assets/katex/auto-render.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function () {
  renderMathInElement(document.body, {
    delimiters: [
      { left: '$$', right: '$$', display: true },
      { left: '\\(', right: '\\)', display: false }
    ],
    throwOnError: false
  });
});
</script>
```

`$$…$$` is display, `\(…\)` is inline. KaTeX covers standard LaTeX math but is
not MathJax — no runtime macros, no `\require`. A failed expression renders red
rather than throwing, so check the page after filing.

### The metas

All required in `<head>`: `source-chat` (URL of the chat that produced it),
`last-quizzed` (empty if never), `prerequisites` (comma-separated filenames),
`tags`, `created`, `last-updated`.

### Cross-references

The graph is the structure. Categories emerge as clusters of links — there is no
taxonomy and no hierarchy.

Before filing: link related blueprints in the body **and** in `cross-refs`, and
edit those blueprints to link back. One write touches several files. Read
`topics/` to see what exists.

**If you cannot see `topics/`** (phone session), produce a standalone blueprint.
**Never link to a blueprint you cannot verify exists** — `validate.py` rejects
dangling links. Note related concepts as HTML comments
(`<!-- future blueprint: dns.html -->`) for laptop follow-up.

**Breadcrumb:** the href must be `../index.html`. Not `/index.html`, which is
root-absolute and breaks over `file://`, and not `../`, which has no server to
turn it into a page and so opens a raw folder listing instead. `validate.py`
rejects both.

### Scope

A blueprint mirrors what one learning session produced — a focused session on DNS
becomes one focused blueprint; a long one on causal inference becomes one
field-through-line with many sections. Don't split a session artificially; don't
merge unrelated ones.

### Filing checklist

- [ ] All `<meta>` filled
- [ ] Asset paths relative, nothing over http
- [ ] Claim-titles, not label-titles
- [ ] Every formula has a "reading it" defining every symbol
- [ ] Each section has a "Remember"
- [ ] "In one breath" written
- [ ] Diagrams earn their place
- [ ] Cross-references added, or noted for follow-up

## How to quiz

The Socratic protocol probes the *model*, not recall. Reading produces a feeling
of understanding that may be false; the quiz forces the user to do the connecting.

1. **Read the blueprint.** "Remember" and "In one breath" are the densest sources.
2. **One question at a time.** Wait for the answer. Never dump a list.
3. **Probe the model.** Good: "Predict what happens if X changes", "Explain why Y,
   in your own words", "Would this still hold if Z?", "Here's a scenario — what
   would you expect?", "Compare A and B — when does the difference matter?"
   Bad: definitions, true/false, "list the steps".
4. **After each answer** say *what specifically* was right or wrong, then build
   the next question on what they showed they understood.
5. **Use the diagrams.** If they're shaky, point at one and ask them to explain
   it. A visual consolidates a wobbly model faster than more prose does.
6. **End with synthesis** — "sum up the whole topic as if explaining to someone
   who hasn't read this." That answer reveals whether the model is integrated.

Afterwards update `<meta name="last-quizzed">` and file. If you can't file, tell
the user the date to set.

**Calibration:** all correct → push toward edge cases next time. Struggling →
anchor back to intuition and diagrams before precise terms. It's a learning tool,
not an exam.


## Filing

1. Save to `topics/<slug>.html`.
2. Maintain cross-references both directions.
3. From the repo root:

   ```bash
   ./scripts/file-blueprint.sh topics/<slug>.html "{{topic}} — {{one-line summary}}"
   ```

   This validates, rebuilds `index.html`, appends `log.md`, stages sources *and*
   the rebuilt index, commits, and pushes if a remote is configured. **Always use
   the script** — never run `git add`/`commit`/`push` or `build-index.py` by hand.
4. Open the filed blueprint from disk: styling applied, math typeset, no red
   KaTeX errors.

Without repo access, output the complete HTML for the user and note any
cross-references for laptop follow-up.

## Keeping the docs honest

When you change how the hub works — the generator, the validator, the filing
script, the CSS contract, or what `index.html` contains — update `README.md` in
the same change, along with any blueprint that documents the machinery.
