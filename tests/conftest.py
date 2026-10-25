# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: a fake web service (REST and OAuth endpoints) that real curl talks to, and apps
in a temporary folder."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from curldex import WebApp, apps
from curldex.cli import main


@dataclass
class FakeService:
    """Records every request. GET /events, /me, /fail, /drop (closes the connection unanswered),
    /auth (OAuth consent), documents; POST /token, /sa-token; DELETE /events/ID; any other POST,
    PATCH or PUT echoes its body and Content-Type."""

    url: str = ""
    requests: list[dict] = field(default_factory=list)
    pkce: dict = field(default_factory=dict)
    documents: dict = field(default_factory=dict)  # path -> a JSON document, or text

    @property
    def last(self) -> dict:
        return self.requests[-1]


def _handler(fake: FakeService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, data=None, headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if data is not None:
                self.wfile.write(data.encode() if isinstance(data, str) else json.dumps(data).encode())

        def _record(self) -> tuple[str, dict]:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            url = urlsplit(self.path)
            fake.requests.append({"method": self.command, "path": url.path, "url": self.path,
                                  "query": {k: v if len(v) > 1 else v[0] for k, v in parse_qs(url.query).items()},
                                  "headers": dict(self.headers), "body": body})
            return url.path, fake.last["query"]

        def do_GET(self):
            route, query = self._record()
            if route in fake.documents:
                return self._send(200, fake.documents[route])
            if route == "/auth":  # the consent page: approves at once and redirects back with a code
                fake.pkce.update(challenge=query["code_challenge"], redirect_uri=query["redirect_uri"])
                target = query["redirect_uri"] + "/?" + urlencode({"state": query["state"], "code": "CODE1"})
                return self._send(302, headers={"Location": target})
            if route == "/me":
                return self._send(200, {"id": "u1", "email": "me@example.com", "secretNote": "PRIVATE-DATA"})
            if route == "/events":
                return self._send(200, {"kind": "list", "items": [{"id": "e1", "summary": "PRIVATE-DATA"},
                                                                   {"id": "e2", "start": {"date": "2026-10-12"}}],
                                        "query": query})
            if route == "/fail":
                return self._send(500, {"error": "boom"})
            if route == "/drop":  # as a broken network would: curl gets no answer at all
                self.close_connection = True
                return None
            self._send(404, {"error": "not found"})

        def do_DELETE(self):
            self._record()
            self._send(204)

        def _echo(self):
            route, query = self._record()
            body = fake.last["body"]
            if route == "/sa-token":
                fake.last["form"] = {k: v[0] for k, v in parse_qs(body).items()}
                return self._send(200, {"access_token": "SA1", "expires_in": 3600})
            if route != "/token":
                kind = self.headers.get("Content-Type", "")
                return self._send(200, {"method": self.command, "content_type": kind, "query": query,
                                        "body": json.loads(body or "null") if "json" in kind else body})
            form = {k: v[0] for k, v in parse_qs(body).items()}
            client = self.headers.get("Authorization") == "Basic " + base64.b64encode(b"C:S").decode() \
                or (form.get("client_id"), form.get("client_secret")) == ("C", "S")
            if form.get("grant_type") == "client_credentials" and client:
                return self._send(200, {"access_token": "CC1", "expires_in": 3600})
            if form.get("grant_type") == "authorization_code":
                challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                if form["code"] == "CODE1" and challenge == fake.pkce.get("challenge") \
                        and form["redirect_uri"] == fake.pkce.get("redirect_uri") and client:
                    return self._send(200, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600,
                                            "scope": "calendar"})
            elif form.get("grant_type") == "refresh_token" and form["refresh_token"] == "RT1":
                return self._send(200, {"access_token": "AT2", "expires_in": 3600})
            self._send(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})

        do_POST = do_PATCH = do_PUT = _echo

    return Handler


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep the developer's own environment out of the tests: no CURLDEX_APPS (which would load their
    apps), and no proxy for loopback (a proxy would break, or hang, the curl and OAuth tests)."""
    for var in ("CURLDEX_APPS", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture
def fake():
    service = FakeService()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(service))
    service.url = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.005}, daemon=True).start()
    yield service
    server.shutdown()
    server.server_close()


API = """\
description: A test service
base_url: {url}
auth: {auth}
commands:
  me:
    description: Who the user is
    path: /me
  list-events:
    description: List events
    path: /events
    params:
      limit: {{type: integer, default: 10}}
      upcoming: {{type: boolean, default: true}}
      owner: {{default: "${{uid}}"}}
      tags: {{type: array}}
  create-event:
    method: POST
    path: /cal/{{cid}}/events
    params:
      cid: {{default: "${{uid}}"}}
      summary: {{required: true}}
      start: {{type: object}}
      tag: {{in: query}}
  delete-event:
    method: DELETE
    path: /events/{{id}}
  fail:
    path: /fail
