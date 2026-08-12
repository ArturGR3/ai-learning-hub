# Reading the hub on your phone, privately

A Raspberry Pi (or any always-on machine) clones the repo, pulls every few
minutes, and serves the folder over plain HTTP. Tailscale puts that machine on a
private network that only your own devices can see.

Nothing is exposed to the internet. No port forwarding, no dynamic DNS, no
certificates, no reverse proxy. The hub is static files, so the server has no job
beyond handing them over.

```
  laptop  ──git push──▶  GitHub  ◀──git pull (every 5 min)──  Pi
                                                               │
                                                        serves ./ on :8080
                                                               │
                                     ┌─────── tailnet ─────────┴──────────┐
                                     │   only devices you have logged in  │
                                     └─────────┬──────────────┬───────────┘
                                            phone          laptop
```

## Before you start

- A Pi, an old laptop, a NAS - anything that stays on and runs Linux.
- SSH access to it.
- A Tailscale account. The free tier covers a personal set of devices; check
  the current device limit at tailscale.com/pricing before you commit to it.
- If the repo is private, a read-only deploy key on the Pi. Generate it with
  `ssh-keygen`, add the public half to the repo's deploy keys, and leave write
  access off.

## 1. Clone the hub on the Pi

```bash
sudo apt update && sudo apt install -y git
git clone <your-hub-repo> ~/learning-hub
```

Confirm it is complete as cloned - no build step should be needed:

```bash
ls ~/learning-hub/index.html ~/learning-hub/assets/blueprint.css
```

## 2. Serve the folder

Python's built-in server is enough for static files on a private network.

`/etc/systemd/system/learning-hub.service`:

```ini
[Unit]
Description=Learning Hub static server
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/learning-hub
ExecStart=/usr/bin/python3 -m http.server 8080 --bind 0.0.0.0
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now learning-hub
curl -sI http://localhost:8080/ | head -1     # expect: HTTP/1.0 200 OK
```

## 3. Pull on a timer

**Pull-only.** This machine never commits and never pushes, so there is no
scenario where it and your laptop disagree about history.

`/etc/systemd/system/learning-hub-pull.service`:

```ini
[Unit]
Description=Pull the learning hub

[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/learning-hub
ExecStart=/usr/bin/git pull --ff-only
```

`/etc/systemd/system/learning-hub-pull.timer`:

```ini
[Unit]
Description=Pull the learning hub every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now learning-hub-pull.timer
systemctl list-timers learning-hub-pull.timer
```

`--ff-only` is deliberate. If the Pi's checkout ever diverges, the pull fails
loudly instead of creating a merge commit on a machine nobody is watching.

## 4. Put it on your tailnet

On the Pi:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4          # note the 100.x.y.z address
```

Install Tailscale on your phone and laptop and log in with the same account.
Then, from your phone:

```
http://<pi-tailscale-name>:8080/
```

That URL works from anywhere with a data connection and resolves for nobody
else. Add it to your home screen.

## Checking it actually works

- Turn wifi off on your phone, load the hub over mobile data. It should still
  resolve - that is the tailnet doing its job, not your home network.
- File a blueprint from your laptop, wait five minutes, reload on the phone.
- Ask a friend to open the same URL. They should get nothing at all.

## When it breaks

| Symptom | Usually |
|---|---|
| Phone cannot resolve the name | Tailscale logged out on the phone or the Pi; `tailscale status` on both |
| Page loads unstyled | You are serving the wrong directory - `WorkingDirectory` must be the repo root |
| New blueprints never appear | The timer is not running, or the pull is failing; `journalctl -u learning-hub-pull` |
| Pull fails every time | Something committed on the Pi. Do not fix it there - `git reset --hard origin/main` |
