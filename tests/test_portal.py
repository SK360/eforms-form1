"""Filing against the FAKE portal in a headless browser: submit gating, pending status, logout, starting new forms."""
import builtins
from types import SimpleNamespace

import pytest

from conftest import TODAY, atf, make_individual, queue

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


def test_waits_for_a_slow_fingerprint_check(configured, portal, monkeypatch):
    """The 'Valid EBTS Package' message can take seconds to show (it once missed a fixed 1.5 s wait). The
    responsible-person form, open as a dialog too, is not that message: nothing in it is clicked, nothing is asked."""
    queue(("S1", "9"))
    state = portal([("S1", "9")], ebts_delay=1500, rp_form=True)
    prompts = []
    monkeypatch.setattr(builtins, "input", lambda msg="": prompts.append(msg) or "")
    rows = file_all()
    assert rows[0]["status"] == "filed" and state["logged_out"] and not state["rp_clicked"]
    assert not any("closed" in p for p in prompts)


def test_no_fingerprint_message_blocks_submit(configured, portal, monkeypatch):
    queue(("S1", "9"))
    state = portal([("S1", "9")], ebts=None, rp_form=True)
    prompts = []
    monkeypatch.setattr(builtins, "input", lambda msg="": prompts.append(msg) or "")
    assert file_all()[0]["status"] == "" and state["logged_out"]
    assert not any("closed" in p for p in prompts)                 # never told to close the responsible-person form
    assert not state["rp_clicked"]


def test_individual_files_without_a_5320(configured, portal):
    queue(("S1", "9"))
    make_individual()
    state = portal([("S1", "9")])
    rows = atf.load_rows()
    atf.file_rows(rows, [0], SimpleNamespace(**OPTS))                         # no 5320.23 stamped or needed
    assert atf.load_rows()[0]["status"] == "filed" and state["logged_out"]
    assert not list(atf.OUT.glob("5320.23*"))


def test_individual_answer_shown_differently_blocks_submit(configured, portal):
    queue(("S1", "9"))
    make_individual()
    state = portal([("S1", "9")], shown_11a="Yes")                            # the verify page shows another answer
    atf.file_rows(atf.load_rows(), [0], SimpleNamespace(**OPTS))
    assert atf.load_rows()[0]["status"] == "" and state["logged_out"]


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
