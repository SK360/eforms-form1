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
    answers(True, D, f"'{tpl}'",                                        # accept disclaimer; a trust (default); template, quoted
            "Doe Family Trust", D, D, "Male", "Asian", D, "Ohio",       # name typed over a prefill; title, DOB, birth state accepted
            D, "Pat Chief", D, D, D, D, "Clark", D,                         # CLEO: official typed, county typed, rest accepted
            D, D, D,                                                        # model, length, description: defaults
            str(f / "prints.eft"), lambda kw: (checks.setdefault("blank_photo", kw["validate"]("")), str(f / "me.jpg"))[1],
            str(f / "My Trust.pdf"), True, str(f / "printer.jpg"), "3d printer", False, "done", QUIT)
    ui.menu()
    out, t, seen = screen.getvalue(), saved(), answers.seen
    assert answers.asked[2][0] == "path" and "5320.23" in answers.asked[2][1]          # template asked first (after trust/individual)
    assert f"Read {len(FIXTURE_ANSWERS)} answers" in out
    assert seen["Agency / department:"] == FIXTURE_ANSWERS["cleo.department"]
    assert seen["Official's title:"] == FIXTURE_ANSWERS["cleo.title"] and seen["Agency ZIP:"] == FIXTURE_ANSWERS["cleo.address.zip"]
    assert seen[DESC_PROMPT] == ui.DEFAULT_DESCRIPTION
    assert seen["Your title in the trust:"] == "Settlor" and t["responsible_persons"][0]["title"] == "Settlor"   # default title
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
    answers(SETTINGS, D, True, *[D] * 9, "SHERIFF", *[D] * 8, D, D, D, True, False, "done", QUIT)
    ui.menu()
    assert saved()["cleo"]["title"] == "SHERIFF" and "stamps correctly" in screen.getvalue()


def test_kept_template_and_defaults_change_nothing(configured, answers, screen):
    before = saved()
    answers(SETTINGS, D, True, *[D] * 18, D, D, D, True, False, "done", QUIT)
    ui.menu()
    assert saved() == before and "stamps correctly" in screen.getvalue()


def test_new_template_values_win(configured, answers, screen):
    t = saved()
    t["cleo"]["official"] = "OLD CHIEF"
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t), encoding="utf-8")
    newt = configured / "files" / "new.pdf"
    shutil.copy(FIXTURE, newt)
    answers(SETTINGS, D, False, str(newt), *[D] * 18, D, D, D, True, False, "done", QUIT)
    ui.menu()
    assert saved()["cleo"]["official"] == FIXTURE_ANSWERS["cleo.official"]


def test_cancel_saves_nothing_and_keeps_template(configured, answers, screen):
    before, tpl = saved(), (atf.FORMS / "5320.23.pdf").read_bytes()
    other = configured / "files" / "other.pdf"
    other.write_bytes(b"%PDF-1.4 not a template")
    answers(SETTINGS, D, False, str(other), "Changed Name", None, QUIT)
    ui.menu()
    out = screen.getvalue()
    assert saved() == before and (atf.FORMS / "5320.23.pdf").read_bytes() == tpl
    assert "nothing saved" in out and "Couldn't read answers" in out
    assert re.search(r"Read \d+ answers", out) is None


def test_new_individual_setup(sandbox, answers, screen):
    """Filing as an individual: no 5320.23 template, no trust; questions 11.A-11.M asked in ATF's words and saved."""
    f = sandbox / "files"
    for name in ("prints.eft", "me.jpg"):
        (f / name).write_bytes(b"x")
    answers(True, "individual",                                         # accept disclaimer; file as an individual
            "01/02/1980", "Male", "White", "Ohio", "Ohio",              # DOB, sex, race, birth state, state of residence
            *["No"] * len(atf.QUESTIONS_11), "",                         # questions 11.A-11.M.1; no UPIN
            "SPRINGFIELD POLICE DEPARTMENT", "Pat Chief", "CHIEF OF POLICE", "200 MAIN ST", "45502", "Springfield", "Clark", "Ohio",
            D, D, D, str(f / "prints.eft"), str(f / "me.jpg"), False, QUIT)
    ui.menu()
    out, t = screen.getvalue(), saved()
    asked = [m for _, m in answers.asked]
    assert not any("5320.23" in m or "Trust" in m or "title in the trust" in m for m in asked)   # nothing about a trust
    assert asked[7].startswith("11.A. Do you intend to make any firearm") and "Questions 11.A-11.M" in out
    assert t["applicant_type"] == "individual" and set(t["individual"]["answers"].values()) == {"No"}
    assert "trust" not in t and "trust_document" not in t["files"] and not (atf.FORMS / "5320.23.pdf").exists()
    assert atf.settings_problems() == [] and "Settings saved" in out


def test_individual_answering_yes_is_saved_but_blocks_filing(sandbox, answers, screen):
    f = sandbox / "files"
    for name in ("prints.eft", "me.jpg"):
        (f / name).write_bytes(b"x")
    answers(True, "individual", "01/02/1980", "Male", "White", "Ohio", "Ohio", "No", "No", "Yes", *["No"] * 10, "",
            "SPRINGFIELD POLICE DEPARTMENT", "Pat Chief", "CHIEF OF POLICE", "200 MAIN ST", "45502", "Springfield", "Clark", "Ohio",
            D, D, D, str(f / "prints.eft"), str(f / "me.jpg"), False, QUIT)
    ui.menu()
    out = screen.getvalue()
    assert "only files when every answer is No" in out and saved()["individual"]["answers"]["11.C"] == "Yes"


