# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""The errors curldex raises, and reading config files: YAML, `${...}` placeholders, and an app's
Credentials.

Catch CurldexError for all the errors; `exit_code` is the CLI's exit status.

Values in credentials.yaml may be literal, `${env:VAR}` (an environment variable) or `${file:PATH}`
(a file's contents; a relative path is relative to the config folder). In api.yaml, `${key}` is a
value from credentials.yaml, e.g. `calendar_id: {default: "${calendar_id}"}`; only credentials.yaml
reads the environment or files, so api.yaml (perhaps imported from a published description) can't.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml


class CurldexError(Exception):
    exit_code = 1


class UsageError(CurldexError, ValueError):
    """Wrong arguments: an unknown API or param, a missing value, a bad name."""

    exit_code = 2


class ConfigError(CurldexError):
    """A config or credentials file is missing or incomplete, or a login is needed."""

    exit_code = 3


class HttpError(CurldexError):
    """The service answered with an error status, or curl failed (status None)."""

    def __init__(self, status: int | None, body: Any):
        """`body` is the service's answer (status set), or curldex's own message (status None)."""
        super().__init__(f"HTTP {status}: {error_summary(body)}" if status else str(body))
        self.status = status
        self.body = body  # the whole answer, for whoever needs more than the message


def error_summary(body: Any) -> str:
    """What an error answer says, for a message: the service's own error text (Google's
    error.message; OAuth's error and error_description), or else the first line of a text answer.
    Never a whole JSON answer, which an echo service fills with the request's headers, credentials
    included."""
    if isinstance(body, str):
        try:
            body = json.loads(body)  # a token endpoint's answer comes as text
        except ValueError:
            return short(next((line.strip() for line in body.splitlines() if line.strip()), "no text"), 120)
    if isinstance(body, dict):
        error = body.get("error")
        said = [error.get("message")] if isinstance(error, dict) else \
            [error, body.get("error_description"), None if isinstance(error, str) else body.get("message")]
        if text := ": ".join(" ".join(part.split()) for part in said if isinstance(part, str) and part.strip()):
            return short(text, 300)
    return "an answer with no error text" if body not in (None, "") else "no answer body"


def short(text: str, limit: int) -> str:
    """The text, cut to `limit` characters with "..." if it's longer."""
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
# A name that says it holds a credential, by its ending (so `pageToken` counts: for query params, discover
# uses an exact list instead)
SECRET_NAME = re.compile(r"(token|secret|pass(w(or)?d)?|^key|(api|access|secret|private|subscription|app)[-_]?key"
                         r"|-key|cookie|auth(orization)?)$", re.IGNORECASE)
SOURCES = ("env:", "file:")
UNSET = (None, "")


class Loader(yaml.SafeLoader):
    """YAML with YAML 1.2's plain values: only true and false are booleans, numbers are plain decimals
    (or 0x.., 0o..), and dates and times stay text. YAML 1.1, PyYAML's default, would read yes, no, on
    and off as booleans (a param named `on` would become True), 2026-10-12 as a date, 12:30 as the
    number 750, and 0123 as an octal number."""


YAML_1_1 = ("bool", "int", "float", "timestamp")  # the plain values YAML 1.1 reads otherwise
Loader.yaml_implicit_resolvers = {
    first: [(tag, regex) for tag, regex in resolvers if tag.split(":")[-1] not in YAML_1_1]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()}
Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
                             list("tTfF"))
Loader.add_implicit_resolver("tag:yaml.org,2002:int", re.compile(
    r"^(?:[-+]?(?:0|[1-9][0-9]*)|0x[0-9a-fA-F]+|0o[0-7]+)$"), list("-+0123456789"))
