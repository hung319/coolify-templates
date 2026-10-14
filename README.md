# Coolify service templates

Service templates for [Coolify](https://coolify.io) that are not in the official catalog, plus a
small CLI that deploys them through the Coolify API — no upstream pull request needed.

## Deploy

```bash
python3 deploy.py
```

That is the whole interface. It asks for the panel URL and an API key the first time (panel →
**Keys & Tokens** → API tokens) and offers to save them to `.env` next to the script, then walks
through the rest as lists:

```
Templates:
   1) netbird
choice> 1

Projects:
   1) SG LOC  (Singapore)
   2) FR LOC  (France)
choice> 1

Servers:
  21) SG 6-16 (194.233.70.145)   194.233.70.145   reachable
choice> 21

About to CREATE a service:
  template   : netbird/docker-compose.yaml
  project    : SG LOC / production
  server     : SG 6-16 (194.233.70.145) (194.233.70.145)
  destination: coolify

Deploy it right away? [y/N] y
Create it? [y/N] y
service created and deployment started: ktctl8ux...
```

The created service id is remembered in `.deploy-state.json`, so updating later is just:

```bash
python3 deploy.py netbird --update --deploy
```

### Non-interactive

```bash
python3 deploy.py --list
python3 deploy.py netbird --create --yes --deploy --project "SG LOC" --server 194.233.70.145
python3 deploy.py netbird --update --service netbird --deploy
python3 deploy.py ./any/compose.yaml --create --yes --deploy --project <uuid|name> --server <uuid|name|ip>
```

`--environment`, `--destination` and `--name` are there too; anything not passed on the command
line is picked from a list. Options: `python3 deploy.py --help`.

`--list` prints every choice with its uuid, including the environments of each project and the
destinations of each server, and passing an unknown `--project` / `--environment` / `--server` /
`--destination` / `--service` prints the same list instead of guessing. When a template references
Coolify magic variables (`${SERVICE_FQDN_*}`) the tool also disables label escaping for that
service, otherwise the panel escapes the `$` and Traefik drops the routers.

## Templates

| Template | What it is |
|---|---|
| [`netbird/`](netbird/) | Self-hosted NetBird: all-in-one server (management, signal, relay, embedded IdP) + dashboard on one domain |

## Why a repo instead of a pull request

Coolify's one-click catalog is pulled from a hardcoded URL (`config/constants.php` →
`services.official = https://cdn.coollabs.io/coolify/service-templates-latest.json`), so a panel
cannot be pointed at a private template source. Deploying from here uses the same API the panel
itself uses:

* create → `POST /api/v1/services` with `docker_compose_raw` (base64) + `instant_deploy`
* update → `PATCH /api/v1/services/{uuid}` with `docker_compose_raw` (base64), which also survives
  redeploys triggered from the panel

## Coolify service templates: what bites

Things learned while making these work:

* A service container is only exposed to Traefik when it carries `traefik.enable=true`; the panel
  runs Traefik with `--providers.docker.exposedbydefault=false`, so without that label every
  `traefik.http.routers.*` label on the container is ignored and requests fall through to whatever
  other container matches (usually a 404).
* `is_container_label_escape_enabled` (default on) escapes `$` in labels, so a label such as
  ``Host(`${SERVICE_FQDN_APP}`)`` reaches the container as the literal `${SERVICE_FQDN_APP}` and
  Traefik silently drops the router (`... is not a valid hostname`). Turn that setting off for
  services whose labels reference Coolify magic variables.
* A router declared with `tls=true` does not match plain HTTP requests, so a service reached
  through a Cloudflare Tunnel (Traefik `http` entrypoint) needs `tls=false` there, plus a second
  router on `https` with `tls=true` if direct HTTPS access is also wanted.
* `SERVICE_FQDN_*` magic variables can carry a path (`SERVICE_FQDN_APP=/api`), but each container
  of a service gets its own generated host, so they cannot put two containers of one service on the
  same domain — that still needs hand-written Traefik labels.
* Panels behind Cloudflare reject requests carrying the default `python-urllib` user agent
  (error 1010, `browser_signature_banned`); `deploy.py` sends its own user agent.
