"""Filing against the FAKE portal in a headless browser: submit gating, pending status, logout, starting new forms."""
import builtins
from types import SimpleNamespace

import pytest

from conftest import TODAY, atf, queue

OPTS = dict(dry=False, submit=True, auto_affirm=True, skip_ssn=True, capture=False)


def file_all(**over):
    rows = atf.load_rows()
    for r in rows:  # the menu stamps each 5320.23 (dated today) before filing
        atf.stamp_5320(r, TODAY)
    atf.file_rows(rows, [i for i, r in enumerate(rows) if r["status"] not in atf.SKIP], SimpleNamespace(**{**OPTS, **over}))
    return atf.load_rows()


def test_files_and_logs_out(configured, portal):
    queue(("S1", "9"))
    state = portal([("S1", "9")])
    rows = file_all()
    assert (rows[0]["status"], rows[0]["application_number"]) == ("filed", "900000001")
    assert rows[0]["overall_length"] == "12" and rows[0]["filed_date"]          # what was filed is recorded
    assert state["logged_out"]
    assert "S1" in (atf.OUT / "submissions.csv").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind, kw", [("invalid fingerprints", dict(ebts="Invalid EBTS Package")),
                                       ("caliber mismatch", dict(shown_caliber="223"))])
def test_check_failure_blocks_submit(configured, portal, kind, kw):
    queue(("S1", "22"))
    state = portal([("S1", "22")], **kw)
    rows = file_all()
    assert rows[0]["status"] == "" and state["logged_out"], kind


def test_missing_confirmation_leaves_row_pending(configured, portal):
    queue(("S1", "9"))
    state = portal([("S1", "9")], confirm=False)
    assert file_all()[0]["status"] == "pending" and state["logged_out"]


def test_ctrl_c_still_logs_out(configured, portal, monkeypatch):
    queue(("S1", "9"))
    state = portal([("S1", "9")])

    def ctrl_c(*a):
        raise KeyboardInterrupt
    monkeypatch.setattr(builtins, "input", ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        file_all()
    assert state["logged_out"] and atf.load_rows()[0]["status"] == ""


def test_batch_opens_each_form_itself(configured, portal, monkeypatch):
    queue(("S1", "9"), ("S2", "9"))
    prompts = []
    monkeypatch.setattr(builtins, "input", lambda msg="": prompts.append(msg) or "")
    portal([("S1", "9"), ("S2", "9")], start_home=True)
    rows = file_all()
    assert [r["status"] for r in rows] == ["filed", "filed"]
    assert not any("SAME window" in p for p in prompts)


def test_reuses_a_form_already_open(configured, portal):
    queue(("S1", "9"))
    portal([("S1", "9")], start_home=False)
    assert file_all()[0]["status"] == "filed"


def test_falls_back_to_manual_when_link_missing(configured, portal, monkeypatch):
    queue(("S1", "9"), ("S2", "9"))
    state = portal([("S1", "9"), ("S2", "9")], start_home=False, link=False)
    asked = []

    def manual(msg=""):
        asked.append(msg)
        if "SAME window" in msg:
            state["page"].evaluate("newForm()")  # the user opens the next form by hand
        return ""
    monkeypatch.setattr(builtins, "input", manual)
    rows = file_all()
    assert [r["status"] for r in rows] == ["filed", "filed"] and any("SAME window" in p for p in asked)
