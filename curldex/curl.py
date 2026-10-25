# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Curl: one HTTP call as a curl command. curl does the HTTP; curldex only builds the command.

A command never holds a secret. The credentials reach curl through a config file that
`curldex auth <app>` writes (refreshing the login first if needed) and prints the path of:

    curl -q -sS -g -K "$(curldex auth google_calendar)" 'https://www.googleapis.com/calendar/v3/users/me/calendarList'
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .config import ConfigError, HttpError, UsageError
from .endpoint import scalar

if TYPE_CHECKING:
    from .webapp import WebApp


def curldex_command() -> str:
    """How a shell runs curldex: the script next to this Python (as in a virtualenv that isn't
    activated) when it isn't the `curldex` on PATH, else just `curldex`."""
    beside = Path(sys.executable).parent / "curldex"
    on_path = shutil.which("curldex")
    if beside.is_file() and not (on_path and Path(on_path).resolve() == beside.resolve()):
        return shlex.quote(str(beside))
    return "curldex"


def readable(command: str) -> str:
    """A command for documentation: stand-ins such as <event_id> unescaped, so they read as such."""
    return re.sub(r"%3C([\w.-]+)%3E", r"<\1>", command)


def form_fields(value: Any, name: str = "") -> list[tuple[str, str]]:
    """Form fields for a value; nested objects and lists use brackets: a[b]=1, a[0]=x."""
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, (list, tuple)):
        items = enumerate(value)
    else:
        return [(name, scalar(value))]
    return [field for key, item in items for field in form_fields(item, f"{name}[{key}]" if name else str(key))]


def quoted_path(path: Path) -> str:
    """A file path for curl's `-F name=@PATH`, double-quoted so a `;` (which would start `;type=`) or a
    `,` in it stays part of the path; curl reads `\\` and `"` escaped inside the quotes."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def confirm(question: str) -> bool:
    """Ask on the terminal; False when there's no terminal to ask on."""
    if not sys.stdin.isatty():
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


class Curl:
    """One HTTP call. str() is the curl command, for a shell or IPython (`!{cmd}`); run() runs it
    and returns the JSON response."""

    def __init__(self, method: str, url: str, headers: dict | None = None, body: Any = None,
                 app: WebApp | None = None, destructive: bool = False, format: str = "json",
                 options: list[str] | tuple[str, ...] = ()):
        self.method = method
        self.url = url
        self.headers = dict(headers or {})
        self.body = body
        self.format = format  # how the body is sent: json, form, multipart or raw
        self.app = app  # whose credentials curl gets; None for a call without credentials
        self.destructive = destructive
        self.options = list(options)  # more curl options, e.g. --compressed

    def args(self, auth: str | None = None) -> list[str]:
        """The command's words; `auth` is what follows -K: a file path, or a shell expression."""
        # -q (first): not the user's ~/.curlrc, whose `location` or `verbose` would follow a redirect with the
        # credentials or print them; -g: no URL globbing, so [ ] { } in a URL are literal
        words = ["curl", "-q", "-sS", "-g", *self.options]
        if self.method != ("GET" if self.body is None else "POST"):
            words += ["-X", self.method]
        for name, value in self.headers.items():  # curl reads "Name:" as "don't send it", "Name;" as empty
            words += ["-H", f"{name}: {value}" if value.strip(" \t") else f"{name};"]
        if auth:
            words += ["-K", auth]
        return [*words, *self.body_args(), self.url]

    def body_args(self) -> list[str]:
        """The body as curl options, by format. A file is a Path, sent as @path; nothing else makes curl
        read a file (--data-raw takes text that starts with @ as it is)."""
        if self.body is None:
            return []
        if self.format == "form":
            # curl's --data-urlencode encodes only the part after '=', not the field name, so encode the
            # name here; a nested key like meta[a&b] would otherwise split the form on the wire
            return [w for name, value in form_fields(self.body)
                    for w in ("--data-urlencode", f"{quote(name, safe='[]')}={value}")] or ["--data", ""]
        if self.format == "multipart":  # the field names were checked as the app loaded
            return [w for name, value in self.body.items() for w in
                    (("-F", f'{name}=@{quoted_path(value)}') if isinstance(value, Path)
                     else ("--form-string", f"{name}={scalar(value)}"))]
        if self.format == "raw":  # text as it is; an object or a list as JSON
            if isinstance(self.body, Path):
                return ["--data-binary", f"@{self.body}"]
            text = self.body if isinstance(self.body, str) else json.dumps(self.body, ensure_ascii=False)
            return ["--data-raw", text]
        return ["--json", json.dumps(self.body, ensure_ascii=False)]

    def command(self, curldex: str | None = None) -> str:
        """The command as shell text; `curldex` is how it runs curldex (default: curldex_command())."""
        auth = f'"$({curldex or curldex_command()} auth {shlex.quote(self.app.name)})"' if self.app else None
        *words, url = self.args(auth)
        quoted = "'" + url.replace("'", "'\"'\"'") + "'"  # always quoted, so <stand-ins> are safe too
        return " ".join([*(word if word is auth else shlex.quote(word) for word in words), quoted])

    def __str__(self) -> str:
        return self.command()

    __repr__ = __str__

    def fetch(self, timeout: int = 120) -> tuple[int, Any]:
        """Run curl: the HTTP status and the response (JSON, else text; None if empty)."""
        auth = str(self.app.auth_file()) if self.app else None
        try:
            proc = subprocess.run([*self.args(auth), "-w", "\n%{http_code}"], capture_output=True, timeout=timeout)
        except FileNotFoundError:
            raise ConfigError("curl is not installed") from None
        except subprocess.TimeoutExpired:
            raise HttpError(None, f"curl timed out after {timeout}s: {self.url}") from None
        except (OSError, ValueError) as e:  # a body too large for the command line (E2BIG), or one with a NUL
            raise HttpError(None, f"curl could not be run: {e}") from None
        if proc.returncode != 0:
            said = [line for line in proc.stderr.decode(errors="replace").splitlines() if line.strip()]
            last = next((line for line in reversed(said) if not line.startswith("curl: try ")), "")
            raise HttpError(None, f"curl failed (exit {proc.returncode}): {last}")
        text, _, status = proc.stdout.decode(errors="replace").rpartition("\n")
        try:
            data = json.loads(text) if text.strip() else None
        except (ValueError, RecursionError):  # not JSON, or nested too deep to parse
            data = text
        return int(status), data

    def run(self, yes: bool = False, timeout: int = 120) -> Any:
        """Run curl and return the response; an error status raises HttpError. A destructive call
        asks first, unless yes=True."""
        if self.destructive and not yes and not confirm(f"{self}\nThis deletes or replaces data. Run it?"):
            raise UsageError("not run: the call deletes or replaces data (pass yes=True to run it)")
        status, data = self.fetch(timeout)
        # curl doesn't follow a redirect (no -L: it would take custom auth headers and cookies to the new host),
        # so one isn't a completed call. 304 Not Modified and 300/305/306 aren't redirects to follow.
        if status >= 400 or status in (301, 302, 303, 307, 308):
            raise HttpError(status, data if status >= 400 else
                            "a redirect (curl -D - shows where to), which curldex doesn't follow")
        return data
