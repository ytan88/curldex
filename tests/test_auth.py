# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Auth types: what reaches curl, the docs they give, and the OAuth2 login."""

import base64
import re
import socket
import stat
import subprocess
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from curldex import ConfigError, CurlAuth, HttpError
from curldex.auth import config_line
from curldex.config import Credentials
from curldex.oauth2 import free_port, has_browser, wait_for_redirect


def test_config_lines_are_quoted():
    assert config_line("header", 'A: b"c\\d') == 'header = "A: b\\"c\\\\d"'
    with pytest.raises(ConfigError, match="line break"):
        config_line("header", "A: b\nc")


@pytest.mark.parametrize(("auth", "credentials", "sent"), [
    ("{type: bearer}", "token: T0K\n", ("header", "Authorization", "Bearer T0K")),
    ("{type: basic}", "username: u\npassword: p w\n", ("header", "Authorization", "Basic dTpwIHc=")),
    ("{type: api_key}", "api_key: K1\n", ("header", "X-API-Key", "K1")),
    ("{type: api_key, in: header, name: X-Token}", "api_key: K1\n", ("header", "X-Token", "K1")),
    ("{type: api_key, in: query, name: key}", "api_key: K1\n", ("query", "key", "K1")),
    ("{type: bearer, prefix: Bot}", "token: T0K\n", ("header", "Authorization", "Bot T0K")),
    ("{type: api_key, name: Authorization, prefix: Token}", "api_key: K1\n", ("header", "Authorization", "Token K1")),
])
def test_credentials_reach_the_service_through_the_curl_config_file(make_app, fake, auth, credentials, sent):
    app = make_app(auth=auth, credentials=credentials)
    command = str(app.curl("me"))
    assert "-K" in command and "T0K" not in command and "K1" not in command
    app.run("me")
    where, name, value = sent
    assert (fake.last["headers"] if where == "header" else fake.last["query"])[name] == value
    curlrc = app.config_dir / ".curlrc"
    assert stat.S_IMODE(curlrc.stat().st_mode) == 0o600


CURL_AUTH = """{type: curl, keys: {key: the key},
  config: ['header = "X-Key: ${key}"', 'header = "X-Secret: ${secret}"', 'url-query = "sig=${sig}"']}"""


def test_the_curl_type_sends_whatever_curl_can(make_app, fake, monkeypatch):
    monkeypatch.setenv("CURLDEX_TEST_SIG", "s 1")
    app = make_app(auth=CURL_AUTH, credentials="key: K\nsecret: 'a\"b\\c'\nsig: ${env:CURLDEX_TEST_SIG}\n")
    assert app.auth.keys == {"key": "the key", "secret": "", "sig": ""}
    assert app.auth.plain() == '-H "X-Key: $KEY" -H "X-Secret: $SECRET" --url-query "sig=$SIG"'
    app.run("me")
    assert fake.last["headers"]["X-Key"] == "K" and fake.last["headers"]["X-Secret"] == 'a"b\\c'
    assert fake.last["query"]["sig"] == "s 1"
    app.credentials.path.write_text("key: K\nsig: s\n")
    assert app.status() == f"missing secret in {app.credentials.path}"


@pytest.mark.parametrize(("config", "credentials", "message"), [
    ("['header: X-Key ${key}']", "key: K\n", "looks like `option = \"value\"`"),  # when the app loads
    ("""['header = "X-Key: ${key}" url = "x"']""", "key: K\n", "looks like `option = \"value\"`"),  # a " unescaped
    ("['header = \"X-Key: ${key}\"']", "key: |\n  two\n  lines\n", "'key' has a line break"),  # when curl needs it
])
def test_bad_curl_auth_is_reported(make_app, config, credentials, message):
    with pytest.raises(ConfigError, match=re.escape(message)) as error:
        make_app(auth=f"{{type: curl, config: {config}}}", credentials=credentials).auth_file()
    assert "two" not in str(error.value)


@pytest.mark.parametrize("line", ['url = "https://elsewhere.test/"', "next", 'config = "/tmp/more.curlrc"',
                                  'output = "/tmp/out.txt"', "location-trusted"])
