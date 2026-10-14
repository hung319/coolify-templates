#!/usr/bin/env python3
"""Create or update a Coolify service from a local docker-compose file.

Uses only the Coolify public API, so a template does not have to be merged into the
official catalog first.

Required environment variables:
  COOLIFY_URL              e.g. https://coolify.example.com
  COOLIFY_API_KEY          panel -> Keys & Tokens -> API tokens
  COOLIFY_PROJECT_UUID     create only (from the project URL)
  COOLIFY_SERVER_UUID      create only
  COOLIFY_DESTINATION_UUID create only
  COOLIFY_SERVICE_UUID     update only
Optional:
  COOLIFY_ENVIRONMENT      default: production
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request


def need(name):
    value = os.environ.get(name)
    if not value:
        sys.exit("missing required environment variable: %s" % name)
    return value


def call(method, url, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("compose", help="path to the docker-compose file")
    parser.add_argument("--create", action="store_true", help="create a new service")
    parser.add_argument("--update", action="store_true", help="update an existing service")
    parser.add_argument("--deploy", action="store_true", help="trigger a deployment (token needs the deploy or root ability)")
    parser.add_argument("--name", help="service name, create only")
    args = parser.parse_args()

    if args.create == args.update:
        sys.exit("pass exactly one of --create / --update")

    api = need("COOLIFY_URL").rstrip("/") + "/api/v1"
    token = need("COOLIFY_API_KEY")
    encoded = base64.b64encode(open(args.compose, "rb").read()).decode()

    if args.create:
        payload = {
            "name": args.name or "service",
            "docker_compose_raw": encoded,
            "project_uuid": need("COOLIFY_PROJECT_UUID"),
            "environment_name": os.environ.get("COOLIFY_ENVIRONMENT", "production"),
            "server_uuid": need("COOLIFY_SERVER_UUID"),
            "destination_uuid": need("COOLIFY_DESTINATION_UUID"),
            "instant_deploy": bool(args.deploy),
        }
        status, body = call("POST", api + "/services", token, payload)
    else:
        payload = {"docker_compose_raw": encoded, "instant_deploy": bool(args.deploy)}
        status, body = call("PATCH", api + "/services/" + need("COOLIFY_SERVICE_UUID"), token, payload)

    print(status, json.dumps(body)[:600])
    sys.exit(0 if status < 300 else 1)


if __name__ == "__main__":
    main()
