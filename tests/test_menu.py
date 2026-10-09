"""The ./atf menu (src/ui.py) with scripted answers: adding, filing, resolving, and screenshot-safe output."""
import csv

from conftest import TICKED, atf, pick, queue, ui

QUIT = lambda kw: "quit"  # noqa: E731


def test_add_then_file_two(configured, portal, answers, screen):
    queue(("T101", "22", "filed", "900000101"), ("T102", "9", "filed", "900000102"))
    portal([("T103", "9"), ("T104", "22")])
    checks = {}

    def serial(kw):
        checks["dup"], checks["blank"] = kw["validate"]("t102"), kw["validate"]("  ")
        return kw["default"]
    answers(pick("Add suppressors"), serial, lambda kw: kw["default"], True, lambda kw: kw["default"], "22", False,
            pick("File 2 queued"), TICKED, False, True, QUIT)
    ui.menu()
    out, rows = screen.getvalue(), atf.load_rows()
    assert [(r["serial_number"], r["caliber"], r["status"]) for r in rows[2:]] == [("T103", "9", "filed"), ("T104", "22", "filed")]
    assert checks == {"dup": "That serial is already in the list", "blank": "Enter a serial number"}
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


def test_next_serial():
    assert (ui.next_serial("S099"), ui.next_serial("A-0009"), ui.next_serial("XYZ")) == ("S100", "A-0010", "")


def test_short_application_number():
    assert (ui.short("900000102"), ui.short(""), ui.short("UNKNOWN"), ui.short("123")) == ("…0102", "", "UNKNOWN", "123")