def test_an_auth_config_line_only_sends_credentials(make_app, line):
    """Other curl options could send the credentials to another host (url, next, location-trusted), or
    read other config or write files (config, output); refused as the app loads."""
    with pytest.raises(ConfigError, match=f"can't use curl's {line.split()[0]} option"):
        make_app(auth=f"""{{type: curl, config: ['header = "X-Key: ${{key}}"', '{line}']}}""", credentials="key: K\n")


@pytest.mark.parametrize(("line", "how"), [
    ('header = "@/home/me/.config/gh/hosts.yml"', 'header = "@FILE" sends the lines of a file'),
    ('url-query = "leak@/home/me/.netrc"', 'url-query = "name@FILE" sends a file'),
    ('cookie = "/home/me/cookies.txt"', 'cookie = "FILE", with no "=", sends the cookies in a file'),
    ('cert = "/home/me/client.pem"', 'cert = "FILE" reads a file'),
    ('key = "/home/me/client.key"', 'key = "FILE" reads a file'),
])
def test_an_auth_config_line_cant_read_a_file(make_app, line, how):
    """curl would read the file and send what's in it to the service. Refused as the app loads; a file
    to read is named in credentials.yaml."""
    with pytest.raises(ConfigError, match=re.escape(f"can't make curl read a local file ({how})")):
        make_app(auth=f"{{type: curl, config: ['{line}']}}")


def test_a_backslash_cant_smuggle_a_file_read_past_the_check(make_app):
    r"""curl unescapes \@ to @, so `header = "\@FILE"` would read a file; refused like a bare @."""
    with pytest.raises(ConfigError, match="can't make curl read a local file"):
        make_app(auth=r"""{type: curl, config: ['header = "\@/etc/hostname"']}""")


def test_an_invalid_credentials_file_doesnt_leak_its_contents(make_app):
    """A YAML error in credentials.yaml must not echo the offending (secret) line."""
    app = make_app(auth="{type: bearer}", credentials="token: @Sup3rS3cret!pw\n")  # @ starts a YAML error
    assert "Sup3rS3cret" not in app.status() and "not valid YAML" in app.status()


def test_an_auth_subclass_may_send_its_own_cookie_file(tmp_path):
    """A login's cookie file, which its code names (reference.md's login form), isn't api.yaml's."""
    class FormLogin(CurlAuth):
        def lines(self):
            return [f'cookie = "{self.credentials.path.parent / "cookies.txt"}"']
    assert FormLogin({}, Credentials(tmp_path, "site")).curl_config() == [f'cookie = "{tmp_path / "cookies.txt"}"']


@pytest.mark.parametrize(("auth", "setting"), [
    ('{type: bearer, prefix: "Bearer ${token}\\"\\nurl = \\"https://elsewhere.test/\\"\\nheader = \\"X:"}', "prefix"),
    ('{type: api_key, name: "X-Key: ${api_key}\\"\\noutput = \\"/tmp/out.txt\\"\\nheader = \\"X"}', "name"),
    ('{type: api_key, prefix: "Key\\r\\nnext"}', "prefix"),
])
def test_a_presets_settings_cant_add_a_curl_option(make_app, auth, setting):
    """A line break in prefix or name would start another curl config line: url sends the token to
    another host; output writes a file. Refused as the app loads."""
    with pytest.raises(ConfigError, match=f"auth.{setting} in api.yaml has a line break in it"):
        make_app(auth=auth, credentials="token: T0K\napi_key: K1\n")


def test_a_quote_in_a_presets_setting_stays_in_its_value(make_app, fake):
    app = make_app(auth="""{type: bearer, prefix: 'Token" url = "https://elsewhere.test/'}""",
                   credentials="token: T0K\n")
    app.run("me")
    assert fake.last["headers"]["Authorization"] == 'Token" url = "https://elsewhere.test/ T0K'
    assert len(fake.requests) == 1


