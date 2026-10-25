# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Verified examples: running them, recording their shape (never their data), and the docs."""

import pytest

from curldex import ConfigError, HttpError, UsageError, Verified
from curldex.verify import load_verified, shape

UNSTEADY = """\
base_url: {url}
auth: {auth}
commands:
  me: {{description: Who the user is, path: /me}}
  drop: {{description: Gets no answer, path: /drop}}
  list-events: {{description: List events, path: /events}}
"""

RECORDED = """\
base_url: {url}
auth: {auth}
commands:
  delete-event: {{method: DELETE, path: '/events/{{id}}', example: {{id: <event_id>}}}}
  create-event:
    method: POST
    path: /events
    params: {{summary: {{}}, kind: {{enum: [a, b]}}, count: {{type: integer}}, start: {{type: object}}}}
    example: {{summary: Dentist, start: {{date: '2026-10-12'}}}}
"""


def test_shape_keeps_structure_not_data():
    data = {"kind": "list", "count": 2, "ok": True, "next": None, "items": [{"id": "a"}, {"id": "b", "tags": ["x"]}],
            "calendars": {"me@private.example": {"busy": []}}, "deep": {"a": {"b": {"c": {"d": 1}}}}}
    assert shape(data) == ("{kind: string, count: integer, ok: boolean, next: null, "
                           "items[]: {id: string, tags[]: string}, calendars: {<key>: {busy[]: ?}}, "
                           "deep: {a: {b: {...}}}}")
    assert shape([1.5]) == "[]: number"
    assert shape(None) == "null"


def test_verify_runs_read_examples_and_records_them(make_app, fake, capsys):
    app = make_app()
    results = app.verify()
    out = capsys.readouterr().out
    assert [r.api for r in results] == ["me", "list-events", "fail"]
    assert "create-event: skipped, a write API" in out and "delete-event: skipped, a destructive API" in out
    assert [r.ok for r in results] == [True, True, False]
    saved = app.verified_file.read_text()
    assert "PRIVATE-DATA" not in saved and "me@example.com" not in saved  # shapes only
    assert "items[]: {id: string, summary: string, start: {date: string}}" in saved
    docs = app.docs_file.read_text()
    assert "Example, verified on " in docs and "Example, failed on " in docs


def test_a_record_without_a_status_isnt_verified():
    assert not Verified("me", "2026-10-24", 0, "", "").ok and Verified("me", "2026-10-24", 204, "", "").ok


def test_verify_writes_only_when_asked(make_app, fake, capsys):
    app = make_app()
    assert app.verify("create-event") == []
    results = app.verify("create-event", yes=True, summary="test event")
    assert results[0].ok and fake.last["body"] == '{"summary": "test event"}'
    capsys.readouterr()
    assert app.verify("delete-event", yes=True) == []
    assert "delete-event: skipped, it needs values for id" in capsys.readouterr().out
    app.verify("delete-event", yes=True, id="e1")
    assert fake.last["method"] == "DELETE"
    assert app.verified_file.read_text().count("status:") == 2  # create-event and delete-event, kept together


def test_writes_are_verified_one_api_at_a_time(make_app, fake):
    """Every write and destructive example in one go would act on real data, and stay there."""
    with pytest.raises(UsageError, match=r"yes=True \(--yes\) needs an API"):
        make_app().verify(yes=True)
    assert not fake.requests


def test_the_record_shows_the_values_given_as_stand_ins(make_app, fake):
    """They may be the user's own data (an event's id, an address), and verified.yaml is committed."""
    app = make_app(api=RECORDED)
    app.verify("delete-event", yes=True, id="REAL-ID-1")
    app.verify("create-event", yes=True, summary="Lunch with Sam", kind="b", count=2, start={"date": "2026-11-02"})
    assert fake.last["body"] == \
        '{"summary": "Lunch with Sam", "kind": "b", "count": 2, "start": {"date": "2026-11-02"}}'  # what ran
    records = load_verified(app)
    assert records["delete-event"].command.endswith("/events/<event_id>'")  # the example's stand-in
    assert '{"summary": "<summary>", "kind": "<kind>", "count": "<count>", "start": {"date": "2026-10-12"}}' in \
        records["create-event"].command  # every given value as <name>; an object as the example's own
    assert "REAL-ID-1" not in app.verified_file.read_text() and "Lunch" not in app.verified_file.read_text()
    app.verify("delete-event", yes=True, id=987654321)  # a number in the path is an id too
    assert load_verified(app)["delete-event"].command.endswith("/events/<event_id>'")


