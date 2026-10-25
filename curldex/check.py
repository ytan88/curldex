# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""`curldex check <app>`: the problems in an app package that show without calling the service. For
whoever writes or edits a package, a person or Claude: run it after every change. (Loading the app
already rejects malformed or misspelled settings in api.yaml.)"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .config import SECRET_NAME, CurldexError, load_yaml, placeholders
from .endpoint import Access
from .verify import example_curl, load_verified

if TYPE_CHECKING:
    from .webapp import WebApp

API_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # lowercase words joined by '-'


def check(app: WebApp) -> list[str]:
    """The problems found, each starting with "error:" (it won't work as meant) or "warning:"."""
    problems: list[str] = []

    def error(text: str) -> None:
        problems.append(f"error: {text}")

    def warning(text: str) -> None:
        problems.append(f"warning: {text}")

    if not app.api_file.exists():
        error(app._no_config())
        return problems
    if not app.base_url:
        error("api.yaml has no base_url")
    elif not app.base_url.startswith(("https://", "${")) and app.auth.plain():
        warning(f"base_url {app.base_url} isn't https, so credentials would cross the network unencrypted")
    if sources := sorted(placeholders(load_yaml(app.api_file), sources=True)):
        listed = ", ".join(f"${{{s}}}" for s in sources)
        error(f"api.yaml uses {listed}: only credentials.yaml may read the environment or "
              "files; put the value there and use ${key}")
    # a literal value where a credential goes would be committed and in every command (a ${key} for one doesn't
    # load); in check, not at load, so that a description's literal default can't make api.yaml unloadable
    if named := [k for k in app.headers if SECRET_NAME.search(k)] + [
            f"{e.name}.{p.name}" for e in app.endpoints.values() for p in e.params.values()
            if p.default is not None and SECRET_NAME.search(p.name)]:
        error(f"api.yaml gives {', '.join(named)} a value, but each is named like a credential, which goes in the auth")
    secrets = set(app.auth.keys)
    for setting in ("token_url", "authorize_url"):  # the client secret is POSTed to these
        if setting in app.auth.SETTING_KEYS and not app.auth.settings.get(setting):
            error(f"auth.{setting} isn't set in api.yaml")
        elif str(app.auth.settings.get(setting) or "").startswith("http://"):
            warning(f"auth.{setting} isn't https, so the OAuth credentials would cross the network unencrypted")
    if {"-k", "--insecure"} & set(app.curl_options):
        warning("curl_options has -k, so curl doesn't check the service's certificate, and anyone on the way could "
                "read the credentials")
    template = app.config_dir / "credentials.example.yaml"
    if keys := [*secrets, *app.used_credentials()]:
        if not template.exists():
            error("no config/credentials.example.yaml, the template users copy to credentials.yaml")
        else:
            values = load_yaml(template)
            if missing := [k for k in keys if k not in values]:
                error(f"credentials.example.yaml lacks {', '.join(missing)}")
            if filled := [k for k in secrets if values.get(k) and not str(values[k]).startswith("${")]:
                warning(f"credentials.example.yaml, which is committed, has a value for {', '.join(filled)}: "
                        "real values go in credentials.yaml")
    if app.credentials.path.exists() and app.credentials.path.stat().st_mode & 0o077:  # its mode, not its content
        warning(f"{app.credentials.path} can be read by others: chmod 600 it")

    for e in app.endpoints.values():
        if not API_NAME.fullmatch(e.name):
            error(f"{e.name}: an API name is lowercase words joined by '-'")
        if not e.description:
            warning(f"{e.name}: no description")
        if e.example is None and any(p.mandatory or p.location == "json" for p in e.params.values()):
            warning(f"{e.name}: no example: (help, API.md and verify use it)")
        if e.method == "POST" and e.access is Access.WRITE:
            guess = Access.guess(e.method, e.name)
            if guess is Access.DESTRUCTIVE:
                warning(f"{e.name}: named like a delete; if it deletes data, set access: destructive")
            elif guess is Access.READ:
                warning(f"{e.name}: named like a read; if it only reads, set access: read")
        if e.format == "raw" and not any(p.location == "json" for p in e.params.values()):
            error(f"{e.name}: format raw needs a param with in: json, the body to send")
        try:
            example_curl(app, e, shared=True)
        except CurldexError as err:
            error(f"{e.name}: its example doesn't fit: {err}")

    if not app.docs_file.exists() or app.docs_file.read_text() != app.docs():
        warning(f"API.md is missing or out of date: run `curldex docs {app.name}`")
    verified = load_verified(app)
    if todo := [n for n in app.endpoints if n not in verified or not verified[n].ok]:
        listed = ", ".join(todo[:6]) + (", ..." if len(todo) > 6 else "")
        warning(f"{len(todo)} of {len(app.endpoints)} APIs not verified against the service yet ({listed}); "
                f"run `curldex verify {app.name}`")
    return problems
