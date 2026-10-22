"""Engine (src/atf.py) without a browser: queue, matching, 5320.23 reading/stamping, settings, mapping resolution."""
import re
import shutil

import pymupdf
import pytest
import yaml

from conftest import FIXTURE, FIXTURE_ANSWERS, FIXTURE_SSN, OTHER_RP, TODAY, add_other_rp, atf, fake_settings, make_individual, queue


def test_token_match_is_whole_token_and_case_insensitive():
    tm = atf._token_match
    assert tm("22", "22") and tm("22", "CAL 22") and not tm("22", "223") and not tm("22", ".223")
    assert tm("S101", "s101") and not tm("S101", "S1015") and tm("Doe Trust", "THE DOE TRUST")
    assert tm("Valid EBTS Package", "Status: Valid EBTS Package | Ok") and not tm("Valid EBTS Package", "Invalid EBTS Package")


def test_pick_row_skips_filed_and_pending(sandbox):
    queue(("S1", "9", "filed", "1"), ("S2", "9", "pending"), ("S3", "9"))
    rows = atf.load_rows()
    assert atf.pick_row(rows, None) == 2
    for bad in (0, 9):
        with pytest.raises(SystemExit, match="does not exist"):
            atf.pick_row(rows, bad)
    with pytest.raises(SystemExit, match="pending"):
        atf.pick_row(rows, 2)
    assert atf.pick_row(rows, 2, force=True) == 1


def test_queue_save_is_atomic_and_reads_excel_bom(sandbox):
    queue(("S1", "9"))
    assert not atf.SUPP.with_suffix(".csv.tmp").exists()
    atf.SUPP.write_bytes(b"\xef\xbb\xbfcaliber,serial_number,status,application_number\r\n9,S1,,\r\n")  # Excel's UTF-8 BOM
    assert atf.load_rows()[0]["caliber"] == "9"


def test_read_template_gets_12_answers_and_never_the_ssn():
    got = atf.read_template(FIXTURE)
    assert got == FIXTURE_ANSWERS
    assert FIXTURE_SSN not in str(got)


def test_split_address_one_line():
    assert atf._split_address("1400 OLD MILL RD NEW CASTLE PA 16101") == {
        "street": "1400 OLD MILL RD", "city": "NEW CASTLE", "state": "Pennsylvania", "zip": "16101"}


def test_stamp_5320_replaces_serial_caliber_date_and_cleo_official(configured):
    dest, missing = atf.stamp_5320({"serial_number": "ABC123", "caliber": "9"}, TODAY)
    assert missing == [] and dest.name == "5320.23_ABC123.pdf"
    doc = pymupdf.open(dest)
    p1, p2 = doc[0].get_text(), doc[1].get_text()
    assert "ABC123" in p1 and "TEMPLATE" not in p1
    assert "PAT EXAMPLE, CHIEF OF POLICE" in p1
    assert TODAY in p2
    assert atf._check_5320(dest, {"serial_number": "ABC123"}, TODAY) == []
    assert len(atf._check_5320(dest, {"serial_number": "ABC123"}, "01/01/2099")) == 1   # stale date flagged
    assert len(atf._check_5320(dest, {"serial_number": "ABC12"}, TODAY)) == 1           # wrong serial flagged


def test_stamp_finds_cleo_line_by_position_not_title(configured):
    t = atf.load_trust()
    t["cleo"].update({"official": "NEW SHERIFF", "title": "SHERIFF"})  # differs from what the template prints
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t), encoding="utf-8")
    dest, missing = atf.stamp_5320({"serial_number": "ABC123", "caliber": "9"}, TODAY)
    text = pymupdf.open(dest)[0].get_text()
    assert missing == [] and "NEW SHERIFF, SHERIFF" in text and "PAT EXAMPLE" not in text


def test_settings_problems(configured):
    assert atf.settings_problems() == []
    t = atf.load_trust()
    t["files"]["photo"] = ""
    assert any("photo" in p for p in atf.settings_problems(t))
    t = atf.load_trust()
    t["files"]["eft"] = str(configured / "files" / "missing.eft")
    assert any("not found" in p for p in atf.settings_problems(t))
    (atf.FORMS / "5320.23.pdf").unlink()
    assert any("5320.23 template" in p for p in atf.settings_problems())


