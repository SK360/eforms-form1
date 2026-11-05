"""How long ATF took (filed -> approved), from the filed and approved dates: the Days column, the wait line in the
Inventory views and the Excel column. Made-up rows only (the phone page's wait is in test_phone.py)."""
from datetime import date

from conftest import atf, pick, ui  # noqa: F401

QUIT = lambda kw: "quit"  # noqa: E731
TODAY = date(2026, 11, 5)


def row(status, filed="", approved=""):
    return {"serial_number": "T1", "status": status, "filed_date": filed, "approved_date": approved}


def test_wait_days():
    assert atf.wait_days(row("approved", "08/10/2026", "10/15/2026")) == 66
    assert atf.wait_days(row("filed", "10/26/2026"), today=TODAY) == 10                  # so far
    assert atf.wait_days(row("pending", "11/05/2026"), today=TODAY) == 0
    for r in (row("approved", "", "10/15/2026"), row("approved", "08/10/2026"), row("", "08/10/2026"),
              row("disapproved", "08/10/2026", "10/15/2026"), row("approved", "10/16/2026", "10/15/2026"),   # the wrong way round
              row("approved", "someday", "10/15/2026")):
        assert atf.wait_days(r) is None, r


def test_approval_waits_prefers_the_last_12_months():
    old = [row("approved", "01/02/2025", "06/01/2025")] * 3                           # 150 days, over a year ago
    new = [row("approved", "08/10/2026", "10/15/2026"), row("approved", "08/11/2026", "10/15/2026"),
           row("approved", "07/01/2026", "10/15/2026"), row("approved", "07/02/2026", "10/15/2026")]   # 66, 65, 106, 105
    w = atf.approval_waits(old + new + [row("filed", "10/01/2026"), row("approved", "", "10/15/2026")], today=TODAY)
    assert w == {"count": 4, "median": 86, "average": 86, "shortest": 65, "longest": 106, "recent": True}
    assert atf.approval_waits(old, today=TODAY) == {"count": 3, "median": 150, "average": 150, "shortest": 150, "longest": 150,
                                                    "recent": False}                    # nothing recent: all of them
    assert atf.approval_waits([row("approved", "10/01/2026", "10/04/2026"), row("approved", "10/01/2026", "10/05/2026")],
                              today=TODAY)["median"] == 4                              # 3.5 rounds up, like the phone page
    assert atf.approval_waits([row("filed", "10/01/2026")], today=TODAY) is None


def test_inventory_views_show_days_and_the_wait(configured, answers, screen):
    atf.save_rows([
        {"serial_number": "T1", "caliber": "22", "overall_length": "12", "status": "approved", "filed_date": "08/10/2026",
         "approved_date": "10/15/2026", "eform_id": "1000001"},
        {"serial_number": "T2", "caliber": "22", "overall_length": "12", "status": "approved", "filed_date": "07/01/2026",
         "approved_date": "10/15/2026", "eform_id": "1000002", "built": "yes", "design": "PCC Short"},
        {"serial_number": "T3", "caliber": "9", "overall_length": "12", "status": "approved", "approved_date": "10/15/2026"},  # by hand
        {"serial_number": "T4", "caliber": "9", "overall_length": "12", "status": "filed", "filed_date": "10/26/2026"}])
    answers(pick("Inventory"), "show", "unbuilt", "back", QUIT)
    ui.menu()
    out = screen.getvalue()
    full = out.split("Inventory · All (4)")[1].split("Inventory · Approved, not built")[0]
    assert "Days" in full and "so far" in full
    cells = lambda text: [c.strip() for c in text.split("│")]  # noqa: E731
    col = cells(next(ln for ln in full.splitlines() if "Serial" in ln)).index("Days")
    days = {s: cells(next(ln for ln in full.splitlines() if f" {s} " in ln))[col] for s in ("T1", "T2", "T3")}
    assert days == {"T1": "66", "T2": "106", "T3": ""}                                   # T3: no filed date, no days
    assert "Filed → approved: median 86 days, average 86 (66–106) · 2 approvals" in " ".join(full.split())
    unbuilt = out.split("Inventory · Approved, not built (2)")[1].split("eForms Form 1")[0]
    assert "Days" in unbuilt and "66" in unbuilt and "Filed → approved" in unbuilt
    assert "median" not in out.split("Inventory · All")[0]                              # the home screen stays as it was


def test_no_wait_line_without_dates(configured, answers, screen):
    atf.save_rows([{"serial_number": "T1", "caliber": "22", "status": "approved", "approved_date": "10/15/2026"}])
    answers(pick("Inventory"), "back", QUIT)
    ui.menu()
    assert "Filed → approved" not in screen.getvalue()


def test_excel_has_days_to_approve(configured):
    from openpyxl import load_workbook
    atf.save_rows([
        {"serial_number": "T1", "caliber": "22", "status": "approved", "filed_date": "08/10/2026", "approved_date": "10/15/2026"},
        {"serial_number": "T2", "caliber": "22", "status": "filed", "filed_date": "10/26/2026"}])
    rows = list(load_workbook(atf.export_inventory(atf.OUT / "inv.xlsx")).active.iter_rows(values_only=True))
    col = rows[0].index("Days to approve")
    assert rows[0][col - 1] == "Approved" and (rows[1][col], rows[2][col]) == (66, None)   # a number; only once approved
