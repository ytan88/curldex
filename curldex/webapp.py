# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""WebApp: one web service's REST API, read from its config/api.yaml.

    import curldex
    cal = curldex.load_app("google_calendar")
    cal.login()                                    # once: connect to the account
    cal.apis()                                     # the APIs
    cal.help("list-events")                        # one API's params and an example curl command
    cmd = cal.curl("list-events", maxResults=5)    # the curl command; in IPython: !{cmd}
    cal.run("list-events", maxResults=5)           # or run it from Python: the JSON

An app that needs code (helpers built on run(), an unusual login, another description format) is a
package whose class subclasses WebApp, setting DIRECTORY and, as needed, AUTH and IMPORTER. An app
without code is just its config/ folder; load_app(name), at the end, finds either. Nothing here names
a service.
"""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import quote, urlencode

from . import apps
from . import discover as _discover
from . import docs as _docs
from . import verify as _verify
from .auth import ApiKeyAuth, Auth, BasicAuth, BearerAuth, CurlAuth, NoAuth
from .config import (
    SECRET_NAME,
    UNSET,
    ConfigError,
    Credentials,
    CurldexError,
    UsageError,
    expand,
    load_yaml,
    mapping,
    placeholders,
    short,
    texts,
    write_private,
)
from .curl import Curl
from .discover import Importer, OpenAPIImporter
from .endpoint import (
    BODY_METHODS,
    METHODS,
    STAND_IN,
    Access,
    Endpoint,
    Request,
    check_header_name,
    has_control,
    query_value,
    scalar,
)
from .oauth2 import OAuth2Auth, OAuth2ClientAuth

if TYPE_CHECKING:
    from .verify import Verified

AUTH_TYPES: dict[str, type[Auth]] = {
    cls.KIND: cls for cls in (NoAuth, BearerAuth, BasicAuth, ApiKeyAuth, CurlAuth, OAuth2Auth, OAuth2ClientAuth)}
TOP_KEYS = {"description", "base_url", "auth", "headers", "curl_options", "source", "commands"}
SOURCE_KEYS = {"url", "include", "exclude"}  # where the API is described, and which of its APIs to keep
# The options curl_options may add to every call, and whether each takes a value (the next item). Others
# could send the credentials to another host or write files (-L, which takes custom headers and cookies
# along on a redirect; --url, -o, -K...).
CURL_OPTIONS = {
    "--compressed": False, "--http1.1": False, "--http2": False,
    "--cacert": True, "--capath": True, "-k": False, "--insecure": False, "-m": True, "--max-time": True,
    "--connect-timeout": True, "--retry": True, "--retry-delay": True, "--retry-max-time": True, "-A": True,
    "--user-agent": True, "-4": False, "--ipv4": False, "-6": False, "--ipv6": False, "--tlsv1.2": False,
    "--tlsv1.3": False, "--path-as-is": False}
COMMANDS = ("auth", "apps", "docs", "login", "discover", "verify", "check")  # the curldex command's words
RESERVED = (*COMMANDS, "help")  # words that can't be app names


class WebApp:
    """A web service's REST API (config/api.yaml) and credentials (config/credentials.yaml)."""

    DIRECTORY: ClassVar[Path | None] = None  # the app's folder, which holds config/
    AUTH: ClassVar[type[Auth] | None] = None  # default: the type named in api.yaml's `auth:`
    IMPORTER: ClassVar[type[Importer]] = OpenAPIImporter  # the format of the description `source:` names

    def __init__(self, directory: Path | str | None = None):
        directory = directory or self.DIRECTORY
        if directory is None:
            raise ConfigError(f"{type(self).__name__}: set DIRECTORY, or pass the app's folder")
        self.dir = Path(directory)
        self.name = self.dir.name
        self.config_dir = self.dir / "config"
        self.api_file = self.config_dir / "api.yaml"
        self.verified_file = self.config_dir / "verified.yaml"
        self.docs_file = self.dir / "API.md"
        self.credentials = Credentials(self.config_dir, self.name)
        # a link in a shared app could make curldex show (in an error) or overwrite a file elsewhere, ~/.bashrc say
        files = (self.api_file, self.verified_file, self.docs_file, self.config_dir / "credentials.example.yaml")
        if links := [f.name for f in files if f.is_symlink()]:
            raise ConfigError(f"{self.name}: {', '.join(links)} is a symbolic link; an app's files must be its own")
        self.reload()

    def reload(self) -> None:
        """Read config/api.yaml (again, after discover() changed it)."""
        config = load_yaml(self.api_file, required=False)
        if unknown := sorted(set(config) - TOP_KEYS):
            raise ConfigError(f"{self.name}: unknown settings in api.yaml: {', '.join(unknown)} "
                              f"(use {', '.join(sorted(TOP_KEYS))})")
        where = f"{self.name}: api.yaml's"
        self.description = " ".join(str(config.get("description") or "").split())
        self.base_url = str(config.get("base_url") or "").rstrip("/")  # may hold ${key}, e.g. a subdomain
        # A literal base_url must be an http(s) URL, so a hostile api.yaml can't set it to a curl option
        # such as `-K<file>`; one that starts with a ${key} (from the user's credentials.yaml) is checked once
        # filled in, in _curl.
        if self.base_url and not self.base_url.startswith("${") and not re.match(r"https?://", self.base_url):
            raise ConfigError(f"{where} base_url must start with http:// or https:// (got {short(self.base_url, 60)})")
        if "?" in self.base_url or "#" in self.base_url:  # else "base?x" + "/path?query" gives a broken double-?
            raise ConfigError(f"{where} base_url can't contain '?' or '#'; put query in the APIs")
        headers = mapping(config.get("headers"), f"{where} headers", "{Accept: application/json}")
        self.headers = {str(k): str(v) for k, v in headers.items()}  # a value may hold ${key} too
        for k, v in self.headers.items():
            check_header_name(k, f"{where} headers")
            if has_control(v):
                raise ConfigError(f"{where} header {k} has a control character in its value")
        self.curl_options = curl_options(texts(config.get("curl_options"), f"{where} curl_options", "[--compressed]"),
                                         f"{where} curl_options")
        self.source = dict(mapping(config.get("source"), f"{where} source", "{url: https://example.com/openapi.json}"))
        if unknown := sorted(set(self.source) - SOURCE_KEYS):
            raise ConfigError(f"{self.name}: unknown settings in api.yaml's source: {', '.join(unknown)} "
                              f"(use {', '.join(sorted(SOURCE_KEYS))})")
        for key in ("include", "exclude"):  # globs of API names
            if key in self.source:
                self.source[key] = texts(self.source[key], f"{where} source.{key}", "[events-*]")
        commands = config.get("commands") or {}
        if not isinstance(commands, dict):
            raise ConfigError(f"{where} commands must map API names to their settings")
        if bad := [n for n in commands if not isinstance(n, str)]:
            raise ConfigError(f"{self.name}: API name {bad[0]!r} isn't text; put it in quotes "
                              "(1e3 and 0o7, say, are otherwise read as numbers)")
        try:
            self.endpoints = {n: Endpoint.from_config(n, s or {}) for n, s in commands.items()}
        except ConfigError as e:
            raise ConfigError(f"{self.name}: {e}") from None
        settings = dict(mapping(config.get("auth"), f"{where} auth", "{type: bearer}"))
        kind = settings.pop("type", "none")
        auth = self.AUTH or AUTH_TYPES.get(str(kind))
        if auth is None:
            raise ConfigError(f"{self.name}: unknown auth type {kind!r} (built in: {', '.join(AUTH_TYPES)})")
        self.auth = auth(settings, self.credentials)
        # a credential outside the auth would appear in every curl command: the auth's own keys, or a key
        # named like one, or any key in a header or param named like one (Authorization: Bearer ${t}, say)
        used = set(self.used_credentials())
        named = {k for k in used if SECRET_NAME.search(k)} | placeholders(
            [v for k, v in self.headers.items() if SECRET_NAME.search(k)]
            + [p.default for e in self.endpoints.values() for p in e.params.values() if SECRET_NAME.search(p.name)])
        if leaked := sorted(({*self.auth.keys, *self.auth.TOKENS} & used) | named):
            raise ConfigError(f"{self.name}: api.yaml's base_url, headers or a param default uses {', '.join(leaked)}, "
                              "a credential, so it would appear in curl commands; only the auth may use it")

    def __repr__(self) -> str:
        return (f"<{type(self).__name__} {self.name}: {len(self.endpoints)} APIs, {self.status()}; "
                ".apis() lists them, .help(api) explains one>")

    # ----- the APIs and their documentation ------------------------------------------------

    def endpoint(self, api: str) -> Endpoint:
        found = self.endpoints.get(api) or self.endpoints.get(api.replace("_", "-"))
        if found is None:
            if not self.api_file.exists():
                raise ConfigError(self._no_config())
            raise UsageError(f"{self.name}: no API {api!r} (has: {', '.join(self.endpoints)})")
        return found

    def apis(self) -> None:
        """Print the APIs: name, access level, method, path and what each does."""
        print(_docs.index(self))

    def help(self, api: str | None = None) -> None:
        """Print an API's params and example curl command (every API's if none given)."""
        print(_docs.help_text(self, api))

    def docs(self) -> str:
        """The whole API reference as Markdown: what `curldex docs <app>` writes to API.md."""
        return _docs.reference(self)

    def write_docs(self) -> Path:
        if not self.api_file.exists():
            raise ConfigError(self._no_config())
        self.docs_file.write_text(self.docs())
        return self.docs_file

    # ----- curl commands -----------------------------------------------------------------------

    def curl(self, api: str, /, **values: Any) -> Curl:
        """The curl command for one call of an API: defaults filled in, required params checked."""
        return self.curl_for(self.endpoint(api), values)

    def curl_for(self, endpoint: Endpoint, values: dict, lookup: Callable[[str], Any] | None = None) -> Curl:
        """curl() for an Endpoint. `lookup` gives each ${key}'s value: by default the user's, from
        credentials.yaml; shared docs use stand-ins such as <calendar_id> instead."""
        lookup = lookup or self.credentials.get
        return self._curl(endpoint.build(endpoint.values(values, lookup)), endpoint.destructive, lookup)

    def request(self, method: str, path: str, query: dict | None = None, body: Any = None) -> Curl:
        """A curl command for any path under base_url, with the app's credentials, for exploring
        an API: request("GET", "/users/me/settings")."""
        method = method.upper()
        if method not in METHODS:
            raise UsageError(f"{self.name}: method must be one of {', '.join(METHODS)}")
        if not path.startswith("/"):  # so the credentials only ever go to base_url's host
            raise UsageError(f"{self.name}: request() takes a path under {self.base_url}, starting with '/'")
        if any(seg in (".", "..") for seg in path.split("?", 1)[0].split("/")):  # curl would resolve these out
            raise UsageError(f"{self.name}: request() path can't contain a '.' or '..' segment")
        if "#" in path:  # curl would drop what follows, the query included
            raise UsageError(f"{self.name}: request() path can't contain '#'")
        if not self.base_url:
            raise ConfigError(self.status())
        if body is None and method in BODY_METHODS:
            body = {}
        req = Request(method, path, {k: query_value(v) for k, v in (query or {}).items() if v is not None}, {}, body)
        return self._curl(req, Access.for_method(method) is Access.DESTRUCTIVE, self.credentials.get)

    def run(self, api: str | Curl, /, yes: bool = False, **values: Any) -> Any:
        """Run an API (or a Curl) and return the JSON response; what service helpers build on.
        A destructive call asks first, unless yes=True."""
        curl = api if isinstance(api, Curl) else self.curl(api, **values)
        return curl.run(yes=yes)

    def _curl(self, req: Request, destructive: bool, lookup: Callable[[str], Any]) -> Curl:
        url = self._filled(self.base_url, lookup, "base_url").rstrip("/") + req.path  # a ${key} value may end in /
        if self.base_url and not (re.match(r"https?://", url) or STAND_IN.match(url)):  # a ${key} gave a curl option?
            raise ConfigError(f"{self.name}: base_url must give an http:// or https:// URL (base_url={self.base_url})")
        if req.query:  # after a fixed query the path may hold; ":/@," left readable (times, emails)
            url += ("&" if "?" in url else "?") + urlencode(req.query, doseq=True, safe=":/@,", quote_via=quote)
        headers = {name: self._filled(value, lookup, f"header {name}") for name, value in self.headers.items()}
        merged = merge_headers(headers, req.headers)  # a param header wins over an app header of the same name
        if req.format in ("form", "multipart"):  # curl sets Content-Type itself (with the multipart boundary)
            merged = {n: v for n, v in merged.items() if n.lower() != "content-type"}
        app = self if self.auth.plain() else None  # an auth that sends nothing needs no -K
        return Curl(req.method, url, merged, req.body, app, destructive, req.format, self.curl_options)

    def _filled(self, value: str, lookup: Callable[[str], Any], what: str) -> str:
        """base_url or a header, with its ${key} values filled in; each one must be set."""
        filled = expand(value, lookup, f"{self.name}: {what}")
        if filled in UNSET and value:  # the value is one ${key}, and it isn't set
            raise ConfigError(f"{self.name}: {what}: {value} is not set")
        if has_control(str(filled)):  # a line break from credentials.yaml would inject a header
            raise ConfigError(f"{self.name}: {what} has a control character in it")
        return scalar(filled) if filled is not None else ""  # 0 and false as written, not empty

    # ----- login and credentials ------------------------------------------------------------

    def status(self) -> str:
        """'ready', or what is missing before calls can work (a credential, or a login)."""
        if not self.api_file.exists():
            return self._no_config()
        if not self.base_url:
            return f"{self.name}: api.yaml has no base_url"
        try:
            if missing := self.credentials.missing(*sorted(placeholders([self.base_url, self.headers]))):
                return missing
            return self.auth.status()
        except CurldexError as e:
            return str(e)

    def login(self, open_browser: bool = True) -> None:
        """Connect to the account (for auth types that need a login)."""
        self.auth.login(open_browser)

    def used_credentials(self) -> list[str]:
        """The credentials.yaml keys that base_url, headers and API defaults refer to as ${key}, such
        as calendar_id. (The auth's own keys are in self.auth.keys.)"""
        defaults = (placeholders(p.default) for e in self.endpoints.values() for p in e.params.values())
        return sorted(set().union(placeholders(self.base_url), placeholders(self.headers), *defaults))

    def auth_file(self) -> Path:
        """Refresh the login if needed and write curl's credentials file (owner-only); its path."""
        path = self.config_dir / ".curlrc"
        lines = "".join(f"{line}\n" for line in self.auth.curl_config())
        write_private(path, "# Written by `curldex auth`; it holds secrets. curl reads it with -K.\n" + lines)
        return path

    def _no_config(self) -> str:
        return (f"{self.name}: no config/api.yaml yet; write it (see the skill curldex-new-app), then "
                f"`curldex discover {self.name}` lists the APIs its source: describes")

    # ----- finding and verifying APIs ----------------------------------------------------------

    def discover(self, add: tuple[str, ...] | list[str] = ()) -> list[str]:
        """List the APIs in the published description (api.yaml's `source:`) that api.yaml lacks, and
        append those matching `add` (names or globs; '*' for all)."""
        return _discover.discover(self, tuple(add))

    def verify(self, api: str | None = None, /, yes: bool = False, **values: Any) -> list[Verified]:
        """Run the examples, and record that they work: the read APIs', or one API's by name; a write or
        destructive one only by name, with yes=True. Values replace the example's."""
        return _verify.verify(self, api, yes, values)


