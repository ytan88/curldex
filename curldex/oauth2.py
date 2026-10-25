# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""OAuth 2.0: an access token from the service's token URL, kept in .state.json until it expires.

    auth:
      type: oauth2
      authorize_url: https://example.com/oauth/authorize
      token_url: https://example.com/oauth/token
      scopes: [read, write]
      authorize_params: {access_type: offline}     # optional: more authorize URL params
      redirect_uri: http://127.0.0.1:8765          # optional: if the provider wants a registered one
      token_auth: basic                            # optional: client ID and secret in a Basic header
      token_params: {audience: https://api.example.com}   # optional: more token request fields

OAuth2Auth (`type: oauth2`) is the authorization-code login with PKCE. login() opens the authorize
URL. After the user allows access, the browser goes to http://127.0.0.1:PORT/?code=..., which
curldex receives, or which the user pastes. login_start() and login_finish(address) are the same
login in two steps, so someone else, such as Claude, can run it while the user only approves in the
browser. The access token is refreshed when it expires.

OAuth2ClientAuth (`type: oauth2_client`: token_url, scopes, token_auth, token_params) is the
client-credentials grant: the app signs in as itself with its client ID and secret, so there is no
browser and no login step.

Both build on OAuth2Base. The token calls use the standard library.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import secrets
import select
import shlex
import socket
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from abc import abstractmethod
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit

from .auth import Auth, config_line
from .config import ConfigError, Credentials, HttpError, error_summary, mapping, texts

LOGIN_TIMEOUT = 600  # seconds login() waits for the browser, or for an address pasted
REQUEST_TIMEOUT = 2  # seconds a connection to the login's listener has to send its request


def valid_token(creds: dict) -> str | None:
    """A saved access token if it's still usable: present, and not past its expiry if one was recorded
    (a token with no recorded expiry is treated as long-lived)."""
    token = creds.get("access_token")
    if not token:
        return None
    if (expires_at := creds.get("expires_at")) is None:
        return token
    try:
        return token if float(expires_at) > time.time() + 60 else None
    except (TypeError, ValueError):
        return None


def lifetime(token: dict) -> float:
    """How many seconds a token answer's access token lasts: its expires_in, else an hour."""
    try:
        return float(token.get("expires_in") or 3600)
    except (TypeError, ValueError):
        return 3600


class OAuth2Base(Auth):
    """What the two grants share: the settings, the scopes, and an access token from the token URL,
    kept until it expires. A subclass says how to get a new one."""

    KEYS: ClassVar[dict[str, str]] = {"client_id": "the OAuth client ID", "client_secret": "the OAuth client secret"}
    TOKENS: ClassVar[tuple[str, ...]] = ("access_token", "refresh_token")
    SETTINGS: ClassVar[dict] = {}  # defaults for api.yaml's auth settings, e.g. a provider's URLs
    SETTING_KEYS = Auth.SETTING_KEYS | {"token_url", "scopes", "token_auth", "token_params"}

    def __init__(self, settings: dict, credentials: Credentials):
        super().__init__({**self.SETTINGS, **settings}, credentials)
        self.settings["scopes"] = texts(self.settings.get("scopes"), f"{self.app}: auth.scopes in api.yaml",
                                        "[https://example.com/auth/read]")
        for key in ("authorize_params", "token_params"):  # merge onto the class defaults, don't replace them
            given = mapping(settings.get(key), f"{self.app}: auth.{key} in api.yaml", "{access_type: offline}")
            self.settings[key] = {**self.SETTINGS.get(key, {}), **given}
        if self.settings.get("token_auth") not in (None, "", "basic"):
            raise ConfigError(f"{self.app}: auth.token_auth in api.yaml can only be basic (the client ID and secret "
                              f"in a Basic header), not {self.settings['token_auth']!r}")

    @property
    def scopes(self) -> list[str]:
        return self.settings["scopes"]

    def setting(self, key: str) -> str:
        if not self.settings.get(key):
            raise ConfigError(f"{self.app}: set auth.{key} in api.yaml")
        return self.settings[key]

    def access_token(self) -> str:
        """A valid access token: the saved one, or a new one if it has (nearly) expired."""
        if token := valid_token(self.credentials.load()):
            return token
        with self.credentials.lock():  # only one process refreshes; re-read in case another just did
            creds = self.credentials.load()
            return valid_token(creds) or self.new_token(creds)

    @abstractmethod
    def new_token(self, creds: dict) -> str:
        """Get a new access token from the token URL, with _token()."""

    def curl_config(self) -> list[str]:
        return [config_line("header", f"Authorization: Bearer {self.access_token()}")]

    def plain(self) -> str:
        return '-H "Authorization: Bearer $ACCESS_TOKEN"'

    def _token(self, form: dict) -> str:
        """Call the token URL; save the tokens and return the access token."""
        client_id = str(self.credentials.require("client_id")[0])  # str: YAML reads 12345 as a number
        secret = str(self.credentials.get("client_secret") or "")
        form = {**self.settings["token_params"], **form}  # curldex's grant_type/code/scope win
        headers = {}
        if self.settings.get("token_auth") == "basic":  # RFC 6749 §2.3.1: form-encode id and secret first
            pair = f"{quote_plus(client_id)}:{quote_plus(secret)}"
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        else:
            form.update(client_id=client_id, **({"client_secret": secret} if secret else {}))
        token = post_form(self.setting("token_url"), form, headers)
        login = form["grant_type"] == "authorization_code"
        # with no expires_in, a login's token lives until it's revoked (GitHub's), unless it can be refreshed; a
        # refreshed or client-credentials token gets the default lifetime, as getting another is cheap
        expires = "expires_in" in token or token.get("refresh_token") or not login
        saved: dict[str, Any] = {"access_token": token["access_token"],
                                 "expires_at": time.time() + lifetime(token) if expires else None}
        if login or token.get("refresh_token"):  # a refresh usually returns none (keep the old); a login replaces it
            saved["refresh_token"] = token.get("refresh_token") or ""  # "" also hides one seeded in credentials.yaml
        self.credentials.save(**saved)
        return token["access_token"]

    def _client_options(self) -> str:
        """How the token calls send the client ID and secret, and token_params, as curl options (for
        describe())."""
        params = self.settings["token_params"]
        extra = "".join(f" --data-urlencode {shlex.quote(f'{k}={v}')}" for k, v in params.items())
        if self.settings.get("token_auth") == "basic":
            return '-u "$CLIENT_ID:$CLIENT_SECRET"' + extra
        return '--data-urlencode client_id="$CLIENT_ID" --data-urlencode client_secret="$CLIENT_SECRET"' + extra


class OAuth2Auth(OAuth2Base):
    """The authorization-code login with PKCE; the access token is refreshed when it expires."""

    KIND = "oauth2"
    LOGIN = True
    OPTIONAL: ClassVar[tuple[str, ...]] = ("client_secret",)  # a public client (PKCE only) has none
    SETTING_KEYS = OAuth2Base.SETTING_KEYS | {"authorize_url", "authorize_params", "redirect_uri"}

    @property
    def how_to_log_in(self) -> str:
        return f"run curldex login {self.app} (in Python: app.login())"

    def status(self) -> str:
        status = super().status()
        if status != "ready":
            return status
        creds = self.credentials.load()
        # logged in if there's a refresh token (we can renew) or a still-valid access token (a provider
        # that issues no refresh token, such as GitHub; it stays usable until it expires or is revoked)
        if not creds.get("refresh_token") and not valid_token(creds):
            return f"login required: {self.how_to_log_in}"
        # the scopes asked for at the login (not those the token answer lists: providers rename or omit some)
        if new := [s for s in self.scopes if s not in creds.get("login_scopes", self.scopes)]:
            return f"login required: {self.how_to_log_in}; for new scopes: {' '.join(new)}"
        return "ready"

    def new_token(self, creds: dict) -> str:
        if not creds.get("refresh_token"):  # nothing to renew with: say what status() says (missing keys, a login)
            raise ConfigError(f"{self.app}: {self.status()}")
        return self._token({"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]})

    def _token(self, form: dict) -> str:
        try:
            return super()._token(form)
        except HttpError as e:
            if "invalid_grant" not in str(e.body):
                raise
            if form["grant_type"] != "refresh_token":
                raise ConfigError(f"{self.app}: the login code expired or was already used; {self.how_to_log_in}") \
                    from None
            # the login is dead, so status() says so too; "" also hides a refresh token seeded in credentials.yaml
            self.credentials.save(access_token=None, refresh_token="", expires_at=None)
            raise ConfigError(f"{self.app}: the login expired or was revoked; {self.how_to_log_in}") from None

    def login(self, open_browser: bool = True) -> None:
        """Log in: open the authorize URL, then wait for the browser's redirect or a pasted address."""
        url = self.login_start()
        print(f"Open this URL, sign in and allow access:\n\n  {url}\n\n"
              "If the browser then says it can't connect, copy the whole address from its address bar\n"
              "and paste it here.", flush=True)
        browser = (lambda: webbrowser.open(url)) if open_browser and has_browser() else (lambda: None)
        address = wait_for_redirect(self.credentials.state()["pending_login"]["redirect_uri"], browser, LOGIN_TIMEOUT)
        if address is None:
            raise ConfigError(f"{self.app}: no answer from the browser; log in again, or in two steps: "
                              f"`curldex login {self.app} --start`, then --finish ADDRESS")
        self.login_finish(address)
        print(f"{self.app}: logged in; tokens saved in {self.credentials.state_path}")

    def login_start(self) -> str:
        (client_id,) = self.credentials.require("client_id")
        authorize_url = self.setting("authorize_url")  # before anything is saved
        verifier = secrets.token_urlsafe(64)
        pending = {"state": secrets.token_urlsafe(16), "verifier": verifier,
                   "redirect_uri": self.settings.get("redirect_uri") or f"http://127.0.0.1:{free_port()}"}
        self.credentials.save(pending_login=pending)
        return authorize_url + "?" + urlencode({
            **self.settings["authorize_params"],  # the app's extras, then the fields curldex controls
            "client_id": client_id, "redirect_uri": pending["redirect_uri"], "response_type": "code",
            "scope": " ".join(self.scopes), "state": pending["state"],
            "code_challenge": pkce_challenge(verifier), "code_challenge_method": "S256"})

    def login_finish(self, address: str) -> None:
        pending = self.credentials.state().get("pending_login")
        if not pending:
            raise ConfigError(f"{self.app}: no login in progress; run `curldex login {self.app} --start` first")
        address = address.strip()
        if len(address) >= 2 and address[0] == address[-1] and address[0] in "'\"":  # a paste with its quotes
            address = address[1:-1].strip()
        query = dict(parse_qsl(address.partition("?")[2].partition("#")[0]))  # no URL parsing to fail on a bad paste
        if "error" in query:
            raise ConfigError(f"{self.app}: the login was refused: {query['error']}")
        if "state" not in query:
            raise ConfigError(f"{self.app}: that isn't the whole address; paste all of it, from the browser's "
                              "address bar")
        if query["state"] != pending["state"]:
            raise ConfigError(f"{self.app}: that address is from another login attempt; start again")
        if not query.get("code"):
            raise ConfigError(f"{self.app}: no code= in that address; paste the whole address from the browser")
        self._token({"grant_type": "authorization_code", "code": query["code"],
                     "redirect_uri": pending["redirect_uri"], "code_verifier": pending["verifier"]})
        self.credentials.save(pending_login=None, login_scopes=self.scopes)

    def describe(self) -> str:
        authorize = self.settings.get("authorize_url") or "AUTHORIZE_URL"
        token = self.settings.get("token_url") or "TOKEN_URL"
        redirect = self.settings.get("redirect_uri") or "http://127.0.0.1:PORT"
        query = urlencode({"redirect_uri": redirect, "response_type": "code", "scope": " ".join(self.scopes),
                           "code_challenge_method": "S256", **self.settings["authorize_params"]})
        client = self._client_options()
        scopes = ", ".join(f"`{s}`" for s in self.scopes) or "none"
        return super().describe() + f"""
**Login:** OAuth 2.0 authorization code with PKCE. Tokens are kept in `config/.state.json` (owner-only),
and the access token is refreshed when it expires.

- Authorize URL: {authorize}
- Token URL: {token}
- Scopes: {scopes}
- Log in once with `curldex login {self.app}` (in IPython, `app.login()`). It opens the authorize URL.
  After you allow access, the browser goes to `{redirect}` with `?code=...` added: curldex receives it, or you
  paste that address (the page may say it can't connect; that's expected).
- Or in two steps, for example when Claude runs them and you approve in the browser:
  `curldex login {self.app} --start` prints the URL, and
  `curldex login {self.app} --finish "ADDRESS"` completes the login with the address the browser landed on.

The same login with plain curl. VERIFIER is a random string; CHALLENGE is base64url(sha256(VERIFIER))
without `=` padding:

```bash
# 1. Open this in a browser, allow access, and copy code= from the address it lands on
{authorize}?client_id=$CLIENT_ID&code_challenge=$CHALLENGE&{query}
# 2. Exchange the code for tokens
curl -sS {token} -d grant_type=authorization_code --data-urlencode code="$CODE" \\
  --data-urlencode redirect_uri={redirect} --data-urlencode code_verifier="$VERIFIER" {client}
# 3. A new access token when it expires
curl -sS {token} -d grant_type=refresh_token --data-urlencode refresh_token="$REFRESH_TOKEN" {client}
```
"""


class OAuth2ClientAuth(OAuth2Base):
    """The client-credentials grant: the app signs in as itself, so there is no login step."""

    KIND = "oauth2_client"

    def access_token(self) -> str:
        # a token got for another client_id or other scopes isn't this one's: get a new one
        if self.credentials.state().get("token_for") != (key := [str(self.credentials.get("client_id")), *self.scopes]):
            self.credentials.save(access_token=None, token_for=key)
        return super().access_token()

    def new_token(self, creds: dict) -> str:
        scope = {"scope": " ".join(self.scopes)} if self.scopes else {}
        return self._token({"grant_type": "client_credentials", **scope})

    def describe(self) -> str:
        token = self.settings.get("token_url") or "TOKEN_URL"
        scope = f' -d scope="{" ".join(self.scopes)}"' if self.scopes else ""
        return super().describe() + f"""
**Token:** OAuth 2.0 client credentials: the app signs in as itself, so there is no login step.
curldex gets an access token when it needs one and keeps it in `config/.state.json` until it expires.

```bash
curl -sS {token} -d grant_type=client_credentials{scope} {self._client_options()}
```
"""


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A token call must not follow a redirect: it would re-send the client secret to the new host."""

    def redirect_request(self, *args: Any) -> None:
        return None


_token_opener = urllib.request.build_opener(_NoRedirect)


def post_form(url: str, form: dict, headers: dict | None = None, timeout: int = 30) -> dict:
    """POST a form to a token URL; the JSON answer. Redirects aren't followed (so a credential never
    reaches another host), and a non-JSON answer is a clean error, not a traceback."""
    request = urllib.request.Request(url, data=urlencode(form).encode(),
                                     headers={"Accept": "application/json", **(headers or {})})
    try:
        with _token_opener.open(request, timeout=timeout) as response:
            body = response.read().decode(errors="replace")
    except urllib.error.HTTPError as e:  # named, so a refused client (invalid_client) isn't read as the API's 401
        body = e.read().decode(errors="replace")
        raise HttpError(None, f"{url} answered HTTP {e.code}: {error_summary(body)}") from None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:  # HTTPException: a garbled answer
        raise HttpError(None, f"could not reach {url}: {getattr(e, 'reason', e)}") from None
    try:
        answer = json.loads(body)
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or "access_token" not in answer:  # GitHub and Slack send errors as 200
        said = f" ({error_summary(answer)})" if isinstance(answer, dict) and answer.get("error") else ""
        raise HttpError(None, f"{url} answered without an access_token{said}")
    return answer


def has_browser() -> bool:
    """Whether webbrowser.open() would open a browser window. Without a display it may run a text
    browser (lynx, w3m) in this terminal instead, and wait for it to quit."""
    return sys.platform == "darwin" or any(map(os.environ.get, ("BROWSER", "DISPLAY", "WAYLAND_DISPLAY")))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_redirect(redirect_uri: str, ready: Callable[[], object] = lambda: None,
                      timeout: float = LOGIN_TIMEOUT) -> str | None:
    """The address the browser lands on: received on the loopback port, or pasted on stdin; None if
    neither comes within `timeout` seconds. ready() runs once the port listens, so a browser opened
    then can't be redirected before it does."""
    target = urlsplit(redirect_uri)
    if target.hostname not in ("127.0.0.1", "localhost") or not target.port:
        ready()
        try:
            return input("Address: ") or None
        except EOFError:  # no terminal to paste into (a non-interactive run): a clean "no answer", not a traceback
            return None
    landed: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT  # a browser's spare connection, which sends nothing, can't hold up the redirect

        def do_GET(self):
            if "state=" not in self.path:  # e.g. the browser asking for /favicon.ico
                self.send_error(404)
                return
            landed.append(f"{target.scheme}://{target.netloc}{self.path}")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"curldex: login received; you can close this tab.")

        def log_message(self, *args):
            pass

    deadline = time.monotonic() + timeout
    try:
        server = HTTPServer((target.hostname, target.port), Handler)
    except OSError as e:  # e.g. a fixed redirect_uri port that another program holds
        raise ConfigError(f"can't listen on {target.netloc} ({e.strerror or e}); log in in two steps instead "
                          "(--start, then --finish ADDRESS)") from None
    with server:
        server.timeout = 0.5
        ready()
        while not landed and time.monotonic() < deadline:
            server.handle_request()
            if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0] and \
                    (line := sys.stdin.readline().strip()):
                landed.append(line)
    return landed[0] if landed else None
