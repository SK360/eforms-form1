"""Born outside the US (issue #6: no state of birth, the country picked instead) and the firearm description's
100-character limit (issue #7: eForms' Form 1 PDF prints only the first 100). Fake data only; no eForms."""
import pymupdf
import pytest
import yaml

from conftest import D, FIXTURE, TODAY, add_other_rp, atf, queue, ui
from test_portal import RP_PAGE, _run_on_fake_popup

QUIT = lambda kw: "quit"  # noqa: E731
OLD_DEFAULT = "I POSSESS NO PARTS OR PLANS, AND WILL NOT PURCHASE OR MANUFACTURE ANY PARTS UNTIL A FORM 1 IS APPROVED"


def edit_settings(change):
    t = atf.load_trust()
    change(t)
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t, sort_keys=False), encoding="utf-8")
    return t


def born_abroad(person, country="GERMANY"):
    person["place_of_birth"] = {"country": country, "state": ""}


def dry_steps():
    lines, row = [], atf.load_rows()[0]
    atf.stamp_all(row, TODAY)
    atf.emit = lambda kind, text, **d: lines.append(text.strip()) if kind == "step" else None
    atf._run_steps(None, atf.load_mapping()["portal"]["steps"], atf._row_context(atf.load_trust(), row), True, [], {"ssn"})
    return lines


# ---------- issue #6: place of birth ----------
def test_born_in_us_for_every_us_spelling_and_for_settings_without_a_country():
    for country in (None, "", "United States of America", "UNITED STATES", "usa", "U.S."):
        assert atf.born_in_us({"place_of_birth": {"country": country, "state": "Ohio"}}), country
    assert atf.born_in_us({}) and atf.birth_country({}) == "UNITED STATES OF AMERICA"
    abroad = {"place_of_birth": {"country": "Germany", "state": "Ohio"}}  # a leftover state is never sent
    assert not atf.born_in_us(abroad) and atf.birth_country(abroad) == "GERMANY" and atf.birth_state(abroad) == ""


def test_born_in_the_us_still_picks_country_then_state(configured):
    queue(("S1", "9"))
    steps = dry_steps()
    assert "select #birthCountry UNITED STATES OF AMERICA" in steps and "select #birthState OHIO" in steps


def test_born_abroad_picks_the_country_and_skips_birth_state(configured):
    queue(("S1", "9"))
    edit_settings(lambda t: born_abroad(t["responsible_persons"][0]))
    steps = dry_steps()
    assert "select #birthCountry GERMANY" in steps and not any("#birthState" in s for s in steps)
    assert atf.settings_problems() == []


def test_state_of_birth_required_only_when_born_in_the_us(configured):
    edit_settings(lambda t: t["responsible_persons"][0]["place_of_birth"].update(state=""))
    assert "missing state of birth (responsible_persons.0.place_of_birth.state)" in atf.settings_problems()
    edit_settings(lambda t: born_abroad(t["responsible_persons"][0]))
    assert atf.settings_problems() == []
    add_other_rp(place_of_birth={"country": "United States of America", "state": ""})
    assert atf.settings_problems() == ["responsible person 2: missing state of birth"]


def test_another_responsible_person_born_abroad_on_the_fake_popup(configured, monkeypatch):
    import test_portal
    queue(("S1", "9"))
    add_other_rp(place_of_birth={"country": "PHILIPPINES", "state": ""})
    monkeypatch.setattr(test_portal, "RP_PAGE", RP_PAGE.replace(
        "<option>UNITED STATES OF AMERICA</option>", "<option>UNITED STATES OF AMERICA</option><option>PHILIPPINES</option>"))
    saved, bad, _ = _run_on_fake_popup()
    assert bad == [] and saved[0]["birthCountry"] == "PHILIPPINES" and saved[0]["birthState"] == "Select a state ..."


def _template_born_in(tmp_path, country):
    """The fake template with 9b (state) left empty and 9c reading `country`, typed like the generator does."""
    doc = pymupdf.open(FIXTURE)
    p2 = doc[1]
    for word in ("OHIO", "UNITED STATES OF AMERICA"):
        for r in p2.search_for(word):
            if r.x0 > 80:  # the typed values, not the form's own "United States of America" checkbox label
                p2.add_redact_annot(r)
    p2.apply_redactions()
    p2.insert_font(fontname="nimbus", fontbuffer=pymupdf.Font("helv").buffer)
    p2.insert_text((390, 407 + 8.4), country, fontname="nimbus", fontsize=9)
    path = tmp_path / "abroad.pdf"
    doc.save(path)
    return path


def test_template_with_a_foreign_country_of_birth(tmp_path):
    got = atf.read_template(_template_born_in(tmp_path, "GERMANY"))
    assert got["responsible_persons.0.place_of_birth.country"] == "GERMANY"
    assert "responsible_persons.0.place_of_birth.state" not in got
    assert "responsible_persons.0.place_of_birth.country" not in atf.read_template(FIXTURE)  # US: as before