def merge_headers(base: dict, extra: dict) -> dict:
    """base with extra laid over it, matched case-insensitively (HTTP header names are
    case-insensitive), so a param's `Content-Type` replaces an app `content-type` rather than being
    sent twice."""
    names = {name.lower() for name in extra}
    return {**{n: v for n, v in base.items() if n.lower() not in names}, **extra}


def curl_options(words: list[str], where: str) -> list[str]:
    """api.yaml's curl_options, once each is checked to be one of CURL_OPTIONS, with its value if it takes one."""
    rest = iter(words)
    for word in rest:
        if word not in CURL_OPTIONS:
            raise ConfigError(f"{where} can't hold {word}, which could send the credentials elsewhere or write files; "
                              f"it may hold {', '.join(CURL_OPTIONS)}")
        if CURL_OPTIONS[word] and next(rest, None) is None:
            raise ConfigError(f"{where}: {word} needs a value after it")
    return words


# ----- finding apps --------------------------------------------------------------------------------

def app_dirs() -> list[Path]:
    """Where apps live: the folders in $CURLDEX_APPS (separated by os.pathsep), then curldex/apps/."""
    extra = [p for p in os.environ.get("CURLDEX_APPS", "").split(os.pathsep) if p]
    return [Path(p) for p in dict.fromkeys([*extra, *apps.__path__])]


