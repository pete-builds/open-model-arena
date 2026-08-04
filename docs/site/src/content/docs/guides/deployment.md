---
title: Deployment
description: Docker, TLS termination, reverse proxies, and where Arena expects to live in your network.
---

Open Model Arena is a small FastAPI + vanilla-JS app in a single Docker
container. Data lives in SQLite (bind-mounted volume). It expects to run
inside a trusted network with an HTTPS reverse proxy in front.

## Docker (recommended)

```yaml
# docker-compose.yml (ships with the repo)
services:
  model-arena:
    image: ghcr.io/pete-builds/open-model-arena:latest
    container_name: model-arena
    restart: unless-stopped
    ports:
      - "3694:3694"
    volumes:
      - arena-data:/app/data
      - ./models.yaml:/app/models.yaml:ro
    environment:
      - ARENA_PASSPHRASE=${ARENA_PASSPHRASE}
      - AUTH_TOKEN_SECRET=${AUTH_TOKEN_SECRET}
      - GATEWAY_API_KEY=${GATEWAY_API_KEY}
      - ARENA_API_TOKENS=${ARENA_API_TOKENS:-}
      - TZ=${TZ:-America/New_York}

volumes:
  arena-data:
```

`docker compose up -d` and you're live at `http://localhost:3694`.

## HTTPS

Auth cookies use `Secure=True`, which requires HTTPS for anything other
than `localhost`. Terminate TLS at any proxy and route to the container.

### Caddy

```caddyfile
arena.example.com {
    reverse_proxy localhost:3694
}
```

That's the whole config — Caddy handles Let's Encrypt automatically.

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name arena.example.com;

    ssl_certificate     /etc/letsencrypt/live/arena.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/arena.example.com/privkey.pem;

    # SSE streams need buffering off + long timeouts.
    proxy_buffering off;
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:3694;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

The `X-Forwarded-For` header feeds the rate limiter — set it once, from
the immediate proxy only. Don't append.

### Cloudflare Tunnel

```yaml
# ~/.cloudflared/config.yml
tunnel: <your-tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json
ingress:
  - hostname: arena.example.com
    service: http://localhost:3694
  - service: http_status:404
```

Cloudflare terminates TLS at the edge and speaks HTTPS to Arena over the
Tunnel; you don't need to expose a port at all.

### Tailscale Funnel

```bash
tailscale serve --https=443 --set-path=/ http://localhost:3694
tailscale funnel 443 on
```

Same idea, over a Tailnet — great for "friends and coworkers" style
sharing without exposing anything to the public internet.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ARENA_PASSPHRASE` | Yes | Shared secret for the browser login |
| `AUTH_TOKEN_SECRET` | Yes | Signs the auth cookie (`openssl rand -hex 32`) |
| `ARENA_API_TOKENS` | No | Comma-separated bearer tokens for `/api/*` |
| `GATEWAY_API_KEY` | No | Any provider API key referenced via `api_key_env` |
| `TZ` | No | Timezone for the "battles today" stat |

Refuses to start if `ARENA_PASSPHRASE` or `AUTH_TOKEN_SECRET` are missing.

## Data

- **SQLite database** in the bind-mounted `/app/data` volume. WAL mode is
  on, so reads (SSE streams) don't block writes (votes).
- **Back it up** like any other SQLite database: `sqlite3 arena.db
  ".backup 'arena.$(date +%F).db'"`.
- Multi-server deployments: not supported yet. If you need horizontal
  scale, run one arena per team and aggregate at the leaderboard level
  yourself. See [SHOWCASE-PLAN.md](https://github.com/pete-builds/open-model-arena/blob/main/docs/SHOWCASE-PLAN.md).

## Container image

Multi-arch images published to GHCR on every `v*.*.*` tag:

```
ghcr.io/pete-builds/open-model-arena:latest
ghcr.io/pete-builds/open-model-arena:vX.Y.Z
ghcr.io/pete-builds/open-model-arena:X.Y.Z
```

Both `linux/amd64` and `linux/arm64` build on every release.