Loader.add_implicit_resolver("tag:yaml.org,2002:float", re.compile(
    r"^(?:[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?|[-+]?[0-9]+[eE][-+]?[0-9]+"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"), list("-+0123456789."))


def parse_yaml(text: str) -> Any:
    return yaml.load(text, Loader=Loader)  # a SafeLoader


def load_yaml(path: Path, required: bool = True, quiet: bool = False) -> dict:
    """Read a YAML mapping. `quiet` keeps the file's contents out of a parse error (for credentials.yaml,
    whose lines are secret): the message then gives only the line number, not the offending text."""
    if not path.exists():
        if required:
            raise ConfigError(f"missing config file: {path}")
        return {}
    try:
        data = parse_yaml(path.read_text()) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as e:  # not UTF-8: no line, and no byte of it shown either
        raise ConfigError(f"{path}: not valid YAML{error_line(e) if quiet else f' ({e})'}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be a mapping")
    return data


def error_line(error: Exception) -> str:
    """Where a YAML error is (" at line N"), without the offending text, which may be a secret."""
    mark = getattr(error, "problem_mark", None)
    return f" at line {mark.line + 1}" if mark is not None else ""


class Dumper(yaml.SafeDumper):
    """A dumper whose quoting matches our Loader: a string that the Loader would read back as a number,
    a date or a boolean (1e3, 0o17, 2026-10-12, on) is quoted, so a value written by discover() round-trips
    as the same string instead of corrupting api.yaml."""


def _reparses_as_non_string(value: str) -> bool:
    if "\n" in value:  # a multi-line string is never read as a scalar number or date
        return False
    try:
        return not isinstance(parse_yaml(value), str)
    except yaml.YAMLError:
        return True


def _represent_str(dumper: yaml.Dumper, value: str) -> yaml.ScalarNode:
    style = "'" if _reparses_as_non_string(value) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


Dumper.add_representer(str, _represent_str)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=110)


def mapping(value: Any, what: str, like: str) -> dict:
    """A setting that must be a mapping ({} if absent). `what` names it, and `like` shows one."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{what} must be a mapping, such as {like}; got {short(repr(value), 60)}")
    return value


def texts(value: Any, what: str, like: str) -> list[str]:
    """A setting that is a list of text or numbers ([] if absent). One alone is a list of one, so that
    `exclude: calendars-clear` isn't read as a glob per character."""
    items = [] if value is None else value if isinstance(value, list) else [value]
    if any(item is None or isinstance(item, (bool, dict, list)) for item in items):
        raise ConfigError(f"{what} must be a list, such as {like}; got {short(repr(value), 60)}")
    return [str(item) for item in items]


def write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read (0600), replacing it in one step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates it 0600; the ~ suffix puts a temp file a crash leaves under the backup-file rules
    # (git-ignored, left out of builds, denied to Claude)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix="~")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(temp, path)
    except BaseException:  # the disk is full, or Ctrl-C: don't leave the temp file (it holds the secret)
        Path(temp).unlink(missing_ok=True)
        raise


def placeholders(value: Any, sources: bool = False) -> set[str]:
    """The credential keys a value refers to as ${key}; with sources=True, its ${env:...} and
    ${file:...} instead."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return set().union(*(placeholders(v, sources) for v in value))
    if isinstance(value, str):
        return {key for key in PLACEHOLDER.findall(value) if key.startswith(SOURCES) == sources}
    return set()


def expand(value: Any, lookup: Callable[[str], Any], where: str = "") -> Any:
    """Replace each ${key} with lookup(key). A string that is exactly one placeholder takes the
    looked-up value as it is (None if unset); inside a longer string a placeholder must be set."""
    if isinstance(value, dict):
        return {k: expand(v, lookup, where) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, lookup, where) for v in value]
    if not isinstance(value, str):
        return value
    if whole := PLACEHOLDER.fullmatch(value):
        return lookup(whole[1])

    def substitute(match: re.Match) -> str:
        found = lookup(match[1])
        if found in UNSET:
            raise ConfigError(f"{where}: ${{{match[1]}}} is not set")
        return str(found)

    return PLACEHOLDER.sub(substitute, value)


class Credentials:
    """An app's credentials: credentials.yaml (written by the user) overlaid with .state.json
    (written by curldex: tokens). The state wins for the keys it holds, so a refresh_token written in
    credentials.yaml only seeds the first login; delete .state.json to seed it again."""

    def __init__(self, directory: Path, app: str):
        self.app = app
        self.path = directory / "credentials.yaml"
        self.state_path = directory / ".state.json"

    def load(self) -> dict:
        return {**self.user_values(), **self.state()}

    def user_values(self) -> dict:
        """credentials.yaml's values, with each ${env:...} and ${file:...} read."""
        values = load_yaml(self.path, required=False, quiet=True)
        if floats := [key for key, value in values.items() if isinstance(value, float)]:  # 1234.5678 loses digits
            raise ConfigError(f"{self.app}: {floats[0]} in {self.path} reads as a number; put it in quotes")
        return expand(values, self._source, str(self.path))

    @contextmanager
    def lock(self) -> Iterator[None]:
        """An exclusive lock across processes, held while a token is refreshed, so two `curldex auth`
        runs at once don't both spend the same refresh token (which a provider that rotates them would
        then revoke)."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path.with_name(".state.lock"), "a") as handle:  # "a": never truncates anything
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def get(self, key: str, default: Any = None) -> Any:
        """A value in credentials.yaml, as a ${key} placeholder gets it: never a token curldex saved in
        .state.json, and never an environment variable or a file directly (see the module's docstring)."""
        value = self.user_values().get(key)
        return default if value in UNSET else value

    def missing(self, *keys: str) -> str:
        """Which of these keys aren't set, as a message ("missing a, b in .../credentials.yaml"), or "".
        It names a ${env:...} or ${file:...} that came up empty, as the file does hold a value then."""
        creds = self.load()
        if not (unset := [key for key in keys if creds.get(key) in UNSET]):
            return ""
        written = load_yaml(self.path, required=False, quiet=True)
        empty = [str(written[k]) for k in unset if str(written.get(k, "")).startswith("${")]
        return f"missing {', '.join(unset)} in {self.where()}" + (f" (empty: {', '.join(empty)})" if empty else "")

    def where(self) -> str:
        """credentials.yaml's path, for messages; when there's none yet, how to start one."""
        if self.path.exists():
            return str(self.path)
        return f"{self.path} (copy credentials.example.yaml, next to it, to start it)"

    def require(self, *keys: str) -> list:
        creds = self.checked(*keys)
        return [creds[key] for key in keys]

    def checked(self, *keys: str) -> dict:
        """The credentials, loaded once, after checking that these keys are set."""
        if missing := self.missing(*keys):
            raise ConfigError(f"{self.app}: {missing}")
        return self.load()

    def state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            state = json.loads(self.state_path.read_text())
        except ValueError:
            state = None
        if not isinstance(state, dict):
            raise ConfigError(f"{self.app}: {self.state_path} is damaged; delete it, then log in again if the app "
                              "has a login")
        return state

    def save(self, **values: Any) -> None:
        """Merge values into .state.json; None removes a key."""
        state = {**self.state(), **values}
        write_private(self.state_path, json.dumps({k: v for k, v in state.items() if v is not None}, indent=2))

    def _source(self, key: str) -> Any:
        if key.startswith("env:"):
            return os.environ.get(key[4:])
        if key.startswith("file:"):
            path = self.path.parent / Path(key[5:]).expanduser()  # an absolute path stays as it is
            try:
                return (path.read_text().strip() or None) if path.is_file() else None
            except (OSError, UnicodeDecodeError):  # unreadable, or not text: say so without showing any of it
                raise ConfigError(f"{self.path}: can't read {path} as text") from None
        return None
