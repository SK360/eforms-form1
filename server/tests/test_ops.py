"""The named operations (db.OPERATIONS): what each allows and refuses, versions (another device's change = Conflict),
request ids (a retry isn't applied twice), and the history each writes."""
import pytest

from atf_server import db


def op(conn, name, device="iPhone", **body):
    return db.operate(conn, name, body, device)


def test_set_and_clear_built(filled):
    v = db.row(filled, "A110")["version"]
    out = op(filled, "set_built", items=[{"serial": "a110", "design": "  PCC   Short ", "version": v}])
    a110 = out["rows"][0]
    assert (a110["built"], a110["design"], a110["version"]) == ("yes", "PCC Short", v + 1)
    op(filled, "clear_built", serial="A110", version=a110["version"])
    assert (db.row(filled, "A110")["built"], db.row(filled, "A110")["design"]) == ("", "")
    hist = db.history(filled, "A110")
    assert [h["operation"] for h in hist] == ["import", "set_built", "clear_built"]
    assert hist[1]["device"] == "iPhone" and hist[1]["before"]["built"] == "" and hist[1]["after"]["design"] == "PCC Short"
    assert filled.execute("SELECT updated_by FROM suppressors WHERE serial = 'A110'").fetchone()[0] == "iPhone"


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