def test_settings_survive_non_ascii(configured):
    t = atf.load_trust()
    t["trust"]["name"] = "Peña Family Trust"
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t, allow_unicode=True), encoding="utf-8")
    assert atf.load_trust()["trust"]["name"] == "Peña Family Trust"


def _dry_steps(skip_tags=()):
    lines = []
    atf.emit = lambda kind, text, **d: lines.append(text.strip()) if kind == "step" else None
    row = atf.load_rows()[0]
    atf.stamp_5320(row, TODAY)
    atf._run_steps(None, atf.load_mapping()["portal"]["steps"], atf._row_context(atf.load_trust(), row), True, [], skip_tags)
    return "\n".join(lines)


def test_shared_mapping_resolves_from_settings(configured):
    queue(("ABC123", "9"))
    steps = _dry_steps()
    assert "{" not in steps                                                   # every placeholder filled, selectors too
    for expected in ("fill #tradeName DOE FAMILY TRUST", "fill #titleOfTheSubmitter Trustee", "has-text('Asian')",
                     "fill #manufacturerCode FMI", "select #cleoAddress-county CLARK", "select #cleoAddress-city SPRINGFIELD",
                     "select #birthState OHIO", "fill #serialNumber ABC123", "fill #weaponCaliber 9",
                     f"upload #photoFile {configured / 'files' / 'me.jpg'}", "fill #description 3D PRINTER"):
        assert expected in steps, expected
    assert steps.count("upload #file-upload") == 2                           # one supporting doc + the trust document


def _dry_individual():
    lines = []
    atf.emit = lambda kind, text, **d: lines.append(text.strip()) if kind == "step" else None
    atf._run_steps(None, atf.load_mapping()["portal"]["steps"], atf._row_context(atf.load_trust(), atf.load_rows()[0]), True, [])
    return lines


def test_individual_steps(configured):
    queue(("ABC123", "9"))
    trust_steps = _dry_steps()
    assert "check #Trust" in trust_steps and "check #Individual" not in trust_steps and "[id='11.A']" not in trust_steps
    make_individual(upin="U123")
    steps = _dry_individual()
    joined = "\n".join(steps)
    assert "{" not in joined and "check #Individual" in steps and "check #Trust" not in steps
    for q, _ in atf.QUESTIONS_11:
        assert f"select [id='{q}'] No" in steps and f"expect [id='{q}'] >> nth=0 No" in steps   # filled, then read back
    assert "select [id='11.M.2'] N/A" in steps and "select [id='17.Aanswer'] US" in steps and "fill #14answer U123" in steps
    for absent in ("#tradeName", "#formFile", "#businessName", "CORPORATION/TRUST", "#titleOfTheSubmitter"):
        assert absent not in joined, absent                                    # no trust, no 5320.23, no trust document
    assert joined.count("upload #file-upload") == 1                            # the supporting doc only
    make_individual(upin="")
    assert "#14answer" not in "\n".join(_dry_individual())                    # no UPIN: left empty


def test_individual_settings_problems(configured):
    make_individual()
    assert atf.settings_problems() == []                                       # no trust, no trust document, no 5320.23
    make_individual(answer="Yes")
    assert any("only files when every answer is No" in p for p in atf.settings_problems())
    t = make_individual(answer="")
    assert any("not answered: 11.C" in p for p in atf.settings_problems())
    assert atf.applicant_type({}) == "trust" and atf.applicant_type(t) == "individual"


def test_ssn_pause_is_left_out_when_skipped(configured):
    queue(("ABC123", "9"))
    has_pause = lambda steps: any(line.startswith("pause") for line in steps.splitlines())  # noqa: E731
    assert has_pause(_dry_steps())
    assert not has_pause(_dry_steps(skip_tags={"ssn"}))


def test_shared_mapping_has_no_personal_values():
    text = atf.MAPPING.read_text(encoding="utf-8")
    values = [v for v in re.findall(r'value: "([^"]*)"', text) if "{" not in v]
    allowed = {"U.S.CITIZEN", "UNITED STATES OF AMERICA", "UNITED STATES", "SILENCER", "CALIBER", "FMI",
               "CORPORATION/TRUST/OTHER LEGAL ENTITY", "Escape", "Valid EBTS Package", "US"}
    sections = set(re.findall(r'action: section, value: "([^"]*)"', text))
    assert [v for v in values if v not in allowed | sections and not v.isdigit()] == []


