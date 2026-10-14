# NetBird (self-hosted) — Coolify service template

| File | What it is |
|---|---|
| `docker-compose.yaml` | ready-to-deploy Coolify **service**: all-in-one `netbird-server` (management, signal, relay, embedded Dex IdP) + `dashboard`, served from one domain |
| `config.yaml` | sample static server config — only needed if you prefer mounting a config file instead of letting the compose generate it |

## Deploy with the CLI

```bash
python3 ../deploy.py netbird            # wizard: pick project, server, confirm
python3 ../deploy.py netbird --create --yes --deploy --project "SG LOC" --server 194.233.70.145
```

Only the panel URL and an API key are needed (asked once, then kept in `.env`); the project,
environment, server and destination are chosen from lists. See the [root README](../README.md).

## Deploy by hand

Coolify → **+ New** → **Service** → **Docker Compose Empty**, then paste the content of
`docker-compose.yaml`. Give the service a domain (any domain, e.g. `mesh.example.com`) —
Coolify fills `SERVICE_FQDN_NETBIRD` and the dashboard/server pick it up automatically.

Afterwards: open the domain, create the first admin account, then **Setup Keys → Create** and on
each machine run `netbird up --setup-key <key> --management-url https://mesh.example.com`.

## Before updating an existing instance

`deploy.py netbird --update` replaces whatever the service ran with this compose, and this compose
generates `authSecret` / `store.encryptionKey` on first start and keeps them in the
`netbird-data` volume. An instance that was created with a mounted `config.yaml` has its own
values there, so copy those two into the volume first, otherwise the SQLite data was encrypted
with a key the new config does not know:

```bash
docker run --rm -v <service>_netbird-data:/data alpine sh -c \
  'printf %s "<authSecret-from-config.yaml>" > /data/auth-secret; printf %s "<encryptionKey>" > /data/encryption-key'
```

## STUN (3478/udp) and the Cloudflare proxy

Peers need STUN to discover their public endpoints and connect directly (instead of going
through the relay). NetBird advertises STUN as the management host plus `stunPorts`, and if
that hostname goes through the Cloudflare proxy (or a Cloudflare Tunnel) the UDP packets never
arrive: the proxy only speaks HTTP(S)/WebSocket.

So the compose runs a small standalone STUN server (coturn, UDP 3478 only) and the generated
server config advertises it at `NB_STUN_HOST` instead of the proxied domain:

```bash
# in Coolify: service -> Environment Variables
NB_STUN_HOST=203.0.113.10          # the server's public IP ...
NB_STUN_HOST=stun.example.com      # ... or a DNS-only (grey cloud) hostname
```

Without it, `NB_STUN_HOST` falls back to the service domain, which is correct when the domain
is *not* proxied (plain DNS A record to the server). Note that setting `stuns` disables the
built-in STUN server of `netbird-server`, which is exactly why coturn is there. Peers pick the
new STUN up when they reconnect.

If you edit `config.yaml` in Coolify (Files/Configuration) instead of using the generated one,
keep the same `stuns:` entry, otherwise a redeploy writes the old file back.

## Things that bite

* **`is_container_label_escape_enabled` must be off** for this service. By default Coolify escapes
  `$` in container labels, so ``Host(`${SERVICE_FQDN_NETBIRD}`)`` lands in the container as a
  literal and Traefik silently drops the routers (`... is not a valid hostname`). Symptom: the
  dashboard loads but says you are not authenticated, because `/api` and `/oauth2` request the
  dashboard container and get a 404.
* The `netbird-server` container must carry `traefik.enable=true`, because Coolify runs Traefik
  with `--providers.docker.exposedbydefault=false`.
* Routers are declared on the plain `http` entrypoint with `tls=false` (needed when the domain
  arrives through a Cloudflare Tunnel) plus a second set on `https` for direct access.
* The gRPC paths (`/management.ManagementService/`, `/signalexchange.SignalExchange/`,
  `/management.ProxyService/`) are proxied with `scheme=h2c`, otherwise the agents cannot reach
  the management API. The dashboard would still look fine, peers just would not connect.
* `3478/udp` must be open inbound (and allowed outbound by any host firewall) for STUN/TURN, i.e.
  for peers to connect directly instead of through the relay.
* `netbird-server`'s own `/health` endpoint always answers `503`
  (`{"status":"unhealthy","listeners":null}`): it is the relay healthcheck, and the relay is
  multiplexed on the management port in this image. The healthcheck in the compose probes the HTTP
  port with bash `/dev/tcp` instead, since the image has neither `curl` nor `wget`.
