"""The named operations (db.OPERATIONS): what each allows and refuses, versions (another device's change = Conflict),
request ids (a retry isn't applied twice), and the history each writes."""
import pytest

from atf_server import db


def op(conn, name, device="iPhone", **body):
    return db.operate(conn, name, body, device)


def test_set_and_clear_built_leave_no_record_of_when(filled):
    """The user's rule: a build must not be recorded with a time anywhere. Only the current state exists."""
    stamp = tuple(filled.execute("SELECT updated_at, updated_by FROM suppressors WHERE serial = 'A110'").fetchone())
    v = db.row(filled, "A110")["version"]
    out = op(filled, "set_built", request_id="b1", items=[{"serial": "a110", "design": "  PCC   Short ", "version": v}])
    a110 = out["rows"][0]
    assert (a110["built"], a110["design"], a110["version"]) == ("yes", "PCC Short", v + 1)
    op(filled, "clear_built", request_id="b2", serial="A110", version=a110["version"])
    assert (db.row(filled, "A110")["built"], db.row(filled, "A110")["design"]) == ("", "")
    assert [h["operation"] for h in db.history(filled, "A110")] == ["import"]     # no history entry
    assert tuple(filled.execute("SELECT updated_at, updated_by FROM suppressors WHERE serial = 'A110'").fetchone()) == stamp
    assert not filled.execute("SELECT 1 FROM requests").fetchone()                  # no request row (it has a time)
    op(filled, "set_built", request_id="b1", items=[{"serial": "A110", "design": "PCC Short"}])
    assert db.row(filled, "A110")["built"] == "yes"                                # a repeat is simply harmless


def test_builds_are_all_or_nothing_and_approved_only(filled):
    with pytest.raises(db.Refused, match="A130 isn't approved"):
        op(filled, "set_built", items=[{"serial": "A110", "design": "x"}, {"serial": "A130", "design": "x"}])
    assert db.row(filled, "A110")["built"] == ""                       # the first one wasn't saved either
    with pytest.raises(db.Refused, match="isn't in the inventory"):
        op(filled, "set_built", items=[{"serial": "NOPE"}])
    with pytest.raises(db.Refused, match="No suppressors"):
        op(filled, "set_built", items=[])


def test_another_devices_change_wins(filled):
    seen = db.row(filled, "A110")["version"]
    op(filled, "set_built", device="PC", items=[{"serial": "A110", "design": "Rimfire", "version": seen}])
    with pytest.raises(db.Conflict) as e:                              # the phone still shows the old version
        op(filled, "set_built", items=[{"serial": "A110", "design": "Other", "version": seen}])
    assert "changed on another device" in str(e.value) and e.value.rows[0]["design"] == "Rimfire"
    assert db.row(filled, "A110")["design"] == "Rimfire"


def test_a_retry_is_not_applied_twice(filled):
    first = op(filled, "add_queued", request_id="r1", rows=[{"serial_number": "A139", "caliber": "9", "overall_length": "12"}])
    again = op(filled, "add_queued", request_id="r1", rows=[{"serial_number": "A139", "caliber": "9", "overall_length": "12"}])
    assert again == first and [h["operation"] for h in db.history(filled, "A139")] == ["add_queued"]
    with pytest.raises(db.Refused, match="already used for something else"):
        op(filled, "remove_queued", request_id="r1", serial="A139")
    with pytest.raises(db.Refused, match="already in the inventory"):    # a different request: refused, not doubled
        op(filled, "add_queued", request_id="r2", rows=[{"serial_number": "a139", "caliber": "9", "overall_length": "12"}])


