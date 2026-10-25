# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""The Google apps: the Discovery importer, the service-account login, and the shipped Google apps."""

import base64
import fnmatch
import json
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from curldex.discover import found
from curldex.shared.google import DiscoveryImporter, GoogleApp, rs256
from curldex.webapp import app_names, load_app

CALENDAR = """
description: Calendar
base_url: https://www.googleapis.com/calendar/v3
auth: {type: oauth2, scopes: ["https://www.googleapis.com/auth/calendar"]}
commands:
  list-events:
    path: /calendars/{calendar_id}/events
    params:
      calendar_id: {default: "${calendar_id}"}
      maxResults: {type: integer}
      singleEvents: {type: boolean, default: true}
"""


def discovery_document(url: str) -> dict:
    return {
        "description": "Demo service. More.", "rootUrl": url + "/", "servicePath": "v1/",
        "schemas": {"Event": {"properties": {"summary": {}, "start": {}}}},
        "resources": {
            "events": {"methods": {
                "list": {"httpMethod": "GET", "path": "events", "description": "Lists events.",
                         "parameters": {"maxResults": {"type": "integer", "location": "query"},
                                        "eventTypes": {"type": "string", "repeated": True, "location": "query"}},
                         "scopes": ["s.full", "s.readonly"]},
                "insert": {"httpMethod": "POST", "path": "events", "request": {"$ref": "Event"}, "scopes": ["s.full"]},
                "delete": {"httpMethod": "DELETE", "path": "events/{eventId}", "parameterOrder": ["eventId"],
                           "parameters": {"eventId": {"type": "string", "location": "path", "required": True}},
                           "scopes": ["s.full"]}}},
            "users": {"resources": {"settings": {"methods": {
                "get": {"httpMethod": "GET", "path": "{+name}", "flatPath": "users/{usersId}/settings",
                        "parameters": {"name": {"type": "string", "location": "path", "required": True}},
                        "scopes": ["s.readonly"]}}}}},
            "freebusy": {"methods": {"query": {"httpMethod": "POST", "path": "freeBusy", "scopes": ["s.readonly"]}}},
        },
    }


def test_discovery_import():
    found = DiscoveryImporter().build(discovery_document("https://demo.test"), "")
    apis = found["commands"]
    assert found["base_url"] == "https://demo.test/v1" and found["description"] == "Demo service."
    assert list(apis) == ["events-list", "events-insert", "events-delete", "users-settings-get", "freebusy-query"]
    assert apis["events-list"]["params"] == {"eventTypes": {"in": "query", "type": "array"},
                                             "maxResults": {"in": "query", "type": "integer"}}
    assert apis["events-insert"]["params"]["body"]["description"] == "Event object: summary, start"
    assert apis["freebusy-query"]["access"] == "read"
    # flatPath names a placeholder that isn't a parameter, so the resource-name path is kept
    assert apis["users-settings-get"]["path"] == "/{name}"
    assert apis["users-settings-get"]["params"]["name"]["reserved"] is True


def test_discovery_keeps_the_apis_the_scopes_allow(fake, tmp_path, capsys):
    fake.documents["/rest"] = discovery_document(fake.url)
    (tmp_path / "cal" / "config").mkdir(parents=True)
    api = tmp_path / "cal" / "config" / "api.yaml"
    api.write_text(f"source: {{url: '{fake.url}/rest'}}\n")
    assert found(GoogleApp(tmp_path / "cal"))["auth"] == {"type": "oauth2"}  # GoogleApp reads Discovery
    api.write_text(f"auth: {{type: oauth2, scopes: [s.readonly]}}\nsource: {{url: '{fake.url}/rest'}}\n")
    assert list(found(GoogleApp(tmp_path / "cal"))["commands"]) == ["events-list", "freebusy-query",
                                                                     "users-settings-get"]
    assert "left out, as auth.scopes don't allow them: events-delete, events-insert" in capsys.readouterr().out


@pytest.fixture
def key_pair(tmp_path) -> tuple[Path, Path]:
    if not shutil.which("openssl"):
        pytest.skip("needs openssl")
    private, public = tmp_path / "key.pem", tmp_path / "pub.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out",
                    str(private)], check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True)
    return private, public


def openssl_verifies(public: Path, message: bytes, signature: bytes, tmp_path: Path) -> bool:
    (tmp_path / "msg").write_bytes(message)
    (tmp_path / "sig").write_bytes(signature)
    return subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(tmp_path / "sig"),
                           str(tmp_path / "msg")], capture_output=True).returncode == 0


def test_rs256_signatures_verify_with_openssl(key_pair, tmp_path):
    private, public = key_pair
    assert openssl_verifies(public, b"a.b", rs256(b"a.b", private.read_text()), tmp_path)  # PKCS#8
    pkcs1 = subprocess.run(["openssl", "rsa", "-in", str(private), "-traditional"], capture_output=True,
                           text=True, check=True).stdout
    assert "BEGIN RSA PRIVATE KEY" in pkcs1
    assert openssl_verifies(public, b"x.y", rs256(b"x.y", pkcs1), tmp_path)
    assert not openssl_verifies(public, b"x.z", rs256(b"x.y", pkcs1), tmp_path)


