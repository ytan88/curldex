# Copyright 2026 Max Tan
# SPDX-License-Identifier: Apache-2.0
"""Param and Endpoint: reading api.yaml entries, checking values, building requests."""

import pytest

from curldex import Access, ConfigError, Curl, Endpoint, UsageError


def endpoint(spec: dict, name: str = "api") -> Endpoint:
    return Endpoint.from_config(name, spec)


@pytest.mark.parametrize(("method", "access"), [("GET", "read"), ("POST", "write"), ("PATCH", "write"),
                                                ("PUT", "destructive"), ("DELETE", "destructive")])
def test_access_comes_from_the_method(method, access):
    assert endpoint({"method": method, "path": "/x"}).access is Access(access)


@pytest.mark.parametrize(("method", "name", "access"), [
    ("POST", "freebusy-query", "read"), ("POST", "messages-search", "read"), ("POST", "get-or-create", "write"),
    ("POST", "batch-delete", "destructive"), ("POST", "calendars-clear", "destructive"),
    ("POST", "create-event", "write"), ("PUT", "list-reactions", "destructive"), ("GET", "delete-preview", "read")])
def test_access_guessed_from_the_name(method, name, access):
    assert Access.guess(method, name) is Access(access)


def test_access_can_be_set_and_is_checked():
    assert endpoint({"method": "POST", "path": "/search", "access": "read"}).access is Access.READ
    with pytest.raises(ConfigError, match="API 'api': unknown access 'maybe'"):
        endpoint({"path": "/x", "access": "maybe"})


def test_param_locations():
    e = endpoint({"method": "POST", "path": "/cal/{cid}/events/{eid}",
                  "params": {"cid": {}, "summary": {}, "tag": {"in": "query"}, "x": {"in": "header"}}})
    assert {n: p.location for n, p in e.params.items()} == \
        {"cid": "path", "summary": "body", "tag": "query", "x": "header", "eid": "path"}
    assert e.params["eid"].mandatory  # path params need no entry of their own
    assert endpoint({"path": "/x", "params": {"q": {}}}).params["q"].location == "query"


def test_bad_config_is_reported():
    with pytest.raises(ConfigError, match="needs a path"):
        endpoint({"path": "x"})
    with pytest.raises(ConfigError, match="unknown type"):
        endpoint({"path": "/x", "params": {"q": {"type": "date"}}})
    with pytest.raises(ConfigError, match="unknown method"):
        endpoint({"method": "FETCH", "path": "/x"})
    with pytest.raises(ConfigError, match="must be mappings"):
        endpoint("/x")  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="its example must be"):
        endpoint({"path": "/x", "example": ["q", 1]})
    assert endpoint({"path": "/x", "example": None}).example is None  # `example:` with no value: no example
    assert endpoint({"path": "/x", "example": {}}).example == {}  # an example with no values
    with pytest.raises(ConfigError, match="param 'q': its settings must be a mapping"):
        endpoint({"path": "/x", "params": {"q": "integer"}})
    with pytest.raises(ConfigError, match=r"param 'itemId' is in: path, but the path has no \{itemId\}"):
        endpoint({"path": "/items/{item_id}", "params": {"itemId": {"in": "path"}}})  # its value would go nowhere


def test_descriptions_are_one_line():
    """So a description written over several lines (`description: |`) keeps the docs' tables whole."""
    e = endpoint({"path": "/x", "description": "Find\nthings",
                  "params": {"q": {"description": "The text.\n  Or words."}}})
    assert e.description == "Find things" and e.params["q"].description == "The text. Or words."


def test_a_file_to_send_is_always_the_callers():
    """A default, or a path in the example, would upload a local file unasked (verify() runs reads)."""
    upload = {"method": "POST", "path": "/up", "format": "multipart"}
    with pytest.raises(ConfigError, match="param 'doc': a file param has no default"):
        endpoint({**upload, "params": {"doc": {"type": "file", "default": "~/.ssh/id_rsa"}}})
    with pytest.raises(ConfigError, match="API 'api': its example's doc is a local file to send: use a stand-in"):
        endpoint({**upload, "params": {"doc": {"type": "file"}}, "example": {"doc": "~/.ssh/id_rsa"}})
    assert endpoint({**upload, "params": {"doc": {"type": "file"}}, "example": {"doc": "<path_to_a_file>"}}).example


