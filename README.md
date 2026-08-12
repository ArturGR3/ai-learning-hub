# Learning Hub

A folder of HTML pages that teach you things, plus a few scripts that keep the
folder tidy.

Clone it and double-click `index.html`. That is the entire setup. No build step,
no server, no account, and nothing on any page talks to the network.

## The idea

Working with an AI assistant makes it very easy to *get the thing done* without
ever learning what you just did. This repo is the counterweight. When you hit a
concept you do not actually understand, you stop, learn it properly, and file
the result here as a **blueprint**: one self-contained HTML page that teaches one
topic, intuition first, with the diagrams and worked examples that make it
stick.

Over time you get a small personal encyclopedia of the things you decided were
worth understanding - written in your language, at your level, and readable
offline forever.

## Start here

```bash
git clone <this-repo> my-learning-hub
cd my-learning-hub
open index.html            # macOS. On Linux: xdg-open index.html
```

`topics/example-blueprint.html` is both a real lesson (why a poll of 1,000 works)
and a demonstration of every convention the hub uses. Read it once before writing
your own.

## Make it yours

**1. Rewrite the top of `AGENTS.md`.** The "Who the user is" section is the only
part of that file that is about *you*, and it is the part that decides whether
the teaching lands. Say how you learn, what you already know, and what "too
basic" looks like for you. Everything below it is machinery that works for
anyone.

**2. Install the `/learn` skill.**

```bash
./scripts/install-skill.sh
```

It creates one symlink, `~/.claude/skills/learn` pointing into this repo, and
tells you so before it does anything. After restarting Claude Code you can type
`/learn` from *any* project on your machine - the skill finds this hub through
the symlink, reads `AGENTS.md`, and files the blueprint here. That is the whole
point: the learning happens where the work happens.

You can skip this and just point an agent at the repo by hand. It works, it is
only more typing.

**3. Delete `topics/example-blueprint.html`** once you have a blueprint of your
own, then run `python3 scripts/build-index.py`.

## The layout

| Path | What it is |
|---|---|
| `topics/*.html` | The blueprints. The knowledge, and the only irreplaceable part. |
| `index.html` | Generated from the blueprints' metadata. Searchable, sortable, committed. |
| `assets/` | The stylesheet, the webfonts, and KaTeX - all vendored, none fetched. |
| `scripts/` | Validate, rebuild the index, file a blueprint, install the skill. |
| `skills/learn/` | The `/learn` skill, versioned alongside the conventions it obeys. |
| `AGENTS.md` | The conventions. `CLAUDE.md` is a symlink to it. |
| `log.md` | Append-only record of what was filed when. |
| `deploy/` | Optional ways to read the hub from your phone. You need none of it. |

## Adding a blueprint

Write it to `topics/<slug>.html` following `AGENTS.md`, then:

```bash
./scripts/file-blueprint.sh topics/<slug>.html "topic - one-line summary"
```

That validates the page, rebuilds `index.html`, appends `log.md`, commits, and
pushes if you have a remote. Use the script rather than git directly - it is what
keeps the committed index in step with the topics.

To check the hub without filing anything:

```bash
python3 scripts/validate.py
```

## Local-first, on purpose

Three rules, and `validate.py` enforces the first two:

1. **No runtime network calls.** Fonts, stylesheet, and the maths renderer all
   ship in `assets/`. Any `http` stylesheet or script is a validation error.
2. **Relative paths only.** Root-absolute paths look fine on a web server and
   break the moment you open the file from disk.
3. **Generated files are committed.** A fresh clone renders with no Python, no
   npm, and no tooling of any kind installed.

The payoff: this repo still works on a plane, in ten years, on a machine that has
never heard of it. Your notes should outlive the tools that made them.

## Reading it on your phone

Optional, and genuinely optional - most people never bother. `deploy/` covers the
two paths if you want one: a private Raspberry Pi over Tailscale, or Cloudflare
Pages. Start by reading `deploy/README.md`, which mostly argues that you do not
need either.

## License

MIT - see `LICENSE`. Use it, fork it, change it, no obligations.

The blueprints you write are yours and are not covered by it. This license is
about the machinery you cloned, not the understanding you build with it.
