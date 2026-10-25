# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Finding APIs: the OpenAPI importer; listing and appending the APIs api.yaml lacks."""

import json
from pathlib import Path

import pytest
import yaml

from curldex import ConfigError, Importer, OpenAPIImporter, UsageError, WebApp
from curldex.discover import found


@pytest.fixture
def source(pets, fake) -> str:
    fake.documents["/openapi.yaml"] = yaml.safe_dump(pets)
    return f"{fake.url}/openapi.json"


def pets_app(directory: Path, api: str) -> WebApp:
    """An app with only config/api.yaml, which names where its API is described."""
    (directory / "config").mkdir(parents=True)
    (directory / "config" / "api.yaml").write_text(api)
    return WebApp(directory)


def test_openapi_import(source, fake, tmp_path):
    content = found(pets_app(tmp_path / "pets", f"source: {{url: '{source}'}}\n"))
    apis = content["commands"]
    assert content["base_url"] == f"{fake.url}/v1" and content["description"] == "A pet store."
    assert list(apis) == ["batch-delete-pets", "create-pet", "delete-pet", "list-pets", "search-pets", "show-pet"]
    assert apis["list-pets"]["params"] == {"limit": {"in": "query", "type": "integer"}}
    assert apis["create-pet"]["params"]["body"] == {"in": "json", "type": "object", "required": True,
                                                    "description": "Pet object: name, tag"}
    assert apis["search-pets"]["access"] == "read" and apis["batch-delete-pets"]["access"] == "destructive"
    assert content["auth"] == {"type": "oauth2", "authorize_url": f"{fake.url}/auth",
                               "token_url": f"{fake.url}/token", "scopes": ["pets.read", "pets.write"]}


def test_an_app_names_what_it_keeps(source, tmp_path, capsys):
    app = pets_app(tmp_path / "pets", f"auth: {{type: oauth2, scopes: [pets.read]}}\n"
                                      f"source: {{url: '{source}', include: ['*-pet*'], exclude: ['delete-*']}}\n")
    assert list(found(app)["commands"]) == ["batch-delete-pets", "list-pets", "search-pets", "show-pet"]
    assert "left out, as auth.scopes don't allow them: create-pet" in capsys.readouterr().out


def test_one_name_to_include_or_exclude_needs_no_brackets(source, tmp_path):
    """Read as one glob per character, `exclude: delete-pet` would leave out nothing."""
    app = pets_app(tmp_path / "pets", f"source: {{url: '{source}', include: '*-pet', exclude: delete-pet}}\n")
    assert list(found(app)["commands"]) == ["create-pet", "show-pet"]


def test_yaml_and_file_descriptions(source, pets, fake, tmp_path):
    assert found(pets_app(tmp_path / "a", f"source: {{url: '{fake.url}/openapi.yaml'}}\n"))["commands"]
    app = pets_app(tmp_path / "b", "source: {url: openapi.json}\n")  # a file next to api.yaml
    (app.config_dir / "openapi.json").write_text(json.dumps(pets))
    assert found(app)["base_url"] == "/v1"  # relative, from a file: only a suggestion, which the author fixes
    doc = {**pets, "servers": [{"url": "https://pets.test/{v}", "variables": {"v": {"default": "v2"}}}]}
    (app.config_dir / "openapi.json").write_text(json.dumps(doc))
    assert found(app)["base_url"] == "https://pets.test/v2"


@pytest.mark.parametrize("stub", ["commands:\n", "commands: {}\n", ""])  # a bare or empty `commands:` last, or none
def test_discover_starts_from_a_stub(source, fake, tmp_path, capsys, stub):
    app = pets_app(tmp_path / "pets", f"# my notes\nsource: {{url: '{source}'}}  # where it's described\n{stub}")
    assert len(app.discover()) == 6
    out = capsys.readouterr().out
    suggested = yaml.safe_load(out.split(":\n\n", 1)[1].split("\npets: ")[0])  # paste-ready YAML
    assert suggested["base_url"] == f"{fake.url}/v1" and suggested["auth"]["type"] == "oauth2"
    assert app.discover(add=["*"]) == ["batch-delete-pets", "create-pet", "delete-pet", "list-pets", "search-pets",
                                       "show-pet"]
    assert len(app.endpoints) == 6 and "### list-pets" in app.docs_file.read_text()
    assert "# my notes" in app.api_file.read_text()


