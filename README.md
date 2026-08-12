# Learning Hub

A place to keep what you learn.

<p align="center">
  <img src="docs/index.png" alt="The Learning Hub index" width="760">
</p>

Every time you understand something new, you write it down as a **blueprint** -
one page that teaches that one topic. The intuition first, then the examples,
then the precise words, with a diagram wherever a picture says it better.

<p align="center">
  <img src="docs/blueprint.png" alt="A blueprint" width="760">
</p>

## The links are the point

A blueprint points at the ones it builds on, and they point back. Those links are
the only structure - there are no folders to organize and no categories to
maintain. Related topics end up near each other because you kept linking them,
and the shape of what you know appears on its own.

That is the idea borrowed from the **LLM wiki**: do the thinking once, when you
write the page, instead of redoing it every time you ask. A chat answers and
disappears. A blueprint stays, and the next one connects to it. What compounds
is not the pages - it is the connections between them.

## Start

```bash
git clone https://github.com/ArturGR3/ai-learning-hub my-learning-hub
cd my-learning-hub
open index.html
```

Everything a page needs travels with it, so your hub opens anywhere, offline,
years from now.

`topics/example-blueprint.html` is a real lesson and a working example of every
convention. Read it before writing your own.

## Write one

Install the skill once:

```bash
./scripts/install-skill.sh
```

Then type `/learn` in any project. Claude teaches you the topic, turns it into a
blueprint, and files it here.

Prefer to write the HTML yourself? File it with:

```bash
./scripts/file-blueprint.sh topics/<name>.html "topic - one line"
```

## Make it yours

Open `AGENTS.md` and rewrite the top section: who you are and how you learn. That
is what an agent reads before teaching you anything. Everything below it already
works for anyone.

Then delete the example and write your first blueprint.

---

Want it on your phone? See `deploy/`. Most people never need it.

MIT licensed. The blueprints you write are yours.
