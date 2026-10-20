import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from optout import web


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTOUT_HOME", str(tmp_path))
    app = web.App()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(app))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(url):
    with urlopen(url) as r:
        return r.headers.get_content_type(), r.read()


def _post(url, data):
    req = Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    with urlopen(req) as r:
        return json.loads(r.read())


def test_index_and_overview_without_identity(server):
    ctype, body = _get(server + "/")
    assert ctype == "text/html" and b"<title>optout</title>" in body
    ov = json.loads(_get(server + "/api/overview")[1])
    assert ov["identity_exists"] is False and ov["people"] == [] and len(ov["registries"]) > 20


def test_save_identity_then_units_and_letter(server, tmp_path):
    res = _post(server + "/api/identity", {"people": [{
        "first_name": "Ann", "last_name": "Lee", "state": "CA",
        "addresses": [{"line1": "1 Main St", "city": "Oakland", "state": "CA", "zip": "94601", "current": True}],
        "emails": ["ann@example.com"], "phones": ["510-555-0100", "510-555-0101", "510-555-0102", "510-555-0103"],
    }]})
    assert res == {"ok": True}
    assert (tmp_path / "identity.yaml").exists()
    ov = json.loads(_get(server + "/api/overview")[1])
    by_id = {r["id"]: r for r in ov["registries"]}
    assert [len(u["describe"].split(",")) for u in by_id["national-dnc"]["units"]] == [3, 1]
    assert by_id["ca-drop"]["applies"] and not by_id["tx-no-call"]["applies"]
    unit = by_id["optoutprescreen"]["units"][0]
    assert unit["has_letter"]
    ctype, letter = _get(server + f"/api/letter?registry=optoutprescreen&key={unit['key']}")
    assert b"Ann Lee" in letter and b"P.O. Box 530200" in letter
    assert _post(server + "/api/mark", {"registry": "optoutprescreen", "status": "done"})["marked"] == 1
    ov = json.loads(_get(server + "/api/overview")[1])
    assert {r["id"]: r for r in ov["registries"]}["optoutprescreen"]["due"] == 0


def test_run_start_without_identity_errors(server):
    assert "error" in _post(server + "/api/run/start", {})
    assert "error" in _post(server + "/api/run/action", {"action": "done"})