def test_row_length_overrides_the_settings_default(configured):
    queue(("ABC123", "9"))
    rows = atf.load_rows()
    rows[0]["overall_length"] = "7.25"
    atf.save_rows(rows)
    steps = _dry_steps()
    assert "fill #overallLength 7.25" in steps and "fill #overallLength 12" not in steps


def test_answers_are_checked_together_and_repicked():
    """Answers picked in a check_picks block are read back together at the end: one the page wipes once is picked
    again; one it keeps wiping stops the run (never goes on blank). A plain pick is checked once, right away."""
    from playwright.sync_api import sync_playwright
    said = []
    atf.emit = lambda kind, text, **d: said.append(text)
    block = lambda sel, val: [{"action": "check_picks", "steps": [{"selector": sel, "action": "select", "value": val}]}]  # noqa: E731
    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page()
        page.set_content("""<select id="q"><option value="">Select</option><option value="1: No">No</option><option value="2">Yes</option></select>
          <select id="c"><option>Select US/NON-US ...</option><option value="U">US</option><option value="N">NON-US</option></select>
          <script>let wipes = 1; document.getElementById('q').addEventListener('change', e => {
              if (wipes-- > 0) setTimeout(() => e.target.selectedIndex = 0, 100); });  // the page resets it once</script>""")
        atf._run_steps(page, block("#q", "No"), {}, False, [])
        assert atf._shown_value(page.locator("#q")) == "No" and any("didn't stick (#q)" in t for t in said)
        atf._run_steps(page, [{"selector": "#c", "action": "select", "value": "US"}], {}, False, [])   # by label, not "U"
        assert atf._shown_value(page.locator("#c")) == "US"
        page.evaluate("wipes = 99")
        with pytest.raises(RuntimeError, match="keeps losing these answers: #q"):
            atf._run_steps(page, block("#q", "Yes"), {}, False, [])
        assert atf._picks is None                                                   # the block is closed even on failure
        bad = []
        page.set_content('<input id="v" value="NON-US">')
        atf._run_steps(page, [{"selector": "#v", "action": "expect", "label": "Citizenship", "value": "US", "exact": True}],
                       {}, False, bad)
        assert bad                                                                  # "US" must not match "NON-US"


# ---------- other responsible persons in the trust ----------
def test_read_person_gets_their_name_address_and_never_the_ssn():
    got = atf.read_person(FIXTURE)
    assert got == {"first_name": "JOHN", "middle_name": "Q", "last_name": "DOE", "email": "JOHN.DOE@EXAMPLE.COM",
                   "home_address.street": "100 OAK ST", "home_address.city": "SPRINGFIELD", "home_address.state": "Ohio",
                   "home_address.zip": "45501", "dob": "01/02/1980", "place_of_birth.state": "Ohio"}
    assert FIXTURE_SSN not in str(got)


def test_each_responsible_person_gets_their_own_5320(configured):
    t = atf.load_trust()
    t["cleo"]["official"] = "NEW CHIEF"                     # your CLEO changed; theirs is whoever they put on their form
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t), encoding="utf-8")
    person = add_other_rp()
    (mine, m1), (theirs, m2) = atf.stamp_all({"serial_number": "ABC123", "caliber": "9"}, TODAY)
    assert m1 == m2 == [] and (mine.name, theirs.name) == ("5320.23_ABC123.pdf", "5320.23_ABC123.jane-roe.pdf")
    assert "NEW CHIEF, CHIEF OF POLICE" in pymupdf.open(mine)[0].get_text()
    p1 = pymupdf.open(theirs)[0].get_text()
    assert "PAT EXAMPLE, CHIEF OF POLICE" in p1 and "NEW CHIEF" not in p1 and "ABC123" in p1
    assert atf._check_5320(theirs, {"serial_number": "ABC123"}, TODAY) == []
    assert atf._find_5320("ABC123") == mine and atf._find_5320("ABC123", person=person) == theirs