def test_discover_writes_nothing_it_cant_append(source, tmp_path):
    """`commands:` not last would need a rewrite that loses the comments; an API that wouldn't load (a
    header param named `X Trace`) would leave api.yaml unloadable. Both are refused, the file untouched."""
    app = pets_app(tmp_path / "pets", f"# my notes\nsource: {{url: '{source}'}}\ncommands:\nheaders: {{}}\n")
    before = app.api_file.read_text()
    with pytest.raises(ConfigError, match=r"`commands:` isn't api.yaml's last block"):
        app.discover(add=["*"])
    assert app.api_file.read_text() == before
    from curldex.discover import append
    with pytest.raises(ConfigError, match="a header name is"):
        append(app, {"bad": {"path": "/x", "params": {"X Trace": {"in": "header"}}}})
    assert app.api_file.read_text() == before


def test_discover_lists_and_adds_what_api_yaml_lacks(source, tmp_path, capsys):
    app = pets_app(tmp_path / "pets", f"# curated by hand\ndescription: Pets\nbase_url: {source}\n"
                                      f"auth: {{type: none}}\nsource: {{url: '{source}'}}\ncommands:\n"
                                      "  pets:  # renamed from list-pets\n    path: /pets/\n")
    missing = app.discover()
    assert "list-pets" not in missing and "show-pet" in missing  # matched by method and path
    assert "suggests" not in capsys.readouterr().out  # description, base_url and auth are set
    assert app.discover(add=["show-*", "search-pets"]) == ["search-pets", "show-pet"]
    text = app.api_file.read_text()
    assert text.startswith("# curated by hand") and "# renamed from list-pets" in text
    assert text.count("#") == 2  # the file's own comments, and none added
    assert list(app.endpoints) == ["pets", "search-pets", "show-pet"]
    with pytest.raises(UsageError, match="no missing API matches"):
        app.discover(add=["nothing-*"])


def test_discover_lists_the_missing_apis_in_columns(tmp_path, capsys):
    doc = {"openapi": "3.0.0", "info": {}, "paths": {
        "/devices": {"get": {"operationId": "listDevices", "summary": "List devices."}},
        "/devices/{device_id}/reboot": {"post": {"operationId": "rebootDevice", "summary": "Reboot a device."}}}}
    app = pets_app(tmp_path / "testbed", "source: {url: openapi.json}\n")
    (app.config_dir / "openapi.json").write_text(json.dumps(doc))
    app.discover()
    rows = [line for line in capsys.readouterr().out.splitlines() if " GET " in line or " POST " in line]
    assert rows == ["  list-devices   GET    /devices                     List devices.",
                    "  reboot-device  POST   /devices/{device_id}/reboot  Reboot a device."]


def test_discover_needs_api_yaml_with_a_source(tmp_path):
    with pytest.raises(ConfigError, match=r"no config/api.yaml yet; write it .* lists the APIs its source"):
        WebApp(tmp_path / "new").discover()
    with pytest.raises(ConfigError, match=r"Set source: \{url: \.\.\.\} in api.yaml"):
        pets_app(tmp_path / "pets", "base_url: https://x.test\n").discover()
    with pytest.raises(ConfigError, match=r"unknown settings in api.yaml's source: format"):
        pets_app(tmp_path / "old", "source: {format: openapi, url: https://x.test/o.json}\n")


def test_apis_that_share_a_route_are_told_apart_by_name():
    from curldex.discover import lacking
    found = {"spaces-get": {"method": "GET", "path": "/v1/{name}"},
             "messages-get": {"method": "GET", "path": "/v1/{name}"}}
    assert list(lacking({"spaces-get": {"path": "/v1/{name}"}}, found)) == ["messages-get"]


def test_importer_helpers():
    assert Importer.name("users.messages.batchDelete") == "users-messages-batch-delete"
    assert Importer.summary("First. Second.") == "First."
    assert Importer.summary("Moves an event to another calendar, i.e. changes an event's organizer. Note that...") \
        == "Moves an event to another calendar, i.e. changes an event's organizer."
    assert Importer.summary("Lists events, e.g. Google Meet ones. More.") == "Lists events, e.g. Google Meet ones."
    assert Importer.param("query", "integer", False, "", None, 5) == {"in": "query", "type": "integer"}
    assert Importer.param("query", "string", True, default="${env:HOME}") == {"in": "query", "required": True}
    apis: dict = {}
    Importer.add(apis, "get-item", {"method": "GET", "path": "/items/{id}", "params": {
        "id": {"in": "path"}, "itemId": {"in": "path"}, "q": {"in": "query"}}})
    assert list(apis["get-item"]["params"]) == ["id", "q"]  # the description's itemId names nothing in the path


