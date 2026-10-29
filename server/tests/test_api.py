"""The HTTP API: who may read what, pairing, the page, PDFs, export, Devices. Phase 1 has no inventory writes."""
from conftest import pair_device
from atf_server import db, tls


def test_unpaired_gets_only_the_page_and_hello(client):
    page = client.get("/")
    assert page.status_code == 200 and "<title>Inventory</title>" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    hello = client.get("/api/hello").json()
    assert hello["api"] == 1 and hello["paired"] is False and len(hello["server_id"]) == 32
    for path in ("/api/inventory", "/api/rows", "/api/rows/A101", "/api/history/A101", "/pdf/A101", "/export.csv",
                 "/api/devices"):
        r = client.get(path)
        assert r.status_code == 401 and r.json()["pair"] is True, path
    assert client.get("/api/inventory", headers={"Authorization": "Bearer nope"}).status_code == 401
    ca = client.get("/ca.crt")                                         # to trust on a phone: public, no pairing
    assert ca.status_code == 200 and ca.headers["content-type"] == "application/x-x509-ca-cert"
    assert tls.fingerprint_pem(ca.text) == hello["ca_fingerprint"]
    r = client.get("/s/A101", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/#A101"


def test_pair_from_a_browser(client, filled):
    code, _ = db.new_pair_code(filled)
    assert client.post("/api/pair", json={"code": "x", "name": "iPhone"}).status_code == 400
    r = client.post("/api/pair", json={"code": code, "name": "iPhone"})
    assert r.status_code == 200 and r.json()["key"]
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/inventory").status_code == 200                          # the cookie now
    assert client.get("/api/hello").json()["paired"] is True


def test_writes_must_be_json(client, filled):
    code, _ = db.new_pair_code(filled)
    r = client.post("/api/pair", data={"code": code, "name": "x"})                  # a form post (CSRF shape)
    assert r.status_code == 415
    assert client.post("/api/build", json={"serial": "A101", "built": False}).status_code in (401, 404, 405)


def test_inventory_for_the_page(client, auth, ca_fp):
    data = client.get("/api/inventory", headers=auth).json()
    a101 = data["suppressors"][0]
    assert (a101["serial"], a101["status"], a101["built"], a101["design"], a101["pdf"]) == ("A101", "approved", True, "PCC Short", True)
    assert a101["application"] == "…0101" and a101["eform"] == "…0101"           # last 4, like the menu
    assert {r["status"] for r in data["suppressors"]} == {"approved", "filed", "queued"}
    assert [s["serial"] for s in data["sbrs"]] == ["R1", "R2"] and data["sbrs"][0]["pdf"] is True
    assert data["can_build"] is True and a101["version"] == 1 and data["server"]["fingerprint"] == ca_fp and data["server"]["device"] == "PC"


def test_full_rows_history_pdf_export(client, auth, filled):
    rows = client.get("/api/rows", headers=auth).json()
    assert rows["suppressors"][0]["application_number"] == "900000101" and rows["suppressors"][0]["version"] == 1
    assert client.get("/api/rows/a101", headers=auth).json()["serial_number"] == "A101"
    assert client.get("/api/rows/NOPE", headers=auth).status_code == 404
    hist = client.get("/api/history/A101", headers=auth).json()["history"]
    assert [h["operation"] for h in hist] == ["import"] and hist[0]["after"]["design"] == "PCC Short"
    pdf = client.get("/pdf/A101", headers=auth)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and "A101.pdf" in pdf.headers["content-disposition"]
    assert client.get("/pdf/R2", headers=auth).status_code == 200
    assert client.get("/pdf/A130", headers=auth).status_code == 404                # filed, no approval yet
    export = client.get("/export.csv", headers=auth)
    assert export.text == db.export_csv(filled) and "attachment" in export.headers["content-disposition"]


def test_operations(client, auth, filled, caplog):
    caplog.set_level("INFO", logger="atf_server")
    r = client.post("/api/ops/set_built", headers=auth, json={"request_id": "x1", "items": [{"serial": "A110", "design": "PCC", "version": 1}]})
    assert r.status_code == 200 and r.json()["rows"][0]["built"] == "yes"
    assert "set_built" not in caplog.text                              # a build isn't even in the log
    r = client.post("/api/ops/add_queued", headers=auth, json={"rows": [{"serial_number": "A139", "caliber": "9", "overall_length": "12"}]})
    assert db.history(filled, "A139")[-1]["device"] == "PC" and "/api/ops/add_queued 200 PC" in caplog.text
    stale = client.post("/api/ops/set_built", headers=auth, json={"items": [{"serial": "A110", "design": "Other", "version": 1}]})
    assert stale.status_code == 409 and stale.json()["rows"][0]["design"] == "PCC"
    assert client.post("/api/ops/set_built", headers=auth, json={"items": [{"serial": "A130"}]}).status_code == 400
    assert client.post("/api/ops/set_built", json={"items": [{"serial": "A110"}]}).status_code == 401
    assert client.post("/api/ops/set_built", headers=auth, data="items=A110").status_code == 415


def test_devices(client, auth, filled, ca_fp):
    other_id, other_key = pair_device(filled, "iPhone")
    d = client.get("/api/devices", headers=auth).json()
    assert [x["name"] for x in d["devices"]] == ["PC", "iPhone"] and d["you"] == 1 and d["fingerprint"] == ca_fp
    code = client.post("/api/devices/code", headers=auth, json={}).json()
    assert len(code["code"]) == 6 and code["minutes"] == db.PAIR_MINUTES
    assert client.post(f"/api/devices/{other_id}/revoke", headers=auth, json={}).json() == {"ok": True}
    assert client.get("/api/inventory", headers={"Authorization": f"Bearer {other_key}"}).status_code == 401
    assert client.post(f"/api/devices/{other_id}/revoke", headers=auth, json={}).status_code == 400
