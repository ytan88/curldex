# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Auth: how an app's credentials reach curl, and how to get them.

curl can send credentials in many ways: headers, query keys, user and password, digest, client
certificates, AWS signatures... So the general auth type, `curl`, is curl config lines with
${key} placeholders that credentials.yaml fills in:

    auth:
      type: curl
      config: ['header = "Authorization: Bot ${token}"']
      keys: {token: "the bot token: Developer Portal > your app > Bot > Reset Token"}

none, bearer, basic and api_key are presets of it; oauth2 (oauth2.py) gets its token by logging in.
An app with an unusual login subclasses one of these and names it in its class (`AUTH = ...`).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import ClassVar

from .config import PLACEHOLDER, SOURCES, UNSET, ConfigError, Credentials, mapping, texts
from .endpoint import check_header_name

# A curl config line: option, or option = "value", the value with its " and \ escaped and no line break
LINE = re.compile(r'([a-z][a-z0-9-]*)(?: = "((?:[^"\\\r\n]|\\.)*)")?')
# The options an auth's curl config lines may use: the ones that send credentials. Others could send
# them to another host or write files (url, next, config, output, location-trusted...).
SENDS = {"header", "user", "url-query", "oauth2-bearer", "basic", "digest", "ntlm", "negotiate", "anyauth",
         "aws-sigv4", "cert", "cert-type", "key", "key-type", "pass", "cookie"}
SHORT = {"header": "-H", "user": "-u"}  # how plain() writes the common options


def reads_file(option: str, value: str) -> str:
    """How curl would read a local file named in this value of a line in api.yaml, or "" if it wouldn't.
    A ${key} is the user's: a lone one may name a file in credentials.yaml."""
    value = re.sub(r"\\(.)", r"\1", value)  # curl unescapes \@ to @, \\ to \, so check what it will see
    written = PLACEHOLDER.sub("", value)  # what api.yaml itself wrote
    if option == "header" and value.startswith("@"):
        return 'header = "@FILE" sends the lines of a file'
    if option == "url-query" and "@" in written and "=" not in written and not value.startswith("+"):
        return 'url-query = "name@FILE" sends a file'
    if option == "cookie" and written and "=" not in written:
        return 'cookie = "FILE", with no "=", sends the cookies in a file'
    if option in ("cert", "key") and not value.startswith("${"):
        return f'{option} = "FILE" reads a file'
    return ""


def quoted(value: str, name: str) -> str:
    """A value escaped to go inside a quoted curl config value (`curl -K FILE`), which may hold any
    character but a line break. `name` says what the value is in an error; errors never show it."""
    if "\n" in value or "\r" in value:
        raise ConfigError(f"{name} has a line break in it")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def config_line(option: str, value: str) -> str:
    """One line of a curl config file: `option = "value"`."""
    return f'{option} = "' + quoted(value, f"a credential for curl's {option} option") + '"'


