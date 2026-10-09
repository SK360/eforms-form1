"""First-run setup wizard / Settings (src/ui.py settings_flow), driven with scripted answers and the fake template."""
import os
import re
import shutil

import pymupdf
import pytest
import yaml

from conftest import FIXTURE, FIXTURE_ANSWERS, FIXTURE_SSN, TODAY, D, atf, ui

QUIT = lambda kw: "quit"  # noqa: E731
SETTINGS = lambda kw: ui.settings_flow  # noqa: E731
DESC_PROMPT = "Firearm description (the description box on the firearm page; edit if you like):"


def saved():
    return yaml.safe_load((atf.DATA / "trust.yaml").read_text(encoding="utf-8"))


def test_new_user_template_first_and_prefilled(sandbox, answers, screen):
    f = sandbox / "files"
    for name in ("prints.eft", "me.jpg", "My Trust.pdf", "printer.jpg"):
        (f / name).write_bytes(b"x")
    tpl = f / "signed 5320.pdf"
    shutil.copy(FIXTURE, tpl)
    checks = {}
    answers(True, f"'{tpl}'",                                           # accept disclaimer; template dragged in, quoted
            "Doe Family Trust", "Trustee", D, "Male", "Asian", D, "Ohio",   # typed over a prefill; DOB, birth state accepted
            D, "Pat Chief", D, D, D, D, "Clark", D,                         # CLEO: official typed, county typed, rest accepted
            D, D, D,                                                        # model, length, description: defaults
            str(f / "prints.eft"), lambda kw: (checks.setdefault("blank_photo", kw["validate"]("")), str(f / "me.jpg"))[1],
            str(f / "My Trust.pdf"), True, str(f / "printer.jpg"), "3d printer", False, QUIT)
    ui.menu()
    out, t, seen = screen.getvalue(), saved(), answers.seen
    assert answers.asked[1][0] == "path" and "5320.23" in answers.asked[1][1]          # template asked first
    assert f"Read {len(FIXTURE_ANSWERS)} answers" in out
    assert seen["Agency / department:"] == FIXTURE_ANSWERS["cleo.department"]
    assert seen["Official's title:"] == FIXTURE_ANSWERS["cleo.title"] and seen["Agency ZIP:"] == FIXTURE_ANSWERS["cleo.address.zip"]
    assert seen[DESC_PROMPT] == ui.DEFAULT_DESCRIPTION
    assert not any("manufacturer" in m.lower() for _, m in answers.asked)
    assert checks["blank_photo"] is not True                                             # photo is required
    assert t["trust"]["name"] == "Doe Family Trust" and t["cleo"]["official"] == "Pat Chief"
    assert t["responsible_persons"][0]["dob"] == FIXTURE_ANSWERS["responsible_persons.0.dob"]
    assert t["cleo"]["address"]["street"] == FIXTURE_ANSWERS["cleo.address.street"] and t["cleo"]["address"]["county"] == "Clark"
    assert t["files"]["trust_document"] == str(f / "My Trust.pdf") and t["docs"] == [{"path": str(f / "printer.jpg"), "description": "3D PRINTER"}]
    assert t.get("accepted_disclaimer") and "manufacturer_code" not in t["firearm"]
    text = (atf.DATA / "trust.yaml").read_text(encoding="utf-8")
    assert FIXTURE_SSN not in text and not any(FIXTURE_SSN in str(v) for v in seen.values())
    assert (atf.FORMS / "5320.23.pdf").read_bytes() == tpl.read_bytes() and atf.SUPP.exists()
    assert atf.settings_problems() == [] and "stamps correctly" in out
    dest, missing = atf.stamp_5320({"serial_number": "ABC1", "caliber": "9"}, TODAY)
    assert not missing and "Pat Chief" in pymupdf.open(dest)[0].get_text()


@pytest.mark.skipif(os.name == "nt", reason="escaped spaces are a macOS/Linux terminal convention")
def test_drag_and_drop_escaped_spaces():
    assert ui._clean_path("/tmp/My\\ Trust.pdf") == "/tmp/My Trust.pdf"


def test_drag_and_drop_quotes_and_windows_paths():
    assert ui._clean_path("'/tmp/a b.pdf'") == str(ui.Path("/tmp/a b.pdf"))
    if os.name == "nt":
        assert ui._clean_path('"C:\\Users\\Jo\\My Files\\t.pdf"') == "C:\\Users\\Jo\\My Files\\t.pdf"


def test_changed_cleo_title_still_stamps(configured, answers, screen):
    answers(SETTINGS, True, *[D] * 9, "SHERIFF", *[D] * 8, D, D, D, True, False, QUIT)
    ui.menu()
    assert saved()["cleo"]["title"] == "SHERIFF" and "stamps correctly" in screen.getvalue()


def test_kept_template_and_defaults_change_nothing(configured, answers, screen):
    before = saved()
    answers(SETTINGS, True, *[D] * 18, D, D, D, True, False, QUIT)
    ui.menu()
    assert saved() == before and "stamps correctly" in screen.getvalue()


def test_new_template_values_win(configured, answers, screen):
    t = saved()
    t["cleo"]["official"] = "OLD CHIEF"
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t), encoding="utf-8")
    newt = configured / "files" / "new.pdf"
    shutil.copy(FIXTURE, newt)
    answers(SETTINGS, False, str(newt), *[D] * 18, D, D, D, True, False, QUIT)
    ui.menu()
    assert saved()["cleo"]["official"] == FIXTURE_ANSWERS["cleo.official"]


def test_cancel_saves_nothing_and_keeps_template(configured, answers, screen):
    before, tpl = saved(), (atf.FORMS / "5320.23.pdf").read_bytes()
    other = configured / "files" / "other.pdf"
    other.write_bytes(b"%PDF-1.4 not a template")
    answers(SETTINGS, False, str(other), "Changed Name", None, QUIT)
    ui.menu()
    out = screen.getvalue()
    assert saved() == before and (atf.FORMS / "5320.23.pdf").read_bytes() == tpl
    assert "nothing saved" in out and "Couldn't read answers" in out
    assert re.search(r"Read \d+ answers", out) is None
