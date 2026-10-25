# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Curl: the command text, and running it against the fake service."""

import shlex
import subprocess

import pytest

from curldex import Curl, HttpError, UsageError
from curldex.curl import curldex_command, readable


class App:  # stands in for a WebApp: only its name is used in the command text
    name = "demo"


def test_command_text():
    assert Curl("GET", "https://x.test/a?b=1").command() == "curl -q -sS -g 'https://x.test/a?b=1'"
    assert Curl("POST", "https://x.test/a", body={"t": "it's"}).command() == \
        "curl -q -sS -g --json '{\"t\": \"it'\"'\"'s\"}' 'https://x.test/a'"
    patch = Curl("PATCH", "https://x.test/a", {"X-Y": "1"}, {}, App()).command("curldex")
    assert patch == "curl -q -sS -g -X PATCH -H 'X-Y: 1' -K \"$(curldex auth demo)\" --json '{}' 'https://x.test/a'"
    assert Curl("GET", "https://x.test/it's").command() == "curl -q -sS -g 'https://x.test/it'\"'\"'s'"
    assert Curl("DELETE", "https://x.test/a").command().startswith("curl -q -sS -g -X DELETE ")


def test_the_apps_name_is_quoted_in_the_command():
    class Odd:  # a WebApp made from a folder whose name isn't a plain word
        name = "my app;x"
    assert "\"$(curldex auth 'my app;x')\"" in Curl("GET", "https://x.test/a", app=Odd()).command("curldex")


def test_the_command_round_trips_through_a_shell():
    body = {"text": "a 'quoted' $HOME `x` \\ \"y\""}
    words = shlex.split(Curl("POST", "https://x.test/a", body=body).command())
    assert words[5] == '{"text": "a \'quoted\' $HOME `x` \\\\ \\"y\\""}'


def test_readable_leaves_stand_ins_alone():
    assert readable("curl https://x.test/e/%3Cevent_id%3E?q=%3C") == "curl https://x.test/e/<event_id>?q=%3C"


def test_curldex_command_runs():
    out = subprocess.run(f"{curldex_command()} --help", shell=True, capture_output=True, text=True)
    assert out.returncode == 0 and "curldex auth <app>" in out.stdout


def test_run_returns_json_and_raises_on_errors(fake):
    assert Curl("GET", f"{fake.url}/me").run()["id"] == "u1"
    assert Curl("DELETE", f"{fake.url}/events/e1").run(yes=True) is None
    with pytest.raises(HttpError) as error:
        Curl("GET", f"{fake.url}/fail").run()
    assert error.value.status == 500 and error.value.body == {"error": "boom"}
    with pytest.raises(HttpError, match="curl failed"):
        Curl("GET", "http://127.0.0.1:9/").run()


def test_only_a_location_redirect_is_not_success():
    """A Location-bearing 3xx (curl doesn't follow it) is an error; 304/300 are successes, not redirects."""
    from curldex import HttpError

    class At(Curl):
        def __init__(self, code):
            super().__init__("GET", "https://x.test/r")
            self._code = code

        def fetch(self, timeout=120):
            return self._code, None
    for code in (301, 302, 303, 307, 308):
        with pytest.raises(HttpError, match="redirect"):
            At(code).run()
    for code in (200, 204, 300, 304, 305):  # not redirects-to-follow: returned, not raised
        assert At(code).run() is None


def test_a_raw_body_is_sent_as_it_is(fake, tmp_path):
    """Text is never read as a file, even when it starts with @ (a default in api.yaml could name one);
    an object or a list goes as JSON; only a file param's Path sends a file."""
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")

    def sent(body, kind="text/plain"):
        return Curl("POST", f"{fake.url}/raw", {"Content-Type": kind}, body, format="raw").run()["body"]
    assert sent(f"@{secret}") == f"@{secret}"
    assert sent("line one\n\nline two") == "line one\n\nline two"
    assert sent({"a": [1, "é"]}, "application/json") == {"a": [1, "é"]}
    assert sent(secret) == "SECRET"


def test_a_curl_error_shows_only_curls_own_message(fake, tmp_path, monkeypatch):
    """With `verbose` in the user's .curlrc, stderr also holds the request's headers."""
    (tmp_path / ".curlrc").write_text("verbose\n")
    monkeypatch.setenv("CURL_HOME", str(tmp_path))
    with pytest.raises(HttpError) as error:
        Curl("GET", f"{fake.url}/fail", {"Authorization": "Bearer SECRET"}, options=["--fail"]).fetch()
    assert str(error.value.body).startswith("curl failed (exit 22): curl: (22)") and "SECRET" not in str(error.value)


def test_a_destructive_call_asks_first(fake):
    with pytest.raises(UsageError, match="pass yes=True"):  # pytest's stdin isn't a terminal: no one to ask
        Curl("DELETE", f"{fake.url}/events/e1", destructive=True).run()
    assert not fake.requests


def test_an_error_message_holds_what_the_service_says_not_its_whole_answer():
    google = {"error": {"code": 403, "message": "The caller does not  have permission", "status": "DENIED"}}
    assert str(HttpError(403, google)) == "HTTP 403: The caller does not have permission"
    oauth = '{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}'
    assert str(HttpError(400, oauth)) == "HTTP 400: invalid_grant: Token has been expired or revoked."
    assert str(HttpError(502, "Bad gateway\n<html>...</html>")) == "HTTP 502: Bad gateway"
    echo = {"headers": {"Authorization": "Bearer SECRET-TOKEN"}, "url": "https://example.com/status/418"}
    error = HttpError(418, echo)
    assert "SECRET-TOKEN" not in str(error) and error.body == echo  # the message never; .body still has it
    assert str(HttpError(None, "curl failed (exit 7): Couldn't connect")) == "curl failed (exit 7): Couldn't connect"


def test_a_curl_error_keeps_its_reason(tmp_path):
    from curldex import HttpError
    with pytest.raises(HttpError, match="option --data-binary: error encountered when reading a file"):
        Curl("POST", "http://127.0.0.1:9/x", body=tmp_path / "missing.txt", format="raw").fetch()