def calendar(tmp_path: Path, credentials: str = "") -> GoogleApp:
    """A Google app, with test credentials."""
    config = tmp_path / "cal" / "config"
    config.mkdir(parents=True)
    (config / "api.yaml").write_text(CALENDAR)
    (config / "credentials.yaml").write_text(credentials)
    return GoogleApp(config.parent)


def test_a_service_account_logs_in_with_a_signed_jwt(key_pair, tmp_path, fake):
    private, public = key_pair
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps({"type": "service_account", "client_email": "bot@demo.iam", "private_key_id": "k1",
                                    "private_key": private.read_text(), "token_uri": f"{fake.url}/sa-token"}))
    cal = calendar(tmp_path, f"calendar_id: me@example.com\nservice_account_file: {key_file}\n")
    assert cal.status() == "ready"
    assert 'Bearer SA1"' in cal.auth_file().read_text()
    jwt = fake.last["form"]["assertion"]
    header, claims, signature = (base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)) for part in jwt.split("."))
    assert json.loads(header) == {"alg": "RS256", "typ": "JWT", "kid": "k1"}
    assert json.loads(claims)["scope"] == "https://www.googleapis.com/auth/calendar"
    assert openssl_verifies(public, jwt.rsplit(".", 1)[0].encode(), signature, tmp_path)
    count = len(fake.requests)
    cal.auth_file()
    assert len(fake.requests) == count  # the token is kept until it expires


def test_a_google_app_logs_in_and_documents_itself(tmp_path):
    cal = calendar(tmp_path, "calendar_id: me@private.example\nclient_id: C\nclient_secret: S\n")
    assert cal.status().startswith("login required: run curldex login cal")
    assert str(cal.curl("list-events", maxResults=2)).endswith(
        "/calendars/me%40private.example/events?maxResults=2&singleEvents=true'")
    text = cal.docs()
    assert "me@private.example" not in text and "<calendar_id>" in text
    assert "https://accounts.google.com/o/oauth2/v2/auth" in text and "service_account_file" in text
    assert "access_type=offline" in text
    assert "share the data with its email address (a calendar, say)" in text  # any Google API, not only Calendar


def test_the_shipped_google_apps_keep_to_their_source_globs():
    for app in map(load_app, app_names()):
        if not isinstance(app, GoogleApp):
            continue
        include, exclude = app.source.get("include") or ["*"], app.source.get("exclude") or []
        assert app.endpoints and app.auth.scopes and app.IMPORTER is DiscoveryImporter
        for name in app.endpoints:
            assert any(fnmatch.fnmatchcase(name, g) for g in include), name
            assert not any(fnmatch.fnmatchcase(name, g) for g in exclude), name


def test_the_calendar_helpers_make_the_calls_they_say(tmp_path, fake):
    from curldex.apps.google_calendar import GoogleCalendar
    folder = tmp_path / "google_calendar"  # the shipped api.yaml only, never the user's own files next to it
    (folder / "config").mkdir(parents=True)
    api = Path(shutil.copy(GoogleCalendar.DIRECTORY / "config" / "api.yaml", folder / "config"))
    api.write_text(api.read_text().replace("https://www.googleapis.com/calendar/v3", fake.url))
    (folder / "config" / "credentials.yaml").write_text('calendar_id: "primary"\nclient_id: C\nclient_secret: S\n')
    cal = GoogleCalendar(folder)
    cal.credentials.save(access_token="AT", expires_at=2**40, refresh_token="RT")  # logged in
    fake.documents["/users/me/settings/timezone"] = {"value": "America/Los_Angeles"}
    fake.documents["/calendars/primary/events"] = {"items": [{"id": "e1"}]}

    assert cal.timezone() == "America/Los_Angeles"
    assert cal.events_on(date(2026, 10, 24)) == [{"id": "e1"}]
    asked = fake.last["query"]  # that day, midnight to midnight in the user's time zone, recurring ones expanded
    assert (asked["timeMin"], asked["timeMax"]) == ("2026-10-24T00:00:00-07:00", "2026-10-25T00:00:00-07:00")
    assert (asked["singleEvents"], asked["orderBy"]) == ("true", "startTime")

    start = datetime(2026, 10, 24, 15, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    made = cal.add_event("Dentist", start, minutes=45, location="Main St")
    assert made["method"] == "POST" and made["body"] == {
        "summary": "Dentist", "start": {"dateTime": "2026-10-24T15:00:00-07:00"},
        "end": {"dateTime": "2026-10-24T15:45:00-07:00"}, "location": "Main St"}
    assert fake.last["headers"]["Authorization"] == "Bearer AT"
    with pytest.raises(ValueError, match="needs a time zone"):
        cal.add_event("Dentist", datetime(2026, 10, 24, 15, 0))
