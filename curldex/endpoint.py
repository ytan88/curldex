# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""The APIs in api.yaml: Param and Endpoint, and the Request an endpoint builds from param values."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import quote

from .config import UNSET, ConfigError, UsageError, expand, placeholders

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
BODY_METHODS = ("POST", "PUT", "PATCH")
LOCATIONS = ("path", "query", "header", "body", "json")  # body: a field of the body; json: the whole body
FORMATS = ("json", "form", "multipart", "raw")  # how the body is sent
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict,
         "array": (list, tuple), "file": (str, PurePath)}  # file: a local file's path; its content is sent
ENDPOINT_KEYS = {"description", "method", "path", "access", "params", "format", "content_type", "example"}
PARAM_KEYS = {"in", "type", "required", "default", "description", "enum", "reserved"}
STAND_IN = re.compile(r"<[\w.-]+>")  # an example value still to be filled in, such as <event_id>
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")  # an HTTP token: no ':', space, control or leading '@'
# Words in an API's name that say what a POST does (see Access.guess)
READS = re.compile(r"^(get|list|search|query|find|lookup|count|export)(-|$)|"
                   r"(^|-)(get|list|search|query|find|lookup|count|batch-get|export)$")
WRITES = re.compile(r"(^|-)(create|insert|add|update|patch|set|put|delete|remove|send|import|move|upload|"
                    r"write|modify|cancel|or)(-|$)")
DELETES = re.compile(r"(^|-)(delete|remove|clear|purge|destroy|wipe|empty|obliterate|revoke)(-|$)")


def check_header_name(name: str, what: str) -> None:
    """Refuse a header name that isn't an HTTP token: no ':', space or control character, and no leading
    '@', which would make curl read a file."""
    if not HEADER_NAME.fullmatch(name):
        raise ConfigError(f"{what}: a header name is letters, digits and !#$%&'*+.^_`|~-; got {name!r}")


def has_control(text: str) -> bool:
    """Whether text has a control character other than a tab: in a header, a line break would inject
    another header."""
    return any((ord(c) < 0x20 and c != "\t") or ord(c) == 0x7f for c in text)


class Access(str, Enum):
    """What a call may do to the user's data."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"

    @property
    def meaning(self) -> str:
        """What the level means, as the docs say it."""
        return {"read": "reads data", "write": "creates or changes data",
                "destructive": "deletes or replaces data"}[self.value]

    @classmethod
    def for_method(cls, method: str) -> Access:
        if method in ("PUT", "DELETE"):
            return cls.DESTRUCTIVE
        return cls.WRITE if method in BODY_METHODS else cls.READ

    @classmethod
    def guess(cls, method: str, name: str) -> Access:
        """The access level an API's method and name suggest. A POST named like a delete
        (batch-delete, calendars-clear) is destructive; one named like a read (freebusy-query,
        messages-search) only reads, unless the name also says it writes (get-or-create)."""
        if method == "POST":
            if DELETES.search(name):
                return cls.DESTRUCTIVE
            if READS.search(name) and not WRITES.search(name):
                return cls.READ
        return cls.for_method(method)


@dataclass
class Request:
    """One call's parts, before they become a URL and curl options."""

    method: str
    path: str
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    body: Any = None  # in the form below; a file param's value is a Path
    format: str = "json"


