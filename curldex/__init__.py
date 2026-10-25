# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""curldex: a Python toolbox that finds, documents and runs the right curl command for any web API.

    import curldex
    cal = curldex.load_app("google_calendar")
    cal.apis()                                       # the APIs
    cal.help("list-events")                          # params and an example curl command
    cmd = cal.curl("list-events", maxResults=5)      # in IPython: !{cmd}

The modules here are the core: the common interface every app implements, and the mechanism. They
name no service. Each web service is a folder in apps/ with config/api.yaml; one that needs code is a
package whose class subclasses WebApp. Code a family of apps shares is in shared/.

A call:
    config.py     the errors (CurldexError, UsageError, ConfigError, HttpError); YAML, ${...}
                  placeholders, Credentials
    endpoint.py   Param, Endpoint: the APIs in api.yaml; Access
    curl.py       Curl: one call as a curl command
    webapp.py     WebApp, which every app subclasses; load_app(name)
Credentials to curl:
    auth.py       Auth; CurlAuth (curl config lines) and its presets none, bearer, basic, api_key
    oauth2.py     OAuth2Auth (browser login), OAuth2ClientAuth (client credentials)
Docs and checks:
    docs.py       apis(), help() and API.md
    verify.py     each API's example, and verifying it against the service
    check.py      `curldex check`: what's wrong with an app package, without calling the service
From a published description to api.yaml:
    discover.py   Importer, OpenAPIImporter; listing the APIs api.yaml lacks, and adding them
The command:
    cli.py        `curldex`
"""

from importlib.metadata import PackageNotFoundError, version

from .auth import ApiKeyAuth, Auth, BasicAuth, BearerAuth, CurlAuth, NoAuth
from .config import ConfigError, CurldexError, HttpError, UsageError
from .curl import Curl
from .discover import Importer, OpenAPIImporter
from .endpoint import Access, Endpoint, Param
from .oauth2 import OAuth2Auth, OAuth2ClientAuth
from .verify import Verified
from .webapp import WebApp, load_app

try:
    __version__ = version("curldex")
except PackageNotFoundError:  # a source tree that isn't installed
    __version__ = "unknown"

__all__ = [
    "Access", "ApiKeyAuth", "Auth", "BasicAuth", "BearerAuth", "ConfigError", "Curl", "CurlAuth", "CurldexError",
    "Endpoint", "HttpError", "Importer", "NoAuth", "OAuth2Auth", "OAuth2ClientAuth", "OpenAPIImporter", "Param",
    "UsageError", "Verified", "WebApp", "load_app",
]
