"""Moving an inventory onto the server (phase 4): the import over the API, taking it back, the PDF list, and the
console's reset (clearing made-up data) and restore (the backup drill)."""
import base64
import hashlib

import pytest

from atf_server import db, demo


def _payload():
    sup, sbrs, pdfs = demo.inventory()
    return {"suppressors": sup, "sbrs": sbrs,
            "pdfs": [{"eform_id": p["eform_id"], "serial": p["serial"], "data": base64.b64encode(p["data"]).decode()} for p in pdfs]}


@pytest.fixture
def empty_client(conn, data_dir, tls_dir):
    from fastapi.testclient import TestClient
    from atf_server.app import create_app
    from atf_server import tls
    from conftest import PAGE, pair_device
    client = TestClient(create_app(data_dir, page=PAGE, ca=tls.ca_path(tls_dir)))
    client.headers["Authorization"] = f"Bearer {pair_device(conn)[1]}"
    return client


def test_move_in_then_take_it_back(empty_client, conn, data_dir):
    r = empty_client.post("/api/import", json=_payload())
    assert r.json() == {"suppressors": 38, "sbrs": 2, "pdfs": 27}
    assert db.history(conn, "A101")[0]["device"] == "PC"
    listed = empty_client.get("/api/pdfs").json()["pdfs"]
    a101 = next(p for p in listed if p["serial"] == "A101")
    data = empty_client.get(f"/api/pdfs/{a101['sha256']}").content
    assert hashlib.sha256(data).hexdigest() == a101["sha256"]
    assert empty_client.get("/api/pdfs/" + "0" * 64).status_code == 404
    assert empty_client.get("/api/pdfs/..%2Fca").status_code == 404
    assert empty_client.post("/api/import", json=_payload()).status_code == 400          # only into an empty server
    assert empty_client.post("/api/import/undo", json={}).json() == {"ok": True}
    assert db.is_empty(conn) and not db.history(conn, "A101") and not list((data_dir / "approved").glob("*.pdf"))
    assert [d["name"] for d in db.devices(conn)] == ["PC"]                               # pairing untouched


def test_undo_only_while_nothing_changed(empty_client, conn):
    empty_client.post("/api/import", json=_payload())
    empty_client.post("/api/ops/set_built", json={"items": [{"serial": "A110", "design": "x"}]})
    r = empty_client.post("/api/import/undo", json={})
    assert r.status_code == 400 and "changed on the server since" in r.json()["error"]
    assert len(db.rows(conn)) == 38


def test_a_failed_import_leaves_nothing(empty_client, conn, data_dir):
    bad = _payload()
    bad["suppressors"][5]["status"] = "lost"
    assert empty_client.post("/api/import", json=bad).status_code == 400
    assert db.is_empty(conn) and not list((data_dir / "approved").glob("*.pdf"))         # no orphan files
    bad = _payload()
    bad["pdfs"][0]["data"] = "not base64!"
    assert empty_client.post("/api/import", json=bad).status_code == 400 and db.is_empty(conn)


def test_reset_and_restore(filled, data_dir):
    backups = data_dir / "backups"
    before_drill = db.backup(filled, backups, label="drill")
    saved = db.reset(filled, data_dir / "approved", backups)
    assert db.is_empty(filled) and saved.exists() and not list((data_dir / "approved").glob("*.pdf"))
    assert not [h for h in filled.execute("SELECT * FROM history WHERE serial IS NOT NULL")]
    db.restore(filled, before_drill, backups)
    assert len(db.rows(filled)) == 38 and db.row(filled, "A101")["design"] == "PCC Short"
    assert any(p.name.startswith("inventory-before-restore-") for p in backups.iterdir())
    with pytest.raises(db.Refused, match="isn't an inventory backup"):
        (data_dir / "junk.db").write_bytes(b"")
        db.restore(filled, data_dir / "junk.db", backups)


def test_console_reset_asks_first(filled, data_dir, monkeypatch, capsys):
    from atf_server.__main__ import main
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert main(["--data", str(data_dir), "reset"]) == 1 and len(db.rows(filled)) == 38
    monkeypatch.setattr("builtins.input", lambda prompt: "RESET")
    assert main(["--data", str(data_dir), "reset"]) == 0 and db.is_empty(filled)
