# Coolify service templates (self-hosted)

Service templates for [Coolify](https://coolify.io) that are not in the official catalog,
plus a small CLI to deploy/update them straight through the Coolify API — no need to get a
template merged upstream first.

## Why this exists

Coolify's one-click catalog is pulled from a hardcoded URL
(`config/constants.php` → `services.official = https://cdn.coollabs.io/coolify/service-templates-latest.json`),
so a panel cannot be pointed at a private template source. Deploying from here uses the same
API the panel itself uses:

* create → `POST /api/v1/services` with `docker_compose_raw` (base64) + `instant_deploy`
* update → `PATCH /api/v1/services/{uuid}` with `docker_compose_raw` (base64, survives redeploys)

## Templates

| Template | What it is |
|---|---|
| [`netbird/`](netbird/) | Self-hosted NetBird (all-in-one server + dashboard) behind one domain |

## Usage

```bash
export COOLIFY_URL=https://coolify.example.com
export COOLIFY_API_KEY=...            # panel: Keys & Tokens → API tokens (needs deploy/root to trigger a deploy)
export COOLIFY_PROJECT_UUID=...       # from the project URL
export COOLIFY_ENVIRONMENT=production
export COOLIFY_SERVER_UUID=...        # required when creating
export COOLIFY_DESTINATION_UUID=...   # required when creating

# create a new service from a template and deploy it
python3 deploy.py netbird/docker-compose.yaml --name netbird --create --deploy

# later: push compose changes to the existing service
COOLIFY_SERVICE_UUID=xxxxxxxx python3 deploy.py netbird/docker-compose.yaml --update --deploy
```

`python3 deploy.py --help` lists the options. Runs on python3 stdlib only.

## Notes on Coolify service templates in general

Things learned the hard way while making these work:

* A service container is only exposed to Traefik when it carries `traefik.enable=true`;
  the panel runs Traefik with `--providers.docker.exposedbydefault=false`, so without that
  label every `traefik.http.routers.*` label on the container is ignored (requests fall
  through to whatever other container matches, usually a 404).
* `is_container_label_escape_enabled` (default on) escapes `$` in labels, so a label such as
  ``Host(`${SERVICE_FQDN_APP}`)`` reaches the container as the literal `${SERVICE_FQDN_APP}`
  and Traefik silently drops the router (`... is not a valid hostname`). Turn that setting off
  for services whose labels reference Coolify magic variables.
* A router declared with `tls=true` does not match plain HTTP requests, so a service reached
  through a Cloudflare Tunnel (Traefik `http` entrypoint) needs the router on `http` with
  `tls=false`, and a second router on `https` with `tls=true` if direct HTTPS is also used.
* `SERVICE_FQDN_*` magic variables can carry a path (`SERVICE_FQDN_APP=/api`), but each
  container of a service gets its own generated host, so they cannot put two containers of one
  service on the same domain — that still needs hand-written Traefik labels.
