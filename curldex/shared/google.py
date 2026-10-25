# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""What the Google apps share, such as google_calendar.

    GoogleApp           the base class of a Google API app
    GoogleAuth          Google's OAuth 2.0 login, or a service account key file (no login)
    DiscoveryImporter   Google API Discovery documents, for discover(), e.g.
                        https://www.googleapis.com/discovery/v1/apis/calendar/v3/rest

Google's endpoints are filled in, and the login asks for offline access so that Google returns a
refresh token. With `service_account_file` set in credentials.yaml, the app acts as that service
account instead: curldex signs a JWT with the account's key (RS256, with the standard library only)
and trades it for an access token.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from curldex import ConfigError, Importer, OAuth2Auth, WebApp
from curldex.discover import is_credential
from curldex.oauth2 import lifetime, post_form, valid_token

# ----- the login -----------------------------------------------------------------------------------

JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")  # DER DigestInfo prefix for SHA-256


class GoogleAuth(OAuth2Auth):
    SETTINGS: ClassVar[dict] = {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "authorize_params": {"access_type": "offline", "prompt": "consent"},  # always return a refresh token
    }
    KEYS: ClassVar[dict[str, str]] = {
        "client_id": "the OAuth client ID (a Desktop app client)",
        "client_secret": "the OAuth client secret",
        "service_account_file": "or a service account's JSON key file, instead of the OAuth client",
        "service_account_subject": "with a service account: the Workspace user to act as (domain-wide delegation)",
    }
    OPTIONAL: ClassVar[tuple[str, ...]] = ("service_account_file", "service_account_subject")

    @property
    def key_file(self) -> Path | None:
        value = self.credentials.get("service_account_file")
        return self.credentials.path.parent / Path(value).expanduser() if value else None

    def status(self) -> str:
        if (key_file := self.key_file) is None:
            return super().status()
        return "ready" if key_file.is_file() else f"service_account_file not found: {key_file}"

    def login(self, open_browser: bool = True) -> None:
        if self.key_file is None:
            super().login(open_browser)
        else:
            print(f"{self.app}: no login needed; it uses the service account in {self.key_file}")

    def login_start(self) -> str:
        if self.key_file is not None:
            raise ConfigError(f"{self.app}: no login needed; it uses a service account key file")
        return super().login_start()

    def access_token(self) -> str:
        if (key_file := self.key_file) is None:
            return super().access_token()
        # the cache is keyed by the file, the subject and the scopes, so changing any of them mints a new
        # token rather than reusing one that acts as the old user or with the old access
        identity = [str(key_file), self.credentials.get("service_account_subject") or "", " ".join(self.scopes)]
        cached = self.credentials.state().get("service_account_token") or {}
        if cached.get("identity") == identity and (token := valid_token(cached)):
            return token
        key = self._key(key_file)
        token = post_form(key["token_uri"], {"grant_type": JWT_GRANT, "assertion": self._jwt(key)})
        self.credentials.save(service_account_token={
            "identity": identity, "access_token": token["access_token"], "expires_at": time.time() + lifetime(token)})
        return token["access_token"]

    def _key(self, key_file: Path) -> dict:
        if not key_file.is_file():
            raise ConfigError(f"{self.app}: service_account_file not found: {key_file}")
        try:
            key = json.loads(key_file.read_text())
            if not (key["client_email"] and key["private_key"]):
                raise KeyError
        except (ValueError, KeyError, TypeError):
            raise ConfigError(f"{self.app}: {key_file.name} is not a service account's JSON key file") from None
        return {"token_uri": self.settings["token_url"], **key}

    def _jwt(self, key: dict) -> str:
        now = int(time.time())
        claims = {"iss": key["client_email"], "scope": " ".join(self.scopes), "aud": key["token_uri"],
                  "iat": now, "exp": now + 3600}
        if subject := self.credentials.get("service_account_subject"):
            claims["sub"] = subject
        header = {"alg": "RS256", "typ": "JWT", "kid": key.get("private_key_id", "")}
        signed = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
        return f"{signed}.{_b64(rs256(signed.encode(), key['private_key']))}"

    def describe(self) -> str:
        return super().describe() + f"""
**Or a service account** (no browser login, and it doesn't expire): set `service_account_file` to the
JSON key file of a Google Cloud service account. curldex signs a JWT with its key (RS256) and trades it
for an access token at the key's `token_uri`; the commands stay the same. The account acts as itself,
so share the data with its email address (a calendar, say); in Google Workspace,
`service_account_subject` makes it act for a user (domain-wide delegation). Then
`curldex login {self.app}` isn't needed.
"""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def rs256(message: bytes, pem: str) -> bytes:
    """An RS256 signature (RSASSA-PKCS1-v1_5 with SHA-256), computed with Python's integers."""
    try:
        der = base64.b64decode("".join(line for line in pem.strip().splitlines() if "-----" not in line))
    except ValueError:  # binascii.Error (not base64): an encrypted key, or a value with literal \n
        raise ConfigError("the service account's private_key isn't a plain RSA key (PEM, not encrypted)") from None
    n, d = rsa_key(der)
    size = (n.bit_length() + 7) // 8
    digest = SHA256_INFO + hashlib.sha256(message).digest()
    padded = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    return pow(int.from_bytes(padded, "big"), d, n).to_bytes(size, "big")


