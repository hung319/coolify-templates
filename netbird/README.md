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


## Optional: NetBird Reverse Proxy (expose services to the internet)

NetBird's [Reverse Proxy](https://docs.netbird.io/manage/reverse-proxy) publishes internal
services (a peer, a network resource) through the mesh. Self-hosted it **requires Traefik**:
Traefik has to pass the TLS connection *through* untouched, because the `netbird-proxy` container
manages its own certificates.

1. Get a token:
   ```bash
   docker exec netbird-server-<uuid> /go/bin/netbird-server --config /etc/netbird/config.yaml \
     admin token create --name my-proxy          # prints nbx_... once
   ```
2. Service environment (Coolify → service → Environment Variables):
   ```
   NB_PROXY_DOMAIN=proxy.example.com            # cluster apex; subdomains are minted from it
   NB_PROXY_TOKEN=nbx_...
   NB_PROXY_MANAGEMENT_ADDRESS=http://netbird-server:80   # over the docker network
   NB_PROXY_ALLOW_INSECURE=true
   NB_PROXY_ADDRESS=:8443
   NB_PROXY_ACME_CERTIFICATES=true
   NB_PROXY_ACME_CHALLENGE_TYPE=tls-alpn-01
   NB_PROXY_CERTIFICATE_DIRECTORY=/certs
   NB_PROXY_FORWARDED_PROTO=https
   NB_PROXY_PROXY_PROTOCOL=true
   NB_PROXY_TRUSTED_PROXIES=<service network CIDR>
   ```
3. Add the service (labels adapted for Coolify: the entrypoint is `https`, not `websecure`):
   ```yaml
     proxy:
       image: 'netbirdio/reverse-proxy:latest'
       extra_hosts: ['<netbird domain>:<service network gateway>']   # split DNS, see below
       volumes: ['netbird-proxy-certs:/certs']
       healthcheck:   # the image has no shell, so CMD + wget (not CMD-SHELL)
         test: ['CMD', 'wget', '-qO-', '--timeout=5', 'http://127.0.0.1:8080/healthz']
       labels:
         - traefik.enable=true
         - traefik.tcp.routers.proxy-passthrough.entrypoints=https
         - 'traefik.tcp.routers.proxy-passthrough.rule=HostSNI(`*`) && !HostSNI(`<netbird domain>`)'
         - traefik.tcp.routers.proxy-passthrough.tls.passthrough=true
         - traefik.tcp.routers.proxy-passthrough.priority=1
         - traefik.tcp.routers.proxy-passthrough.service=proxy-tls
         - traefik.tcp.services.proxy-tls.loadbalancer.server.port=8443
         - traefik.tcp.services.proxy-tls.loadbalancer.serverstransport=pp-v2@file
   ```
4. `serversTransports` is not a label — put it in Traefik's file provider
   (`/data/coolify/proxy/dynamic/netbird-proxy-pp.yaml`):
   ```yaml
   tcp:
     serversTransports:
       pp-v2:
         proxyProtocol:
           version: 2
   ```

Gotchas:
- Wildcard DNS `*.proxy.example.com` → the server, **DNS-only** (grey cloud). A Cloudflare-proxied
  record terminates TLS and breaks both the passthrough and the `tls-alpn-01` challenge.
- `!HostSNI(<netbird domain>)` keeps the dashboard/API terminating TLS at Traefik.
- **No hairpin NAT?** If the server cannot reach its own public IP, the proxy's NetBird client
  cannot reach Signal/relay and every proxied service returns 504. Point the NetBird domain at the
  service network's gateway with `extra_hosts` (split DNS) so it goes through the local Traefik.
- The token created with `admin token create` makes a management-wide (shared) cluster; services
  are reachable publicly. NetBird-only access and the "Proxy Cluster" target need a proxy running
  as an embedded peer (`netbird proxy`).

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
