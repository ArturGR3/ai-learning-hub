# Learning Hub

Keep the explanations that made something click.

You are working with an AI and something comes up that you do not really
understand. You ask. It explains, it clicks, you move on. Then the chat scrolls
away, and a month later you are asking the same question again.

This is where that explanation goes instead: one page per topic, written for
you, that you keep and keep editing.

## The loop

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/loop-dark.png">
  <img src="docs/loop.png" alt="A conversation becomes a blueprint, the blueprint joins your hub, and you come back to re-read it, edit it and be quizzed on it">
</picture>

Nothing here is finished. A blueprint is a page you go back to: fix the part
that was wrong, add the example you only understood later, link it to the topic
you learned this week.

## What a blueprint looks like

<p align="center"><img src="docs/blueprint.png" alt="A blueprint" width="760"></p>

Plain English first, then the examples, then the precise terms, with a diagram
wherever a picture says it faster. It is one self-contained HTML file, so it
opens anywhere, offline, years from now.

## It grows into something

<p align="center"><img src="docs/index.png" alt="The index, listing blueprints with their tags and which are due for a quiz" width="760"></p>

<p align="center"><sub>The index after a few months. It is generated from your
blueprints, and it ships with one.</sub></p>

Blueprints link to the ones they build on. That graph is the only organization -
no folders, no categories, nothing to file things under. Clusters form because
you kept linking, and the shape of what you know appears on its own.

This is the idea behind Karpathy's **LLM wiki**: do the reasoning once, when you
write the page, instead of redoing it in every chat.

## Start

```bash
git clone https://github.com/ArturGR3/ai-learning-hub my-learning-hub
cd my-learning-hub
open index.html
```

`topics/example-blueprint.html` is a real lesson and an example of every
convention.

## Write your first one

Install the `/learn` skill once:

```bash
./scripts/install-skill.sh
```

It symlinks `skills/learn/` into `~/.claude/skills/` and changes nothing else.
Then type `/learn` in any Claude Code session, in any project. It teaches you
the topic, turns it into a blueprint, files it here, and offers to quiz you.

Writing the HTML yourself works the same way. File it with:

```bash
./scripts/file-blueprint.sh topics/<name>.html "topic - one line"
```

It validates the page, rebuilds `index.html`, appends `log.md`, commits, and
pushes if you have a remote.

## Make it yours

Rewrite the top section of `AGENTS.md`: who you are and how you learn. That is
what an agent reads before teaching you anything. Everything below it works for
anyone.

Then delete the example and write your first blueprint.

---

To read your hub on your phone, `deploy/` covers the two ways. MIT licensed; the
blueprints you write are yours.
