"""Schema 5 / API 5: an SBR (SBS, AOW...) made from an existing receiver queues in the suppressors table with its
markings (db.NFA_QUEUE_COLS), is filed like a suppressor, and leaves the queue once record_sbrs records its approval."""
import csv
import io

import pytest

from atf_server import db


def op(conn, name, device="PC", **body):
    return db.operate(conn, name, body, device)


def sbr(**over):
    row = {"serial_number": "XYZ123", "caliber": "5.56", "overall_length": "30", "type": "SHORT BARRELED RIFLE",
           "mfr_code": "", "mfr_name": "EXAMPLE ARMS LLC", "mfr_address": "ANYTOWN, OH", "mfr_country": "UNITED STATES",
           "model": "M4E1", "barrel_length": "10.3", "unit": "CALIBER", "description": "RECEIVER MARKED MULTI",
           "photo": "C:\\Users\\me\\markings.jpg"}
    row.update(over)
    return row


def approval(serial="XYZ123", eform="3000001"):
    return {"serial_number": serial, "type": "SHORT BARRELED RIFLE", "caliber": "5.56", "model": "M4E1", "barrel_length": "10.3",
            "overall_length": "30", "approved_date": "12/01/2026", "eform_id": eform, "control_number": "", "conditions": ""}


def test_migration_5_adds_the_queue_columns_empty_for_suppressors(data_dir):
    data_dir.mkdir()
    c = db.connect(data_dir / "inventory.db")
    for v in (1, 2, 3, 4):
        c.execute("BEGIN")
        for statement in db._statements(db.MIGRATIONS[v]):
            c.execute(statement)
        c.execute(f"PRAGMA user_version = {v}")
        c.execute("COMMIT")
    c.execute("INSERT INTO suppressors (serial, caliber, overall_length, updated_at, updated_by) VALUES ('S1', '9', '12', 'x', 'x')")
    db.migrate(c, data_dir / "backups")
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5
    r = db.row(c, "S1")
    assert {k: r[k] for k in db.NFA_QUEUE_COLS} == {k: "" for k in db.NFA_QUEUE_COLS} and not db.is_nfa(r)
    assert list((data_dir / "backups").glob("*.db"))
    c.close()


def test_an_sbr_is_queued_and_edited_with_its_markings(filled):
    op(filled, "add_queued", rows=[sbr()])
    r = db.row(filled, "XYZ123")
    assert db.is_nfa(r) and r["status"] == "" and {k: r[k] for k in sbr()} == sbr()
    out = op(filled, "edit_queued", serial="XYZ123", version=r["version"],
             changes={"model": "M4E1 ENHANCED", "barrel_length": "11.5", "mfr_code": "AP2"})["rows"][0]
    assert (out["model"], out["barrel_length"], out["mfr_code"], out["mfr_name"]) == ("M4E1 ENHANCED", "11.5", "AP2", "EXAMPLE ARMS LLC")
    assert [h["operation"] for h in db.history(filled, "XYZ123")] == ["add_queued", "edit_queued"]


@pytest.mark.parametrize("change, why", [({"type": "BAZOOKA"}, "unknown firearm type"), ({"barrel_length": "ten"}, "barrel length"),
                                         ({"unit": "MM"}, "CALIBER or GAUGE"), ({"description": "X" * 121}, "too long")])
def test_bad_markings_are_refused(filled, change, why):
    with pytest.raises(db.Refused, match=why):
        op(filled, "add_queued", rows=[sbr(**change)])
    assert db.row(filled, "XYZ123") is None


def test_a_suppressor_type_is_stored_as_a_suppressor(filled):
    op(filled, "add_queued", rows=[{"serial_number": "S9", "caliber": "9", "overall_length": "12", "type": "SILENCER"}])
    assert not db.is_nfa(db.row(filled, "S9")) and db.row(filled, "S9")["type"] == ""


def test_filing_keeps_a_code_found_on_eforms_only_for_an_sbr(filled):
    op(filled, "add_queued", rows=[sbr(), {"serial_number": "S9", "caliber": "9", "overall_length": "12"}])
    op(filled, "mark_pending", serial="XYZ123", filed_date="11/25/2026", mfr_code="AP2")
    op(filled, "mark_pending", serial="S9", filed_date="11/25/2026", mfr_code="FMI")
    assert (db.row(filled, "XYZ123")["status"], db.row(filled, "XYZ123")["mfr_code"]) == ("pending", "AP2")
    assert db.row(filled, "S9")["mfr_code"] == ""
    op(filled, "mark_filed", serial="XYZ123", application_number="2027000001")
    op(filled, "mark_pending", serial="XYZ123", force=True, mfr_code="OTHER")       # a re-file never replaces it
    assert db.row(filled, "XYZ123")["mfr_code"] == "AP2"


def test_an_approved_sbr_leaves_the_queue_for_the_sbr_list(filled):
    op(filled, "add_queued", rows=[sbr()])
    op(filled, "mark_pending", serial="XYZ123", filed_date="11/25/2026")
    op(filled, "mark_filed", serial="XYZ123", application_number="2027000001")
    op(filled, "record_sbrs", rows=[approval()])
    assert db.row(filled, "XYZ123") is None and [s["serial_number"] for s in db.sbrs(filled)].count("XYZ123") == 1
    ops = [(h["operation"], h["after"] is None) for h in db.history(filled, "XYZ123")]
    assert ops[-2:] == [("record_sbrs", False), ("record_sbrs", True)]                 # recorded, then out of the queue


def test_a_queued_sbr_or_a_suppressor_stays_when_an_sbr_with_its_serial_is_approved(filled):
    op(filled, "add_queued", rows=[sbr(), {"serial_number": "S9", "caliber": "9", "overall_length": "12"}])
    op(filled, "mark_pending", serial="S9", filed_date="11/25/2026")
    op(filled, "record_sbrs", rows=[approval(), approval(serial="S9", eform="3000002")])
    assert db.row(filled, "XYZ123")["status"] == "" and db.row(filled, "S9")["status"] == "pending"


def test_import_and_export_carry_the_markings(conn, data_dir):
    plain = {"serial_number": "S1", "caliber": "9", "overall_length": "12", "status": "filed", "filed_date": "11/01/2026"}
    db.import_inventory(conn, data_dir / "approved", [plain, dict(sbr(), status="")], [], device="test")
    assert {k: db.row(conn, "XYZ123")[k] for k in sbr()} == sbr()
    exported = list(csv.DictReader(io.StringIO(db.export_csv(conn))))
    assert set(db.NFA_QUEUE_COLS) <= set(exported[0]) and exported[1]["mfr_name"] == "EXAMPLE ARMS LLC"


def test_export_of_suppressors_only_keeps_the_old_header(filled):
    assert db.export_csv(filled).splitlines()[0] == ",".join(db.COLS)