def rsa_key(der: bytes) -> tuple[int, int]:
    """The modulus and private exponent of a DER RSA key: PKCS#8 (BEGIN PRIVATE KEY, as in Google's
    key files) or PKCS#1 (BEGIN RSA PRIVATE KEY)."""
    try:
        fields = _der_items(_der_items(der)[0][1])
        if fields[1][0] == 0x30:  # PKCS#8: version, algorithm, then the PKCS#1 key in an OCTET STRING
            return rsa_key(fields[2][1])
        numbers = [int.from_bytes(value, "big") for _, value in fields]  # version, n, e, d, p, q, ...
        n, d = numbers[1], numbers[3]
        if n < 2 or d < 1:  # a degenerate key (e.g. modulus 0) would crash pow(); it isn't a real RSA key
            raise ValueError("degenerate RSA key")
        return n, d
    except (IndexError, ValueError):
        raise ConfigError("the service account's private_key isn't an RSA key") from None


def _der_items(data: bytes) -> list[tuple[int, bytes]]:
    """The (tag, value) items, one after another, in DER-encoded data."""
    items, pos = [], 0
    while pos < len(data):
        tag, length = data[pos], data[pos + 1]
        pos += 2
        if length & 0x80:
            count = length & 0x7F
            length, pos = int.from_bytes(data[pos:pos + count], "big"), pos + count
        items.append((tag, data[pos:pos + length]))
        pos += length
    return items


# ----- Discovery documents -------------------------------------------------------------------------

class DiscoveryImporter(Importer):
    FORMAT = "discovery"

    def build(self, document: dict, url: str) -> dict:
        if "resources" not in document and "methods" not in document:
            raise ConfigError(f"{url}: not a Google API Discovery document")
        schemas = document.get("schemas", {})
        apis: dict[str, dict] = {}
        for words, method in _methods(document):
            parameters = method.get("parameters", {})
            order = method.get("parameterOrder", [])
            params = {}
            for name in order + sorted(set(parameters) - set(order)):
                p = parameters[name]
                # only the auth carries a credential, never a per-call param
                if is_credential(name, p.get("location", "query")) or p.get("deprecated"):
                    continue
                params[name] = self.param(p.get("location", "query"), "array" if p.get("repeated") else p.get("type"),
                                          p.get("required", False), p.get("description", ""), p.get("enum"),
                                          p.get("default"))
            path = _path(method)
            for name in re.findall(r"\{\+(\w+)\}", path):  # {+name}: the value may contain "/"
                params.setdefault(name, {"in": "path", "required": True})["reserved"] = True
            if request := method.get("request"):
                ref = request.get("$ref", "")
                fields = list(schemas.get(ref, {}).get("properties", {}))
                params["body" if "body" not in params else "request-body"] = self.body(ref, fields, True)
            self.add(apis, "-".join(self.name(word) for word in words), {
                "method": method.get("httpMethod", "GET").upper(),
                "path": "/" + re.sub(r"\{\+(\w+)\}", r"{\1}", path).lstrip("/"),
                "description": self.summary(method.get("description", "")),
                "params": params,
                "_scopes": method.get("scopes", []),
            })
        return {"description": self.summary(document.get("description") or document.get("title", "")),
                "base_url": (document.get("rootUrl", "") + document.get("servicePath", "")).rstrip("/"),
                "auth": {"type": "oauth2"}, "commands": apis}


def _path(method: dict) -> str:
    """A method's path. A resource-name path such as v1/{+name} (name=spaces/AAA) is kept as it is:
    its flatPath (v1/spaces/{spacesId}) names placeholders that aren't parameters."""
    flat = method.get("flatPath")
    if flat and set(re.findall(r"\{\+?(\w+)\}", flat)) <= set(method.get("parameters", {})):
        return flat
    return method.get("path", "")


def _methods(node: dict, words: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], dict]]:
    """Every method, with the resource names that lead to it: (("events", "list"), {...})."""
    for name, method in (node.get("methods") or {}).items():
        yield (*words, name), method
    for name, resource in (node.get("resources") or {}).items():
        yield from _methods(resource, (*words, name))


class GoogleApp(WebApp):
    """A Google API: logs in with Google (or a service account); its API is described in a Discovery document."""

    AUTH = GoogleAuth
    IMPORTER = DiscoveryImporter


__all__ = ["DiscoveryImporter", "GoogleApp", "GoogleAuth"]