@pytest.mark.parametrize(("auth", "message"), [
    ("{type: bearer, prefx: Bot}", "unknown settings in api.yaml's auth: prefx (auth type bearer takes keys, prefix)"),
    ("{type: oauth2, scope: [read]}", "unknown settings in api.yaml's auth: scope (auth type oauth2 takes"),
    ("{type: oauth2_client, authorize_url: https://x.test}", "unknown settings in api.yaml's auth: authorize_url"),
    ("{type: none, config: [basic]}", "unknown settings in api.yaml's auth: config (auth type none takes none)"),
    ("{type: basic, config: [basic]}", "unknown settings in api.yaml's auth: config"),
    ("{type: api_key, in: querry}", "auth.in in api.yaml is header or query, not 'querry'"),
    ("{type: oauth2, token_auth: Basic}", "auth.token_auth in api.yaml can only be basic"),
])
def test_misspelled_auth_settings_are_refused(make_app, auth, message):
    """Ignored, they'd fail without a word: `scope:` would log in with no scopes, `in: querry` send a header."""
    with pytest.raises(ConfigError, match=re.escape(message)):
        make_app(auth=auth)


@pytest.mark.parametrize("config", [  # the examples in the skill's reference.md
    """['header = "DD-API-KEY: ${api_key}"', 'header = "DD-APPLICATION-KEY: ${app_key}"']""",
    """[digest, 'user = "${username}:${password}"']""",
    """['cert = "${cert_file}"', 'key = "${key_file}"']""",
    """['aws-sigv4 = "aws:amz:${region}:execute-api"', 'user = "${access_key_id}:${secret_access_key}"']""",
    """['header = "Cookie: ${cookie}"']""",
    """'header = "X-Key: ${key}"'""",  # one line alone
    """['cookie = "${cookie_file}"', 'cookie = "sid=${sid}"', 'url-query = "key=${key}"']""",  # files the user names
])
def test_the_curl_type_takes_what_sends_credentials(make_app, config):
    assert make_app(auth=f"{{type: curl, config: {config}}}").auth.lines()


def test_status_names_what_is_missing(make_app):
    app = make_app(auth="{type: basic}", credentials="username: u\n")
    assert app.status() == f"missing password in {app.credentials.path}"


def test_describe_shows_how_without_any_secret(make_app):
    text = make_app(auth="{type: bearer}", credentials="token: SECRET\n").auth.describe()
    assert '-H "Authorization: Bearer $TOKEN"' in text and "SECRET" not in text
    assert '-K "$(curldex auth testapp)"' in text


def test_a_service_without_credentials_gets_no_k_option(make_app):
    app = make_app()
    assert "-K" not in str(app.curl("me"))
    assert "needs no credentials" in app.auth.describe()


# ----- OAuth2 ------------------------------------------------------------------------------------

def test_oauth2_login_in_two_steps(make_app, fake, oauth):
    app = make_app(auth=oauth, credentials="client_id: C\nclient_secret: S\n")
    assert app.status() == "login required: run curldex login testapp (in Python: app.login())"
    url = app.auth.login_start()
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith(f"{fake.url}/auth?")
    assert query["code_challenge_method"] == "S256" and query["scope"] == "calendar" and query["client_id"] == "C"
    with pytest.raises(ConfigError, match="another login attempt"):
        app.auth.login_finish(f"{query['redirect_uri']}/?state=wrong&code=CODE1")
    with pytest.raises(ConfigError, match="that isn't the whole address"):  # only the code, say
        app.auth.login_finish("CODE1")
    subprocess.run(["curl", "-q", "-sS", "-o", "/dev/null", url], check=True)  # the user approves in the browser
    app.auth.login_finish(f"{query['redirect_uri']}/?state={query['state']}&code=CODE1")
    assert app.status() == "ready"
    app.run("me")
    assert fake.last["headers"]["Authorization"] == "Bearer AT1"
    assert "pending_login" not in app.credentials.state()