@dataclass(frozen=True)
class Param:
    name: str
    location: str  # one of LOCATIONS
    type: str = "string"
    required: bool = False
    default: Any = None  # may be "${key}": a value from credentials.yaml
    description: str = ""
    enum: tuple | None = None
    reserved: bool = False  # a path value that may contain "/" (e.g. name=spaces/AAA)

    @classmethod
    def from_config(cls, name: str, spec: dict, method: str, path: str) -> Param:
        if unknown := sorted(set(spec) - PARAM_KEYS):
            raise ConfigError(f"param '{name}': unknown settings {', '.join(unknown)} "
                              f"(use {', '.join(sorted(PARAM_KEYS))}; inside {{...}}, a comma ends an unquoted value)")
        if bad := [k for k in ("required", "reserved") if not isinstance(spec.get(k, False), bool)]:  # `no` is text
            raise ConfigError(f"param '{name}': {bad[0]} is true or false, not {spec[bad[0]]!r}")
        location = "path" if "{" + name + "}" in path else \
            spec.get("in") or ("body" if method in BODY_METHODS else "query")
        if location not in LOCATIONS:
            raise ConfigError(f"param '{name}': unknown location {location!r} (use one of {', '.join(LOCATIONS)})")
        if location == "path" and "{" + name + "}" not in path:
            raise ConfigError(f"param '{name}' is in: path, but the path has no {{{name}}}")
        if location == "body" and (set(name) & set('=;"') or has_control(name)):
            raise ConfigError(f"param '{name}': a body field name can't hold =, ;, \", or a control character "
                              "(curl would read it as another form field or a filename)")
        if location == "header":
            check_header_name(name, f"param '{name}'")
        kind = spec.get("type", "object" if location == "json" else "string")
        if not isinstance(kind, str) or kind not in TYPES:  # a list such as [string, "null"] is OpenAPI's, not ours
            raise ConfigError(f"param '{name}': unknown type {kind!r} (use one of {', '.join(TYPES)})")
        if kind == "file" and spec.get("default") is not None:  # it would send a local file unasked
            raise ConfigError(f"param '{name}': a file param has no default; whoever calls the API names the file")
        enum = spec.get("enum")
        if isinstance(enum, dict):
            raise ConfigError(f"param '{name}': enum must be a list of the allowed values, such as [new, old]")
        if enum is not None and not isinstance(enum, list):
            enum = [enum]  # one value alone is a list of one, not a value per character
        return cls(name, location, kind, location == "path" or bool(spec.get("required")), spec.get("default"),
                   " ".join(str(spec.get("description") or "").split()), tuple(enum) if enum else None,
                   bool(spec.get("reserved")))

    @property
    def mandatory(self) -> bool:
        """The caller must give a value: it's required and has no default."""
        return self.required and self.default is None

    def check(self, value: Any) -> Any:
        """The value, if it has the declared type (numbers are accepted for strings). A stand-in such
        as <event_id> is accepted whatever the type, so an example may use one for a non-string param;
        verify() skips any call that still holds one."""
        if isinstance(value, str) and STAND_IN.fullmatch(value):
            return value
        if self.type == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        numeric = self.type in ("integer", "number")
        if not isinstance(value, TYPES[self.type]) or (numeric and isinstance(value, bool)):
            raise UsageError(f"param '{self.name}' expects {self.type}, got {value!r}")
        if self.enum:  # for an array, each item must be allowed; for a scalar, the value itself
            items = value if self.type == "array" and isinstance(value, (list, tuple)) else [value]
            if bad := [v for v in items if v not in self.enum and not (isinstance(v, str) and STAND_IN.fullmatch(v))]:
                raise UsageError(f"param '{self.name}' must be one of {', '.join(map(str, self.enum))}; "
                                 f"got {bad[0]!r}")
        return value


