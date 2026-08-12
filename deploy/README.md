# Deploying - which you probably should not do

Start with the argument against.

The hub is a folder of HTML files with every asset vendored inside it. Opening
`index.html` from disk is not a degraded fallback, it is the intended interface,
and it is faster and more reliable than any hosted version of the same thing.
Deploying adds a machine that can be down, a URL that can leak, and a copy that
can drift from the repo. It buys exactly one thing: reading the hub on a device
that does not have the checkout.

So the honest decision tree is short.

```
Do you read the hub on your phone?
  |
  +-- No  ---------------------------> you are done. Close this folder.
  |
  +-- Yes, and it is private to me ---> pi-tailscale.md
  |
  +-- Yes, and I want a URL that
      just works from anywhere -------> cloudflare.md
```

## The two paths

| | `pi-tailscale.md` | `cloudflare.md` |
|---|---|---|
| Who can reach it | only your own devices | anyone with the URL |
| Needs hardware | a Pi or any always-on box | none |
| Needs an account | Tailscale (free tier) | Cloudflare (free tier) |
| Setup | ~15 minutes | ~5 minutes |
| Your notes are | on hardware you own | on someone else's |

**Private repos work with both.** Cloudflare Pages builds from a private repo
and serves the result publicly - the repo stays closed, the *site* does not.
That distinction catches people out, so make the choice deliberately: anything
you would not want indexed should not go through Pages.

## The rule both paths share

Whatever serves the hub is **pull-only**. It clones, it pulls, it serves. It
never commits and never pushes. One-way means no merge conflicts, no surprise
history, and no scenario where the server is the only copy of something.

## Neither, and still on your phone

Sync the folder with whatever you already use - Syncthing, iCloud Drive, Dropbox,
a `git pull` in Termux - and open `index.html` in a file browser. The hub is
self-contained, so this genuinely works. It is clumsier to browse than a URL, and
it costs nothing to try before setting up a server.
