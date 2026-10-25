# curldex app reference

The formats of everything in an app, as curldex reads them. The source of truth is the core, the
modules in `curldex/`: `endpoint.py` (APIs and params), `auth.py` and `oauth2.py` (auth types),
`webapp.py` (the class), `curl.py` (how a call becomes a command).

## Layout

```
curldex/apps/<app>/                  <app>: a lowercase Python name
  config/api.yaml                    the APIs, with their examples
  __init__.py                        only if the app has code: the class; `App = <Class>`
  config/credentials.example.yaml    committed template; the user copies it to credentials.yaml
  config/credentials.yaml            the user's values: git-ignored, never read by Claude
  config/.state.json, config/.curlrc written by curldex: tokens and curl's credentials (git-ignored)
  config/verified.yaml               written by `curldex verify`
  API.md                             written by `curldex docs`
```

Apps live in `curldex/apps/`, and in any folder listed in `$CURLDEX_APPS`. Code several apps
share (one vendor's login, an importer) goes in `curldex/shared/<family>.py`, such as
`curldex/shared/google.py`, and their classes subclass its base class.

## api.yaml

```yaml
description: Discord - servers, channels and messages, as a bot
base_url: https://discord.com/api/v10        # may hold ${key}: https://${subdomain}.example.com/api
auth: {type: bearer, prefix: Bot}            # see "Auth"
headers:                                     # optional: sent with every call; may hold ${key}, never a secret
  User-Agent: DiscordBot (https://github.com/ytan88/curldex, 0.1)
curl_options: [--compressed]                 # optional: more curl options for every call; see "curl options"
source:                                      # optional: where the API is described, for `curldex discover`
  url: https://raw.githubusercontent.com/discord/discord-api-spec/main/specs/openapi.json   # or a file, relative to api.yaml
  include: [get-my-user, list-messages, create-message]     # optional: globs of API names to keep (default: all)
  exclude: [delete-*]                                       # optional: globs to leave out
commands:                                    # the APIs, by the service's own names; last in the file, so discover can append to it
  create-message:
    description: Post a message in a channel
    method: POST                             # GET (default), POST, PUT, PATCH or DELETE
    path: /channels/{channel_id}/messages    # {name}: a path param
    access: write                            # optional; see "Access levels"
    format: json                             # optional; see "Bodies"
    params:
      channel_id: {description: The channel's id}
      content: {required: true, description: "The text, up to 2000 characters"}
    example: {channel_id: <channel_id>, content: Hello from curl}   # for help, API.md and verify
```

`example:` gives realistic param values for the API's example call, shown by help and API.md and
run by `verify`. A string such as `"<id>"` is a stand-in: `verify` skips that API until it's given a
real value. Any `<word>` in an example's text counts as one, so write example text without HTML
tags. Without an example (or with `example:` and no value), the API's required params get
stand-ins. Examples are committed (they end up in `API.md` and `verified.yaml`), so use made-up
values, never real addresses or ids.

`source:` is read by `curldex discover`, in OpenAPI 3 unless the app's class sets another `IMPORTER`.
For OAuth, `auth.scopes` also narrows what it lists: only the APIs those scopes allow. `discover`
leaves out operations and params the description marks deprecated (one only described as deprecated,
"Deprecated and ignored.", is yours to delete), operations served from another host than the
description's server (an app has one `base_url`), and any param that carries a credential (an
`Authorization`, `token`, `api_key` or `Cookie` param, or one that matches a declared API-key
scheme): a credential belongs in `auth`, never in a per-call param, so you add it there instead.

A `${key}` in `base_url`, `headers` or a default is a value from credentials.yaml. It may not be a
credential: one the auth uses, a token curldex saves (`access_token`, `refresh_token`), a key named
like one (`token`, `api_key`, `password`...), or any key in a header named like one
(`Authorization`). It would appear in every curl command, so the app doesn't load.

### Params

