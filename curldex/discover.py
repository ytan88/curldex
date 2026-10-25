# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""From a published API description to api.yaml.

An Importer turns one description format (OpenAPI, Google API Discovery, ...) into api.yaml content;
OpenAPIImporter, below, reads OpenAPI 3, the vendor-neutral format and WebApp.IMPORTER's default.
found() reads the description api.yaml's `source:` names, keeping the APIs the app wants.
discover() lists the APIs api.yaml lacks and appends the ones chosen, keeping the file's names and
comments. It never writes base_url or auth, which the app's author checks: it prints what the
description suggests for them.
"""

from __future__ import annotations

import fnmatch
import json
import re
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import unquote, urljoin, urlsplit

import yaml

from .config import SECRET_NAME, ConfigError, HttpError, UsageError, dump_yaml, error_line, load_yaml, parse_yaml, short
from .curl import Curl
from .endpoint import METHODS, TYPES, Access, Endpoint

if TYPE_CHECKING:
    from .webapp import WebApp


class Importer(ABC):
    """Turns one API description format into api.yaml content. The helpers are for build()."""

    FORMAT: ClassVar[str] = ""  # its name, as API.md shows it: openapi, discovery, ...

    @abstractmethod
    def build(self, document: dict, url: str) -> dict:
        """api.yaml's content for a description: description, base_url, auth and commands. An API
        may list the OAuth scopes that allow it under "_scopes", each one an alternative ("a b" if
        it takes both); an app whose auth.scopes are set keeps only the APIs they allow."""

    @staticmethod
    def name(text: str) -> str:
        """An API name: lowercase words joined by '-' (eventsList -> events-list)."""
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", text)
        return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()

    @staticmethod
    def summary(text: str, limit: int = 160) -> str:
        """A description's first sentence, on one line, from a (possibly hostile) published description;
        only its first paragraph counts.
        A sentence ends before a capital letter, and not at "e.g." or "i.e." ("Moves an event, i.e.
        changes its organizer."). Control characters (see no_controls) and simple HTML tags are removed;
        a leading "Required."/"Optional." label (Google's style) is dropped."""
        text = " ".join(re.split(r"\n\s*\n", str(text), maxsplit=1)[0].split())
        text = re.sub(r"</?(?:p|code|a|em|strong|b|i|br)\b[^>]*>", "", no_controls(text))
        text = re.sub(r"^(Required|Optional|Output only|Input only|Immutable|Deprecated)\.\s*", "", text)
        return short(re.split(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+(?=[A-Z])", text, maxsplit=1)[0], limit)

    @staticmethod
    def param(location: str, kind: Any, required: bool, description: str = "", enum: list | None = None,
              default: Any = None) -> dict:
        """A param entry. Only a required param keeps its default (Gmail's userId=me), so a caller
        can leave it out; an optional one is simply not sent."""
        entry: dict[str, Any] = {"in": location}
        if kind in TYPES and kind != "string":
            entry["type"] = kind
        if required:
            entry["required"] = True
            if default is not None and "${" not in str(default):  # a description can't name credentials
                entry["default"] = default
        if description:
            entry["description"] = Importer.summary(description)
        if enum:
            entry["enum"] = list(enum)
        return entry

    @staticmethod
    def body(schema_name: str, fields: list[str], required: bool, kind: Any = "object") -> dict:
        """The `in: json` param that holds a whole request body: an object, described by its fields, or
        another JSON type (an array, say)."""
        kind = kind if kind in ("array", "string", "integer", "number", "boolean") else "object"
        listed = ", ".join(fields[:20]) + (", ..." if len(fields) > 20 else "")
        entry: dict[str, Any] = {"in": "json", "type": kind,
                                 "description": f"{schema_name or 'JSON'} {kind}" + (f": {listed}" if fields else "")}
        if required:
            entry["required"] = True
        return entry

    @staticmethod
    def add(apis: dict, name: str, api: dict) -> str:
        """Add an API under a unique name, which is returned, with its access level guessed from
        its name where the method alone would get it wrong. A path param that its path doesn't name
        (a mistake in the description) is left out, as api.yaml wouldn't load with it. Every text in it
        loses its control characters (see no_controls), names and values included."""
        api = clean(api)
        unique, n = name, 2
        while unique in apis:
            unique, n = f"{name}-{n}", n + 1
        if (access := Access.guess(api["method"], unique)) is not Access.for_method(api["method"]):
            api["access"] = access.value
        api["params"] = {param: spec for param, spec in (api.get("params") or {}).items()
                         if spec.get("in") != "path" or "{" + param + "}" in api["path"]}
        apis[unique] = api
        return unique


def found(app: WebApp) -> dict:
    """The app's published description (api.yaml's `source:`), read by its IMPORTER: api.yaml content
    with the APIs that source's include and exclude globs keep, and that auth.scopes (if set) allow."""
    url = app.source.get("url")
    if not url:
        raise ConfigError(f"{app.name}: where is its API described? Set source: {{url: ...}} in api.yaml")
    if not re.match(r"https?://", url):  # a file, which must stay inside the app's config folder
        config_dir = app.config_dir.resolve()
        resolved = (config_dir / url).resolve()  # an absolute url drops config_dir; .. is resolved out
        inside = resolved == config_dir or config_dir in resolved.parents
        if str(url).startswith("~") or not inside:  # ~, an absolute path, or .. that escapes
            raise ConfigError(f"{app.name}: source.url must be a file inside the app's config folder, "
                              f"not {short(str(url), 60)}")
        url = str(resolved)
    try:  # a malformed description fails on a type it didn't expect (not ValueError: UsageError is one)
        content = app.IMPORTER().build(load_document(url), url)
    except (AttributeError, TypeError, KeyError, IndexError) as e:
        raise ConfigError(f"{url}: not a description curldex can read ({type(e).__name__}: {e})") from None
    include, exclude = app.source.get("include") or ["*"], app.source.get("exclude") or []

    def kept(name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, g) for g in include) and \
            not any(fnmatch.fnmatchcase(name, g) for g in exclude)
    apis = {n: api for n, api in sorted(content.get("commands", {}).items()) if kept(n)}
    if elsewhere := [n for n in content.pop("_elsewhere", []) if kept(n)]:
        print(f"left out, served from another host than base_url: {', '.join(elsewhere)}")
    # a path relative to the app's base_url when that already holds part of it (base_url .../api/v2, the
    # description's server https://pokeapi.co and its paths /api/v2/...)
    full, base = urlsplit(content.get("base_url") or "").path.rstrip("/"), urlsplit(app.base_url).path.rstrip("/")
    for api in apis.values():
        if base and (full + api["path"]).startswith(base + "/"):
            api["path"] = (full + api["path"])[len(base):]
    if not apis:
        raise UsageError(f"{app.name}: no APIs in {url} match source's include and exclude globs")
    scopes = app.auth.settings.get("scopes")
    if scopes and (uncovered := [n for n, api in apis.items() if not allows(scopes, set(api.get("_scopes") or ()))]):
        print(f"left out, as auth.scopes don't allow them: {', '.join(uncovered)}")
        apis = {n: api for n, api in apis.items() if n not in uncovered}
    return {**content, "commands": apis}


def load_document(url: str) -> dict:
    """An API description, as JSON or YAML, from a URL (fetched with curl) or a file."""
    if re.match(r"https?://", url):
        # a description is public, so -L (follow redirects) sends no credentials; raw.githubusercontent and
        # others redirect. Anything but 2xx is a failure, named, not a "not an API description" further down.
        status, text = Curl("GET", url, {"Accept": "application/json, application/yaml"}, options=["-L"]).fetch()
        if not 200 <= status < 300:
            raise HttpError(status, f"could not fetch {url} (HTTP {status})")
    else:
        path = Path(url).expanduser()
        if not path.is_file():
            raise ConfigError(f"no such file: {path}")
        text = path.read_text()
    if isinstance(text, str):
        try:
            text = json.loads(text) if text.lstrip().startswith("{") else parse_yaml(text)
        except (ValueError, RecursionError, yaml.YAMLError) as e:  # don't echo the file's contents (a secret?)
            raise ConfigError(f"{url}: neither JSON nor YAML{error_line(e)}") from None
    if not isinstance(text, dict):
        raise ConfigError(f"{url}: not an API description")
    return text


def allows(scopes: Iterable[str], need: set[str]) -> bool:
    """Whether these scopes allow an API that one of `need` allows (or that needs none)."""
    return not need or any(set(alternative.split()) <= set(scopes) for alternative in need)


# ----- OpenAPI 3 -----------------------------------------------------------------------------------

OPENAPI_METHODS = tuple(method.lower() for method in METHODS)  # how an OpenAPI path item names them
# Param names that carry a credential, matched exactly: webapp.SECRET_NAME matches by ending, which here
# would drop paging params such as pageToken
CREDENTIALS = {"authorization", "cookie", "set-cookie", "token", "api-key", "apikey", "x-api-key", "api-secret",
               "apisecret", "access-token", "accesstoken", "refresh-token", "client-secret", "clientsecret",
               "private-token", "privatetoken", "password", "secret"}


def is_credential(name: str, location: str = "query") -> bool:
    """Whether a param name looks like a credential, so it belongs in the auth, not in every command. A
    header goes by its name's ending too (X-Auth-Token): unlike a query's pageToken, it's never paging."""
    return name.lower().replace("_", "-") in CREDENTIALS or (location == "header" and bool(SECRET_NAME.search(name)))


def no_controls(text: str) -> str:
    """A string from a (possibly hostile) description with control characters removed, so an escape
    sequence can't reach the terminal, api.yaml or API.md, nor a line break a request header."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(text))


def clean(value: Any) -> Any:
    """A value from a description with no_controls() applied to every text in it, keys included."""
    if isinstance(value, dict):
        return {no_controls(k): clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return no_controls(value) if isinstance(value, str) else value


class OpenAPIImporter(Importer):
    FORMAT = "openapi"

    def build(self, document: dict, url: str) -> dict:
        if not str(document.get("openapi", "")).startswith("3"):
            raise ConfigError(f"{url}: not an OpenAPI 3 document. Convert Swagger 2.0 first (source url "
                              "https://converter.swagger.io/api/convert?url=<its URL>); a Google Discovery document "
                              "needs a GoogleApp class (curldex/shared/google.py)")
        schemes = (document.get("components") or {}).get("securitySchemes") or {}
        key_params = {(s.get("name", "").lower(), s.get("in"))  # an API key the service declares as a param
                      for s in (_resolve(document, v) for v in schemes.values()) if s.get("type") == "apiKey"}
        apis: dict[str, dict] = {}
        elsewhere = []  # the names of operations served from another host (GitHub's uploads)
        for path, item in (document.get("paths") or {}).items():
            shared = item.get("parameters", [])
            for method in OPENAPI_METHODS:
                op = item.get(method)
                if not op or op.get("deprecated"):
                    continue
                api_name = self.name(str(op.get("operationId") or "")) or self.name(f"{method}-{path}")
                if (op.get("servers") or item.get("servers") or document.get("servers")) != document.get("servers"):
                    elsewhere.append(api_name)  # name() keeps only [a-z0-9-]
                    continue
                params = {}
                for p in (_resolve(document, p) for p in shared + op.get("parameters", [])):
                    name, loc = p.get("name", ""), p.get("in")
                    if p.get("deprecated"):
                        continue
                    # leave out params that carry a credential: only the auth may, and it reaches curl via -K.
                    # a path param can't be a credential (it's part of the URL), so keep it (e.g. Stripe {token})
                    if loc in ("path", "query", "header") and (loc == "path" or not is_credential(name, loc)) \
                            and (name.lower(), loc) not in key_params:
                        schema = _resolve(document, p.get("schema", {}))
                        params[name] = self.param(loc, _type(schema), p.get("required", False),
                                                  p.get("description", ""), schema.get("enum"), schema.get("default"))
                api = {"method": method.upper(), "path": path,
                       "description": self.summary(op.get("summary") or op.get("description", ""))}
                body_format, body, content_type = _body(document, op.get("requestBody"), params)
                if body_format != "json":
                    api["format"] = body_format
                if content_type:
                    api["content_type"] = content_type
                self.add(apis, api_name, {**api, "params": {**params, **body}, "_scopes": _scopes(document, op)})
        info = document.get("info", {})
        return {"description": self.summary(info.get("description") or info.get("title", "")),
                "base_url": no_controls(_server(document, url)).rstrip("/"), "auth": _auth(document),
                "commands": apis, "_elsewhere": elsewhere}


def _resolve(document: dict, node: Any) -> Any:
    """Follow a local $ref (#/...), a JSON Pointer in a URI fragment (RFC 6901): ~1 is /, ~0 is ~, and
    %XX is percent-decoded; a numeric token indexes into a list. A $ref cycle ends the walk."""
    seen: set[str] = set()
    while isinstance(node, dict) and str(node.get("$ref", "")).startswith("#/"):
        ref = node["$ref"]
        if ref in seen:
            return {}  # a $ref that refers back to itself
        seen.add(ref)
        target: Any = document
        for raw in ref[2:].split("/"):
            part = unquote(raw).replace("~1", "/").replace("~0", "~")
            if isinstance(target, list):
                target = target[int(part)] if part.isdigit() and int(part) < len(target) else {}
            else:
                target = target.get(part, {}) if isinstance(target, dict) else {}
        node = target
    return node


def _is_binary(schema: dict) -> bool:
    """Whether a schema describes binary content to upload as a file: OpenAPI 3.0's `format: binary`
    or 3.1's `contentEncoding: binary` (3.1 dropped `format: binary`)."""
    return schema.get("format") == "binary" or schema.get("contentEncoding") == "binary"


def _type(schema: dict) -> Any:
    """A schema's type; OpenAPI 3.1 may list several, such as ["string", "null"]; without one, the first
    alternative's in anyOf or oneOf (Stripe's `anyOf: [object, ""]`)."""
    kind = schema.get("type")
    if kind is None:
        kind = next((t for s in schema.get("anyOf") or schema.get("oneOf") or [] if isinstance(s, dict)
                     and (t := _type(s)) not in (None, "null")), None)
    return next((k for k in kind if k != "null"), None) if isinstance(kind, list) else kind


def _body(document: dict, request_body: Any, taken: dict) -> tuple[str, dict, str]:
    """The body's format, its params, and (for a raw body) its content type. A JSON body becomes one
    `in: json` param; a form or multipart body a param per field (binary fields are files); any other
    media type (octet-stream, text, XML) a raw body, one `in: json` param (a file when it's binary).
    The media type is matched without its parameters, so `application/json; charset=utf-8` still counts."""
    body = _resolve(document, request_body or {})
    content = body.get("content", {})
    base = {media: media.split(";", 1)[0].strip().lower() for media in content}  # media type without ;charset etc.
    required = body.get("required", False)
    if media := next((m for m, b in base.items() if b == "application/json" or b.endswith("+json")), None):
        schema = content[media].get("schema", {})
        resolved = _resolve(document, schema)
        # "request-body" when "body" is taken by a param, or by a field the curator may add as one (an issue's body)
        name = "body" if "body" not in taken and "body" not in (resolved.get("properties") or {}) else "request-body"
        return "json", {name: Importer.body(str(schema.get("$ref", "")).rsplit("/", 1)[-1],
                                            list(resolved.get("properties", {})), required, _type(resolved))}, ""
    for want, body_format in (("application/x-www-form-urlencoded", "form"), ("multipart/form-data", "multipart")):
        if media := next((m for m, b in base.items() if b == want), None):
            schema = _resolve(document, content[media].get("schema", {}))
            if not schema.get("properties"):  # an empty form (Stripe gives every GET one): no body
                return "json", {}, ""
            req = set(schema.get("required", []))
            params = {}
            for name, field in (schema.get("properties") or {}).items():
                field = _resolve(document, field)
                kind = "file" if _is_binary(field) else _type(field)
                params[name] = Importer.param("body", kind, name in req, field.get("description", ""),
                                              field.get("enum"))
            return body_format, params, ""
    if content:  # some other media type: send the body raw, as it is
        media = next(iter(content))
        schema = _resolve(document, content[media].get("schema", {}))
        kind = "file" if _is_binary(schema) else "string"
        name = "body" if "body" not in taken else "request-body"
        return "raw", {name: {"in": "json", "type": kind, **({"required": True} if required else {}),
                              "description": f"the request body ({base[media]})"}}, base[media]
    return "json", {}, ""


def _scopes(document: dict, op: dict) -> list[str]:
    """The OAuth scopes that allow an operation (its own `security`, else the document's). Each
    requirement is an alternative, and the scopes within one are all needed, so they're joined by
    spaces. If one alternative needs no scopes (an API key, say), the operation needs none."""
    requirements = op.get("security", document.get("security", []))
    if any(not any(requirement.values()) for requirement in requirements):
        return []
    return [" ".join(scope for scopes in requirement.values() for scope in scopes) for requirement in requirements]


def _server(document: dict, url: str) -> str:
    server = (document.get("servers") or [{"url": "/"}])[0]
    base = server.get("url", "/")
    for name, variable in (server.get("variables") or {}).items():
        base = base.replace("{" + name + "}", str(variable.get("default", "")))
    if re.match(r"https?://", base) or not re.match(r"https?://", url):
        return base  # from a file, a relative URL: discover() only suggests base_url, and the author fixes it
    return urljoin(url, base)


def _auth(document: dict) -> dict:
    """api.yaml's auth for the document's security schemes, preferring the ones it requires."""
    schemes = (document.get("components") or {}).get("securitySchemes") or {}
    required = [name for requirement in document.get("security", []) for name in requirement]
    for name in required + list(schemes):
        scheme = _resolve(document, schemes.get(name, {}))
        kind, http = scheme.get("type"), str(scheme.get("scheme", "")).lower()
        if kind == "http" and http in ("bearer", "basic"):
            return {"type": http}
        if kind == "apiKey" and scheme.get("in") in ("header", "query"):
            return {"type": "api_key", "in": scheme["in"], "name": scheme.get("name", "X-API-Key")}
        flows = (scheme.get("flows") or {}) if kind == "oauth2" else {}
        if flow := flows.get("authorizationCode"):  # a browser login, preferred when both are offered
            return {"type": "oauth2", "authorize_url": flow.get("authorizationUrl"), "token_url": flow.get("tokenUrl"),
                    "scopes": list(flow.get("scopes", {}))}
        if flow := flows.get("clientCredentials"):  # the app signs in as itself
            return {"type": "oauth2_client", "token_url": flow.get("tokenUrl"), "scopes": list(flow.get("scopes", {}))}
    return {"type": "none"}


# ----- adding APIs to api.yaml ---------------------------------------------------------------------

def discover(app: WebApp, add: tuple[str, ...] = ()) -> list[str]:
    """List the APIs in the description that api.yaml lacks, and append the ones matching `add`
    (names or globs; '*' for all). Returns the names missing, or added."""
    if not app.api_file.exists():
        raise ConfigError(app.status())
    content = found(app)
    config = load_yaml(app.api_file)
    suggested = {k: content[k] for k in ("description", "base_url", "auth") if content.get(k) and not config.get(k)}
    if suggested:
        print(f"api.yaml has no {', '.join(suggested)}. The description suggests (check, then paste into api.yaml):\n\n"
              + dump_yaml(suggested))
    missing = lacking(config.get("commands") or {}, content["commands"])
    if not add:
        limited = " (source.include limits the list)" if app.source.get("include") else ""
        print(f"{app.name}: {len(missing)} of {len(content['commands'])} APIs in the description aren't in "
              f"api.yaml{limited}")
        names = max(map(len, missing), default=0)
        paths = max((len(api["path"]) for api in missing.values()), default=0)
        for name, api in missing.items():  # in columns, so the paths and descriptions line up
            row = f"  {name:<{names}}  {api['method']:<6} {api['path']:<{paths}}  {api.get('description', '')}"
            print(row.rstrip())
        if missing:
            print(f"Add them with: curldex discover {app.name} --add NAME_OR_GLOB ...")
        return list(missing)
    chosen = {n: api for n, api in missing.items() if any(fnmatch.fnmatchcase(n, g) for g in add)}
    if chosen and (unmatched := [g for g in add if not any(fnmatch.fnmatchcase(n, g) for n in missing)]):
        print(f"no missing API matches {', '.join(unmatched)}")
    if not chosen:
        raise UsageError(f"{app.name}: no missing API matches {', '.join(add)}")
    append(app, {n: _public(api) for n, api in chosen.items()})
    print(f"{app.name}: added {', '.join(chosen)}")
    return list(chosen)


def lacking(current: dict, found: dict) -> dict:
    """The found APIs that api.yaml lacks, matched by name or by method and path, so an API that
    was renamed still counts. When several found APIs share a method and path (GET /v1/{name}
    serves spaces and messages alike), only the name counts."""
    shared = Counter(_route(api) for api in found.values())
    have = {_route(api) for api in current.values()}
    return {n: api for n, api in found.items()
            if n not in current and (shared[_route(api)] > 1 or _route(api) not in have)}


def append(app: WebApp, apis: dict) -> None:
    """Add APIs at the end of api.yaml's commands, keeping the file's comments. A file with no APIs
    yet gets a `commands:` line last. Nothing is written if an API wouldn't load, or if `commands:`
    isn't the file's last block."""
    for name, api in apis.items():  # so a bad description can't leave an api.yaml that no longer loads
        Endpoint.from_config(name, api)
    text = app.api_file.read_text()
    config = parse_yaml(text) or {}
    current = config.get("commands") or {}
    head = text.rstrip()
    if not current:  # replace a last `commands:` or `commands: {}`, or add one
        head, stub = re.subn(r"(?:^|\n)commands:[ \t]*(?:\{[ \t]*\})?\Z", "", head)
        if stub or "commands" not in config:
            head = f"{head.rstrip()}\n\ncommands:".lstrip()
    block = "".join(f"  {line}" if line.strip() else line for line in dump_yaml(apis).splitlines(True))
    updated = f"{head}\n{block}" if not current else f"{head}\n\n{block}"  # right under an empty `commands:`
    try:
        appended = (parse_yaml(updated) or {}).get("commands") == {**current, **apis}
    except yaml.YAMLError:
        appended = False
    if not appended:
        raise ConfigError(f"{app.name}: `commands:` isn't api.yaml's last block; move it to the end, then run "
                          "this again")
    app.api_file.write_text(updated)
    app.reload()
    app.write_docs()


def _route(api: dict) -> tuple[str, str]:
    """An API's method and path, with the path's param names blanked ({userId} ~ {user_id})."""
    path = re.sub(r"\{[^}]*\}", "{}", "/" + str(api.get("path", "")).strip("/"))
    return str(api.get("method", "GET")).upper(), path


def _public(api: dict) -> dict:
    """An API as api.yaml keeps it: without discover's own keys, and without empty settings."""
    return {k: v for k, v in api.items() if not k.startswith("_") and v not in ("", {}, [])}