def test_one_allowed_value_needs_no_brackets():
    assert endpoint({"path": "/x", "params": {"sort": {"enum": "newest"}}}).params["sort"].enum == ("newest",)
    assert endpoint({"path": "/x", "params": {"n": {"type": "integer", "enum": [1, 2]}}}).params["n"].enum == (1, 2)
    with pytest.raises(ConfigError, match="param 'sort': enum must be a list"):
        endpoint({"path": "/x", "params": {"sort": {"enum": {"new": 1}}}})


def test_values_fill_defaults_and_check_types():
    e = endpoint({"path": "/x/{cid}", "params": {"cid": {"default": "${uid}"}, "n": {"type": "integer", "default": 5},
                                                 "kind": {"enum": ["a", "b"]}, "q": {}}})
    assert e.values({"q": 7}, {"uid": "u1"}.get) == {"cid": "u1", "n": 5, "q": "7"}  # numbers are fine as strings
    with pytest.raises(UsageError, match="'n' expects integer"):
        e.values({"n": "5"}, {"uid": "u1"}.get)
    with pytest.raises(UsageError, match="'n' expects integer"):
        e.values({"n": True}, {"uid": "u1"}.get)
    with pytest.raises(UsageError, match="must be one of a, b"):
        e.values({"kind": "c"}, {"uid": "u1"}.get)
    with pytest.raises(UsageError, match="unknown params nope"):
        e.values({"nope": 1}, {"uid": "u1"}.get)
    with pytest.raises(ConfigError, match=r"set uid in credentials\.yaml"):
        e.values({}, {}.get)
    with pytest.raises(UsageError, match="missing required param 'id'"):
        endpoint({"path": "/x/{id}"}).values({}, {}.get)


def test_a_caller_may_send_an_empty_string():
    """"" from the caller is a real value (clear a field); only None leaves a param out."""
    e = endpoint({"method": "PATCH", "path": "/x", "params": {"note": {}, "n": {"type": "integer", "default": 5}}})
    assert e.values({"note": ""}, {}.get) == {"note": "", "n": 5}
    assert e.values({"note": None}, {}.get) == {"n": 5}


def test_non_string_values_are_json_not_repr_in_path_and_header():
    e = endpoint({"path": "/t/{on}", "params": {"on": {"type": "boolean"}, "x": {"in": "header", "type": "boolean"},
                                                "m": {"in": "header", "type": "object"}}})
    req = e.build(e.values({"on": True, "x": False, "m": {"a": 1}}, {}.get))
    assert req.path == "/t/true" and req.headers == {"x": "false", "m": '{"a":1}'}


def test_a_header_value_cant_inject_another_header():
    e = endpoint({"path": "/h", "params": {"X": {"in": "header"}}})
    with pytest.raises(UsageError, match="control character"):
        e.build(e.values({"X": "ok\r\nX-Injected: evil"}, {}.get))


def test_a_path_value_cant_escape_the_base_url():
    e = endpoint({"method": "DELETE", "path": "/items/{id}/sub/{sid}",
                  "params": {"id": {}, "sid": {"reserved": True}}})
    with pytest.raises(UsageError, match="can't be"):
        e.build(e.values({"id": "..", "sid": "x"}, {}.get))
    with pytest.raises(UsageError, match="can't be"):
        e.build(e.values({"id": "1", "sid": "a/../b"}, {}.get))  # a reserved value's segments too


def test_a_body_field_name_cant_break_out():
    """curl -F / --data-urlencode would read = ; " as structure; refused when the app loads."""
    for bad in ('image=@/etc/passwd', 'x;type=text/x', 'a"b'):
        with pytest.raises(ConfigError, match="a body field name can't hold"):
            endpoint({"method": "POST", "path": "/x", "params": {bad: {}}})