def test_add_change_and_remove_another_responsible_person(configured, answers, screen):
    """Prefilled from THEIR template (never the SSN); copied into forms/ only on save; numbered in the template check."""
    f = configured / "files"
    for name in ("jane.eft", "jane.jpg"):
        (f / name).write_bytes(b"x")
    theirs = f / "jane 5320.pdf"
    shutil.copy(FIXTURE, theirs)
    answers(SETTINGS, D, True, *[D] * 18, D, D, D, True, False,           # everything about you kept, as before
            "add", f"'{theirs}'",                                          # add one: their template (dragged, quoted)
            "Co-Trustee", "Jane", D, "Roe", "JR", D, D, "Female", "White", D,  # names typed over JOHN Q DOE; suffix JR
            D, D, D, D,                                                    # home address: read from their template
            str(f / "jane.eft"), str(f / "jane.jpg"), "done", QUIT)
    ui.menu()
    t, seen, out = saved(), answers.seen, screen.getvalue()
    assert seen["First name:"] == "JOHN" and seen["Home street address:"] == "100 OAK ST" and seen["Email (optional):"] == "JOHN.DOE@EXAMPLE.COM"
    assert FIXTURE_SSN not in str(seen) and FIXTURE_SSN not in (atf.DATA / "trust.yaml").read_text(encoding="utf-8")
    other = t["responsible_persons"][1]
    assert (other["first_name"], other["middle_name"], other["last_name"], other["suffix"], other["title"]) == ("Jane", "Q", "Roe", "JR", "Co-Trustee")
    assert other["home_address"] == {"street": "100 OAK ST", "zip": "45501", "city": "SPRINGFIELD", "state": "Ohio"}
    assert other["template"] == "5320.23_jane-roe.pdf" and (atf.FORMS / "5320.23_jane-roe.pdf").read_bytes() == theirs.read_bytes()
    assert atf.settings_problems() == [] and "all 2 5320.23 templates stamp correctly" in out
    choices = []
    answers(SETTINGS, D, True, *[D] * 18, D, D, D, True, False,
            lambda kw: (choices.extend(c.title for c in kw["choices"]), "remove 0")[1], True, "done", QUIT)
    ui.menu()
    assert choices == ["Done (you + 1)", "Add a responsible person", "Change Jane Roe (Co-Trustee)", "Remove Jane Roe (Co-Trustee)"]
    assert len(saved()["responsible_persons"]) == 1


def test_cancelling_a_new_responsible_person_copies_nothing(configured, answers, screen):
    theirs = configured / "files" / "jane.pdf"
    shutil.copy(FIXTURE, theirs)
    before = saved()
    answers(SETTINGS, D, True, *[D] * 18, D, D, D, True, False, "add", str(theirs), "Co-Trustee", None, QUIT)
    ui.menu()
    assert saved() == before and list(atf.FORMS.glob("5320.23_*")) == []


def test_settings_goes_straight_to_other_responsible_persons(configured, answers, screen):
    """A trust's Settings asks first: everything, or just the other responsible persons (no walk through the rest)."""
    from conftest import pick
    f = configured / "files"
    for name in ("jane.eft", "jane.jpg"):
        (f / name).write_bytes(b"x")
    theirs = f / "jane.pdf"
    shutil.copy(FIXTURE, theirs)
    before = saved()
    answers(pick("Settings"), pick("Other responsible persons in the trust (0)"), "add", str(theirs),
            "Co-Trustee", "Jane", "", "Roe", "(none)", D, D, "Female", "White", D, D, D, D, D,
            str(f / "jane.eft"), str(f / "jane.jpg"), "done", QUIT)
    ui.menu()
    asked = [m for _, m in answers.asked]
    assert not any(m.startswith(("Trust name", "Keep your current 5320.23", "Agency", "Fingerprint file")) for m in asked)
    t = saved()
    assert t["responsible_persons"][0] == before["responsible_persons"][0] and t["cleo"] == before["cleo"]
    assert t["responsible_persons"][1]["last_name"] == "Roe" and (atf.FORMS / "5320.23_jane-roe.pdf").exists()
    assert "all 2 5320.23 templates stamp correctly" in screen.getvalue()


def test_individual_settings_menu(configured, answers, screen):
    """An individual has no other responsible persons: All settings, where the inventory lives, Check my setup."""
    from conftest import make_individual
    make_individual()
    seen = {}
    answers(lambda kw: ui.settings_menu, lambda kw: (seen.setdefault("menu", [str(c.title) for c in kw["choices"]]), ui.settings_flow)[1],
            lambda kw: None, QUIT)
    ui.menu()
    assert seen["menu"] == ["All settings (you, your answers, CLEO, firearm, files)",
                            "Where your inventory lives (this computer)", "Check my setup (files, templates, browser; no eForms)", "Back"]
    assert answers.asked[2][1] == "Who files the Form 1s?"
