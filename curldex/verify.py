# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Each API's example, and verifying it: verify() runs the examples against the service and records that
they worked, in config/verified.yaml: the date, the HTTP status, the command (which holds no secrets,
and shows the values given to verify() as stand-ins) and the shape of the response. No response data
is kept, so the file can be committed and shared.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from .config import ConfigError, HttpError, UsageError, dump_yaml, error_summary, load_yaml
from .curl import Curl, readable
from .endpoint import STAND_IN, Access, Endpoint

if TYPE_CHECKING:
    from .webapp import WebApp

FIELD = re.compile(r"@?[A-Za-z_][A-Za-z0-9_]{0,63}")  # a key that names a field, not one holding data
# (a key with a '.', '-' or other sign is data: a hostname, an email address, a dotted id)
SCALARS = {str: "string", bool: "boolean", int: "integer", float: "number", type(None): "null"}
MAX_DEPTH, MAX_FIELDS = 3, 25


def example(endpoint: Endpoint) -> dict:
    """Param values for an API's example: its `example:` in api.yaml, else a stand-in such as
    "<event_id>" for each value that must be given, whatever its type, so that verify() skips the API
    until a real value is supplied (it never calls the service with a made-up id)."""
    if endpoint.example is not None:
        return dict(endpoint.example)
    return {p.name: stand_in_for(p.name) for p in endpoint.params.values() if p.mandatory}


def stand_in_for(name: str) -> str:
    """A stand-in for a param's value: <name>, with signs a stand-in can't hold as _ (page[size] ->
    <page_size_>), so that it's still read as one."""
    return "<" + re.sub(r"[^\w.-]", "_", name) + ">"


def _has_stand_in(value: Any) -> bool:
    """Whether a value still holds a <stand-in> anywhere: a string, or nested in an object or a list."""
    if isinstance(value, str):
        return bool(STAND_IN.search(value))
    if isinstance(value, dict):
        return any(_has_stand_in(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_stand_in(v) for v in value)
    return False


def example_curl(app: WebApp, endpoint: Endpoint, values: dict | None = None, shared: bool = False) -> Curl:
    """The Curl for an API's example (or these values). `shared` puts <key> where a value would come
    from credentials.yaml, so the command can go into shared docs."""
    given = example(endpoint) if values is None else values
    return app.curl_for(endpoint, given, (lambda key: f"<{key}>") if shared else None)


@dataclass(repr=False)
class Verified:
    """One example that ran."""

    api: str
    date: str
    status: int
    command: str
    shape: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300  # a 3xx (an unfollowed redirect) isn't a verified success

    def __repr__(self) -> str:  # short, as IPython echoes the list verify() returns
        return f"<{self.api}: {self.note()}>"

    def note(self) -> str:
        return f"{'verified' if self.ok else 'failed'} on {self.date} (HTTP {self.status})"


def load_verified(app: WebApp) -> dict[str, Verified]:
    records = load_yaml(app.verified_file, required=False)
    return {api: Verified(api, str(r.get("date")), int(r.get("status", 0)), str(r.get("command", "")),
                          str(r.get("shape", "")))
            for api, r in records.items() if isinstance(r, dict)}


def verify(app: WebApp, api: str | None, yes: bool, values: dict[str, Any]) -> list[Verified]:
    """Run the examples of the read APIs, or of one API (a write or destructive one only with
    yes=True); values replace the example's. Records the results in config/verified.yaml and updates
    API.md. The record shows each value given here as a stand-in (_shareable), since it may be the
    user's own data.

    An API whose call gets no answer (curl fails: the network, a timeout) isn't recorded, and the
    others still run; HttpError then names it. If the run stops (a login that expired, say), what
    ran so far is recorded first."""
    if not app.api_file.exists():
        raise ConfigError(app._no_config())
    if values and api is None:
        raise UsageError("param values need an API: verify(api, name=value)")
    if yes and api is None:
        raise UsageError("yes=True (--yes) needs an API: a write or destructive example acts on real data, so "
                         "verify them one at a time, on data made for the test")
    if api and (unknown := sorted(set(values) - set(app.endpoint(api).params))):  # before "it needs values"
        raise UsageError(f"{api}: unknown params {', '.join(unknown)}")
    results: list[Verified] = []
    unanswered: list[str] = []
    try:
        for endpoint in [app.endpoint(api)] if api else list(app.endpoints.values()):
            if endpoint.access is not Access.READ and not yes:
                print(f"{endpoint.name}: skipped, a {endpoint.access.value} API (verify it by itself, on test data, "
                      "with yes=True or --yes)")
                continue
            given = {**example(endpoint), **values}
            if todo := [k for k, v in given.items() if _has_stand_in(v)]:
                print(f"{endpoint.name}: skipped, it needs values for {', '.join(todo)}")
                continue
            try:
                status, data = example_curl(app, endpoint, given).fetch()
            except HttpError as e:  # no answer: nothing to record about the API itself
                print(_redact(f"{endpoint.name}: not verified, {e}", _secret_values(app)))
                unanswered.append(endpoint.name)
                continue
            shown = _shareable(endpoint, given, values)
            command = readable(example_curl(app, endpoint, shown, shared=True).command("curldex"))
            # last guard: a service that echoes a credential (as a response key, or in its error message) must
            # not leave it in a committed file or on screen; read after the call, which may refresh a token
            secrets = _secret_values(app)
            result = Verified(endpoint.name, date.today().isoformat(), status, command, _redact(shape(data), secrets))
            said = result.shape if result.ok else error_summary(data) if status >= 400 else \
                "a redirect, which curldex doesn't follow" if status in (301, 302, 303, 307, 308) else ""
            print(f"{endpoint.name}: {result.note()}  {_redact(said, secrets)}")
            results.append(result)
    finally:
        if results:
            _save_verified(app, results)
    if unanswered:
        raise HttpError(None, f"{app.name}: no answer for {', '.join(unanswered)}; the other results are saved")
    return results


def _secret_values(app: WebApp) -> list[str]:
    """The app's own credential values worth scrubbing from what verify records and prints: the auth's keys
    and the tokens it saves (also one saved nested, as Google's service-account token is), as strings of 8+
    characters (so a short, common value isn't redacted everywhere)."""
    creds = app.credentials.load()
    values = [creds.get(k) for k in set(app.auth.keys) | set(app.auth.TOKENS)]
    values += [v.get("access_token") for v in creds.values() if isinstance(v, dict)]
    return sorted({v for v in values if isinstance(v, str) and len(v) >= 8}, key=len, reverse=True)


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "<redacted>")
    return text