@dataclass(frozen=True)
class Endpoint:
    """One API: method, path, access level and params."""

    name: str
    method: str
    path: str
    description: str = ""
    access: Access = Access.READ
    params: dict[str, Param] = field(default_factory=dict)
    format: str = "json"  # how the body is sent: one of FORMATS
    content_type: str = ""  # with format raw: the body's media type
    example: dict | None = None  # realistic param values for its example call

    @classmethod
    def from_config(cls, name: str, spec: dict) -> Endpoint:
        try:
            if not isinstance(spec, dict) or not isinstance(spec.get("params") or {}, dict) \
                    or not isinstance(spec.get("example") or {}, dict):  # `example:` alone: none
                raise ConfigError("its settings, its params and its example must be mappings")
            if unknown := sorted(set(spec) - ENDPOINT_KEYS):
                raise ConfigError(f"unknown settings {', '.join(unknown)} (use {', '.join(sorted(ENDPOINT_KEYS))})")
            if (body := spec.get("format", "json")) not in FORMATS:
                raise ConfigError(f"unknown format {body!r} (use one of {', '.join(FORMATS)})")
            if has_control(str(spec.get("content_type") or "")):  # it's sent as a header
                raise ConfigError("content_type has a control character in it")
            method = str(spec.get("method", "GET")).upper()
            if method not in METHODS:
                raise ConfigError(f"unknown method {method!r}")
            path = spec.get("path")
            if not path or not str(path).startswith("/"):
                raise ConfigError("needs a path starting with '/'")
            access = spec.get("access") or Access.for_method(method).value
            if access not in [a.value for a in Access]:
                raise ConfigError(f"unknown access {access!r} (use one of {', '.join(a.value for a in Access)})")
            given = spec.get("params") or {}
            if bad := [n for n in given if not isinstance(n, str)]:
                raise ConfigError(f"param name {bad[0]!r} isn't text; put it in quotes")
            if bad := [n for n, s in given.items() if not isinstance(s or {}, dict)]:
                raise ConfigError(f"param {bad[0]!r}: its settings must be a mapping, such as {{type: integer}}")
            params = {n: Param.from_config(n, s or {}, method, path) for n, s in given.items()}
            for n in re.findall(r"\{(\w[\w.-]*)\}", path):  # path params need no entry of their own
                params.setdefault(n, Param(n, "path", required=True))
            for n, p in params.items():  # where curl can send a file, and what a multipart body holds
                if p.type == "file" and (body, p.location) not in (("multipart", "body"), ("raw", "json")):
                    raise ConfigError(f"param '{n}': a file is an in: body field of a multipart body, or the "
                                      "in: json param of a raw one")
                if body == "multipart" and p.location == "json":
                    raise ConfigError(f"param '{n}': a multipart body takes in: body fields, not in: json")
                if body == "raw" and p.location == "body":
                    raise ConfigError(f"param '{n}': a raw body is one in: json param, not in: body fields")
            example = spec.get("example") or {}
            if files := [n for n, p in params.items() if p.type == "file" and n in example
                         and not (isinstance(example[n], str) and STAND_IN.fullmatch(example[n]))]:
                raise ConfigError(f"its example's {files[0]} is a local file to send: use a stand-in such as "
                                  "<path_to_a_file>, which whoever calls the API fills in")
        except ConfigError as e:
            raise ConfigError(f"API '{name}': {e}") from None
        return cls(name, method, path, " ".join(str(spec.get("description") or "").split()), Access(access), params,
                   body, str(spec.get("content_type") or ""), spec.get("example"))

    @property
    def destructive(self) -> bool:
        return self.access is Access.DESTRUCTIVE

    def values(self, given: dict[str, Any], lookup: Callable[[str], Any]) -> dict[str, Any]:
        """The values to send: the given ones checked, defaults filled in (a "${key}" default from
        lookup), and required params present."""
        if unknown := sorted(set(given) - set(self.params)):
            raise UsageError(f"{self.name}: unknown params {', '.join(unknown)} (has: {', '.join(self.params)})")
        values = {}
        for p in self.params.values():
            if p.name in given:  # a value the caller gave: only None (not "") means "leave it out"
                value = given[p.name]
                unset = value is None
            else:  # fall back to the default, whose unset ${key} is "leave it out"
                value = expand(p.default, lookup, f"{self.name}.{p.name}") if p.default is not None else None
                unset = value in UNSET
            if not unset:
                try:
                    values[p.name] = p.check(value)
                except UsageError as e:
                    raise UsageError(f"{self.name}: {e}") from None
            elif p.required:
                if keys := placeholders(p.default):
                    raise ConfigError(f"{self.name}: set {', '.join(sorted(keys))} in credentials.yaml "
                                      f"(or pass {p.name}=...)")
                raise UsageError(f"{self.name}: missing required param '{p.name}'")
        return values

    def build(self, values: dict[str, Any]) -> Request:
        """Place each value in the path, query string, headers or body."""
        req = Request(self.method, self.path, format=self.format)
        fields: dict[str, Any] = {}
        whole = None
        for name, value in values.items():
            param = self.params[name]
            if param.type == "file":
                value = Path(value).expanduser()
            if param.location == "path":
                text = scalar(value)
                # an empty segment changes the path, and curl resolves . and .. away, out of base_url's path
                if any(seg in ("", ".", "..") for seg in (text.split("/") if param.reserved else [text])):
                    raise UsageError(f"{self.name}: param '{name}' can't be empty, '.' or '..'")
                req.path = req.path.replace("{" + name + "}", quote(text, safe="/" if param.reserved else ""))
            elif param.location == "query":
                req.query[name] = query_value(value)
            elif param.location == "header":
                text = scalar(value)
                if has_control(text):
                    raise UsageError(f"{self.name}: header '{name}' can't contain a control character")
                req.headers[name] = text
            elif param.location == "json":
                whole = value
            else:
                fields[name] = value
        if isinstance(whole, dict):
            req.body = {**whole, **fields}
        elif whole is not None:
            if fields:  # a non-object whole body (an array, say) can't also carry separate fields
                raise UsageError(f"{self.name}: the whole-body param carries {type(whole).__name__}, so there's "
                                 f"nowhere to put {', '.join(fields)}")
            req.body = whole
        elif fields or (self.method in BODY_METHODS and self.format in ("json", "form")):
            req.body = fields  # some services refuse a POST without a body
        if self.format == "raw" and req.body is not None and "content-type" not in map(str.lower, req.headers):
            req.headers["Content-Type"] = self.content_type or "application/octet-stream"
        return req


def scalar(value: Any) -> str:
    """A value as text for a path, a header, a query or a form field: booleans as true/false and null as
    null (JSON), a container as JSON, so none of them is sent as Python's repr (True, {'a': 1})."""
    if isinstance(value, (bool, dict, list, tuple)) or value is None:
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def query_value(value: Any) -> str | list[str]:
    """How a value appears in a query string: a list repeats the key."""
    return [scalar(v) for v in value] if isinstance(value, (list, tuple)) else scalar(value)