"""


@pytest.fixture
def apps_dir(tmp_path: Path, monkeypatch) -> Path:
    """A folder for test apps, found by the CLI through $CURLDEX_APPS."""
    folder = tmp_path / "apps"
    folder.mkdir()
    monkeypatch.setenv("CURLDEX_APPS", str(folder))
    monkeypatch.setattr(apps, "__path__", list(apps.__path__))  # load_app() adds to it; undo that
    yield folder
    for name in [m for m in sys.modules if m.startswith("curldex.apps.") and m.count(".") == 2]:
        if str(folder) in str(getattr(sys.modules[name], "__file__", "")):
            del sys.modules[name]


@pytest.fixture
def make_app(apps_dir: Path, fake: FakeService) -> Callable[..., WebApp]:
    def make(name: str = "testapp", auth: str = "{type: none}", credentials: str = "uid: u1\n",
             api: str = API) -> WebApp:
        """apps/<name>/config/{api,credentials}.yaml, as a plain WebApp."""
        config = apps_dir / name / "config"
        config.mkdir(parents=True)
        (config / "api.yaml").write_text(api.format(url=fake.url, auth=auth))
        (config / "credentials.yaml").write_text(credentials)
        return WebApp(config.parent)
    return make


@pytest.fixture
def oauth(fake: FakeService) -> str:
    return f'{{type: oauth2, authorize_url: "{fake.url}/auth", token_url: "{fake.url}/token", scopes: [calendar]}}'


def openapi_document(url: str) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Pets", "description": "A pet store. With pets."},
        "servers": [{"url": "/v1"}],
        "security": [{"auth": ["pets.read"]}],
        "components": {
            "securitySchemes": {"auth": {"type": "oauth2", "flows": {"authorizationCode": {
                "authorizationUrl": f"{url}/auth", "tokenUrl": f"{url}/token",
                "scopes": {"pets.read": "", "pets.write": ""}}}}},
            "parameters": {"limit": {"name": "limit", "in": "query", "schema": {"type": "integer"}}},
            "schemas": {"Pet": {"properties": {"name": {}, "tag": {}}}},
        },
        "paths": {
            "/pets": {
                "get": {"operationId": "listPets", "summary": "List pets.",
                        "parameters": [{"$ref": "#/components/parameters/limit"}]},
                "post": {"operationId": "createPet", "security": [{"auth": ["pets.write"]}],
                         "requestBody": {"required": True, "content": {"application/json": {
                             "schema": {"$ref": "#/components/schemas/Pet"}}}}},
            },
            "/pets/{petId}": {
                "parameters": [{"name": "petId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "get": {"operationId": "showPet"},
                "delete": {"operationId": "deletePet", "security": [{"auth": ["pets.write"]}]},
            },
            "/pets:search": {"post": {"operationId": "searchPets"}},
            "/pets:batchDelete": {"post": {"operationId": "batchDeletePets"}},
            "/old": {"get": {"operationId": "old", "deprecated": True}},
        },
    }


@pytest.fixture
def pets(fake: FakeService) -> dict:
    """An OpenAPI document, served at /openapi.json."""
    fake.documents["/openapi.json"] = openapi_document(fake.url)
    return fake.documents["/openapi.json"]


@pytest.fixture
def cli(capsys) -> Callable[..., tuple[int, str, str]]:
    """Run the curldex command in this process: (exit code, stdout, stderr)."""
    def run(*args: str) -> tuple[int, str, str]:
        try:
            code = main(list(args))
        except SystemExit as e:  # argparse's usage errors
            code = e.code
        out, err = capsys.readouterr()
        return code, out, err
    return run
