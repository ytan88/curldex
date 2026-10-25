# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""YAML, placeholders, settings and Credentials."""

import stat

import pytest

from curldex import ConfigError
from curldex.check import check
from curldex.config import Credentials, dump_yaml, expand, mapping, parse_yaml, placeholders, texts


def test_yaml_plain_values_are_read_as_yaml_1_2_reads_them():
    """YAML 1.1 would read on as True, 2026-10-12 as a date, 12:30 as 750, 0123 as 83 and 1_000 as 1000."""
    text = "a: on\nb: 2026-10-12\nc: 12:30\nd: 0123\ne: 1_000\nf: 2026-10-12T15:00:00-07:00\ng: yes\n"
    assert parse_yaml(text) == {"a": "on", "b": "2026-10-12", "c": "12:30", "d": "0123", "e": "1_000",
                                "f": "2026-10-12T15:00:00-07:00", "g": "yes"}
    assert parse_yaml("[true, False, 0, -7, +3, 0x1F, 0o17, 1.5, .5, 1e3, -2.5E-1, ~, null]") == \
        [True, False, 0, -7, 3, 31, 15, 1.5, 0.5, 1000.0, -0.25, None, None]
    for value in ("12:30", "2026-10-12", "on", "0123", "1_000"):  # what curldex writes reads back the same
        assert parse_yaml(dump_yaml({"v": value})) == {"v": value}


def test_settings_of_the_wrong_shape_say_what_they_should_be():
    with pytest.raises(ConfigError, match=r"auth must be a mapping, such as \{type: bearer\}; got 'bearer'"):
        mapping("bearer", "auth", "{type: bearer}")
    assert mapping(None, "auth", "{}") == {}
    assert texts("calendars-clear", "exclude", "[x]") == ["calendars-clear"]  # one alone, not one per character
    assert texts(["--max-time", 30], "curl_options", "[x]") == ["--max-time", "30"]
    assert texts(None, "exclude", "[x]") == []
    for wrong in ({"a": 1}, [["a"]], [None], True):
        with pytest.raises(ConfigError, match="exclude must be a list, such as"):
            texts(wrong, "exclude", "[x]")


def test_a_whole_placeholder_keeps_the_value_and_its_type():
    values = {"n": 5, "name": "cal"}
    assert expand("${n}", values.get) == 5
    assert expand("${missing}", values.get) is None
    assert expand({"a": ["id-${name}"]}, values.get) == {"a": ["id-cal"]}


def test_a_placeholder_inside_text_must_be_set():
    with pytest.raises(ConfigError, match=r"\$\{missing\} is not set"):
        expand("x-${missing}", {}.get, "here")


def test_placeholders_are_credential_keys_only():
    assert placeholders({"a": "${uid}", "b": ["${env:HOME}", "${file:x}", "${cid}-x"]}) == {"uid", "cid"}


def test_credentials_from_env_and_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_TOKEN", "from-env")
    (tmp_path / "token.txt").write_text("from-file\n")
    (tmp_path / "credentials.yaml").write_text("a: ${env:TEST_TOKEN}\nb: ${file:token.txt}\nc: ${file:nope}\n")
    creds = Credentials(tmp_path, "app")
    assert creds.load() == {"a": "from-env", "b": "from-file", "c": None}
    assert creds.missing("a", "c", "d").startswith("missing c, d in ")
    with pytest.raises(ConfigError, match="missing c, d in"):
        creds.require("a", "c", "d")


def test_without_credentials_yaml_messages_say_to_copy_the_template(tmp_path):
    with pytest.raises(ConfigError, match=r"credentials.yaml \(copy credentials.example.yaml"):
        Credentials(tmp_path, "app").require("token")


def test_the_state_wins_and_is_owner_only(tmp_path):
    """A refresh token written in credentials.yaml only seeds the login: a newer saved one wins."""
    (tmp_path / "credentials.yaml").write_text('token: ""\nrefresh_token: OLD\nuser: me\n')
    creds = Credentials(tmp_path, "app")
    creds.save(token="T1", refresh_token="NEW", extra=1)
    assert creds.load() == {"token": "T1", "refresh_token": "NEW", "user": "me", "extra": 1}
    assert stat.S_IMODE(creds.state_path.stat().st_mode) == 0o600
    creds.save(extra=None)
    assert "extra" not in creds.state()


def test_a_placeholder_reads_credentials_yaml_only(tmp_path):
    """Never the tokens curldex saved: a ${key} in api.yaml goes into curl commands."""
    (tmp_path / "credentials.yaml").write_text("uid: u1\n")
    creds = Credentials(tmp_path, "app")
    creds.save(access_token="AT-SECRET", uid="saved")
    assert creds.get("access_token") is None and creds.get("uid") == "u1"
    assert creds.load()["access_token"] == "AT-SECRET"  # the auth still reads what it saved


def test_a_damaged_state_file_says_what_to_do(tmp_path):
    (tmp_path / ".state.json").write_text("{not json")
    with pytest.raises(ConfigError, match=r"\.state\.json is damaged; delete it, then log in again"):
        Credentials(tmp_path, "app").load()


SOURCED = """\
description: x
base_url: {url}
# A comment may quote ${{env:CURLDEX_TEST_SECRET}}: check doesn't flag comments
auth: {{type: api_key, prefix: "x-${{env:CURLDEX_TEST_SECRET}}"}}
commands:
  me:
    path: /me
    params:
      q: {{default: "${{env:CURLDEX_TEST_SECRET}}"}}
      f: {{default: "${{file:/etc/hostname}}"}}
"""


def test_only_credentials_yaml_reads_the_environment_or_files(make_app, monkeypatch):
    """api.yaml may come from a published description: its ${env:...} and ${file:...} never expand."""
    monkeypatch.setenv("CURLDEX_TEST_SECRET", "s3cr3t")
    app = make_app(api=SOURCED, credentials="api_key: K\n")
    command = str(app.curl("me"))
    assert "s3cr3t" not in command and "q=" not in command and "f=" not in command
    with pytest.raises(ConfigError, match=r"\$\{env:CURLDEX_TEST_SECRET\} is not set"):
        app.auth.curl_config()
    problems = [p for p in check(app) if "credentials.yaml may read" in p]
    assert problems == ["error: api.yaml uses ${env:CURLDEX_TEST_SECRET}, ${file:/etc/hostname}: "
                        "only credentials.yaml may read the environment or files; put the value there and use ${key}"]


def test_an_empty_env_or_unreadable_file_is_named(tmp_path, monkeypatch):
    monkeypatch.delenv("CURLDEX_NO_SUCH_VAR", raising=False)
    (tmp_path / "credentials.yaml").write_text("token: ${env:CURLDEX_NO_SUCH_VAR}\n")
    assert Credentials(tmp_path, "t").missing("token").endswith("(empty: ${env:CURLDEX_NO_SUCH_VAR})")
    (tmp_path / "bin.key").write_bytes(b"\xff\xfe secret")
    (tmp_path / "credentials.yaml").write_text("token: ${file:bin.key}\n")
    with pytest.raises(ConfigError, match=r"can't read .*bin\.key as text"):
        Credentials(tmp_path, "t").load()


def test_a_credentials_file_that_isnt_utf8_is_a_clean_error(tmp_path):
    (tmp_path / "credentials.yaml").write_bytes(b"token: \xff\xfe\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        Credentials(tmp_path, "t").load()