| Setting | Meaning | Default |
|---|---|---|
| `in` | `path`, `query`, `header`, `body` (one field of the body) or `json` (the whole body) | `path` if `{name}` is in the path; else `body` for POST, PUT and PATCH; else `query` |
| `type` | `string`, `integer`, `number`, `boolean`, `object`, `array`, `file` (a local path; the file's content is sent) | `string`; `object` for `in: json` |
| `required` | the caller must give it (or it has a default) | always true for path params, else false |
| `default` | a value, or `"${key}"` for a value from credentials.yaml | none |
| `description` | one line | |
| `enum` | the allowed values: a list (one value alone needs no brackets) | |
| `reserved` | a path value that may contain `/` (`name: spaces/AAA`) | false |

A path param needs no entry unless it has a description or a default. Any other setting is an
error, so a misspelling such as `requird:` can't go unnoticed. A `"${key}"` default of a non-string
param needs a literal value in credentials.yaml (one from `${env:...}` or `${file:...}` arrives as
text).

A `file` param has no `default`, and its value in `example:` is a stand-in such as
`<path_to_a_file>`: whoever calls the API names the file to send.

How values travel:
- **path:** URL-encoded; with `reserved: true` its `/` stay.
- **query:** booleans as `true` and `false`; an array repeats the key (`tags=a&tags=b`); an object
  as JSON.
- **header:** as text. A header's name is an HTTP token: letters, digits and ``!#$%&'*+.^_`|~-``.

Query params in brackets, such as an OpenAPI `deepObject` (Stripe's `created[gte]`, `expand[]`), are
declared by hand as the params they become: `created[gte]: {type: integer}`, `expand[]: {type: array}`.

### Access levels

They decide what Claude, `verify` and `run()` may do without asking:
- **read:** only reads;
- **write:** creates or changes;
- **destructive:** deletes or replaces.

The method sets the default: GET is read; POST and PATCH are write; PUT and DELETE are
destructive. Override it with `access:` where that's wrong:
- a POST search is `read`;
- a POST that deletes or clears is `destructive`;
- a PUT that only adds something (a reaction, a membership) is `write`.

### Bodies

`format` says how the body is sent:

| format | Body | curl |
|---|---|---|
| `json` (default) | the `in: body` fields as an object; or the `in: json` value (merged with the fields if it's an object) | `--json '{...}'` |
| `form` | the `in: body` fields, URL-encoded; nested values in brackets: `meta[k]=v`, `items[0]=x` | `--data-urlencode name=value` per field |
| `multipart` | the `in: body` fields; `type: file` params are uploaded as files | `--form-string name=value`, `-F name=@path` |
| `raw` | the `in: json` param, as it is: text (an object or a list as JSON), or a `type: file` path | `--data-raw 'text'` or `--data-binary @path` |

A `type: file` param is an `in: body` field of a `multipart` body, or the `in: json` param of a `raw`
one; a `multipart` body takes no `in: json` param, and a `raw` one no `in: body` field. The app doesn't
load otherwise.

With `raw`, set `content_type` (default `application/octet-stream`). A `json` or `form` POST, PUT or
PATCH always sends a body, `{}` (or an empty form) if nothing else, because some services refuse a
POST without one; an empty `multipart` or `raw` one sends none.

### curl options

`curl_options` may hold only these, so that an app's config can't send the credentials to another
host or write files: `--compressed`, `--http1.1`, `--http2`, `--cacert FILE`,
`--capath DIR`, `-k`/`--insecure` (`curldex check` warns: it turns off the certificate check),
`-m`/`--max-time N`, `--connect-timeout N`, `--retry N`, `--retry-delay N`, `--retry-max-time N`,
`-A`/`--user-agent TEXT`, `-4`/`--ipv4`, `-6`/`--ipv6`, `--tlsv1.2`, `--tlsv1.3` and `--path-as-is`.
Every command already has `-q` (the user's `~/.curlrc` doesn't apply) and `-g`. Each word is an item
of its own, a value included: `[--max-time, 30]`. Any other
option, such as `-o` or `-L`, is an error when the app loads. curl doesn't follow redirects: with
`-L` it would take custom auth headers and cookies to the new host. `run()` and `verify` report a
redirect (301, 302, 303, 307, 308) as an error; point the API at where it redirects to.

### YAML
- Quote `"${key}"`, and any value containing `: ` or starting with `{`, `[`, `@` or `*`.
- In a `{...}` mapping, also quote any value containing a comma: `{description: "a, b"}`.
  Unquoted, the comma ends the value, and curldex rejects what follows as an unknown setting. Quote a
  name with brackets there too: `{"created[gte]": 1700000000}`.
- curldex reads YAML 1.2's plain values: only `true` and `false` are booleans, and numbers are plain
  decimals. So `on`, `off`, `yes` and `no` are safe as param names, and `2026-10-12`, `12:30` and
  `0123` stay text.
- A list of one may be written without brackets: `exclude: calendars-clear`.

## Auth

`auth:` in api.yaml. Each type that takes credentials (all but `none`) may also take
`keys: {key: what it is and where to get it}`, which replaces the built-in description of a key in
API.md. Each type takes only `keys` and the settings in its row below; any other is an error when
the app loads, so a misspelling such as `scope:` can't go unnoticed.

| type | What curl sends | credentials.yaml keys | Settings |
|---|---|---|---|
| `none` | nothing | | |
| `bearer` | `Authorization: Bearer <token>` | `token` | `prefix` replaces `Bearer` (`Bot`, `token`, ...) |
| `basic` | `-u user:password` | `username`, `password` | |
| `api_key` | `<name>: <key>`, or `?<name>=<key>` | `api_key` | `name` (default `X-API-Key`), `in: header` or `query`, `prefix` |
| `curl` | any curl config lines | every `${key}` in them | `config: [...]` |
| `oauth2` | `Authorization: Bearer <access token>`, after a browser login | `client_id`, `client_secret` | below |
| `oauth2_client` | the same; the token comes from the client-credentials grant | `client_id`, `client_secret` | below |

**curl:** each line is `option = "value"` or a bare `option`, as in a curl config file. `${key}`
inside a value is filled from credentials.yaml (where a value may itself be `${env:VAR}` or
`${file:PATH}`; api.yaml never reads the environment or files directly). A line may use only an
option that sends credentials: `header`, `user`, `url-query`, `oauth2-bearer`, `basic`, `digest`,
`ntlm`, `negotiate`, `anyauth`, `aws-sigv4`, `cert`, `cert-type`, `key`, `key-type`, `pass` or
`cookie`. Another, such as `url` or `output`, is an error when the app loads, since it could send
the credentials elsewhere or write files. Nor may a line make curl read a local file, since it would
send what's in it: `header = "@FILE"`, `url-query = "name@FILE"` (with no `=`), `cookie = "FILE"`
(with no `=`), and `cert` or `key` with a path written in api.yaml are errors too. A file curl reads,
such as a client certificate or a cookie jar, is named in credentials.yaml: `cert = "${cert_file}"`,
`cookie = "${cookie_file}"`. Even so, curl can send credentials in more ways than any list of types:

```yaml
auth: {type: curl, config: ['header = "DD-API-KEY: ${api_key}"', 'header = "DD-APPLICATION-KEY: ${app_key}"']}
auth: {type: curl, config: [digest, 'user = "${username}:${password}"']}               # HTTP digest
auth: {type: curl, config: ['cert = "${cert_file}"', 'key = "${key_file}"']}           # a client certificate
auth: {type: curl, config: ['aws-sigv4 = "aws:amz:${region}:execute-api"', 'user = "${access_key_id}:${secret_access_key}"']}
# (a value the auth uses can't also be a ${key} in base_url: write the region there, or keep it under a second key)
auth: {type: curl, config: ['header = "Cookie: ${cookie}"']}                           # a website session
auth: {type: curl, config: ['user = "${api_key}:"']}                                   # a key as the user, no password (Stripe)
```

**oauth2** settings:
- `authorize_url`, `token_url`, `scopes: [...]`;
- `authorize_params: {...}`: more authorize URL params, e.g. `access_type: offline` to get a
  refresh token;
- `redirect_uri`: if the provider needs a registered one (default `http://127.0.0.1:<free port>`);
- `token_auth: basic`: client ID and secret in a Basic header, rather than in the form;
- `token_params: {...}`: more token request fields, e.g. `audience`.

The user logs in once, with `app.login()` in IPython or `curldex login <app>`, or in two steps with
`--start` and `--finish`, so that Claude can run it. curldex keeps the tokens and refreshes them.

`oauth2_client` takes `token_url`, `scopes`, `token_auth` and `token_params`, and has no login step.

**An unusual login:** subclass one of the classes in a package and name it in the class:
`AUTH = MyAuth`. A subclass that takes settings of its own lists them too:
`SETTING_KEYS = OAuth2Auth.SETTING_KEYS | {"tenant"}`. `GoogleAuth` in `curldex/shared/google.py`
adds Google service accounts to `OAuth2Auth`.

For a website with a login form, subclass `CurlAuth`. Its `login()` posts the form with curl, which
saves the session cookie in the config folder as `session.cookies`: `*.cookies` is git-ignored, left
out of builds and denied to Claude, so the session can't be committed, packed or read. The password
reaches curl on its standard input, never on its command line:

```python
import subprocess
from typing import ClassVar

from curldex import Auth, ConfigError
from curldex.auth import config_line
from curldex.config import write_private

LOGIN_URL = "https://example.com/login"
SESSION_COOKIE = "sessionid"                         # the cookie the site sets once you're logged in


class FormLogin(Auth):
    KIND = "form_login"
    LOGIN = True                                     # API.md tells users to log in first
    KEYS: ClassVar[dict[str, str]] = {"username": "your user name", "password": "your password"}

    @property
    def jar(self):
        return self.credentials.path.parent / "session.cookies"

    def status(self) -> str:
        status = super().status()
        return f"login required: run curldex login {self.app}" if status == "ready" and not self.jar.exists() \
            else status

    def login(self, open_browser: bool = True) -> None:
        user, password = self.credentials.require("username", "password")
        write_private(self.jar, "")                  # owner-only before curl fills it
        form = config_line("data-urlencode", f"username={user}") + "\n" \
            + config_line("data-urlencode", f"password={password}") + "\n"
        done = subprocess.run(["curl", "-q", "-sSf", "-o", "/dev/null", "-c", str(self.jar), "-K", "-", LOGIN_URL],
                              input=form, text=True)  # -f: a refused login fails; -K -: the form from stdin
        if done.returncode or f"\t{SESSION_COOKIE}\t" not in self.jar.read_text():  # refused (a CSRF cookie
            self.jar.unlink()                                                      # alone doesn't count)
            raise ConfigError(f"{self.app}: the login failed; check username and password")
        print(f"{self.app}: logged in")

    def curl_config(self) -> list[str]:
        if (status := self.status()) != "ready":    # without the jar, curl would quietly send no cookie
            raise ConfigError(f"{self.app}: {status}")
        return [config_line("cookie", str(self.jar))]  # code may name a file; a line in api.yaml can't

    def plain(self) -> str:
        return '--cookie "$COOKIE_FILE"'             # API.md shows this, not your path
```

The app's class sets `AUTH = FormLogin`, and api.yaml needs no `auth:`; the form's field names,
`LOGIN_URL` and `SESSION_COOKIE` are the site's. When the session ends, calls answer 401 or redirect
to the login page: run `curldex login <app>` again. To log out, delete `config/session.cookies`.

When a cookie copied from the browser is good enough, `type: curl` with a `Cookie` header needs no
code.

## The class: `__init__.py`, only if the app has code

An app with no code is just its `config/` folder: `curldex.load_app("<app>")` gives a plain
`WebApp`. Write `__init__.py` only for helpers, an unusual login, a family base class or another
description format:

```python
"""<Service>: <what it is>, as curl commands, plus <helpers>.

    import curldex
    app = curldex.load_app("<app>")              # a <Class>
    app.apis()                                   # the APIs
    cmd = app.curl("<api>", <param>=<value>)     # in IPython: !{cmd}
    app.<task>(<args>)                           # a helper
"""

from __future__ import annotations

from pathlib import Path

from curldex import WebApp


class <Class>(WebApp):
    DIRECTORY = Path(__file__).parent

    def <task>(self, <args>) -> dict:
        """<What it does>; for a task that takes several calls or computed values."""
        return self.run("<api>", <param>=<value>)


App = <Class>
```

**A family app** subclasses the family's base class instead of `WebApp`, and gets its login and
description format from it. Any Google API:

```python
"""Google Drive: its REST API as curl commands."""

from __future__ import annotations

from pathlib import Path

from curldex.shared.google import GoogleApp


class GoogleDrive(GoogleApp):
    DIRECTORY = Path(__file__).parent


App = GoogleDrive
```

Its api.yaml has `auth: {type: oauth2, scopes: [...]}` (GoogleAuth fills in Google's endpoints),
and `source: {url: ...}` is the API's Discovery document:
`https://www.googleapis.com/discovery/v1/apis/<api>/<version>/rest` for most APIs. If that answers
404, as Meet's does, use the `discoveryRestUrl` that `https://www.googleapis.com/discovery/v1/apis?name=<api>`
lists, such as `https://meet.googleapis.com/$discovery/rest?version=v2`. Its credentials.example.yaml has
`client_id`, `client_secret`, `service_account_file` and `service_account_subject`. With a service
account, `primary` (or `me`) is the account's own, empty calendar or mailbox, so default such an id to
a `${key}` from credentials.yaml, as google_calendar does with `calendar_id`.

| Class attribute | Meaning |
|---|---|
| `DIRECTORY` | the package folder; always `Path(__file__).parent` |
| `AUTH` | an Auth class, for an unusual login (default: `auth: type`) |
| `IMPORTER` | the format of the description `source:` names (default `OpenAPIImporter`; `GoogleApp` sets `DiscoveryImporter`). A new format subclasses `curldex.Importer` |

What helpers build on:
- `self.run(api, yes=False, **values)`: the JSON (a destructive call needs `yes=True`);
- `self.curl(api, **values)`: the Curl;
- `self.request(method, path, query=None, body=None)`: a Curl for any path under base_url;
- `self.credentials.get(key)`: a value from credentials.yaml.

## credentials.example.yaml

Committed, with empty values; it's the user's setup guide:

```yaml
# discord credentials: copy this file to credentials.yaml (git-ignored), fill it in and
# `chmod 600` it. Values may be literal, ${env:VAR} or ${file:PATH}.

# The bot's token: https://discord.com/developers/applications > your application > Bot >
# Reset Token. Paste the token alone, without "Bot " (or keep it in the environment:
# token: ${env:DISCORD_TOKEN}).
token: ""
```

## Commands

```
curldex check <app>                  what's wrong with the app, without calling the service
curldex help <app> [api]             the APIs, or one API's help (as the user sees them; also `curldex <app> [api]`)
curldex docs <app>                   write API.md
curldex verify <app> [api] [--yes] [PARAM=VALUE ...]   run examples against the service; record them
curldex login <app> [--start | --finish ADDRESS]       OAuth login
curldex discover <app> [--add NAME_OR_GLOB ...]   the APIs api.yaml lacks from its source:; --add appends them
curldex auth <app>                   write curl's credentials file; print its path
```

In Python, `app.request("GET", "/path", query={...})` tries a call to any path under base_url,
with the app's credentials, before you write its entry in api.yaml.

## Done means
- `curldex check <app>` reports no errors.
- Every API has a one-line description and the right access level.
- An `example:` covers every API with required params or a body.
- `credentials.example.yaml` says where to get each value.
- The read APIs are verified. Writes are verified on test data, or the reason they aren't is stated.
- `API.md` has been regenerated.
- The app is listed in `README.md`, unless the user asked for no changes outside the app.
