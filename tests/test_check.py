# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""`curldex check`: problems in an app package, found without calling the service."""

from curldex import WebApp
from curldex.check import check

API = """\
description: Demo
base_url: {url}
auth: {{type: bearer}}
headers: {{X-Account: "${{account}}"}}
commands:
  list-things:
    description: List things
    path: /things
  get-thing:
    description: Get a thing
    path: /things/{{id}}
  search-things:
    description: Search things
    method: POST
    path: /things/search
"""


def demo(apps_dir, fake, api=API, template='token: ""\naccount: ""\n'):
    """A YAML-only app: config/ and nothing else."""
    folder = apps_dir / "demo"
    (folder / "config").mkdir(parents=True)
    (folder / "config" / "api.yaml").write_text(api.format(url=fake.url))
    if template is not None:
        (folder / "config" / "credentials.example.yaml").write_text(template)
    return folder


def test_a_good_package_has_only_the_expected_warnings(cli, apps_dir, fake):
    demo(apps_dir, fake)
    assert cli("docs", "demo")[0] == 0
    code, out, _ = cli("check", "demo")
    assert code == 0 and "0 errors" in out
    assert "search-things: named like a read; if it only reads, set access: read" in out
    assert "3 of 3 APIs not verified" in out


def test_problems_are_reported(cli, apps_dir, fake):
    api = API.replace("path: /things/search", "path: /things/search\n    format: raw").replace(
        "path: /things/{{id}}", "path: /things/{{id}}\n    example: {{key: 1}}")
    demo(apps_dir, fake, api=api, template="account: ''\n")
    code, out, _ = cli("check", "demo")
    assert code == 3
    for problem in ["error: credentials.example.yaml lacks token",
                    "error: search-things: format raw needs a param with in: json",
                    "error: get-thing: its example doesn't fit: get-thing: unknown params key",
                    "warning: API.md is missing or out of date"]:
        assert problem in out, problem


def test_a_credential_of_the_auth_elsewhere_in_api_yaml_is_refused(cli, apps_dir, fake):
    """In a header (or base_url, or a default) it would appear in every curl command."""
    demo(apps_dir, fake, api=API.replace('X-Account: "${{account}}"', 'X-Token: "${{token}}"'))
    code, _, err = cli("check", "demo")
    assert code == 3 and "demo: api.yaml's base_url, headers or a param default uses token, a credential," in err


def test_check_a_plain_folder(make_app):
    app = make_app()
    problems = check(app)
    assert not any("package" in p for p in problems)  # an app needs no code
    assert any(p.startswith("error: no config/credentials.example.yaml") for p in problems)


def test_an_unchecked_certificate_and_a_credentials_file_others_can_read_are_flagged(make_app):
    app = make_app(api="base_url: {url}\nauth: {auth}\ncurl_options: [-k]\ncommands: {{me: {{path: /me}}}}\n")
    app.credentials.path.chmod(0o644)
    problems = check(app)
    assert any(p.startswith("warning: curl_options has -k, so curl doesn't check the service's certificate")
               for p in problems)
    assert f"warning: {app.credentials.path} can be read by others: chmod 600 it" in problems
    app.credentials.path.chmod(0o600)
    assert not any("read by others" in p for p in check(app))


def test_names_and_bodies_are_checked(make_app):
    api = ("base_url: {url}\nauth: {auth}\ncommands:\n  ListThings: {{description: x, path: /things}}\n"
           "  things-delete: {{description: x, method: POST, path: /things/delete}}\n")
    problems = check(make_app(api=api))
    assert "error: ListThings: an API name is lowercase words joined by '-'" in problems
    assert "warning: things-delete: named like a delete; if it deletes data, set access: destructive" in problems


def test_a_body_param_must_fit_its_format_at_load(make_app):
    """A file only in a multipart field or a raw body, and no whole-body param in multipart: anything else
    would send a path as text, or crash help/docs."""
    import pytest

    from curldex import ConfigError
    tmpl = ("base_url: {url}\nauth: {auth}\ncommands:\n"
            "  upload: {{description: x, method: POST, path: /up, format: FMT, params: {{doc: PARAM}}}}\n")
    for fmt, param, message in [("form", "{{type: file}}", "a file is an in: body field of a multipart body"),
                                ("json", "{{type: file}}", "a file is an in: body field"),
                                ("raw", "{{type: file}}", "a file is an in: body field"),
                                ("multipart", "{{in: json, type: string}}", "a multipart body takes in: body fields")]:
        with pytest.raises(ConfigError, match=message):
            make_app(name=f"up{fmt}", api=tmpl.replace("FMT", fmt).replace("PARAM", param))


def test_check_an_app_without_api_yaml(apps_dir):
    (apps_dir / "newapp" / "config").mkdir(parents=True)
    assert check(WebApp(apps_dir / "newapp")) == [
        "error: newapp: no config/api.yaml yet; write it (see the skill curldex-new-app), then "
        "`curldex discover newapp` lists the APIs its source: describes"]


def test_an_oauth_app_without_its_urls_and_a_filled_template_are_flagged(make_app):
    app = make_app(auth="{type: oauth2, scopes: [repo]}", credentials="client_id: C\n")
    (app.config_dir / "credentials.example.yaml").write_text('client_id: "abc123"\nclient_secret: ""\n')
    problems = check(app)
    assert "error: auth.authorize_url isn't set in api.yaml" in problems
    assert any("credentials.example.yaml, which is committed, has a value for client_id" in p for p in problems)


def test_a_literal_credential_in_api_yaml_is_an_error(make_app):
    app = make_app(api="base_url: {url}\nauth: {auth}\nheaders: {{Authorization: 'Bearer sk_live_1'}}\ncommands:\n"
                       "  me: {{path: /me, description: x, params: {{api_key: {{default: AIza1, required: true}}}}}}\n")
    assert "error: api.yaml gives Authorization, me.api_key a value, but each is named like a credential, which goes " \
           "in the auth" in check(app)
