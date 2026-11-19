"""Filing an SBR (or SBS/AOW) made from an existing receiver: queue columns, 5320.23 items 4a-4c, the portal steps
(original manufacturer's code, "not in the list" model/caliber, barrel length, optional markings photo) and the
queue row leaving once its approval is recorded. All on fake data."""
import pymupdf
import pytest

from conftest import TODAY, atf, queue


def sbr(**over):
    row = {"serial_number": "XYZ123", "type": "SHORT BARRELED RIFLE", "mfr_code": "AP2", "mfr_name": "EXAMPLE ARMS LLC",
           "mfr_address": "1 FACTORY RD, ANYTOWN, OH 45000", "mfr_country": "UNITED STATES", "model": "M4E1",
           "caliber": "5.56", "unit": "CALIBER", "barrel_length": "10.3", "overall_length": "30",
           "description": "RECEIVER MARKED MULTI", "photo": "", "status": ""}
    row.update(over)
    return row


def add_sbr(**over):
    atf.add_queued([sbr(**over)])
    return next(r for r in atf.load_rows() if atf.is_nfa_row(r))


def dry_steps(row):
    lines = []
    atf.emit = lambda kind, text, **d: lines.append(text.strip()) if kind == "step" else None
    atf._run_steps(None, atf.load_mapping()["portal"]["steps"], atf._row_context(atf.load_trust(), row), True, [])
    return "\n".join(lines)


def test_queue_keeps_the_sbr_columns_and_suppressor_files_keep_their_header(configured):
    queue(("S1", "9"))
    assert atf.SUPP.read_text(encoding="utf-8").splitlines()[0] == ",".join(atf.COLS)   # nothing new for suppressor users
    row = add_sbr()
    assert (row["mfr_code"], row["model"], row["barrel_length"]) == ("AP2", "M4E1", "10.3")
    assert [atf.is_nfa_row(r) for r in atf.load_rows()] == [False, True]


def test_sbr_needs_every_marking_and_one_caliber(configured, tmp_path):
    assert atf.nfa_row_problems(sbr()) == []
    assert atf.nfa_row_problems({"serial_number": "S1", "caliber": "9"}) == []           # a suppressor: not checked here
    probs = " ".join(atf.nfa_row_problems(sbr(mfr_code="", barrel_length="", caliber="MULTI")))
    assert "ATF code" in probs and "barrel length" in probs and "ONE caliber" in probs
    assert "120" in " ".join(atf.nfa_row_problems(sbr(description="X" * 121)))
    assert "not found" in " ".join(atf.nfa_row_problems(sbr(photo=str(tmp_path / "nope.jpg"))))
    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * (3 * 1024 * 1024 + 1))
    assert "3 MB" in " ".join(atf.nfa_row_problems(sbr(photo=str(big))))


def test_5320_gets_type_original_manufacturer_and_model(configured):
    row = add_sbr()
    dest, missing = atf.stamp_5320(row, TODAY)
    assert missing == []
    page = pymupdf.open(dest)[0]
    item4 = pymupdf.Rect(20, 360, 590, 475)                       # items 4a-4e on the generator's layout
    text = page.get_text(clip=item4)
    for want in ("SHORT BARRELED RIFLE", "EXAMPLE ARMS LLC", "1 FACTORY RD, ANYTOWN, OH 45000", "M4E1", "5.56", "XYZ123"):
        assert want in text, want
    for gone in ("SILENCER", "DOE FAMILY TRUST", "TEMPLATE"):     # the template's suppressor values
        assert gone not in text, gone


def test_suppressor_5320_item_4_is_untouched(configured):
    queue(("ABC123", "9"))
    dest, missing = atf.stamp_5320(atf.load_rows()[0], TODAY)
    text = pymupdf.open(dest)[0].get_text(clip=pymupdf.Rect(20, 360, 590, 475))
    assert missing == [] and "SILENCER" in text and "DOE FAMILY TRUST" in text


def test_portal_steps_for_an_sbr(configured, tmp_path):
    photo = tmp_path / "files" / "markings.jpg"
    photo.write_bytes(b"x")
    row = add_sbr(photo=str(photo))
    atf.stamp_5320(row, TODAY)
    steps = dry_steps(row)
    assert "{" not in steps
    for expected in ("fill #manufacturerCode AP2", "select #country UNITED STATES", "select #productType SHORT BARRELED RIFLE",
                     "check #descriptionNotListed", "fill #weaponModel M4E1", "fill #weaponCaliber 5.56",
                     "select #unitOfMeasure CALIBER", "fill #barrelLength 10.3", "fill #overallLength 30",
                     "fill #serialNumber XYZ123", "fill #description RECEIVER MARKED MULTI",
                     f"upload #file-upload {photo}", "fill #description PHOTO OF RECEIVER MARKINGS",
                     "expect #weaponType SHORT BARRELED RIFLE", "expect #barrelLength 10.3"):
        assert expected in steps, expected
    assert "FMI" not in steps


