"""Moving the inventory to the server and back (phase 4), against the REAL server (server/atf_server, run from
server/.venv in a temp folder; skipped where that isn't installed). Made-up records only."""
import time

import pytest

from conftest import atf, connect, pick, ui
import remote

QUIT = lambda kw: "quit"  # noqa: E731


def inventory():
    """A made-up inventory with every kind of row: queued, pending, filed, approved (built or not, with conditions),
    a never-file row, SBRs, approval PDFs listed in index.csv and one only named after its serial."""
    rows = [{"serial_number": "A101", "caliber": "22", "overall_length": "12", "status": "approved", "application_number": "900000101",
             "filed_date": "08/10/2026", "approved_date": "10/15/2026", "eform_id": "1000101", "control_number": "20261000101",
             "conditions": "Mark per 27 CFR 479.102", "built": "yes", "design": "PCC Short"},
            {"serial_number": "A102", "caliber": ".22", "overall_length": "6.5", "status": "approved", "application_number": "900000102",
             "filed_date": "08/10/2026", "approved_date": "10/15/2026", "eform_id": "1000102"},
            {"serial_number": "A103", "caliber": "9", "overall_length": "12", "status": "approved", "approved_date": "02/14/2026",
             "eform_id": "1000103", "control_number": "2026100010"},
            {"serial_number": "A120", "caliber": "5.56", "overall_length": "12", "status": "filed", "application_number": "900000120",
             "filed_date": "10/20/2026"},
            {"serial_number": "A121", "caliber": "5.56", "overall_length": "12", "status": "pending", "filed_date": "10/28/2026"},
            {"serial_number": "A130", "caliber": "9", "overall_length": "12", "status": ""},
            {"serial_number": "A131", "caliber": "9", "overall_length": "", "status": "skip"}]
    atf.save_rows([{c: r.get(c, "") for c in atf.COLS} for r in rows])
    atf.save_sbrs([{"serial_number": "R1", "caliber": "9", "model": "PCC", "barrel_length": "8", "overall_length": "24",
                    "approved_date": "09/02/2026", "eform_id": "2000001", "control_number": "", "conditions": ""}])
    folder = atf.OUT / "approved"
    folder.mkdir()
    for name in ("A101.pdf", "A102_2.pdf", "R1.pdf", "A103.pdf"):
        (folder / name).write_bytes(b"%PDF-1.4 made-up " + name.encode())
    atf._save_approved_index(folder, {"1000101": "A101.pdf", "1000102": "A102_2.pdf", "2000001": "R1.pdf"})


def _rows(path, cols):
    return atf._norm(atf._read_csv(path), cols)


def test_try_move_and_copy_back(server, configured):
    inventory()
    before = {p.name: p.read_bytes() for p in (atf.DATA).iterdir()}
    connect(server)
    tried = atf.move_to_server(try_only=True)
    assert (tried["problems"], tried["moved"], tried["suppressors"], tried["built"], tried["sbrs"], tried["pdfs"]) == ([], False, 7, 1, 1, 4)
    assert atf._server().rows() == ([], [])                                  # taken back
    assert {p.name: p.read_bytes() for p in atf.DATA.iterdir() if p.name != "server.yaml"} == before   # nothing here changed

    moved = atf.move_to_server()
    assert moved["moved"] and moved["problems"] == []
    assert sorted(moved["renamed"]) == sorted(f"{n}.moved-to-server-{time.strftime('%Y-%m-%d')}.csv" for n in ("suppressors", "sbrs"))
    assert not atf.SUPP.exists()
    original = _rows(atf.DATA / moved["renamed"][0], atf.COLS)
    assert atf._norm(atf.load_rows(), atf.COLS) == original                 # the app now reads the server: same rows
    assert atf.approved_pdf_for(atf.load_rows()[0]).read_bytes() == b"%PDF-1.4 made-up A101.pdf"

    back = atf.copy_from_server()                                           # and back again
    assert back["problems"] == [] and back["pdfs"] == 0                     # the PDFs were never removed here
    assert _rows(atf.SUPP, atf.COLS) == original and len(_rows(atf._sbrs_path(), atf.SBR_COLS)) == 1
    with pytest.raises(remote.ServerError, match="only goes into an empty server"):
        atf.move_to_server()


def test_a_difference_moves_nothing(server, configured, monkeypatch):
    inventory()
    connect(server)
    with monkeypatch.context() as m:
        m.setattr(atf, "_differences", lambda *a: ["Suppressor A101: caliber is '22' here, '9' on the server"])
        rep = atf.move_to_server()
    assert rep["moved"] is False and rep["problems"] and atf.SUPP.exists()
    assert atf._server().rows() == ([], [])


def test_copy_back_fetches_missing_pdfs_and_keeps_the_old_files(server, configured):
    inventory()
    connect(server)
    atf.move_to_server()
    for f in (atf.OUT / "approved").iterdir():                              # say this computer lost its PDFs
        f.unlink()
    atf.SUPP.write_text("caliber,serial_number\n9,OLD1\n", encoding="utf-8")   # and someone started a new list here
    rep = atf.copy_from_server()
    assert rep["pdfs"] == 4 and rep["problems"] == [] and rep["renamed"] == [f"suppressors.replaced-by-server-{time.strftime('%Y-%m-%d')}.csv"]
    assert sorted(p.name for p in (atf.OUT / "approved").iterdir()) == ["A101.pdf", "A102.pdf", "A103.pdf", "R1.pdf", "index.csv"]
    assert atf._approved_index(atf.OUT / "approved")["1000102"] == "A102.pdf"


def test_menu_moves_and_comes_back(server, configured, answers, screen):
    inventory()
    found = remote.probe(server["address"])
    answers(pick("Settings"), pick("Where your inventory lives"), "connect", server["address"], True, "", "PC", server["code"](),
            True, "try", QUIT)
    ui.menu()
    out = screen.getvalue()
    assert "Your server is empty" in out and "Everything matched" in out and atf.SUPP.exists()
    assert atf.server_settings()["fingerprint"] == found["fingerprint"]   # the CA it showed is the one it saved
    answers(pick("Settings"), pick("Where your inventory lives"), "move", "move", True, QUIT)
    ui.menu()
    assert "Moved and checked" in screen.getvalue() and not atf.SUPP.exists()
    answers(pick("Settings"), pick("Where your inventory lives"), "local", "copy", QUIT)
    ui.menu()
    out = screen.getvalue()
    assert "Copied: 7 suppressors, 1 SBRs" in out and "This computer's inventory is used again" in out
    assert atf.server_settings() is None and len(atf.load_rows()) == 7


def test_monkeypatch_undo_cant_leave_the_sandbox(monkeypatch, sandbox):
    """A test's monkeypatch.undo() must not point the app back at the real folders."""
    monkeypatch.undo()
    assert atf.DATA == sandbox / "data" and atf.SUPP == sandbox / "data" / "suppressors.csv"
