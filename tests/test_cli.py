# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""The curldex command: each subcommand and its exit codes."""

import re
from urllib.parse import parse_qs, urlsplit

import yaml


def test_usage_and_unknown_apps(cli, apps_dir):
    assert cli()[0] == 2
    code, out, _ = cli("--help")
    assert code == 0 and "curldex auth <app>" in out
    code, _, err = cli("nope")
    assert code == 2 and "no app 'nope'" in err
    assert cli("discover")[0] == 2  # argparse: the app is missing
    code, out, _ = cli("help")
    assert code == 0 and "curldex help <app> [api]" in out
    code, out, _ = cli("--version")
    assert code == 0 and re.fullmatch(r"curldex \S+\n", out)


def test_command_words_are_not_app_names(cli, make_app):
    make_app("help")
    code, _, err = cli("help", "help")
    assert code == 2 and "no app 'help'" in err
    assert "help" not in cli("apps")[1]


def test_help_runs_no_other_command(cli, make_app):
    app = make_app()
    for command in ("verify", "docs", "auth", "login", "discover", "check", "apps"):
        code, _, err = cli("help", command, "testapp", "--yes")
        assert code == 2 and "only shows an app's APIs" in err
    assert not app.verified_file.exists() and not app.docs_file.exists()


def test_apps_and_help(cli, make_app, apps_dir):
    make_app()
    (apps_dir / "broken" / "config").mkdir(parents=True)
    (apps_dir / "broken" / "config" / "api.yaml").write_text("commands: [not, a, mapping]\n")
    code, out, _ = cli("apps")
    assert code == 0 and "| testapp " in out and "ready" in out and "| broken " in out and "error:" in out
    code, out, _ = cli("testapp")
    assert code == 0 and "| list-events " in out
    assert cli("help", "testapp") == (code, out, "")  # the read-only form, for permission rules
    for args in (("testapp", "list-events"), ("testapp", "help", "list-events"), ("help", "testapp", "list-events")):
        code, out, _ = cli(*args)
        assert code == 0 and out.startswith("### list-events")
    code, out, _ = cli("testapp", "help")
    assert code == 0 and "### me" in out and "### fail" in out
    code, _, err = cli("testapp", "nope")
    assert code == 2 and "no API 'nope'" in err


def test_auth_prints_the_credentials_file(cli, make_app):
    app = make_app(auth="{type: bearer}", credentials="token: T0K\n")
    code, out, _ = cli("auth", "testapp")
    assert code == 0 and out.strip() == str(app.config_dir / ".curlrc")
    assert 'header = "Authorization: Bearer T0K"' in (app.config_dir / ".curlrc").read_text()
    app.credentials.path.write_text("")
    code, _, err = cli("auth", "testapp")
    assert code == 3 and "missing token in" in err


def test_login_in_two_steps(cli, make_app, fake, oauth):
    import subprocess
    app = make_app(auth=oauth, credentials="client_id: C\nclient_secret: S\n")
    code, out, err = cli("login", "testapp", "--start")
    url = out.strip()
    assert code == 0 and url.startswith(f"{fake.url}/auth?") and "--finish" in err
    subprocess.run(["curl", "-q", "-sS", "-o", "/dev/null", url], check=True)
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    code, out, _ = cli("login", "testapp", "--finish", f"{query['redirect_uri']}/?state={query['state']}&code=CODE1")
    assert code == 0 and "logged in" in out and app.status() == "ready"
    code, _, err = cli("login", "testapp", "--finish", "http://127.0.0.1:1/?state=x&code=y")
    assert code == 3 and "no login in progress" in err


def test_docs_and_verify(cli, make_app, fake):
    app = make_app()
    code, out, _ = cli("docs", "testapp")
    assert code == 0 and out.strip() == str(app.docs_file) and app.docs_file.exists()
    code, out, _ = cli("verify", "testapp", "list-events", "limit=3", "upcoming=false")
    assert code == 0 and "list-events: verified on" in out
    recorded = yaml.safe_load(app.verified_file.read_text())["list-events"]["command"]
    assert "limit=<limit>" in recorded and "upcoming=<upcoming>" in recorded  # given values recorded as stand-ins
    assert "limit=3" not in recorded and fake.last["query"]["limit"] == "3"  # but the call itself used the real value
    assert cli("verify", "testapp")[0] == 1  # /fail answers 500
    code, _, err = cli("verify", "testapp", "me", "oops")
    assert code == 2 and "PARAM=VALUE" in err
    code, _, err = cli("verify", "testapp", "--yes")  # every write example at once: refused
    assert code == 2 and "--yes) needs an API" in err
    assert cli("verify", "testapp", "list-events", "owner=1e5", "limit=2")[0] == 0
    assert fake.last["query"]["owner"] == "1e5" and fake.last["query"]["limit"] == "2"  # a string stays as typed
    make_app("uploader", api="base_url: {url}\nauth: {auth}\ncommands:\n  upload:\n    method: POST\n"
                             "    path: /upload\n    format: multipart\n    params: {{file: {{type: file}}}}\n")
    assert cli("verify", "uploader", "upload", "--yes", f"file={__file__}")[0] == 0  # a path stays text
    assert 'filename="test_cli.py"' in fake.last["body"]
    code, _, err = cli("verify", "testapp", "list-events", "limit=two")
    assert code == 2 and "limit expects integer" in err
    code, out, _ = cli("verify", "testapp", "delete-event")  # a destructive API, without --yes
    assert code == 2 and "skipped" in out
    for argv in (["--yes", "list-events", "limit=4"], ["list-events", "--yes", "limit=4"]):  # any order
        assert cli("verify", "testapp", *argv)[0] == 0 and fake.last["query"]["limit"] == "4"


def test_discover_fills_a_yaml_only_app(cli, apps_dir, fake, pets):
    stubs = {"plain": "base_url: https://x.test\n", "pets": f"source: {{url: '{fake.url}/openapi.json'}}\n"}
    for name, api in stubs.items():
        (apps_dir / name / "config").mkdir(parents=True)
        (apps_dir / name / "config" / "api.yaml").write_text(api)
    code, _, err = cli("discover", "plain")
    assert code == 3 and "Set source:" in err
    code, out, _ = cli("discover", "pets")
    assert code == 0 and "0 of 6" not in out and "6 of 6 APIs" in out and f"base_url: {fake.url}/v1" in out
    assert cli("discover", "pets", "--add", "*")[0] == 0
    code, out, _ = cli("pets")
    assert code == 0 and "| list-pets " in out
    code, out, _ = cli("discover", "pets")
    assert code == 0 and "0 of 6 APIs" in out


def test_an_empty_finish_address_fails_at_once(cli, make_app, oauth):
    make_app(auth=oauth, credentials="client_id: C\n")
    code, _, err = cli("login", "testapp", "--finish", "")
    assert code == 3 and "no login in progress" in err