def test_an_openapi_body_that_is_an_array():
    schema = {"type": "array", "items": {"type": "string"}}
    doc = {"openapi": "3.0.0", "info": {}, "paths": {"/batch": {"post": {"operationId": "batch", "requestBody": {
        "required": True, "content": {"application/json": {"schema": schema}}}}}}}
    assert OpenAPIImporter().build(doc, "")["commands"]["batch"]["params"] == {
        "body": {"in": "json", "type": "array", "description": "JSON array", "required": True}}


def test_scopes_needed_together_stay_together():
    from curldex.discover import _scopes, allows
    assert _scopes({}, {"security": [{"auth": ["a", "b"]}, {"auth": ["c"]}]}) == ["a b", "c"]
    assert _scopes({}, {"security": [{"auth": ["a", "b"]}, {"key": []}]}) == []  # a key alone will do
    assert allows(["a", "b"], {"a b"}) and allows(["c"], {"a b", "c"}) and not allows(["a"], {"a b"})


def test_openapi_form_multipart_and_3_1_types():
    doc = {"openapi": "3.1.0", "info": {"title": "T"}, "servers": [{"url": "https://t.test"}],
           "components": {"securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "Authorization"},
                                              "oauth": {"type": "oauth2", "flows": {}}}},
           "security": [{"key": []}, {"oauth": ["read"]}],
           "paths": {"/charges": {"post": {"operationId": "createCharge", "requestBody": {"content": {
               "application/x-www-form-urlencoded": {"schema": {"required": ["amount"], "properties": {
                   "amount": {"type": "integer"}, "note": {"type": ["string", "null"]}}}}}}}},
               "/files": {"post": {"operationId": "uploadFile", "requestBody": {"content": {"multipart/form-data": {
                   "schema": {"properties": {"file": {"type": "string", "format": "binary"}}}}}}}},
               "/items": {"get": {"operationId": "listItems", "parameters": [
                   {"name": "q", "in": "query", "schema": {"type": ["string", "null"]}}]}}}}
    found = OpenAPIImporter().build(doc, "")
    apis = found["commands"]
    assert apis["create-charge"]["format"] == "form"
    assert apis["create-charge"]["params"] == {"amount": {"in": "body", "type": "integer", "required": True},
                                              "note": {"in": "body"}}
    assert apis["upload-file"]["format"] == "multipart" and apis["upload-file"]["params"]["file"]["type"] == "file"
    assert apis["list-items"]["params"] == {"q": {"in": "query"}} and "format" not in apis["list-items"]
    assert all(api["_scopes"] == [] for api in apis.values())  # the API key alone allows each
    assert found["auth"] == {"type": "api_key", "in": "header", "name": "Authorization"}


def test_an_openapi_client_credentials_flow_suggests_oauth2_client():
    from curldex.discover import _auth

    def document(*flows):
        flow = {"tokenUrl": "https://example.com/token", "scopes": {"read": "Read"}, "authorizationUrl": "https://x"}
        return {"components": {"securitySchemes": {"oauth": {"type": "oauth2", "flows": dict.fromkeys(flows, flow)}}}}
    assert _auth(document("clientCredentials")) == {"type": "oauth2_client", "token_url": "https://example.com/token",
                                                     "scopes": ["read"]}
    assert _auth(document("clientCredentials", "authorizationCode"))["type"] == "oauth2"  # a login, if offered