def test_oauth2_interactive_login_receives_the_redirect(make_app, fake, oauth, monkeypatch, capsys):
    app = make_app(auth=oauth, credentials="client_id: C\nclient_secret: S\n")

    def browser(url):  # approves at once: the fake consent page redirects to curldex's listener
        threading.Thread(target=lambda: urllib.request.urlopen(url, timeout=5).read(), daemon=True).start()
    monkeypatch.setattr("webbrowser.open", browser)
    monkeypatch.setenv("DISPLAY", ":0")
    app.login()
    assert "logged in" in capsys.readouterr().out
    assert app.credentials.state()["access_token"] == "AT1"


def test_a_login_the_browser_never_finishes_ends(make_app, oauth, monkeypatch):
    app = make_app(auth=oauth, credentials="client_id: C\n")
    monkeypatch.setattr("curldex.oauth2.LOGIN_TIMEOUT", 0.2)
    with pytest.raises(ConfigError, match=r"no answer from the browser; .* --start`, then --finish ADDRESS"):
        app.login(open_browser=False)


def test_a_degenerate_rsa_key_is_a_clean_error():
    """A key whose modulus is 0 would crash pow(); it's refused as not-an-RSA-key, not a traceback."""
    import base64

    from curldex.shared.google import rs256
    der = bytes.fromhex("300c020100020100020101020102")  # PKCS#1 SEQ: version 0, n 0, e 1, d 2
    pem = "-----BEGIN RSA PRIVATE KEY-----\n" + base64.encodebytes(der).decode() + "-----END RSA PRIVATE KEY-----"
    with pytest.raises(ConfigError, match="isn't an RSA key"):
        rs256(b"msg", pem)


def test_token_params_cant_override_curldex_grant_fields(make_app, fake):
    """token_params are extras: curldex's grant_type and scope still win."""
    app = make_app(auth=f'{{type: oauth2_client, token_url: "{fake.url}/token", scopes: [read], '
                        'token_params: {grant_type: password}}', credentials="client_id: C\nclient_secret: S\n")
    app.run("me")
    token_call = next(r for r in fake.requests if r["path"] == "/token")
    assert "grant_type=client_credentials" in token_call["body"] and "grant_type=password" not in token_call["body"]


def test_an_idle_connection_doesnt_hold_up_the_login(monkeypatch):
    """Browsers often open a spare connection that sends nothing; the redirect on the next one lands."""
    monkeypatch.setattr("curldex.oauth2.REQUEST_TIMEOUT", 0.2)
    port = free_port()
    landing = f"http://127.0.0.1:{port}/?state=s&code=c"
    spare: list[socket.socket] = []

    def browser():
        spare.append(socket.create_connection(("127.0.0.1", port)))  # connects first, and stays silent
        closer = threading.Timer(3, spare[0].close)  # without the timeout, the login would wait for this
        closer.daemon = True
        closer.start()
        threading.Thread(target=lambda: urllib.request.urlopen(landing, timeout=5).read(), daemon=True).start()
    started = time.monotonic()
    assert wait_for_redirect(f"http://127.0.0.1:{port}", browser, timeout=5) == landing
    assert time.monotonic() - started < 2
    spare[0].close()


def test_scopes_may_be_one_text(make_app, oauth):
    app = make_app(auth=oauth.replace("[calendar]", "https://example.com/auth/calendar"), credentials="client_id: C\n")
    assert app.auth.scopes == ["https://example.com/auth/calendar"]  # not one scope per character