class Auth(ABC):
    """How an app's credentials reach curl, and how to get them (log in)."""

    KIND: ClassVar[str] = ""  # the `auth: {type: ...}` in api.yaml
    KEYS: ClassVar[dict[str, str]] = {}  # credentials.yaml keys -> what each one is
    OPTIONAL: ClassVar[tuple[str, ...]] = ()  # keys that may stay blank
    TOKENS: ClassVar[tuple[str, ...]] = ()  # what it saves in .state.json; like KEYS, only the auth may use them
    LOGIN: ClassVar[bool] = False  # whether calls need a login() first
    SETTING_KEYS: ClassVar[frozenset[str]] = frozenset({"keys"})  # what api.yaml's `auth:` may set, besides type

    def __init__(self, settings: dict, credentials: Credentials):
        self.settings = settings  # api.yaml's `auth:` section
        self.credentials = credentials
        if unknown := sorted(set(settings) - self.SETTING_KEYS):  # so that a misspelling can't go unnoticed
            raise ConfigError(f"{self.app}: unknown settings in api.yaml's auth: {', '.join(unknown)} "
                              f"(auth type {self.KIND} takes {', '.join(sorted(self.SETTING_KEYS)) or 'none'})")
        if "keys" in settings:
            settings["keys"] = mapping(settings["keys"], f"{self.app}: auth.keys in api.yaml", "{token: what it is}")

    @property
    def app(self) -> str:
        return self.credentials.app

    @property
    def keys(self) -> dict[str, str]:
        """The credentials.yaml keys it uses, and what each one is."""
        return {**self.KEYS, **{k: v for k, v in (self.settings.get("keys") or {}).items() if k in self.KEYS}}

    @abstractmethod
    def curl_config(self) -> list[str]:
        """curl config lines that send the credentials, e.g. `header = "Authorization: Bearer ..."`."""

    @abstractmethod
    def plain(self) -> str:
        """The curl options that send the same credentials from shell variables, for calls without
        curldex, e.g. `-H "Authorization: Bearer $TOKEN"`; "" when it sends nothing, and then the commands
        have no -K."""

    def status(self) -> str:
        """'ready', or what is missing before calls can work."""
        return self.credentials.missing(*(k for k in self.keys if k not in self.OPTIONAL)) or "ready"

    def login(self, open_browser: bool = True) -> None:
        print(f"{self.app}: no login needed ({self.KIND} auth)")

    def login_start(self) -> str:
        """The first of two login steps: the URL to open in a browser."""
        raise ConfigError(self._no_browser_login())

    def login_finish(self, address: str) -> None:
        """The second login step: finish with the address the browser landed on."""
        raise ConfigError(self._no_browser_login())

    def _no_browser_login(self) -> str:
        return f"{self.app}: no browser login ({self.KIND} auth); " + \
            (f"run curldex login {self.app}" if self.LOGIN else "no login needed")

    def describe(self) -> str:
        """The auth as documentation (Markdown): what curl sends, the credentials, how they reach
        curl, and how to send the same calls without curldex."""
        if not self.plain():
            return f"**Type:** `{self.KIND}`. The service needs no credentials; the commands have no `-K`.\n"
        keys = "\n".join(f"- `{key}`: {what or '(see credentials.example.yaml)'}"
                         + (" (optional)" if key in self.OPTIONAL else "") for key, what in self.keys.items())
        return (f"**Type:** `{self.KIND}`. curl sends the credentials as `{self.plain()}` would.\n\n"
                f"**Credentials** go in the app's `config/credentials.yaml` (git-ignored; `chmod 600`). "
                f"Values may be literal, `${{env:VAR}}` or `${{file:PATH}}` (a file outside the repository, or one "
                f"named `*.key` in `config/`, which git, builds and Claude leave alone).\n\n{keys}\n\n"
                f"**How they reach curl:** every command has `-K \"$(curldex auth {self.app})\"`. That writes "
                "the credentials to the owner-only file `config/.curlrc` and prints its path, so a command "
                "never holds a secret and can be shown, shared and logged, and the secret stays out of shell "
                "history and the process list.\n\n"
                f"**Without curldex:** replace `-K \"$(curldex auth {self.app})\"` with `{self.plain()}`.\n")