def test_credential_params_and_odd_bodies_and_refs(tmp_path):
    """Credential params aren't imported, a JSON-pointer $ref resolves, a non-JSON body is raw, and a
    media type with ;charset is still JSON."""
    doc = {"openapi": "3.0.3", "info": {"title": "T"}, "servers": [{"url": "https://api.test/v1"}],
           "components": {"securitySchemes": {"k": {"type": "apiKey", "in": "header", "name": "X-Secret-Key"}}},
           "paths": {
               "/items": {"get": {"operationId": "listItems", "parameters": [
                   {"$ref": "#/paths/~1items/get/x"}, {"name": "token", "in": "query", "schema": {}},
                   {"name": "X-Secret-Key", "in": "header", "schema": {}}],
                   "x": {"name": "page", "in": "query", "schema": {"type": "integer"}}}},
               "/up": {"post": {"operationId": "uploadBlob", "requestBody": {"required": True, "content": {
                   "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}}},
               "/j": {"post": {"operationId": "makeJ", "requestBody": {"content": {
                   "application/json; charset=utf-8": {"schema": {"properties": {"a": {}}}}}}}}}}
    app = pets_app(tmp_path / "oa", "base_url: https://api.test/v1\nsource: {url: openapi.json}\ncommands:\n")
    (app.config_dir / "openapi.json").write_text(json.dumps(doc))
    assert app.discover(add=["*"]) == ["list-items", "make-j", "upload-blob"]
    assert list(app.endpoints["list-items"].params) == ["page"]  # token and the API key left out
    up = app.endpoints["upload-blob"]
    assert up.format == "raw" and up.content_type == "application/octet-stream"
    assert next(iter(up.params.values())).type == "file"
    assert app.endpoints["make-j"].format == "json"


def test_source_url_cant_escape_the_config_folder(tmp_path):
    """A file source.url can't read outside config/ (absolute, ~, or ..), and a parse error of a file
    doesn't echo its contents."""
    from curldex import ConfigError
    from curldex.discover import found
    secret = tmp_path / "secret.txt"
    secret.write_text('api_key: "AKIA_SECRET')  # invalid YAML (unterminated quote)
    for bad in (str(secret), "~/x", "../../etc/passwd"):
        api = f"base_url: https://x.test\nsource: {{url: '{bad}'}}\ncommands:\n"
        app = pets_app(tmp_path / f"a{abs(hash(bad))}", api)
        with pytest.raises(ConfigError, match="must be a file inside the app's config folder") as e:
            found(app)
        assert "AKIA_SECRET" not in str(e.value)


def test_shape_masks_a_data_keyed_map():
    """An object whose values are all objects is a map keyed by data (usernames/ids): its keys are data."""
    from curldex.verify import shape
    m = shape({"alice": {"email": "a", "role": "r"}, "bob": {"email": "b"}})
    assert m == "{<key>: {email: string, role: string}}"
    assert shape({"alice": {"role": "r"}, "bob": {}}) == "{<key>: {role: string}}"  # even an empty record
    assert shape({"chan7": [1, 2], "chanX": [3]}) == "{<key>[]: integer}"  # array-valued map
    # an object with scalar fields too (a normal record, e.g. an event) keeps its field names
    assert shape({"id": "x", "start": {"dateTime": "t"}}) == "{id: string, start: {dateTime: string}}"


def test_openapi_3_1_binary_upload_is_a_file(tmp_path):
    """OpenAPI 3.1 uses contentEncoding: binary, not format: binary."""
    doc = {"openapi": "3.1.0", "info": {}, "servers": [{"url": "https://x.test"}], "paths": {"/up": {"post": {
        "operationId": "up", "requestBody": {"content": {"multipart/form-data": {"schema": {"properties": {
            "file": {"type": "string", "contentEncoding": "binary"}, "note": {"type": "string"}}}}}}}}}}
    app = pets_app(tmp_path / "u", "base_url: https://x.test\nsource: {url: openapi.json}\ncommands:\n")
    (app.config_dir / "openapi.json").write_text(json.dumps(doc))
    app.discover(add=["*"])
    assert app.endpoints["up"].params["file"].type == "file" and app.endpoints["up"].format == "multipart"


def test_a_ref_cycle_and_a_number_like_name_dont_corrupt(tmp_path, capsys):
    """A $ref loop ends rather than hangs; a name YAML 1.2 reads as a number is quoted or refused."""
    from curldex.config import dump_yaml, parse_yaml
    assert parse_yaml(dump_yaml({"1e3": "x", "on": 1})) == {"1e3": "x", "on": 1}  # keys stay text
    doc = {"openapi": "3.0.3", "info": {}, "components": {"parameters": {
        "a": {"$ref": "#/components/parameters/b"}, "b": {"$ref": "#/components/parameters/a"}}},
        "paths": {"/x": {"get": {"operationId": "getX",
                                 "parameters": [{"$ref": "#/components/parameters/a"}]}}}}
    app = pets_app(tmp_path / "cyc", "base_url: https://x.test\nsource: {url: openapi.json}\ncommands:\n")
    (app.config_dir / "openapi.json").write_text(json.dumps(doc))
    assert app.discover(add=["*"]) == ["get-x"]  # resolves to {} and doesn't hang


def test_shape_masks_a_map_with_a_null_hole():
    """A sparse object/array map (some entries null) still masks its data keys."""
    from curldex.verify import shape
    assert shape({"U01x": {"name": "n"}, "U02y": None}) == "{<key>: {name: string}}"
    assert shape({"a": [1], "b": None}) == "{<key>[]: integer}"
    assert shape({"id": "x", "profile": {"a": "b"}, "count": 2}) == "{id: string, profile: {a: string}, count: integer}"


def test_a_hostile_path_cant_carry_control_characters():
    """An ESC in a description's path is stripped so it can't reach the terminal/api.yaml/API.md."""
    from curldex.discover import no_controls
    assert no_controls("/a\x1b[31mX/{id}") == "/a[31mX/{id}"


def test_openapi_import_details(make_app, fake, pets):
    """An operation served from another host is left out; anyOf gives a type; a deprecated param, an empty
    form body and HTML tags in a summary are dropped."""
    from curldex.discover import OpenAPIImporter
    doc = {"openapi": "3.0.0", "servers": [{"url": "https://api.x.test"}], "paths": {
        "/assets": {"post": {"operationId": "upload", "servers": [{"url": "https://uploads.x.test"}]}},
        "/items": {"get": {"operationId": "list", "summary": "<p>List the <code>items</code>.</p>",
                           "parameters": [{"name": "old", "in": "query", "deprecated": True},
                                          {"name": "meta", "in": "query", "schema": {"anyOf": [{"type": "object"},
                                                                                              {"type": "string"}]}}],
                           "requestBody": {"content": {"application/x-www-form-urlencoded": {"schema": {}}}}}}}}
    apis = OpenAPIImporter().build(doc, "https://api.x.test/openapi.json")["commands"]
    assert list(apis) == ["list"]
    assert apis["list"]["description"] == "List the items."
    assert apis["list"]["params"] == {"meta": {"in": "query", "type": "object"}} and "format" not in apis["list"]


def test_every_imported_text_loses_its_control_characters():
    """A hostile description can't put an escape into help, nor a line break into a request header."""
    from curldex.discover import OpenAPIImporter
    doc = {"openapi": "3.0.0", "paths": {"/u": {"post": {"operationId": "up", "parameters": [
        {"name": "q\x1b[31m", "in": "query", "schema": {"enum": ["\x1b[2Jx"]}}],
        "requestBody": {"content": {"text/plain\r\nX-Injected: yes": {"schema": {"type": "string"}}}}}}}}
    api = OpenAPIImporter().build(doc, "https://x.test/openapi.json")["commands"]["up"]
    assert "q[31m" in api["params"] and api["params"]["q[31m"]["enum"] == ["[2Jx"]
    assert api["content_type"] == "text/plainx-injected: yes"


def test_discover_fits_paths_to_base_url_and_names_bodies(tmp_path, capsys):
    """base_url .../api/v2 with a description whose server lacks /api/v2 (PokeAPI): paths come relative to
    base_url; a typo in --add is named; a whole body whose object has a `body` field is `request-body`."""
    app = pets_app(tmp_path / "poke", "base_url: https://poke.test/api/v2\nsource: {url: openapi.json}\ncommands:\n")
    issue = {"content": {"application/json": {"schema": {"properties": {"title": {}, "body": {}}}}}}
    paths = {"/api/v2/pokemon": {"get": {"operationId": "list"}, "post": {"operationId": "add", "requestBody": issue}}}
    (app.config_dir / "openapi.json").write_text(
        json.dumps({"openapi": "3.1.0", "servers": [{"url": "https://poke.test"}], "paths": paths}))
    commands = found(app)["commands"]
    assert commands["list"]["path"] == "/pokemon" and "request-body" in commands["add"]["params"]
    assert app.discover(add=["list", "lsit"]) == ["list"]
    assert "no missing API matches lsit" in capsys.readouterr().out


def test_a_malformed_description_is_a_clean_error(tmp_path):
    app = pets_app(tmp_path / "bad", "source: {url: openapi.json}\ncommands:\n")
    (app.config_dir / "openapi.json").write_text('{"openapi": "3.0.0", "paths": {"/x": "str"}}')
    with pytest.raises(ConfigError, match="not a description curldex can read"):
        found(app)