def test_a_browser_is_opened_only_with_a_display(monkeypatch):
    """Without one, webbrowser may run a text browser (lynx) in the terminal and block the login."""
    monkeypatch.setattr("sys.platform", "linux")
    for name in ("BROWSER", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    assert not has_browser()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert has_browser()


def test_a_google_app_keeps_offline_access_when_it_adds_an_authorize_param(make_app):
    """An app's authorize_params merge onto, not replace, a family's (access_type=offline)."""
    from urllib.parse import parse_qs, urlsplit

    from curldex.shared.google import GoogleApp

    class G(GoogleApp):
        pass
    config = make_app(auth="{type: oauth2, scopes: [x], authorize_params: {hd: example.com}}",
                      credentials="client_id: C\n").config_dir
    q = parse_qs(urlsplit(G(config.parent).auth.login_start()).query)
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and q["hd"] == ["example.com"]


@pytest.mark.parametrize("answer", [
    {"access_token": "A1"},  # no refresh token (GitHub): logged in while the token lasts
    {"access_token": "A1", "scope": "repo,gist"},  # comma-separated
    {"access_token": "A1", "scope": ["user:email"]},  # scopes renamed or left out: still ready
    {"access_token": "A1", "expires_in": "soon"},  # a non-numeric expiry: no crash
])
def test_oauth2_handles_non_google_token_responses(make_app, oauth, monkeypatch, answer):
    app = make_app(auth=oauth.replace("[calendar]", "[repo, gist]"), credentials="client_id: C\n")
    app.credentials.save(expires_at=1)  # from an earlier login: a new token doesn't inherit it
    monkeypatch.setattr("curldex.oauth2.post_form", lambda *a, **k: answer)
    app.auth.credentials.save(pending_login={"state": "s", "verifier": "v", "redirect_uri": "u"})
    app.auth.login_finish("http://127.0.0.1/?state=s&code=c")
    assert app.status() == "ready"


def test_a_token_error_sent_with_200_keeps_its_message(monkeypatch):
    import io

    from curldex import HttpError, oauth2
    body = b'{"error": "bad_verification_code", "error_description": "The code is incorrect."}'
    monkeypatch.setattr(oauth2._token_opener, "open", lambda *a, **k: io.BytesIO(body))
    with pytest.raises(HttpError, match=r"without an access_token .bad_verification_code: The code is incorrect"):
        oauth2.post_form("https://x.test/token", {"grant_type": "x"})


def test_a_refused_refresh_ends_the_login(make_app, oauth, monkeypatch):
    from curldex import HttpError
    app = make_app(auth=oauth, credentials="client_id: C\n")
    app.credentials.save(refresh_token="RT", access_token="old", expires_at=1)

    def refused(*a, **k):
        raise HttpError(400, '{"error": "invalid_grant"}')
    monkeypatch.setattr("curldex.oauth2.post_form", refused)
    with pytest.raises(ConfigError, match="the login expired or was revoked"):
        app.auth.access_token()
    assert app.status().startswith("login required")


def test_a_busy_redirect_port_is_a_clean_error():
    import socket

    from curldex.oauth2 import wait_for_redirect
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        with pytest.raises(ConfigError, match=r"can't listen on 127.0.0.1"):
            wait_for_redirect(f"http://127.0.0.1:{busy.getsockname()[1]}", timeout=1)


def test_a_number_like_credential_must_be_quoted(make_app):
    app = make_app(auth="{type: bearer}", credentials="token: 1234567890123.4567890123456\n")
    with pytest.raises(ConfigError, match=r"token in .* reads as a number; put it in quotes"):
        app.auth_file()


def test_a_non_json_token_response_is_a_clean_error(monkeypatch):
    """An HTML answer with status 200 from the token endpoint is a clean HttpError, not a JSONDecodeError."""
    import io

    from curldex import HttpError, oauth2

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(oauth2._token_opener, "open", lambda *a, **k: Resp(b"<html>maintenance</html>"))
    with pytest.raises(HttpError, match="without an access_token"):
        oauth2.post_form("https://x.test/token", {"grant_type": "x"})


def test_token_basic_auth_is_form_encoded(make_app, fake):
    """RFC 6749 §2.3.1 - the client id and secret are form-encoded before base64."""
    import base64
    from urllib.parse import quote_plus
    app = make_app(auth=f'{{type: oauth2_client, token_url: "{fake.url}/token", token_auth: basic}}',
                   credentials="client_id: C\nclient_secret: 'a+b/c:d'\n")
    with pytest.raises(HttpError):  # the fake rejects this client; we only care what header was sent
        app.auth._token({"grant_type": "client_credentials"})
    sent = next(r for r in fake.requests if r["path"] == "/token")["headers"]["Authorization"]
    expected = "Basic " + base64.b64encode(f"{quote_plus('C')}:{quote_plus('a+b/c:d')}".encode()).decode()
    assert sent == expected


def test_oauth2_refreshes_an_expired_token(make_app, fake, oauth):
    app = make_app(auth=oauth, credentials="client_id: C\nclient_secret: S\n")
    app.credentials.save(access_token="OLD", refresh_token="RT1", expires_at=time.time() - 1)
    app.run("me")
    assert fake.last["headers"]["Authorization"] == "Bearer AT2"
    assert app.credentials.state()["refresh_token"] == "RT1"  # kept: the refresh returned no new one


def test_oauth2_says_when_the_login_expired(make_app, oauth):
    app = make_app(auth=oauth, credentials="client_id: C\nclient_secret: S\n")
    app.credentials.save(refresh_token="REVOKED", expires_at=0)
    with pytest.raises(ConfigError, match=r"the login expired or was revoked; run curldex login testapp \(in Py"):
        app.auth_file()


def test_oauth2_asks_for_a_new_login_when_scopes_change(make_app, oauth):
    app = make_app(auth=oauth.replace("[calendar]", "[calendar, mail]"), credentials="client_id: C\n")
    app.credentials.save(refresh_token="RT1", login_scopes=["calendar"])
    assert app.status() == "login required: run curldex login testapp (in Python: app.login()); for new scopes: mail"


def test_oauth2_describes_the_raw_token_calls(make_app, fake, oauth):
    text = make_app(auth=oauth, credentials="client_id: C\nclient_secret: s3cr3t-v4lue\n").auth.describe()
    assert f"{fake.url}/token" in text and "grant_type=refresh_token" in text and "code_challenge=$CHALLENGE" in text
    assert "s3cr3t-v4lue" not in text and "curldex login testapp --start" in text


@pytest.mark.parametrize("token_auth", ["", ", token_auth: basic"])
def test_oauth2_client_credentials(make_app, fake, token_auth):
    auth = (f'{{type: oauth2_client, token_url: "{fake.url}/token", scopes: [read], '
            f'token_params: {{audience: api}}{token_auth}}}')
    app = make_app(auth=auth, credentials="client_id: C\nclient_secret: S\n")
    assert app.status() == "ready" and not app.auth.LOGIN
    assert "app.login()" not in app.docs()  # API.md doesn't ask for a login there is none of
    app.run("me")
    assert fake.last["headers"]["Authorization"] == "Bearer CC1"
    token_call = next(r for r in fake.requests if r["path"] == "/token")
    assert ("Basic " in token_call["headers"].get("Authorization", "")) == bool(token_auth)
    assert "grant_type=client_credentials" in token_call["body"] and "scope=read" in token_call["body"]
    assert "audience=api" in token_call["body"]
    app.run("me")
    assert [r["path"] for r in fake.requests].count("/token") == 1  # the token is kept until it expires
    assert "grant_type=client_credentials" in app.auth.describe() and "s3cr" not in app.auth.describe()


def test_numeric_client_ids_and_client_credentials_expiry(make_app, monkeypatch):
    sent = {}
    monkeypatch.setattr("curldex.oauth2.post_form", lambda url, form, headers: sent.update(headers) or
                        {"access_token": "CC"})  # no expires_in
    app = make_app(auth='{type: oauth2_client, token_url: "https://x.test/token", token_auth: basic}',
                   credentials="client_id: 12345\nclient_secret: 678\n")
    app.auth_file()  # a number from YAML doesn't crash the Basic header
    assert sent["Authorization"] == "Basic " + base64.b64encode(b"12345:678").decode()
    assert app.credentials.state()["expires_at"]  # a client-credentials token is got again, not kept forever


def test_a_revoked_seeded_refresh_token_stays_revoked(make_app, oauth, monkeypatch):
    from curldex import HttpError
    app = make_app(auth=oauth, credentials="client_id: C\nrefresh_token: DEAD\n")

    def refused(*a, **k):
        raise HttpError(400, '{"error": "invalid_grant"}')
    monkeypatch.setattr("curldex.oauth2.post_form", refused)
    with pytest.raises(ConfigError):
        app.auth.access_token()
    assert app.status().startswith("login required")


def test_an_api_key_name_must_be_a_header_name(make_app):
    with pytest.raises(ConfigError, match=r"auth.name in api.yaml: a header name is"):  # "@x": curl reads a file
        make_app(auth='{type: api_key, name: "@x"}', credentials="api_key: K\n")


def test_a_login_without_a_browser_step_says_how_to_log_in(make_app):
    class FormLogin(CurlAuth):
        KIND, LOGIN = "form_login", True
    app = make_app(auth="{type: bearer}", credentials="token: T\n")
    with pytest.raises(ConfigError, match=r"no browser login .form_login auth.; run curldex login testapp"):
        FormLogin({}, app.credentials).login_finish("http://x")
    with pytest.raises(ConfigError, match=r"no browser login .bearer auth.; no login needed"):
        app.auth.login_start()


def test_a_new_login_replaces_an_old_refresh_token(make_app, oauth, monkeypatch):
    app = make_app(auth=oauth, credentials="client_id: C\n")
    app.credentials.save(refresh_token="OLD", pending_login={"state": "s", "verifier": "v", "redirect_uri": "u"})
    monkeypatch.setattr("curldex.oauth2.post_form", lambda *a, **k: {"access_token": "NEW", "expires_in": 3600})
    app.auth.login_finish("http://127.0.0.1/?state=s&code=c")
    assert not app.credentials.load().get("refresh_token")  # never refresh as the earlier login


def test_concurrent_auth_runs_refresh_once(make_app, fake, oauth):
    """The lock: two `curldex auth` at once must not both spend a refresh token a provider may rotate."""
    import sys
    app = make_app(auth=oauth, credentials="client_id: C\nclient_secret: S\n")
    app.credentials.save(access_token="OLD", refresh_token="RT1", expires_at=time.time() - 10,
                         login_scopes=["calendar"])
    runs = [subprocess.Popen([sys.executable, "-m", "curldex", "auth", "testapp"], stdout=subprocess.DEVNULL)
            for _ in range(6)]
    assert all(run.wait() == 0 for run in runs)
    assert len([r for r in fake.requests if r["path"] == "/token"]) == 1


def test_auth_says_what_status_says(make_app, oauth):
    app = make_app(auth=oauth, credentials="")
    with pytest.raises(ConfigError, match="testapp: missing client_id in "):  # not "log in": that would fail too
        app.auth_file()
    (app.config_dir / "credentials.yaml").write_text("client_id: C\n")
    with pytest.raises(ConfigError, match="testapp: login required: run curldex login testapp"):
        app.auth_file()


def test_a_bad_paste_is_a_clean_error(make_app, oauth):
    app = make_app(auth=oauth, credentials="client_id: C\n")
    app.auth.login_start()
    with pytest.raises(ConfigError, match="that isn't the whole address"):
        app.auth.login_finish("http://[broken")


def test_an_auth_line_cant_escape_a_line_break(make_app):
    with pytest.raises(ConfigError, match=r"can't hold \\n or \\r"):
        make_app(auth="""{type: curl, config: ['header = "X-A: a\\r\\nX-Injected: yes"']}""")
    make_app(name="ok", auth="""{type: curl, config: ['header = "X-A: a\\\\r"']}""")  # an escaped \\: a backslash


def test_client_credentials_token_follows_the_client(make_app, fake):
    app = make_app(auth=f'{{type: oauth2_client, token_url: "{fake.url}/token"}}',
                   credentials="client_id: C\nclient_secret: S\n")
    app.auth_file()
    app.credentials.save(access_token="KEEP")  # cached for C
    assert "KEEP" in app.auth_file().read_text()
    (app.config_dir / "credentials.yaml").write_text("client_id: OTHER\nclient_secret: S\n")
    with pytest.raises(HttpError, match="answered HTTP 400"):  # a new token for the new client (the fake refuses it)
        app.auth_file()