def test_a_stand_in_is_allowed_for_any_type_and_an_array_enum_checks_each_item():
    e = endpoint({"path": "/x/{id}", "params": {"id": {"type": "integer"},
                                                "cols": {"type": "array", "enum": ["A", "B"]}}})
    assert e.values({"id": "<id>", "cols": ["A", "B"]}, {}.get) == {"id": "<id>", "cols": ["A", "B"]}
    with pytest.raises(UsageError, match="must be one of A, B; got 'C'"):
        e.values({"id": 1, "cols": ["A", "C"]}, {}.get)


def test_fields_cant_ride_a_non_object_whole_body():
    e = endpoint({"method": "POST", "path": "/x", "params": {"body": {"in": "json", "type": "array"}, "extra": {}}})
    with pytest.raises(UsageError, match="nowhere to put extra"):
        e.build(e.values({"body": [1, 2], "extra": "e"}, {}.get))


def test_build_places_each_value():
    e = endpoint({"method": "PATCH", "path": "/files/{name}/{id}",
                  "params": {"name": {"reserved": True}, "on": {"type": "boolean", "in": "query"},
                             "tags": {"type": "array", "in": "query"}, "x": {"in": "header"},
                             "title": {}, "whole": {"in": "json"}}})
    req = e.build({"name": "a/b", "id": "c/d e", "on": True, "tags": ["t1", "t2"], "x": 3,
                   "title": "T", "whole": {"k": 1}})
    assert req.path == "/files/a/b/c%2Fd%20e"
    assert req.query == {"on": "true", "tags": ["t1", "t2"]}
    assert req.headers == {"x": "3"}
    assert req.body == {"k": 1, "title": "T"}


def test_an_empty_post_body_only_for_json_and_form():
    """Some services refuse a POST without a body; but an empty multipart or raw body is no body."""
    assert endpoint({"method": "POST", "path": "/x"}).build({}).body == {}
    assert endpoint({"method": "POST", "path": "/x", "format": "form"}).build({}).body == {}
    assert endpoint({"method": "DELETE", "path": "/x"}).build({}).body is None
    for body_format in ("multipart", "raw"):
        req = endpoint({"method": "POST", "path": "/x", "format": body_format}).build({})
        assert req.body is None
        assert Curl(req.method, "https://h/x", body=req.body, format=body_format).args() == \
            ["curl", "-q", "-sS", "-g", "-X", "POST", "https://h/x"]  # still a POST, with nothing in it


def test_a_path_value_cant_be_empty_and_a_path_param_is_always_required():
    e = endpoint({"method": "DELETE", "path": "/files/{id}", "params": {"id": {"required": False}}})
    assert e.params["id"].mandatory  # else a literal {id} would be sent
    with pytest.raises(UsageError, match="param 'id' can't be empty"):
        e.build(e.values({"id": ""}, lambda key: None))  # DELETE /files/ would act on the collection


def test_a_raw_body_keeps_a_content_type_param_in_any_case():
    e = endpoint({"method": "POST", "path": "/up", "format": "raw",
                  "params": {"content-type": {"in": "header"}, "data": {"in": "json", "type": "string"}}})
    assert e.build({"content-type": "text/csv", "data": "a,b"}).headers == {"content-type": "text/csv"}


def test_an_empty_header_value_is_sent_empty():
    assert Curl("GET", "https://x.test/", {"X-Empty": ""}).args()[4:6] == ["-H", "X-Empty;"]


def test_a_raw_body_takes_no_body_fields():
    with pytest.raises(ConfigError, match="a raw body is one in: json param"):
        endpoint({"method": "POST", "path": "/r", "format": "raw", "params": {"text": {}}})


def test_a_blank_header_value_is_sent_empty():
    assert Curl("GET", "https://x.test/", {"X-Blank": " "}).args()[4:6] == ["-H", "X-Blank;"]
