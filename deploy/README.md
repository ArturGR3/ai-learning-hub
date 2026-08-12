# Reading your hub on your phone

Opening the folder on a laptop is one way in. Reading on a phone is the other,
and for a lot of people it is the main one - a blueprint is a good thing to
re-read on a train, and a quiz works fine over a screen you already have in
your hand.

Your phone does not have the checkout, so something has to serve the folder to
it. Two setups do that. Pick by who you want to be able to reach it.

```
Who should be able to open it?
  |
  +-- only my own devices ------------> pi-tailscale.md
  |
  +-- me, from anything, no fuss,
      and I accept a public URL ------> cloudflare.md
```

## The two, side by side

| | **Pi + Tailscale** | **Cloudflare Pages** |
|---|---|---|
| Who can reach it | only devices on your tailnet | anyone with the URL |
| Hardware | a Pi, NAS, or any always-on box | none |
| Account | Tailscale, free tier | Cloudflare, free tier |
| Setup | ~15 minutes | ~5 minutes |
| Your notes live | on hardware you own | on someone else's |
| Works offline at home | yes | no |

**Pi + Tailscale** is the private one. Nothing is exposed to the internet, no
port forwarding, no certificates. It costs you a machine that stays on.

**Cloudflare Pages** is the fast one, and needs no hardware at all. The tradeoff
is real and worth stating plainly: Pages builds from a private repo but serves
the result to **anyone with the URL**. That URL is unguessable, not secret - it
shows up in browser history, link previews, and certificate transparency logs.
Cloudflare Access can put a login in front of it, which fixes that at the cost
of a sign-in on every device (see `cloudflare.md`).

Nothing stops you running both. They read the same repo and neither one writes
to it.

## The rule both share

Whatever serves the hub is **pull-only**. It clones, it pulls, it serves. It
never commits and never pushes. One-way means no merge conflicts, no surprise
history, and no situation where the server holds the only copy of something.

## The third option, if you want no server

Sync the folder with whatever you already use - Syncthing, iCloud Drive,
Dropbox, a `git pull` in Termux - and open `index.html` from a file browser.
Every asset ships inside the repo, so this genuinely works. Browsing is clumsier
than tapping a URL, and it costs nothing to try before setting up either of the
above.