class CurlAuth(Auth):
    """Credentials as curl config lines, `option = "value"`, with ${key} placeholders. The lines are
    checked as the app loads, and again before curl gets them: each may use only an option that sends
    credentials (SENDS). The lines api.yaml writes also can't make curl read a local file
    (reads_file); a subclass's own lines() are code, such as a login's cookie file."""

    KIND = "curl"
    SETTING_KEYS = Auth.SETTING_KEYS | {"config"}

    def __init__(self, settings: dict, credentials: Credentials):
        super().__init__(settings, credentials)
        self.config = texts(settings.get("config"), f"{self.app}: auth.config in api.yaml",
                            """['header = "X-Key: ${key}"']""")
        self.checked_lines()
        for line in self.config:
            if not ((match := LINE.fullmatch(line)) and match[2] is not None):
                continue
            if re.search(r"(?<!\\)(\\\\)*\\[nrv]", match[2]):  # curl turns \r\n in a quoted value into a line break
                raise ConfigError(f"{self.app}: an auth config line in api.yaml can't hold \\n or \\r (another header)")
            if how := reads_file(match[1], match[2]):
                raise ConfigError(f"{self.app}: an auth config line in api.yaml can't make curl read a local file "
                                  f"({how}); only credentials.yaml may name one, through a ${{key}}")

    def lines(self) -> list[str]:
        return self.config

    def checked_lines(self) -> list[str]:
        """lines(), once each is checked to be one curl config line with an option that sends credentials."""
        lines = self.lines()
        for line in lines:
            if not (match := LINE.fullmatch(line)):
                raise ConfigError(f'{self.app}: an auth config line looks like `option = "value"` or `option`; '
                                  f"got {line!r}")
            if match[1] not in SENDS:
                raise ConfigError(f"{self.app}: an auth config line can't use curl's {match[1]} option, which sends "
                                  f"no credentials (it may use {', '.join(sorted(SENDS))})")
        return lines

    @property
    def keys(self) -> dict[str, str]:
        described = {**self.KEYS, **(self.settings.get("keys") or {})}
        used = dict.fromkeys(key for line in self.lines() for key in PLACEHOLDER.findall(line)
                             if not key.startswith(SOURCES))
        return {key: described.get(key, "") for key in used}

    def curl_config(self) -> list[str]:
        lines = self.checked_lines()
        creds = self.credentials.checked(*self.keys)  # once, for every ${key} in every line
        return [PLACEHOLDER.sub(lambda m: self._value(m[1], creds), line) for line in lines]

    def plain(self) -> str:
        words = []
        for line in self.lines():
            option, value = LINE.fullmatch(line).groups()
            words.append(SHORT.get(option, f"--{option}"))
            if value is not None:
                words.append('"' + PLACEHOLDER.sub(lambda m: "$" + re.sub(r"\W", "_", m[1]).upper(), value) + '"')
        return " ".join(words)

    def _value(self, key: str, creds: dict) -> str:
        """A credential, escaped for a quoted config value. Errors name the key, never the value."""
        value = creds.get(key)
        if value in UNSET:
            raise ConfigError(f"{self.app}: ${{{key}}} is not set")
        return quoted(str(value).strip(), f"{self.app}: '{key}'")

    def _setting(self, key: str, default: str = "") -> str:
        """A preset's setting, as it goes inside a line's quoted value: escaped, and refused if it has a
        line break, which would start another line, with any curl option."""
        return quoted(str(self.settings.get(key) or default), f"{self.app}: auth.{key} in api.yaml")


class NoAuth(CurlAuth):
    KIND = "none"
    SETTING_KEYS = frozenset()

    def lines(self) -> list[str]:
        return []


class BearerAuth(CurlAuth):
    """`Authorization: Bearer <token>`. `prefix` replaces the word Bearer (Discord: `prefix: Bot`)."""

    KIND = "bearer"
    KEYS: ClassVar[dict[str, str]] = {"token": "the API token"}
    SETTING_KEYS = Auth.SETTING_KEYS | {"prefix"}

    def lines(self) -> list[str]:
        return [f'header = "Authorization: {self._setting("prefix", "Bearer")} ${{token}}"']


class BasicAuth(CurlAuth):
    KIND = "basic"
    KEYS: ClassVar[dict[str, str]] = {"username": "the user name", "password": "the password"}
    SETTING_KEYS = Auth.SETTING_KEYS

    def lines(self) -> list[str]:
        return ['user = "${username}:${password}"']


class ApiKeyAuth(CurlAuth):
    """A key in a header (`name`, default X-API-Key; `prefix` puts a word before the key) or in the
    query string (`in: query`)."""

    KIND = "api_key"
    KEYS: ClassVar[dict[str, str]] = {"api_key": "the API key"}
    SETTING_KEYS = Auth.SETTING_KEYS | {"name", "in", "prefix"}

    def __init__(self, settings: dict, credentials: Credentials):
        super().__init__(settings, credentials)
        if settings.get("in", "header") not in ("header", "query"):
            raise ConfigError(f"{self.app}: auth.in in api.yaml is header or query, not {settings['in']!r}")
        if settings.get("in", "header") == "header":
            check_header_name(str(settings.get("name", "X-API-Key")), f"{self.app}: auth.name in api.yaml")

    def lines(self) -> list[str]:
        name = self._setting("name", "X-API-Key")
        if self.settings.get("in") == "query":
            return [f'url-query = "{name}=${{api_key}}"']
        prefix = f"{self._setting('prefix')} " if self.settings.get("prefix") else ""
        return [f'header = "{name}: {prefix}${{api_key}}"']
