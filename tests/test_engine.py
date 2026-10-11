"""Engine (src/atf.py) without a browser: queue, matching, 5320.23 reading/stamping, settings, mapping resolution."""
import re
import shutil

import pymupdf
import pytest
import yaml

from conftest import FIXTURE, FIXTURE_ANSWERS, FIXTURE_SSN, TODAY, atf, fake_settings, make_individual, queue


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
