"""Filing and approvals with an inventory server (phase 5), against the fake portal AND the real server: pending must
be confirmed by the server before Sign & Submit (fail-closed); a filing result goes to the retry file first and is
never lost; another device's change stops filing before the browser opens; resolve and approvals go to the server."""
from types import SimpleNamespace

import pytest

from conftest import TODAY, atf, connect
import remote

OPTS = dict(dry=False, submit=True, auto_affirm=True, skip_ssn=True, capture=False)


def queued_on_server(server, *serials):
    connect(server)
    atf.add_queued([{"serial_number": s, "caliber": "9", "overall_length": "12"} for s in serials])
    for r in atf.load_rows():
        atf.stamp_5320(r, TODAY)


def file_all(**over):
    rows = atf.load_rows()
    atf.file_rows(rows, [i for i, r in enumerate(rows) if r["status"] not in atf.SKIP], SimpleNamespace(**{**OPTS, **over}))
    atf._FETCHED.clear()
    return {r["serial_number"]: r for r in atf.load_rows()}


def history(serial):
    return [h["operation"] for h in atf._server().get(f"/api/history/{serial}")["history"]]


def test_files_through_the_server(server, configured, portal):
    queued_on_server(server, "S1")
    state = portal([("S1", "9")])
    rows = file_all()
    assert (rows["S1"]["status"], rows["S1"]["application_number"], rows["S1"]["filed_date"]) == ("filed", "900000001", TODAY)
    assert history("S1") == ["add_queued", "mark_pending", "mark_filed"] and atf.outbox() == []
    assert "S1" in (atf.OUT / "submissions.csv").read_text(encoding="utf-8") and state["logged_out"]


def test_no_pending_from_the_server_no_submit(server, configured, portal, monkeypatch):
    queued_on_server(server, "S1")
    state = portal([("S1", "9")])
    post = remote.Server.post

    def refuse_pending(self, path, body, timeout=None):
        if path.endswith("/mark_pending"):
            raise remote.Unreachable("No answer at the server (timed out).")
        return post(self, path, body, timeout)
    with monkeypatch.context() as m:
        m.setattr(remote.Server, "post", refuse_pending)
        rows = file_all()
    assert rows["S1"]["status"] == "" and not (atf.OUT / "submissions.csv").exists()          # nothing was filed
    assert state["logged_out"] and "mark_pending" not in history("S1")


def test_a_filing_result_waits_in_the_retry_file(server, configured, portal, monkeypatch):
    queued_on_server(server, "S1", "S2")
    portal([("S1", "9"), ("S2", "9")])
    post = remote.Server.post

    def server_gone_after_submit(self, path, body, timeout=None):
        if path.endswith("/mark_filed"):
            raise remote.Unreachable("No answer at the server (connection refused).")
        return post(self, path, body, timeout)
    with monkeypatch.context() as m:
        m.setattr(remote.Server, "post", server_gone_after_submit)
        rows = file_all()
        with pytest.raises(SystemExit, match="hasn't reached your server yet"):                  # no new filing meanwhile
            atf._server_precheck(atf.load_rows(), [1])
    waiting = atf.outbox()
    assert [(e["serial"], e["number"]) for e in waiting] == [("S1", "900000001")]
    assert rows["S1"]["status"] == "pending" and rows["S2"]["status"] == ""                     # the batch stopped
    assert "900000001" in (atf.OUT / "submissions.csv").read_text(encoding="utf-8")            # logged here anyway
    assert atf.outbox_replay() == [] and not atf._outbox_path().exists()                         # the server is back
    atf._FETCHED.clear()
    s1 = next(r for r in atf.load_rows() if r["serial_number"] == "S1")
    assert (s1["status"], s1["application_number"]) == ("filed", "900000001")
    atf._outbox_add(waiting[0])                                                                  # sent twice: harmless
    assert atf.outbox_replay() == [] and history("S1").count("mark_filed") == 1


def test_another_devices_change_stops_filing_before_the_browser(server, configured, portal):
    queued_on_server(server, "S1")
    state = portal([("S1", "9")])
    rows = atf.load_rows()
    atf.edit_queued("S1", {"caliber": "45"})                                                     # e.g. the phone
    with pytest.raises(SystemExit, match="changed on your server"):
        atf.file_rows(rows, [0], SimpleNamespace(**OPTS))
    assert "page" not in state                                                                   # the browser never opened


def test_server_unreachable_filing_doesnt_start(server, configured, portal):
    queued_on_server(server, "S1")
    state = portal([("S1", "9")])
    rows = atf.load_rows()
    settings = atf.server_settings()                                                             # the server moved away
    atf.save_server_settings(dict(settings, addresses=["https://127.0.0.1:9"]))
    remote.Server.working.clear()
    with pytest.raises(SystemExit, match="can't be reached, so filing doesn't start"):
        atf.file_rows(rows, [0], SimpleNamespace(**OPTS))
    assert "page" not in state


def test_resolve_pending_on_the_server(server, configured):
    queued_on_server(server, "S1", "S2")
    for s in ("S1", "S2"):
        atf._server_op("mark_pending", {"serial": s, "filed_date": TODAY})
    atf.resolve_pending("S1", "900000123")
    atf.resolve_pending("s2")
    rows = {r["serial_number"]: r for r in atf.load_rows()}
    assert (rows["S1"]["status"], rows["S1"]["application_number"]) == ("filed", "900000123")
    assert (rows["S2"]["status"], rows["S2"]["filed_date"]) == ("", "")
    assert "900000123" in (atf.OUT / "submissions.csv").read_text(encoding="utf-8")


def test_approvals_go_to_the_server(server, configured):
    queued_on_server(server, "S1")
    atf._server_op("mark_pending", {"serial": "S1", "filed_date": TODAY})
    atf._server_op("mark_filed", {"serial": "S1", "application_number": "900000001"})
    pdf = atf.OUT / "approved" / "S1.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4 made-up approval S1")
    rec = {"type": "SILENCER", "approved": True, "serial": "S1", "approval_date": "10/30/2026", "eform_id": "1000001",
           "control_number": "20261000001", "conditions": "", "overall_length": "12", "caliber": "9", "model": "X",
           "barrel_length": "", "pdf": str(pdf)}
    unknown = dict(rec, serial="S9", eform_id="1000009", pdf="")
    sbr = dict(rec, type="RIFLE (SHORT BARREL)", serial="R1", eform_id="2000001", barrel_length="8", pdf="")
    assert atf.apply_approvals([rec, unknown, sbr], import_unknown=True) == (["S1"], ["S9"])
    assert atf.record_sbrs([rec, unknown, sbr]) == 1
    assert atf.send_approval_pdfs([rec, unknown, sbr]) == 1 and atf.send_approval_pdfs([rec]) == 0
    rows = {r["serial_number"]: r for r in atf.load_rows()}
    assert (rows["S1"]["status"], rows["S1"]["approved_date"], rows["S1"]["eform_id"]) == ("approved", "10/30/2026", "1000001")
    assert rows["S9"]["status"] == "approved" and [s["serial_number"] for s in atf.load_sbrs()] == ["R1"]
    assert atf.approved_pdf_for(rows["S1"]).read_bytes() == b"%PDF-1.4 made-up approval S1"
    assert atf.apply_approvals([rec]) == ([], [])                                                # nothing new: nothing sent