def _shareable(endpoint: Endpoint, given: dict, values: dict) -> dict:
    """The values to record for a call made with `given`. Every value passed to verify() may be the
    user's own data (an id, an address, an account number), so none is recorded as given: each is shown
    as the example's own stand-in for it, or as <name>. A value equal to the example's own is kept, since
    that one is already committed in api.yaml and safe to share."""
    own = endpoint.example or {}
    shown = dict(given)
    for name, value in values.items():
        if value == own.get(name):
            continue  # the example's own value: already shareable
        stand_in = own.get(name)
        if isinstance(stand_in, str) and STAND_IN.search(stand_in):
            shown[name] = stand_in
        elif isinstance(value, (dict, list, tuple)):  # a stand-in text wouldn't fit an object or array param
            shown[name] = own.get(name, {} if isinstance(value, dict) else [stand_in_for(name)])  # [] sends nothing
        else:
            shown[name] = stand_in_for(name)
    return shown


def _save_verified(app: WebApp, results: list[Verified]) -> None:
    """Merge the results into config/verified.yaml, without the APIs api.yaml no longer has, and update
    API.md."""
    records = {v.api: v for v in load_verified(app).values() if v.api in app.endpoints} | {v.api: v for v in results}
    saved = {name: {k: v for k, v in asdict(r).items() if k != "api"} for name, r in sorted(records.items())}
    app.verified_file.write_text("# Written by `curldex verify`: examples that ran against the service.\n"
                                 "# Only the command and the response's shape are kept, no data.\n"
                                 + dump_yaml(saved))
    app.write_docs()


def shape(value: Any, depth: int = 0) -> str:
    """A JSON value's structure, without its data: field names and types, such as
    {kind: string, items[]: {id: string, start: {dateTime: string}}}. A key that holds data (an
    email address, say) shows as <key>."""
    if depth > MAX_DEPTH:  # arrays nested in arrays, say
        return "..."
    if isinstance(value, list):
        return f"[]: {_items(value, depth)}"
    if not isinstance(value, dict):
        return SCALARS.get(type(value), type(value).__name__)
    if depth == MAX_DEPTH:
        return "{...}"
    items = list(value.items())
    # an object whose values are all objects that share a field, or all arrays, is a map keyed by data (ids,
    # usernames, dates): those keys are response data, so show one merged value under <key> rather than the
    # real keys. Objects with nothing in common ({resources: {...}, rate: {...}}) are a record's fields.
    # (A record with scalar fields keeps its field names; a scalar-valued map can't be told from an
    # all-scalar record by shape, so its keys are kept -- see SECURITY.md.)
    nonnull = [v for _, v in items if v is not None]
    if len(items) >= 2 and nonnull and all(isinstance(v, dict) for v in nonnull) and _alike(nonnull):
        return "{<key>: " + shape(_merged(nonnull), depth + 1) + "}"
    if len(items) >= 2 and nonnull and all(isinstance(v, list) for v in nonnull):
        return "{<key>[]: " + _items([x for lst in nonnull for x in lst], depth + 1) + "}"
    fields: dict[str, str] = {}
    for key, item in value.items():
        name = key if FIELD.fullmatch(str(key)) else "<key>"
        if name not in fields:
            fields[name] = f"{name}[]: {_items(item, depth + 1)}" if isinstance(item, list) \
                else f"{name}: {shape(item, depth + 1)}"
    listed = list(fields.values())
    return "{" + ", ".join(listed[:MAX_FIELDS] + (["..."] if len(listed) > MAX_FIELDS else [])) + "}"


def _items(items: list, depth: int) -> str:
    """One shape for a list's items; objects are merged, so fields only some of them have show too."""
    if not items:
        return "?"
    if all(isinstance(item, dict) for item in items):
        return shape(_merged(items), depth)
    return shape(items[0], depth + 1)


def _alike(records: list[dict]) -> bool:
    """Whether the (non-empty) records share a field, as the values of a map keyed by data do."""
    fields = [set(record) for record in records if record]
    return not fields or bool(set.intersection(*fields))


def _merged(records: list[dict]) -> dict:
    """Every field any of the records has, with the first value seen."""
    merged: dict = {}
    for record in records:
        for key, value in record.items():
            merged.setdefault(key, value)
    return merged
