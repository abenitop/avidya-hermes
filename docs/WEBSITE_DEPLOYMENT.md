# Website exposure — hermes.ts-al.com

How Avidya's Hermes dashboard is actually reachable from the internet.
Written 2026-08-02 because this had never been documented anywhere
before — verified against the real running config, not assumed. Compare
with [Calibre-Web's equivalent
doc](https://github.com/abenitop/calibre-web-ops/blob/main/WEBSITE.md) —
same domain, same Caddy instance, different app.

## The chain

```
Internet
  │
  ├─ DNS: hermes.ts-al.com  A  217.216.94.182   (Cloudflare — DNS only)
  │
  ▼
Caddy (systemd: caddy.service, root, ports 80/443)
  │  /etc/caddy/Caddyfile:
  │    hermes.ts-al.com {
  │        basicauth {
  │            abp  <bcrypt hash>
  │        }
  │        reverse_proxy localhost:9119 {
  │            header_up Host localhost:9119
  │        }
  │    }
  │  Automatic HTTPS via Let's Encrypt, terminated here.
  │  header_up Host override: the dashboard backend validates the Host
  │  header itself, so Caddy is told to send the literal backend host
  │  (localhost:9119) rather than forwarding the real hermes.ts-al.com
  │  Host it received -- without this the backend would likely reject
  │  the request as a Host-header mismatch.
  ▼
hermes-dashboard.service (systemd --user, avidya)
  ExecStart=.../venv/bin/python -m hermes_cli.main dashboard
            --port 9119 --skip-build --no-open
  Listens on 127.0.0.1:9119 ONLY -- not reachable directly from outside,
  Caddy + basicauth is the only path in. Correct: never widen this to
  0.0.0.0 without also reconsidering the auth story.
```

**Cloudflare's real role here: DNS only, not a proxy.** Confirmed by
comparing `dig hermes.ts-al.com` against this VPS's own real public IP
(`curl -4 ifconfig.me`) -- identical. Not orange-clouded. Caddy is
directly internet-facing.

## Auth

HTTP Basic Auth at the Caddy layer, one account (`abp`), bcrypt hash in
the Caddyfile (`/etc/caddy/Caddyfile` -- treat that file as containing a
real credential, same handling as any other secret; it's not in this
repo and shouldn't be). The dashboard app itself has no separate login
layer as far as this doc's investigation went -- basicauth is the only
gate. If the dashboard is ever changed to bind wider than
127.0.0.1, that single gate becomes the *only* thing standing between
the internet and Avidya's control surface.

## Related but not web-exposed: hermes-gateway.service

Same host, same `~/.hermes` install, different systemd unit
(`hermes-gateway.service`) -- runs `hermes_cli.main gateway run`, the
actual agent loop and the WhatsApp bridge (`bridge.js --port 3001`,
localhost-only). Not reachable via Caddy or any domain; the dashboard
(9119) is the only piece of this stack with a public URL. Both units
share `ExecStartPost`/`ExecStopPost` hooks that push `ntfy.sh/avidya-tsalx`
notifications on start/stop -- that's your real-time signal if either
one restarts or crashes.

## Operating it

- Status: `systemctl --user status hermes-dashboard.service hermes-gateway.service`
  (note: `--user`, not `sudo systemctl` -- these are user-level units
  under `avidya`, not system-level like Calibre-Web's)
- Logs: `journalctl --user -u hermes-dashboard.service`
- Restart just the dashboard (not the agent/WhatsApp gateway):
  `systemctl --user restart hermes-dashboard.service`
- Caddy config: `/etc/caddy/Caddyfile` (system-level, needs `sudo`) --
  edit, then `sudo systemctl reload caddy`.
- Isolate a problem: `curl -v http://localhost:9119` on-box (bypasses
  Caddy/DNS/TLS/auth entirely) vs. `curl -v -u abp:<password>
  https://hermes.ts-al.com` from outside.

## What this doesn't cover

Hermes's own internal architecture, skills, memory, or WhatsApp
integration -- see `docs/MANIFESTO.md` and the rest of `docs/` in this
repo. This file is specifically about the HTTP path from a browser to
the running dashboard.