def test_markings_photo_is_optional(configured):
    row = add_sbr()
    atf.stamp_5320(row, TODAY)
    steps = dry_steps(row)
    assert "PHOTO OF RECEIVER MARKINGS" not in steps and "fill #barrelLength 10.3" in steps


def test_suppressor_steps_are_unchanged(configured):
    queue(("ABC123", "9"))
    row = atf.load_rows()[0]
    atf.stamp_5320(row, TODAY)
    steps = dry_steps(row)
    for expected in ("fill #manufacturerCode FMI", "select #productType SILENCER", "select #unitOfMeasure CALIBER",
                     "expect #weaponType SILENCER", "fill #weaponModel N/A"):
        assert expected in steps, expected
    assert "descriptionNotListed" not in steps and "barrelLength" not in steps


def test_filing_refuses_an_incomplete_sbr_before_the_browser(configured, monkeypatch):
    from types import SimpleNamespace
    add_sbr(model="")
    monkeypatch.setattr(atf, "_browser", lambda: pytest.fail("the browser must not open"))
    rows = atf.load_rows()
    with pytest.raises(SystemExit, match="model as engraved"):
        atf.file_rows(rows, [0], SimpleNamespace(dry=False, submit=True, auto_affirm=True, skip_ssn=True, capture=False))


def test_approved_sbr_leaves_the_queue_for_the_sbr_inventory(configured):
    add_sbr()
    rows = atf.load_rows()
    rows[0].update(status="filed", application_number="123456789")
    atf.save_rows(rows)
    rec = {"type": "SHORT BARRELED RIFLE", "serial": "XYZ123", "caliber": "5.56", "model": "M4E1", "barrel_length": "10.3",
           "overall_length": "30", "approved": True, "approval_date": "12/01/2026", "eform_id": "1234567"}
    assert atf.record_sbrs([rec]) == 1
    assert atf.load_rows() == []
    assert [r["serial_number"] for r in atf.load_sbrs()] == ["XYZ123"]


def test_a_queued_sbr_is_not_removed_by_someone_elses_approval(configured):
    add_sbr()                                    # still queued: never filed, so an approval can't be for it
    rec = {"type": "SHORT BARRELED RIFLE", "serial": "XYZ123", "caliber": "5.56", "model": "M4E1", "barrel_length": "10.3",
           "overall_length": "30", "approved": True, "approval_date": "12/01/2026", "eform_id": "1234567"}
    atf.record_sbrs([rec])
    assert len(atf.load_rows()) == 1


def test_menu_adds_an_sbr_and_refuses_multi(configured, answers, screen):
    from conftest import pick, ui
    seen = {}

    def caliber(kw):
        seen["multi"] = kw["validate"]("MULTI")
        return "5.56"
    answers(pick("Add suppressors"), pick("An SBR"), pick("Short Barreled Rifle"), "XYZ123", "ap2", "Example Arms LLC",
            "1 Factory Rd, Anytown, OH 45000", lambda kw: kw["default"], "m4e1", caliber, "10.3", "30", "receiver marked multi",
            "", True, lambda kw: "quit")
    ui.menu()
    row = atf.load_rows()[0]
    assert seen["multi"].startswith("One caliber")
    assert (row["type"], row["mfr_code"], row["mfr_name"], row["model"], row["caliber"], row["barrel_length"], row["photo"]) == (
        "SHORT BARRELED RIFLE", "AP2", "EXAMPLE ARMS LLC", "M4E1", "5.56", "10.3", "")
    assert row["description"] == "RECEIVER MARKED MULTI" and row["mfr_country"] == "UNITED STATES"
    assert "Added XYZ123 (SBR)" in screen.getvalue()


def test_cleo_copy_gets_items_4a_to_4c_and_still_no_serial(configured):
    import shutil
    from conftest import CLEO_FIXTURE
    shutil.copy(CLEO_FIXTURE, atf.cleo_template())
    row = add_sbr()
    [(n, dest, probs)] = atf.stamp_cleo_all(row, TODAY)
    assert probs == []
    text = pymupdf.open(dest)[0].get_text()
    assert "SHORT BARRELED RIFLE" in text and "EXAMPLE ARMS LLC" in text and "M4E1" in text
    assert "XYZ123" not in text
