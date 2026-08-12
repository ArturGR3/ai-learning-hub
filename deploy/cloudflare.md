# Reading the hub from a URL

Cloudflare Pages serves the repo at an `https://` address. It works from a
private repo, needs no hardware, and takes about five minutes.

**Read this before anything else:** a private repo and a private site are
different things. Pages builds from your private repo and serves the result to
**anyone who has the URL**. The URL is unguessable, not secret - it appears in
browser history, in link previews, in anything you paste it into, and in
Certificate Transparency logs. Treat the site as public, because it is.

If that is not acceptable, use `pi-tailscale.md` instead, or nothing at all.

## Setup

1. Push the hub to GitHub or GitLab. Private is fine.
2. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** →
   **Connect to Git**. Authorise the repo.
3. Build settings - this is the part people overspecify:

   | Field | Value |
   |---|---|
   | Framework preset | None |
   | Build command | *(leave empty)* |
   | Build output directory | `/` |

   There is no build. `index.html` is generated on your laptop and committed, so
   the repo is already the finished site. Any build command here is a way to
   break it.
4. **Save and Deploy.** You get `https://<project>.pages.dev`.

Every push to the default branch redeploys. Other branches get preview URLs -
turn previews off in the project settings if you would rather they did not exist.

## Confirm it worked

Open the site and check three things:

- **It is styled.** Unstyled means the CSS path did not resolve. The hub uses
  relative paths (`assets/blueprint.css`, not `/assets/...`) so that the same
  files work from disk and from a server; if you changed that, change it back.
- **Maths renders**, if you have a blueprint with maths in it.
- **The network tab shows no external requests.** Everything should come from
  your own origin. That is the local-first rule still holding.

## Putting a login in front of it

Cloudflare Access can require a Google or email login before the site loads,
which turns the public URL into a private one:

**Zero Trust** → **Access** → **Applications** → **Add a self-hosted
application**, point it at your `pages.dev` hostname, and add a policy allowing
your own email address.

This is free for a small number of users - confirm the current limit on
Cloudflare's pricing page. It is a real fix for the public-URL problem, but it
adds a login step on every device and a dependency on an identity provider. The
Pi route avoids both.

## Cost

The free tier covers a personal hub comfortably: static requests are unmetered
and builds are limited per month, far above what one person filing blueprints
will use. Limits change - check the current Pages pricing page rather than
trusting this sentence.

## Turning it off

Delete the Pages project in the dashboard. The site stops resolving immediately.
Nothing in the repo changes, and the hub still opens from disk exactly as before
- which is the point of building it this way.
