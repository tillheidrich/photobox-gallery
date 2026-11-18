"""Guard: a gallery a stranger may not know about answers like an unknown name
on EVERY public route that takes a gallery slug (18.11.2026).

The routes are collected from the Flask url_map, so a route added later is
covered without touching this file. For a draft (unpublished) event and for an
unknown slug each route must give the same status, the same body bytes and the
same headers (Date aside). A route that cannot be exercised this way is listed
in ``SKIP`` with the reason; a new skip needs one.
"""
from __future__ import annotations

import json
import re
import secrets

import pytest

from app import db, ecosystem, gallery_settings as gsm
from tests.test_link_preview import RED_PIC, _photo

#: Prefixes of routes that are not public (operator, booth API with device key).
NOT_PUBLIC = ("/admin", "/api/v2/booth", "/api/v1/booth", "/mcp", "/kunde", "/client")

#: rule -> why it is not compared here.
SKIP: dict = {}

#: Headers that legitimately differ between two requests.
VOLATILE = {"Date"}


def _slug_rules():
    from app import create_app
    app = create_app()
    out = []
    for rule in app.url_map.iter_rules():
        if "slug" not in rule.arguments or rule.rule.startswith(NOT_PUBLIC):
            continue
        methods = sorted(m for m in rule.methods if m in ("GET", "POST"))
        for m in methods:
            out.append(pytest.param(rule.rule, m, sorted(rule.arguments), rule.endpoint,
                                    id=f"{m} {rule.rule}"))
    return out


@pytest.fixture()
def draft(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    db.set_setting("silent_mode", "0")
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Geheime Hochzeit {k}", slug=f"geheim-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 0, "gallery_json": json.dumps(gsm.clean({}))})
    ev = db.get_event_by_id(eid)
    pic = _photo(ev, RED_PIC)
    return ev, pic


def _url(rule, args, slug, pic):
    url = rule
    for a in args:
        if a == "slug":
            val = slug
        elif re.search(r"<int:%s>" % a, rule):
            val = str(pic["id"])
        elif "token" in a:
            val = pic.get("share_token") or "x"
        else:
            val = "x"
        url = re.sub(r"<(?:[a-z]+:)?%s>" % a, val, url)
    return url


def _comparable(resp, slug):
    body = resp.get_data().replace(slug.encode(), b"SLUG")
    headers = sorted((k, v.replace(slug, "SLUG")) for k, v in resp.headers if k not in VOLATILE)
    return resp.status_code, body, headers


@pytest.mark.parametrize("rule,method,args,endpoint", _slug_rules())
def test_a_draft_answers_exactly_like_an_unknown_name(client, draft, rule, method, args, endpoint):
    if rule in SKIP:
        pytest.skip(SKIP[rule])
    ev, pic = draft
    unknown = "geheim-" + secrets.token_hex(4)
    call = client.get if method == "GET" else client.post
    a = call(_url(rule, args, ev["slug"], pic))
    b = call(_url(rule, args, unknown, pic))
    sa, sb = _comparable(a, ev["slug"]), _comparable(b, unknown)
    assert sa[0] == sb[0], f"status {sa[0]} vs {sb[0]}"
    assert sa[2] == sb[2], "headers differ"
    assert sa[1] == sb[1], "body differs"
    assert ev["name"].encode() not in a.get_data()