def test_other_responsible_person_settings_problems(configured):
    add_other_rp(last_name="", photo="/nowhere/jane.jpg")
    probs = atf.settings_problems()
    assert "responsible person 2: missing last name" in probs
    assert any(p.startswith("responsible person 2: photo not found") for p in probs)
    assert not any("Jane" in p for p in probs)                                 # numbered, never named (home screen)
    (atf.FORMS / OTHER_RP["template"]).unlink()
    assert "responsible person 2: no 5320.23 template at forms/5320.23_jane-roe.pdf" in atf.settings_problems()
    make_individual()
    assert atf.settings_problems() == [] and atf.other_rps(atf.load_trust()) == []   # individuals have no others


def test_mapping_adds_each_other_responsible_person(configured):
    queue(("ABC123", "9"))
    assert "#addResponsiblePerson" not in _dry_steps()                         # just you: nothing added, no count check
    add_other_rp(middle_name="Q", suffix="JR")
    atf.stamp_all(atf.load_rows()[0], TODAY)
    steps = _dry_steps()
    assert "{" not in steps
    for expected in ("click #addResponsiblePerson", "fill #title Co-Trustee", "fill #firstName Jane", "fill #lastName Roe",
                     "select #middleNameDetail INITIAL ONLY", "fill #middleName Q", "select #cadence JR",
                     "fill #emailAddress jane@example.com", "fill #street 300 ELM ST", "fill #zipCode 45503",
                     "select #state OHIO", "select select[name='city'] SPRINGFIELD", "fill #dateOfBirth 03/04/1985",
                     "select #sex FEMALE", "has-text('White')", f"upload #photoFile {configured / 'files' / 'jane.jpg'}",
                     f"upload #formFile {atf.OUT / '5320.23_ABC123.jane-roe.pdf'}", "expect tr:has([id='1Edit']) Roe",
                     "expect input[id$='Edit'] 2"):
        assert expected in steps, expected
    assert steps.count("dismiss_dialog") == 2 and steps.count("upload #formFile") == 2
    assert steps.index("fill #tradeName") < steps.index("click #addResponsiblePerson") < steps.index("#cleoAgencyOrDepartmentName")


def test_filing_refuses_a_missing_5320_of_another_person(configured):
    from types import SimpleNamespace
    queue(("ABC123", "9"))
    add_other_rp()
    atf.stamp_5320(atf.load_rows()[0], TODAY)                                  # only yours
    opts = SimpleNamespace(dry=True, submit=False, auto_affirm=True, skip_ssn=True, capture=False)
    with pytest.raises(SystemExit, match="titled 'Co-Trustee' for serial ABC123"):
        atf.file_rows(atf.load_rows(), [0], opts)


def test_fill_handles_a_dropdown_or_a_wrapped_field(sandbox):
    """A step's id may turn out to be a dropdown, or a wrapper around the input (the trust's Add Responsible Person
    popup, first live try: '#title ... is not an <input>'). Anything else fails, and the failing page is saved."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page()
        page.set_default_timeout(3000)
        page.set_content("""<div class="modal">Add Responsible Person <span id="title"><label>Title</label><input></span>
          <select id="kind"><option>Select ...</option><option>TRUSTEE</option></select><input id="plain"><p id="text">x</p></div>""")
        steps = [{"selector": "#title", "action": "fill", "value": "Trustee"}, {"selector": "#kind", "action": "fill", "value": "Trustee"},
                 {"selector": "#plain", "action": "fill", "value": "Roe"}]
        atf._run_steps(page, steps, {}, False, [])
        assert page.locator("#title input").input_value() == "Trustee" and atf._shown_value(page.locator("#kind")) == "TRUSTEE"
        assert page.locator("#plain").input_value() == "Roe"
        with pytest.raises(RuntimeError, match="#text is a <p> with no field inside it"):
            atf._run_steps(page, [{"selector": "#text", "action": "fill", "value": "x"}], {}, False, [])
        atf._save_failure(page)
    saved_html = next(atf.OUT.glob("failed_*.html")).read_text(encoding="utf-8")
    record = yaml.safe_load(next(atf.OUT.glob("failed_*.jsonl")).read_text(encoding="utf-8"))
    assert saved_html.startswith('<div class="modal">') and 'id="title"' in saved_html
    assert record["failed_step"] == "#text" and any(c.get("id") == "kind" for c in record["controls"])
