"""The ./atf menu (src/ui.py) with scripted answers: adding, filing, resolving, and screenshot-safe output."""
import csv

import pytest

from conftest import TICKED, atf, pick, queue, ui

QUIT = lambda kw: "quit"  # noqa: E731


def test_add_then_file_two(configured, portal, answers, screen):
    queue(("T101", "22", "filed", "900000101"), ("T102", "9", "filed", "900000102"))
    portal([("T103", "9"), ("T104", "22")])
    checks = {}

    def serial(kw):
        checks["dup"], checks["blank"] = kw["validate"]("t102"), kw["validate"]("  ")
        return kw["default"]
    answers(pick("Add suppressors"), serial, lambda kw: kw["default"], "6.5", True, lambda kw: kw["default"], "22",
            lambda kw: (checks.setdefault("len_default", kw["default"]), kw["default"])[1], False,
            pick("File 2 queued"), TICKED, False, True, QUIT)
    ui.menu()
    out, rows = screen.getvalue(), atf.load_rows()
    assert [(r["serial_number"], r["caliber"], r["status"]) for r in rows[2:]] == [("T103", "9", "filed"), ("T104", "22", "filed")]
    assert checks == {"dup": "That serial is already in the list", "blank": "Enter a serial number", "len_default": "6.5"}
    assert [r["overall_length"] for r in rows[2:]] == ["6.5", "6.5"]                       # per suppressor, recorded
    for shown in ("✔ Applicant type", "✔ Responsible person", "Serial number", "Your turn in the browser",
                  "Filed: application #…0001", "Filed: application #…0002", "Result"):
        assert shown in out, shown
    assert "DOE FAMILY TRUST" not in out.upper() and "900000102" not in out and "(hidden)" in out  # screenshot-safe
    assert (atf.OUT / "5320.23_T103.pdf").exists()


def test_declining_confirmation_opens_no_browser(configured, answers, screen, monkeypatch):
    queue(("S1", "9"))
    monkeypatch.setattr(atf, "_browser", lambda: (_ for _ in ()).throw(AssertionError("browser opened")))
    answers(pick("File 1 queued"), TICKED, False, False, QUIT)
    ui.menu()
    assert atf.load_rows()[0]["status"] == ""


def test_incomplete_settings_block_filing(configured, answers, screen):
    queue(("S1", "9"))
    (atf.FORMS / "5320.23.pdf").unlink()
    answers(pick("File 1 queued"), QUIT)
    ui.menu()
    assert "Fix these in Settings first" in screen.getvalue() and atf.load_rows()[0]["status"] == ""


def test_resolve_pending(configured, answers, screen):
    queue(("S1", "9", "pending"), ("S2", "9", "pending"))
    checks = {}

    def number(kw):
        checks["bad"] = kw["validate"]("20-27")
        return "900000201"
    answers(pick("⚠ Resolve"), lambda kw: kw["choices"][0].value, "filed", number, pick("⚠ Resolve"), "queued", QUIT)
    ui.menu()
    rows = atf.load_rows()
    assert (rows[0]["status"], rows[0]["application_number"], rows[1]["status"]) == ("filed", "900000201", "")
    assert checks["bad"] == "Digits only"
    with open(atf.OUT / "submissions.csv", newline="", encoding="utf-8") as f:
        assert list(csv.reader(f))[-1][1:] == ["S1", "9", "900000201"]


def test_home_shows_last_five_filed_and_short_numbers(configured, answers, screen):
    queue(*[(f"T{n:03d}", "9", "filed", f"9000000{n:02d}") for n in range(1, 11)], ("T011", "9"))
    answers(QUIT)
    ui.menu()
    out = screen.getvalue()
    assert "… 5 earlier" in out and "T005" not in out and "T006" in out and "T011" in out
    assert "…0010" in out and "900000010" not in out


@pytest.mark.parametrize("last, suggested", [
    ("A046", "A047"), ("A099", "A100"), ("ABC-0099", "ABC-0100"), ("0012", "0013"), ("SN99", "SN100"),
    ("MS-2026-01", "MS-2026-02"), ("2026-001A", "2026-002A"), ("abc12x", "abc13x"), ("AB 12", "AB 13"),
    ("XYZ", ""), ("", "")])
def test_next_serial(last, suggested):
    assert ui.next_serial(last) == suggested


def test_short_application_number():
    assert (ui.short("900000102"), ui.short(""), ui.short("UNKNOWN"), ui.short("123")) == ("…0102", "", "UNKNOWN", "123")


def test_serial_key_is_natural():
    assert sorted(["S10", "S9", "s100", "S010A", "A1"], key=atf.serial_key) == ["A1", "S9", "S10", "S010A", "s100"]


def test_filed_dates_backfilled_from_the_log(configured):
    queue(("T1", "9", "filed", "900000001"), ("T2", "9"))
    (atf.OUT / "submissions.csv").write_text("timestamp,serial_number,caliber,application_number\n2026-10-08T21:57:48,T1,9,900000001\n",
                                             encoding="utf-8")
    rows = atf.backfill_filed_dates()
    assert rows[0]["filed_date"] == "10/08/2026" and rows[1]["filed_date"] == ""
    assert atf.load_rows()[0]["filed_date"] == "10/08/2026"


def test_approved_rows_are_never_refiled(configured):
    queue(("T1", "9", "approved"), ("T2", "9"))
    assert atf.pick_row(atf.load_rows(), None) == 1
    with pytest.raises(SystemExit, match="approved"):
        atf.pick_row(atf.load_rows(), 1)


def test_inventory_screen(configured, answers, screen):
    atf.save_rows([
        {"serial_number": "T10", "caliber": "22", "status": "approved", "approved_date": "08/15/2026", "eform_id": "1000010", "conditions": ""},
        {"serial_number": "T9", "caliber": "22", "status": "approved", "approved_date": "02/14/2026", "eform_id": "1000009",
         "conditions": "MARKING"},
        {"serial_number": "T11", "caliber": "9", "status": "filed", "filed_date": "10/01/2026", "application_number": "900000011"},
        {"serial_number": "T12", "caliber": "9", "status": ""}])
    answers(pick("Inventory"), "approved", "back", QUIT)
    ui.menu()
    out = screen.getvalue()
    full = out.split("Inventory · All (4)")[1].split("Inventory · Approved")[0]
    assert full.index("T9") < full.index("T10") < full.index("T11") < full.index("T12")        # serial order
    assert "waiting" in full and "…0011" in full and "900000011" not in full and "⚠ conditions" in full
    assert "4 suppressors · 2 approved · 1 in progress · 1 queued" in out
    approved = out.split("Inventory · Approved (2)")[1].split("eForms Form 1")[0]                # just that view, not the home screen after
    assert "T9" in approved and "T11" not in approved and "T12" not in approved
