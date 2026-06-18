# Deploy — one command

The whole application + web + data plane runs from the repo root:

```bash
docker compose up -d --build      # postgres, redis, dashboard_api, socket_server,
                                  # ws_audio_receiver, web (TLS) — all in one
docker compose ps                 # status
docker compose logs -f dashboard_api
docker compose down               # stop everything
```

Dashboard: **https://platform.lescall.dhronai.com** (TLS terminated by the `web` container).

## What's containerized vs native
- **Containers (this compose):** Postgres, Redis, dashboard API, socket_server,
  ws_audio_receiver, web (nginx + Let's Encrypt TLS + React SPA).
- **Native host services (the SIP/media edge):** FreeSWITCH + Kamailio.
  - FreeSWITCH packages are token-gated (no image) and host networking is the
    correct, lowest-latency setup for RTP/SIP.
  - Kamailio's carrier-ACL reload + GCP firewall sync run as host systemd path-units.
  - Containers reach them over `localhost` via `network_mode: host`.

## Data
- Postgres data lives in the `pgdata` Docker volume. App tables are created from
  `docker/postgres-init/01-app-schema.sql` + the dashboard_api seed on first boot;
  Kamailio's `address`/`trusted`/`version` tables from `10-kamailio-schema.sql`.
- `.env` (secrets: Sarvam, OpenAI, Google OAuth) is read from `calling-agent/.env`.
- Bind mounts: `recordings/`, `data/`, `credit_log/`, `/etc/freeswitch`,
  `/etc/letsencrypt` (ro), and the `/tmp/ai_telephony_playback` shared with FreeSWITCH.

## Rollback to native services
```bash
docker compose down
sudo systemctl enable --now postgresql redis-server nginx
# (the native Postgres data dir was left intact; native dashboard via systemd unit
#  lescall-dashboard-api if re-enabled)
```

## TLS renewal note
Certs are mounted read-only from the host `/etc/letsencrypt`. `certbot renew` on the
host updates them; after renewal, reload the web container:
`docker compose restart web`. (Host certbot's `--nginx` reload no longer applies since
nginx is containerized — use `certbot renew` + the restart, or a deploy-hook.)
