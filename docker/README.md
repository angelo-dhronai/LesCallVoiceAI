# lescall Docker

Backend services run with `network_mode: host` so the existing hardcoded localhost
wiring (ESL :9000, media WS :9001, playback :9004, dashboard API :8080) works unchanged.
The web SPA is bridge-networked on `:8088`.

## Build & run (FreeSWITCH stays on host — default, lowest risk)
```bash
docker compose -f docker/docker-compose.yml up -d --build
# app (socket_server + ws_audio_receiver), dashboard_api, web
```
Visit the dashboard at `http://<server>:8088`.

## Also containerize FreeSWITCH (verify first)
```bash
docker compose -f docker/docker-compose.yml --profile fs-container up -d --build
```
⚠️ `Dockerfile.freeswitch` must be completed/verified before use:
1. Pick a FreeSWITCH 1.11.x base matching the host.
2. Build `mod_audio_stream` against its dev headers and `COPY` the `.so` into
   `/usr/lib/freeswitch/mod/`.
3. Confirm a real inbound call still streams to `ws://127.0.0.1:9001`.

Until verified, keep FreeSWITCH on the host (omit the profile).

## Notes
- `.env` is read from `calling-agent/.env` (SARVAM_API_KEY etc.).
- `data/` (SQLite), `recordings/`, `credit_log/` are bind-mounted for persistence.
- The dashboard API generates FreeSWITCH gateway/dialplan into `FS_CONF_DIR`
  (`/etc/freeswitch` by default) and reloads via `fs_cli`; the container needs
  access to those (host networking + a mounted/host fs_cli, or run on the host).
