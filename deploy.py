#!/usr/bin/env python3
"""Deploy a Coolify service template from this repo.

Only the panel URL and an API key are needed; everything else (template, project,
environment, server, destination, existing service) is picked from a list.

    python3 deploy.py                 # interactive wizard
    python3 deploy.py netbird         # pre-select a template
    python3 deploy.py --list          # what is available, on this repo and on the panel
    python3 deploy.py netbird --update --deploy --service netbird
    python3 deploy.py ./some/compose.yaml --create --yes --project FR-LOC --server 159.69.41.211

Credentials are read from (in order) the environment, a `.env` next to this script,
or an interactive prompt. Nothing else has to be configured up front.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, ".env")
STATE_FILE = os.path.join(HERE, ".deploy-state.json")
UA = "coolify-templates/1.0"


# ---------------------------------------------------------------- credentials

def load_env_file(path: str = ENV_FILE) -> None:
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def save_env_file(url: str, key: str) -> None:
    with open(ENV_FILE, "w") as handle:
        handle.write("COOLIFY_URL=%s\nCOOLIFY_API_KEY=%s\n" % (url, key))
    os.chmod(ENV_FILE, 0o600)


def credentials() -> tuple[str, str]:
    url = os.environ.get("COOLIFY_URL", "").rstrip("/")
    key = os.environ.get("COOLIFY_API_KEY", "")
    if not url:
        url = input("Coolify panel URL (e.g. https://coolify.example.com): ").strip().rstrip("/")
    if not key:
        key = getpass.getpass("API key (panel -> Keys & Tokens): ").strip()
    if not url or not key:
        sys.exit("both the panel URL and an API key are required")
    if not os.path.exists(ENV_FILE) and os.isatty(sys.stdin.fileno()):
        answer = input("Save them to %s for next time? [Y/n] " % ENV_FILE).strip().lower()
        if answer in ("", "y", "yes"):
            save_env_file(url, key)
            print("saved to", ENV_FILE)
    return url, key


# ---------------------------------------------------------------------- http

class Coolify:
    def __init__(self, url: str, key: str):
        self.api = url + "/api/v1"
        self.key = key

    def call(self, method: str, path: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.key)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", UA)  # panels behind Cloudflare reject the urllib default
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read().decode()
                return resp.status, (json.loads(body) if body.strip() else {})
        except urllib.error.HTTPError as err:
            body = err.read().decode()
            try:
                return err.code, json.loads(body)
            except ValueError:
                return err.code, {"raw": body}

    def get(self, path):
        status, body = self.call("GET", path)
        if status >= 300:
            sys.exit("GET %s failed: %s %s" % (path, status, json.dumps(body)[:300]))
        return body

    def describe(self, path):
        status, body = self.call("GET", path)
        if status >= 300:
            sys.exit("%s -> %s %s" % (path, status, json.dumps(body)[:200]))
        return body


# ---------------------------------------------------------------- choosing

def pick(title: str, items, label, default=None, allow_blank=True):
    """Numbered picker. Returns the chosen item (or `default` when the list is empty)."""
    items = list(items)
    if not items:
        return default
    print("\n%s" % title)
    for index, item in enumerate(items, 1):
        print("  %2d) %s" % (index, label(item)))
    if default is not None:
        print("  (Enter = %s)" % label(default))
    while True:
        try:
            answer = input("choice> ").strip()
        except EOFError:
            if default is not None:
                return default
            sys.exit("nothing to read from stdin; pass the matching --project/--server/--service flags")
        if answer == "" and default is not None:
            return default
        if answer == "" and not allow_blank:
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1]
        match = [i for i in items if answer.lower() in label(i).lower()]
        if len(match) == 1:
            return match[0]
        if len(match) > 1:
            print("  ambiguous, matches:", ", ".join(label(i) for i in match[:5]))
        else:
            print("  ?")


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return input("%s [y/N] " % question).strip().lower() in ("y", "yes")


# ------------------------------------------------------------------ templates

def templates() -> list[tuple[str, str]]:
    """(name, path) for every <folder>/docker-compose.yaml in this repo."""
    found = []
    for entry in sorted(os.listdir(HERE)):
        folder = os.path.join(HERE, entry)
        for filename in ("docker-compose.yaml", "docker-compose.yml", "compose.yaml"):
            path = os.path.join(folder, filename)
            if os.path.isdir(folder) and os.path.exists(path):
                found.append((entry, path))
                break
    return found


def resolve_template(argument: str | None):
    found = templates()
    if argument:
        as_path = argument if os.path.exists(argument) else os.path.join(HERE, argument)
        if os.path.isfile(as_path):
            return os.path.basename(os.path.dirname(os.path.abspath(as_path))) or "service", as_path
        folder = os.path.join(HERE, argument)
        if os.path.isdir(folder):
            for _, path in found:
                if path.startswith(folder + os.sep):
                    return argument, path
        matches = [t for t in found if t[0] == argument]
        if matches:
            return matches[0]
        sys.exit("template not found: %s" % argument)
    if not found:
        sys.exit("no templates in %s (expected <folder>/docker-compose.yaml)" % HERE)
    return pick("Templates:", found, lambda t: t[0], default=found[0] if len(found) == 1 else None)


# --------------------------------------------------------------------- state

def state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE))
        except ValueError:
            return {}
    return {}


def remember(template: str, uuid: str) -> None:
    data = state()
    data[template] = uuid
    json.dump(data, open(STATE_FILE, "w"), indent=2)


# --------------------------------------------------------------------- flows

def choose_targets(api: Coolify, args):
    projects = api.get("/projects")
    names = [p["name"] for p in projects]
    project = args.project
    if project:
        match = [p for p in projects if project in (p["uuid"],)] or [p for p in projects if project.lower() == p["name"].lower()]
        project = match[0] if match else None
    if not project:
        project = pick("Projects:", projects, lambda p: "%s  (%s)" % (p["name"], p.get("description") or p["uuid"]))

    detail = api.get("/projects/" + project["uuid"])
    environments = detail.get("environments") or []
    if not environments:
        sys.exit("project %s has no environment" % project["name"])
    environment = None
    if args.environment:
        environment = next((e for e in environments if e["name"] == args.environment), None)
        if environment is None:
            sys.exit("environment %s not found in project %s" % (args.environment, project["name"]))
    if environment is None:
        default = next((e for e in environments if e["name"] == "production"), environments[0])
        environment = default if (len(environments) == 1 or args.yes) else pick("Environments:", environments, lambda e: e["name"], default=default)

    servers = api.get("/servers")
    servers = [s for s in servers if s.get("is_usable", True)]
    server = None
    if args.server:
        server = next((s for s in servers if args.server in (s["uuid"], s["ip"], s["name"])), None)
        if server is None:
            sys.exit("server %s not found" % args.server)
    if server is None:
        server = pick("Servers:", servers,
                      lambda s: "%-28s %-16s %s" % (s["name"], s.get("ip", ""), "reachable" if s.get("is_reachable") else "UNREACHABLE"))

    destinations = api.get("/servers/%s/destinations" % server["uuid"]) or []
    destination = None
    if args.destination:
        destination = next((d for d in destinations if args.destination in (d.get("uuid"), d.get("name"))), None)
    elif len(destinations) > 1:
        destination = pick("Destinations:", destinations, lambda d: "%s (%s)" % (d.get("name"), d.get("uuid")))
    elif len(destinations) == 1:
        destination = destinations[0]
    return project, environment, server, destination


def create(api: Coolify, name: str, compose_path: str, args) -> str:
    project, environment, server, destination = choose_targets(api, args)
    rel = os.path.relpath(compose_path, HERE)
    shown = compose_path if rel.startswith("..") else rel
    print("\nAbout to CREATE a service:")
    print("  template   : %s" % shown)
    print("  project    : %s / %s" % (project["name"], environment["name"]))
    print("  server     : %s (%s)" % (server["name"], server.get("ip")))
    print("  destination: %s" % (destination["name"] if destination else "default"))
    deploy_now = args.deploy or (not args.yes and confirm("Deploy it right away?", False))
    if not args.yes and not confirm("Create it?", False):
        sys.exit("aborted")

    payload = {
        "name": args.name or name,
        "docker_compose_raw": base64.b64encode(open(compose_path, "rb").read()).decode(),
        "project_uuid": project["uuid"],
        "environment_name": environment["name"],
        "server_uuid": server["uuid"],
        "instant_deploy": bool(deploy_now),
    }
    if destination:
        payload["destination_uuid"] = destination["uuid"]
    status, body = api.call("POST", "/services", payload)
    if status >= 300:
        sys.exit("create failed: %s %s" % (status, json.dumps(body)[:400]))
    uuid = body.get("uuid")
    print("\nservice created%s: %s" % (" and deployment started" if deploy_now else "", uuid))
    if body.get("domains"):
        print("domains:", ", ".join(body["domains"]))
    print("next time: python3 deploy.py %s --update --service %s --deploy" % (name, uuid))
    return uuid


def update(api: Coolify, name: str, compose_path: str, args) -> str:
    services = api.get("/services")
    target = args.service or state().get(name)
    service = None
    if target:
        service = next((s for s in services if target in (s["uuid"], s["name"])), None)
    if service is None:
        if not services:
            sys.exit("no services on this panel")
        default = next((s for s in services if s["name"].startswith(name)), None)
        service = pick("Services:", services, lambda s: "%-40s %s" % (s["name"], s["uuid"]), default=default)
    rel = os.path.relpath(compose_path, HERE)
    print("\nAbout to UPDATE a service:")
    print("  service : %s (%s)" % (service["name"], service["uuid"]))
    print("  template: %s" % (rel if not rel.startswith("..") else compose_path))
    if not confirm("Push the compose and redeploy?", args.yes):
        sys.exit("aborted")
    status, body = api.call("PATCH", "/services/" + service["uuid"], {
        "docker_compose_raw": base64.b64encode(open(compose_path, "rb").read()).decode(),
        "instant_deploy": True,
    })
    if status >= 300:
        sys.exit("update failed: %s %s" % (status, json.dumps(body)[:400]))
    print("\nupdated:", json.dumps(body)[:200])
    return service["uuid"]


def listing(api: Coolify):
    print("Templates in this repo:")
    for name, path in templates():
        print("  - %-18s %s" % (name, os.path.relpath(path, HERE)))
    print("\nProjects:")
    for project in api.get("/projects"):
        detail = api.get("/projects/" + project["uuid"])
        envs = ", ".join(e["name"] for e in detail.get("environments") or [])
        print("  - %-22s %s [%s]" % (project["name"], project["uuid"], envs))
    print("\nServers:")
    for server in api.get("/servers"):
        print("  - %-28s %-16s %s" % (server["name"], server.get("ip", ""),
                                      "reachable" if server.get("is_reachable") else "UNREACHABLE"))
    print("\nServices:")
    for service in api.get("/services"):
        print("  - %-40s %s" % (service["name"], service["uuid"]))
    print("\nRemembered:" if state() else "", json.dumps(state()) if state() else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("template", nargs="?", help="template folder or a path to a compose file")
    parser.add_argument("--create", action="store_true", help="create a new service (default when nothing else is given)")
    parser.add_argument("--update", action="store_true", help="push the compose to an existing service")
    parser.add_argument("--deploy", action="store_true", help="do not ask, deploy immediately")
    parser.add_argument("--yes", action="store_true", help="skip the confirmations (for scripts)")
    parser.add_argument("--list", action="store_true", help="list templates, projects, servers and services")
    parser.add_argument("--name", help="service name to use when creating")
    parser.add_argument("--project", help="project name or uuid")
    parser.add_argument("--environment", help="environment name")
    parser.add_argument("--server", help="server name, ip or uuid")
    parser.add_argument("--destination", help="destination name or uuid")
    parser.add_argument("--service", help="existing service name or uuid (update)")
    args = parser.parse_args()

    load_env_file()
    url, key = credentials()
    api = Coolify(url, key)

    if args.list:
        listing(api)
        return

    name, compose_path = resolve_template(args.template)
    if args.update:
        uuid = update(api, name, compose_path, args)
    else:
        uuid = create(api, name, compose_path, args)
    remember(name, uuid)


if __name__ == "__main__":
    main()