def app_folder(name: str) -> Path | None:
    if not name.isidentifier() or name in RESERVED:
        return None
    return next((d / name for d in app_dirs() if (d / name / "config").is_dir()), None)


def app_names() -> list[str]:
    """The apps: folders with a config/ folder."""
    return sorted({f.name for d in app_dirs() if d.is_dir() for f in d.iterdir()
                   if f.name.isidentifier() and f.name not in RESERVED and (f / "config").is_dir()})


def load_app(name: str) -> WebApp:
    """An app by name: its package's `App` class, or a plain WebApp for a folder with only config/."""
    folder = app_folder(name)
    if folder is None:
        raise UsageError(f"no app {name!r} (apps: {', '.join(app_names()) or 'none yet'})")
    if not (folder / "__init__.py").exists():
        return WebApp(folder)
    if str(folder.parent) not in apps.__path__:  # an app from $CURLDEX_APPS
        apps.__path__.insert(0, str(folder.parent))  # so `import curldex.apps.<name>` finds it
    module = importlib.import_module(f"{apps.__name__}.{name}")
    if not (isinstance(getattr(module, "App", None), type) and issubclass(module.App, WebApp)):
        raise UsageError(f"{name} isn't an app: {folder}/__init__.py sets no App (its WebApp class)")
    return module.App()
