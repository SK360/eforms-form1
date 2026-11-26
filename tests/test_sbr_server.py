"""SBRs (SBS, AOW...) with the inventory server (API 5), against the REAL server: queued and edited there with their
markings, filed through it (pending confirmed first, the manufacturer code found on eForms kept), out of its queue once
approved, moved to it and copied back; an older server is named as the reason, before anything is saved."""
from types import SimpleNamespace

import pytest

from conftest import TODAY, atf, connect
import remote
from test_sbr import sbr

OPTS = dict(dry=False, submit=True, auto_affirm=True, skip_ssn=True, capture=False)


def rows():
    atf._FETCHED.clear()
    return {r["serial_number"]: r for r in atf.load_rows()}


def history(serial):
    return [h["operation"] for h in atf._server().get(f"/api/history/{serial}")["history"]]


def approval(serial="XYZ123"):
    return {"type": "SHORT BARRELED RIFLE", "approved": True, "serial": serial, "approval_date": "12/01/2026", "eform_id": "3000001",
            "control_number": "", "conditions": "", "overall_length": "30", "caliber": "5.56", "model": "M4E1",
            "barrel_length": "10.3", "pdf": ""}


def test_an_sbr_is_queued_and_edited_on_the_server(server, configured):
    connect(server)
    atf.add_queued([sbr()])
    r = rows()["XYZ123"]
    assert atf.is_nfa_row(r) and {k: r[k] for k in atf.NFA_QUEUE_COLS} == {k: sbr()[k] for k in atf.NFA_QUEUE_COLS}
    assert atf.nfa_row_problems(r) == []
    atf.edit_queued("XYZ123", {"mfr_name": "OTHER ARMS INC"}, version=r["version"])    # a new name: looked up again
    r = rows()["XYZ123"]
    assert (r["mfr_name"], r["mfr_code"]) == ("OTHER ARMS INC", "") and history("XYZ123") == ["add_queued", "edit_queued"]
    page = atf._server().get("/api/inventory")["suppressors"]
    assert [(p["serial"], p["type"], p["model"], p["barrel"]) for p in page] == [("XYZ123", "SHORT BARRELED RIFLE", "M4E1", "10.3")]


def test_an_sbr_files_through_the_server(server, configured, portal):
    connect(server)
    atf.add_queued([sbr()])
    atf.stamp_5320(rows()["XYZ123"], TODAY)
    state = portal([("XYZ123", "5.56")])
    todo = atf.load_rows()
    atf.file_rows(todo, [0], SimpleNamespace(**OPTS))
    r = rows()["XYZ123"]
    assert (r["status"], r["application_number"], r["filed_date"]) == ("filed", "900000001", TODAY) and state["logged_out"]
    assert history("XYZ123") == ["add_queued", "mark_pending", "mark_filed"] and atf.outbox() == []


def test_a_code_found_on_eforms_is_kept_with_the_pending_row(server, configured):
    connect(server)
    atf.add_queued([sbr(mfr_code="")])
    todo = atf.load_rows()
    todo[0]["mfr_code"] = "AP2"                       # what _file_one keeps after the name search (not saved yet)
    atf._set_pending(todo, 0, "30")
    assert (rows()["XYZ123"]["status"], rows()["XYZ123"]["mfr_code"]) == ("pending", "AP2")


def test_an_approved_sbr_leaves_the_server_queue(server, configured):
    connect(server)
    atf.add_queued([sbr()])
    todo = atf.load_rows()
    atf._set_pending(todo, 0, "30")
    assert atf._set_filed(todo, 0, "2027000001")
    assert atf.record_sbrs([approval()]) == 1
    assert "XYZ123" not in rows() and [s["serial_number"] for s in atf.load_sbrs()] == ["XYZ123"]
    assert history("XYZ123")[-1] == "record_sbrs"


def test_a_queue_with_an_sbr_moves_to_the_server_and_back(server, configured):
    atf.save_rows([dict({c: "" for c in atf.QUEUE_COLS}, serial_number="S1", caliber="9", overall_length="12"),
                   dict({c: "" for c in atf.QUEUE_COLS}, **sbr())])
    connect(server)
    rep = atf.move_to_server()
    assert rep["moved"] and rep["problems"] == []
    assert rows()["XYZ123"]["mfr_name"] == "EXAMPLE ARMS LLC" and not atf.is_nfa_row(rows()["S1"])
    back = atf.copy_from_server()
    assert back["problems"] == []
    assert atf._read_csv(atf.SUPP)[0].keys() >= set(atf.NFA_QUEUE_COLS)


def test_an_older_server_is_named_before_anything_is_saved(server, configured, monkeypatch):
    connect(server)
    get = remote.Server.get
    with monkeypatch.context() as m:
        m.setattr(remote.Server, "get", lambda self, path, **kw: {"api": 4} if path == "/api/hello" else get(self, path, **kw))
        with pytest.raises(atf.ServerReadOnly, match="too old"):
            atf.add_queued([sbr()])
        assert any("too old" in p for p in atf.nfa_row_problems(dict({c: "" for c in atf.QUEUE_COLS}, **sbr())))
        atf.add_queued([{"serial_number": "S1", "caliber": "9", "overall_length": "12"}])      # suppressors still work
    assert sorted(rows()) == ["S1"]