def test_verify_never_calls_with_a_made_up_id(make_app, fake):
    """An API with no example and a required typed id gets a <stand-in>, not 1, so verify skips it
    instead of deleting item 1; a stand-in inside a value (even nested) skips it too."""
    api = ("base_url: {url}\nauth: {auth}\ncommands:\n"
           "  del-num: {{method: DELETE, path: '/events/{{id}}', params: {{id: {{type: integer}}}}}}\n"
           "  get-nested: {{path: /events, params: {{q: {{}}}}, example: {{q: 'id:<event_id>'}}}}\n")
    app = make_app(api=api)
    before = len(fake.requests)
    out = app.verify("del-num", yes=True)
    assert out == [] and len(fake.requests) == before  # not called at all
    assert app.verify("get-nested") == [] and len(fake.requests) == before  # <event_id> inside the value


def test_records_of_apis_api_yaml_no_longer_has_dont_count(make_app):
    app = make_app()
    app.verified_file.write_text("".join(f"{name}: {{date: '2026-10-11', status: 200, command: x, shape: y}}\n"
                                         for name in ("me", "gone", "also-gone")))
    assert "- **APIs:** 5, with 1 examples verified" in app.docs()
    app.verify("me")
    assert sorted(load_verified(app)) == ["me"]  # dropped from verified.yaml once it's written again


def test_an_api_without_an_answer_doesnt_lose_the_others(make_app, capsys):
    app = make_app(api=UNSTEADY)
    with pytest.raises(HttpError, match="no answer for drop; the other results are saved"):
        app.verify()
    assert "drop: not verified, curl failed" in capsys.readouterr().out
    assert sorted(load_verified(app)) == ["list-events", "me"]  # the API after the failure ran too


def test_a_run_that_stops_keeps_what_ran(make_app, monkeypatch):
    app = make_app(api=UNSTEADY, auth="{type: bearer}", credentials="token: T\n")
    real, calls = app.auth_file, []

    def auth_file():  # the login expires after the first call
        calls.append(1)
        if len(calls) > 1:
            raise ConfigError("testapp: the login expired or was revoked")
        return real()
    monkeypatch.setattr(app, "auth_file", auth_file)
    with pytest.raises(ConfigError, match="login expired"):
        app.verify()
    assert list(load_verified(app)) == ["me"]


def test_verify_redacts_the_apps_own_secret_from_the_shape(make_app, fake):
    """If the service echoes a credential as a response key, it must not land in verified.yaml."""

    from curldex.verify import _redact, _secret_values
    app = make_app(auth="{type: bearer}", credentials="token: s3cr3tTOKENvalue123\n")
    secrets = _secret_values(app)
    assert "s3cr3tTOKENvalue123" in secrets
    assert _redact("{s3cr3tTOKENvalue123: {a: string}}", secrets) == "{<redacted>: {a: string}}"


def test_a_bracketed_param_gets_a_stand_in_verify_skips():
    from curldex import Endpoint
    from curldex.verify import example
    e = Endpoint.from_config("list", {"path": "/things", "params": {"filter[owner]": {"required": True}}})
    assert example(e) == {"filter[owner]": "<filter_owner_>"}


def test_verify_says_why_an_api_failed_and_echoes_briefly(make_app, capsys):
    app = make_app(api="base_url: {url}\nauth: {auth}\ncommands:\n  gone: {{path: /missing, description: x}}\n")
    results = app.verify("gone")
    assert "gone: failed on" in capsys.readouterr().out
    assert repr(results[0]).startswith("<gone: failed on ")


def test_verify_redacts_a_credential_echoed_in_an_error(make_app, capsys, monkeypatch):
    app = make_app(auth="{type: bearer}", credentials="token: SUPERSECRETTOKEN123\n",
                   api="base_url: {url}\nauth: {auth}\ncommands:\n  me: {{path: /me, description: x}}\n")
    monkeypatch.setattr("curldex.curl.Curl.fetch", lambda self, timeout=120: (
        401, {"error": {"message": "invalid credentials: Bearer SUPERSECRETTOKEN123"}}))
    app.verify("me")
    out = capsys.readouterr().out
    assert "SUPERSECRETTOKEN123" not in out and "Bearer <redacted>" in out


def test_shape_survives_deep_nesting():
    import json
    assert shape(json.loads("[" * 500 + "]" * 500)).endswith("...")
    assert shape({"resources": {"core": {"limit": 1}}, "rate": {"limit": 1, "used": 0}}).startswith("{resources:")