def test_setup_born_abroad(sandbox, answers, screen):
    """Individual setup: No to "Born in the United States?" asks the country (a US spelling is turned away), no state."""
    f = sandbox / "files"
    for name in ("prints.eft", "me.jpg"):
        (f / name).write_bytes(b"x")
    checks = {}

    def country(kw):
        checks["usa"], checks["blank"] = kw["validate"]("usa"), kw["validate"]("  ")
        return "germany"
    answers(True, "individual", "01/02/1980", "Male", ["White"], False, country, "Ohio",
            *["No"] * len(atf.QUESTIONS_11), "",
            "SPRINGFIELD POLICE DEPARTMENT", "Pat Chief", "CHIEF OF POLICE", "200 MAIN ST", "45502", "Springfield", "Clark", "Ohio",
            D, D, D, str(f / "prints.eft"), str(f / "me.jpg"), False, QUIT)
    ui.menu()
    asked = [m for _, m in answers.asked]
    assert "State of birth:" not in asked and "Born in the United States?" in asked
    assert "answer Yes" in str(checks["usa"]) and checks["blank"] is not True
    t = yaml.safe_load((atf.DATA / "trust.yaml").read_text(encoding="utf-8"))
    assert t["responsible_persons"][0]["place_of_birth"] == {"country": "GERMANY", "state": ""}
    assert atf.settings_problems() == []


# ---------- issue #7: firearm description ----------
def test_default_description_fits_and_the_old_one_does_not():
    assert len(ui.DEFAULT_DESCRIPTION) <= atf.DESCRIPTION_MAX < len(OLD_DEFAULT)


def test_long_description_blocks_filing_and_says_where_it_is_cut(configured):
    edit_settings(lambda t: t["firearm"].update(description=OLD_DEFAULT))
    probs = atf.settings_problems()
    assert probs == ["the firearm description is 102 characters: the Form 1 PDF eForms makes prints only the first 100 "
                     "(it would end \"...FORM 1 IS APPROV\"); shorten it in Settings"]
    edit_settings(lambda t: t["firearm"].update(description="X" * 100))
    assert atf.settings_problems() == []


def test_setup_turns_away_a_description_over_100(configured, answers, screen):
    checks = {}

    def desc(kw):
        checks["long"], checks["ok"] = kw["validate"](OLD_DEFAULT), kw["validate"](ui.DEFAULT_DESCRIPTION)
        return ui.DEFAULT_DESCRIPTION
    answers(lambda kw: ui.settings_flow, D, True, *[D] * 18, desc, D, D, D, True, False, "done", QUIT)
    ui.menu()
    assert "102 characters" in str(checks["long"]) and checks["ok"] is True
    assert atf.load_trust()["firearm"]["description"] == ui.DEFAULT_DESCRIPTION


@pytest.mark.parametrize("limit", [100, 120])
def test_description_read_back_catches_a_box_that_cut_it(configured, limit):
    """The mapping reads the box back right after filling it: a cut description is a mismatch (nothing is submitted)."""
    from playwright.sync_api import sync_playwright
    queue(("S1", "9"))
    steps = [s for s in atf.load_mapping()["portal"]["steps"] if s.get("selector") == "#description"
             and s.get("value") == "{description}"]
    assert [s["action"] for s in steps] == ["fill", "expect"]
    row = atf.load_rows()[0]
    atf.stamp_all(row, TODAY)
    ctx = dict(atf._row_context(atf.load_trust(), row), description=OLD_DEFAULT.lower())
    bad = []
    atf.emit = lambda *a, **k: None
    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page()
        page.set_content(f'<textarea id="description" maxlength="{limit}" oninput="this.value=this.value.toUpperCase()"></textarea>')
        atf._run_steps(page, steps, ctx, False, bad)
    assert (bad != []) == (limit < len(OLD_DEFAULT))  # uppercased by the page: still a match when it all fit


def test_long_dropdown_names_the_closest_choices():
    from playwright.sync_api import sync_playwright
    opts = "".join(f"<option>{c}</option>" for c in ["Select a country ...", "GERMANY", "GHANA", "GREECE", "GUAM"]
                   + [f"COUNTRY {i}" for i in range(20)])
    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page()
        page.set_content(f'<select id="birthCountry">{opts}</select>')
        with pytest.raises(atf.NoSuchOption) as e:
            atf._pick(page.locator("#birthCountry"), "GERMNAY", wait_ms=500, grace_ms=0)
    assert "'GERMNAY' isn't one of the choices on the page (closest: GERMANY" in str(e.value)
    assert "24 choices in all" in str(e.value)