def test_queue_edits(filled):
    op(filled, "add_queued", rows=[{"serial_number": "A139", "caliber": "9", "overall_length": "12"},
                                   {"serial_number": "A140", "caliber": "9", "overall_length": "6.5"}])
    assert db.row(filled, "A140")["status"] == "" and db.row(filled, "A140")["overall_length"] == "6.5"
    for bad, why in ((dict(rows=[{"serial_number": "A141", "caliber": "9", "overall_length": "long"}]), "number of inches"),
                     (dict(rows=[{"serial_number": "A141", "caliber": "", "overall_length": "12"}]), "caliber is missing"),
                     (dict(rows=[{"serial_number": "A141", "caliber": "9", "overall_length": "12"},
                                 {"serial_number": "a141", "caliber": "9", "overall_length": "12"}]), "already")):
        with pytest.raises(db.Refused, match=why):
            op(filled, "add_queued", **bad)
    assert db.row(filled, "A141") is None
    v = db.row(filled, "A139")["version"]
    op(filled, "edit_queued", serial="A139", version=v, changes={"serial_number": "A142", "caliber": "45"})
    assert db.row(filled, "A139") is None and db.row(filled, "A142")["caliber"] == "45"
    with pytest.raises(db.Refused, match="already in the inventory"):
        op(filled, "edit_queued", serial="A142", changes={"serial_number": "A140"})
    with pytest.raises(db.Refused, match="only queued"):
        op(filled, "edit_queued", serial="A101", changes={"caliber": "9"})   # filed/approved rows are the record
    with pytest.raises(db.Refused, match="Can't change status"):
        op(filled, "edit_queued", serial="A142", changes={"status": "approved"})
    op(filled, "remove_queued", serial="A142")
    assert db.row(filled, "A142") is None and db.history(filled, "A142")[-1]["operation"] == "remove_queued"
    with pytest.raises(db.Refused, match="only queued"):
        op(filled, "remove_queued", serial="A101")
    with pytest.raises(db.Refused, match="Unknown operation"):
        op(filled, "replace_everything")


def test_last_change_grows(filled):
    before = db.last_change(filled)
    op(filled, "clear_built", serial="A101")
    assert db.last_change(filled) == before + 1


def test_filing_operations(filled):
    v = db.row(filled, "A136")["version"]
    op(filled, "mark_pending", device="PC", serial="A136", version=v, filed_date="10/28/2026", overall_length="12")
    assert (db.row(filled, "A136")["status"], db.row(filled, "A136")["filed_date"]) == ("pending", "10/28/2026")
    with pytest.raises(db.Refused, match="isn't queued on your server"):        # a second Sign & Submit: refused
        op(filled, "mark_pending", serial="A136")
    with pytest.raises(db.Refused, match="digits"):
        op(filled, "mark_filed", serial="A136", application_number="12-34")
    op(filled, "mark_filed", serial="A136", application_number="900000136")
    assert (db.row(filled, "A136")["status"], db.row(filled, "A136")["application_number"]) == ("filed", "900000136")
    with pytest.raises(db.Refused, match="already recorded for another"):
        op(filled, "mark_pending", serial="A137")
        op(filled, "mark_filed", serial="A137", application_number="900000136")
    op(filled, "mark_filed", serial="A137", application_number="UNKNOWN")       # filed; the number couldn't be read
    assert (db.row(filled, "A137")["status"], db.row(filled, "A137")["application_number"]) == ("filed", "")
    op(filled, "mark_pending", serial="A138")
    op(filled, "unmark_pending", serial="A138")
    assert (db.row(filled, "A138")["status"], db.row(filled, "A138")["filed_date"]) == ("", "")
    with pytest.raises(db.Refused, match="isn't queued"):
        op(filled, "mark_pending", serial="A130")                                # filed already
    op(filled, "mark_pending", serial="A130", force=True)                        # a deliberate re-file
    assert [h["operation"] for h in db.history(filled, "A136")] == ["import", "mark_pending", "mark_filed"]


def test_approval_operations(filled):
    op(filled, "record_approvals", updates=[{"serial": "A130", "fields": {"status": "approved", "approved_date": "10/30/2026",
                                                                          "eform_id": "1000130", "overall_length": "6"}}],
       added=[{"serial_number": "A200", "caliber": "9", "overall_length": "12", "approved_date": "10/30/2026", "eform_id": "1000200"}])
    a130 = db.row(filled, "A130")
    assert (a130["status"], a130["approved_date"], a130["overall_length"]) == ("approved", "10/30/2026", "12")   # length kept
    assert db.row(filled, "A200")["status"] == "approved"
    with pytest.raises(db.Refused, match="only approval details"):
        op(filled, "record_approvals", updates=[{"serial": "A130", "fields": {"built": "yes"}}])
    with pytest.raises(db.Refused, match="only approval details"):
        op(filled, "record_approvals", updates=[{"serial": "A130", "fields": {"status": "queued"}}])
    op(filled, "record_sbrs", rows=[{"serial_number": "R3", "caliber": "9", "eform_id": "2000003", "approved_date": "10/30/2026"},
                                    {"serial_number": "R1", "eform_id": "2000001", "conditions": "Mark it"}])
    sbrs = {s["serial_number"]: s for s in db.sbrs(filled)}
    assert sbrs["R3"]["approved_date"] == "10/30/2026" and sbrs["R1"]["conditions"] == "Mark it" and sbrs["R1"]["model"] == "PCC"
