"""Filing an SBR (or SBS/AOW) made from an existing receiver: queue columns, 5320.23 items 4a-4c, the portal steps
(original manufacturer's code, "not in the list" model/caliber, barrel length, optional markings photo) and the
queue row leaving once its approval is recorded. All on fake data."""
import pymupdf
import pytest

from conftest import TODAY, atf, queue


def sbr(**over):
    row = {"serial_number": "XYZ123", "type": "SHORT BARRELED RIFLE", "mfr_code": "AP2", "mfr_name": "EXAMPLE ARMS LLC",
           "mfr_address": "1 FACTORY RD, ANYTOWN, OH 45000", "mfr_country": "UNITED STATES", "model": "M4E1",
           "caliber": "5.56", "unit": "CALIBER", "barrel_length": "10.3", "overall_length": "30",
           "description": "RECEIVER MARKED MULTI", "photo": "", "status": ""}
    row.update(over)
    return row


def add_sbr(**over):
    atf.add_queued([sbr(**over)])
    return next(r for r in atf.load_rows() if atf.is_nfa_row(r))


def dry_steps(row):
    lines = []
    atf.emit = lambda kind, text, **d: lines.append(text.strip()) if kind == "step" else None
    atf._run_steps(None, atf.load_mapping()["portal"]["steps"], atf._row_context(atf.load_trust(), row), True, [])
    return "\n".join(lines)


def test_queue_keeps_the_sbr_columns_and_suppressor_files_keep_their_header(configured):
    queue(("S1", "9"))
    assert atf.SUPP.read_text(encoding="utf-8").splitlines()[0] == ",".join(atf.COLS)   # nothing new for suppressor users
    row = add_sbr()
    assert (row["mfr_code"], row["model"], row["barrel_length"]) == ("AP2", "M4E1", "10.3")
    assert [atf.is_nfa_row(r) for r in atf.load_rows()] == [False, True]


def test_sbr_needs_every_marking_and_one_caliber(configured, tmp_path):
    assert atf.nfa_row_problems(sbr()) == []
    assert atf.nfa_row_problems({"serial_number": "S1", "caliber": "9"}) == []           # a suppressor: not checked here
    assert atf.nfa_row_problems(sbr(mfr_code="")) == []                                  # eForms finds the code by name
    probs = " ".join(atf.nfa_row_problems(sbr(mfr_name="", barrel_length="", caliber="MULTI")))
    assert "manufacturer's name" in probs and "barrel length" in probs and "ONE caliber" in probs
    assert "120" in " ".join(atf.nfa_row_problems(sbr(description="X" * 121)))
    assert "not found" in " ".join(atf.nfa_row_problems(sbr(photo=str(tmp_path / "nope.jpg"))))
    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * (3 * 1024 * 1024 + 1))
    assert "3 MB" in " ".join(atf.nfa_row_problems(sbr(photo=str(big))))


def test_5320_gets_type_original_manufacturer_and_model(configured):
    row = add_sbr()
    dest, missing = atf.stamp_5320(row, TODAY)
    assert missing == []
    page = pymupdf.open(dest)[0]
    item4 = pymupdf.Rect(20, 360, 590, 475)                       # items 4a-4e on the generator's layout
    text = page.get_text(clip=item4)
    for want in ("SHORT BARRELED RIFLE", "EXAMPLE ARMS LLC", "1 FACTORY RD, ANYTOWN, OH 45000", "M4E1", "5.56", "XYZ123"):
        assert want in text, want
    for gone in ("SILENCER", "DOE FAMILY TRUST", "TEMPLATE"):     # the template's suppressor values
        assert gone not in text, gone


def test_suppressor_5320_item_4_is_untouched(configured):
    queue(("ABC123", "9"))
    dest, missing = atf.stamp_5320(atf.load_rows()[0], TODAY)
    text = pymupdf.open(dest)[0].get_text(clip=pymupdf.Rect(20, 360, 590, 475))
    assert missing == [] and "SILENCER" in text and "DOE FAMILY TRUST" in text


def test_portal_steps_for_an_sbr(configured, tmp_path):
    photo = tmp_path / "files" / "markings.jpg"
    photo.write_bytes(b"x")
    row = add_sbr(photo=str(photo))
    atf.stamp_5320(row, TODAY)
    steps = dry_steps(row)
    assert "{" not in steps
    for expected in ("pick_manufacturer code AP2", "select #country UNITED STATES", "select #productType SHORT BARRELED RIFLE",
                     "pick_model M4E1 / 5.56",
                     "select #unitOfMeasure CALIBER", "fill #barrelLength 10.3", "fill #overallLength 30",
                     "fill #serialNumber XYZ123", "fill #description RECEIVER MARKED MULTI",
                     f"upload #file-upload {photo}", "fill #description PHOTO OF RECEIVER MARKINGS",
                     "expect #weaponType SHORT BARRELED RIFLE", "expect #barrelLength 10.3"):
        assert expected in steps, expected
    assert "FMI" not in steps


def test_markings_photo_is_optional(configured):
    row = add_sbr()
    atf.stamp_5320(row, TODAY)
    steps = dry_steps(row)
    assert "PHOTO OF RECEIVER MARKINGS" not in steps and "fill #barrelLength 10.3" in steps


def test_without_a_code_the_manufacturer_is_found_by_name(configured):
    row = add_sbr(mfr_code="")
    atf.stamp_5320(row, TODAY)
    steps = dry_steps(row)
    assert "pick_manufacturer by name EXAMPLE ARMS LLC" in steps and "FMI" not in steps


def test_suppressor_steps_are_unchanged(configured):
    queue(("ABC123", "9"))
    row = atf.load_rows()[0]
    atf.stamp_5320(row, TODAY)
    steps = dry_steps(row)
    for expected in ("pick_manufacturer code FMI", "select #productType SILENCER", "select #unitOfMeasure CALIBER",
                     "expect #weaponType SILENCER", "pick_model N/A / 9"):
        assert expected in steps, expected
    assert "descriptionNotListed" not in steps and "barrelLength" not in steps


def test_filing_refuses_an_incomplete_sbr_before_the_browser(configured, monkeypatch):
    from types import SimpleNamespace
    add_sbr(model="")
    monkeypatch.setattr(atf, "_browser", lambda: pytest.fail("the browser must not open"))
    rows = atf.load_rows()
    with pytest.raises(SystemExit, match="model as engraved"):
        atf.file_rows(rows, [0], SimpleNamespace(dry=False, submit=True, auto_affirm=True, skip_ssn=True, capture=False))


def test_approved_sbr_leaves_the_queue_for_the_sbr_inventory(configured):
    add_sbr()
    rows = atf.load_rows()
    rows[0].update(status="filed", application_number="123456789")
    atf.save_rows(rows)
    rec = {"type": "SHORT BARRELED RIFLE", "serial": "XYZ123", "caliber": "5.56", "model": "M4E1", "barrel_length": "10.3",
           "overall_length": "30", "approved": True, "approval_date": "12/01/2026", "eform_id": "1234567"}
    assert atf.record_sbrs([rec]) == 1
    assert atf.load_rows() == []
    assert [r["serial_number"] for r in atf.load_sbrs()] == ["XYZ123"]


def test_a_queued_sbr_is_not_removed_by_someone_elses_approval(configured):
    add_sbr()                                    # still queued: never filed, so an approval can't be for it
    rec = {"type": "SHORT BARRELED RIFLE", "serial": "XYZ123", "caliber": "5.56", "model": "M4E1", "barrel_length": "10.3",
           "overall_length": "30", "approved": True, "approval_date": "12/01/2026", "eform_id": "1234567"}
    atf.record_sbrs([rec])
    assert len(atf.load_rows()) == 1


def test_menu_adds_an_sbr_and_refuses_multi(configured, answers, screen):
    from conftest import pick, ui
    seen = {}

    def caliber(kw):
        seen["multi"] = kw["validate"]("MULTI")
        return "5.56"
    answers(pick("Add suppressors"), pick("An SBR"), pick("Short Barreled Rifle"), "XYZ123", "Example Arms LLC",
            "Anytown, OH", lambda kw: kw["default"], "m4e1", caliber, "10.3", "30", "receiver marked multi",
            "", True, lambda kw: "quit")
    ui.menu()
    row = atf.load_rows()[0]
    assert seen["multi"].startswith("One caliber")
    assert (row["type"], row["mfr_code"], row["mfr_name"], row["mfr_address"], row["model"], row["caliber"], row["barrel_length"],
            row["photo"]) == ("SHORT BARRELED RIFLE", "", "EXAMPLE ARMS LLC", "ANYTOWN, OH", "M4E1", "5.56", "10.3", "")
    assert row["description"] == "RECEIVER MARKED MULTI" and row["mfr_country"] == "UNITED STATES"
    assert "Added XYZ123 (SBR)" in screen.getvalue()


def test_cleo_copy_gets_items_4a_to_4c_and_still_no_serial(configured):
    import shutil
    from conftest import CLEO_FIXTURE
    shutil.copy(CLEO_FIXTURE, atf.cleo_template())
    row = add_sbr()
    [(n, dest, probs)] = atf.stamp_cleo_all(row, TODAY)
    assert probs == []
    text = pymupdf.open(dest)[0].get_text()
    assert "SHORT BARRELED RIFLE" in text and "EXAMPLE ARMS LLC" in text and "M4E1" in text
    assert "XYZ123" not in text


# eForms' manufacturer popup as mapped live 2026-11-19: a PrimeNG autocomplete that matches any part of a name
FAKE_MFR = """<input type="radio" id="byName" checked><input type="radio" id="byCode"><input id="manufacturerCode">
<div id="manufacturerNameInput"><p-autocomplete id="manufacturerName"><span><input type="text"></span></p-autocomplete></div><div id="panel"></div>
<input id="manufacturerCodeFromName" readonly><button id="verifyManufacturer">Verify Manufacturer</button>
<button id="clearManufacturer">Clear</button>
<script>
const LIST = {"AERO PRECISION INC": "AP1", "AERO PRECISION LLC": "AP2", "PALMETTO STATE ARMORY": "PS1",
              "PALMETTO STATE ARMORY LLC": "PS2", "RAWL, JAMES B DBA PALMETTO ARMAMENT": "RW1"};
let chosen = null;
const panel = document.getElementById('panel'), inp = () => document.querySelector('#manufacturerName input');
function rebind() { const box = inp(); box.addEventListener('keyup', () => { const q = box.value.toUpperCase();
  const hits = q.length < 3 ? [] : Object.keys(LIST).filter(n => n.includes(q));
  panel.innerHTML = hits.length ? '<div class="ui-autocomplete-panel"><ul>' + hits.map(n =>
    '<li class="ui-autocomplete-list-item"><span>' + n + '</span></li>').join('') + '</ul></div>' : ''; }); }
rebind();
panel.addEventListener('click', e => { const li = e.target.closest('li'); if (li) { chosen = li.innerText.trim(); inp().value = chosen; panel.innerHTML = ''; } });
document.getElementById('verifyManufacturer').onclick = () => {
  document.getElementById('manufacturerCodeFromName').value = document.getElementById('byCode').checked ? '' : (LIST[chosen] || '');
  if (document.getElementById('manufacturerCodeFromName').value) inp().disabled = true; };   // eForms locks a verified name
document.getElementById('clearManufacturer').onclick = () => { inp().disabled = false; inp().value = ''; chosen = null;
  document.getElementById('manufacturerCodeFromName').value = ''; };
</script>"""


@pytest.fixture
def mfr_page():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    br = pw.chromium.launch(headless=True)
    page = br.new_page()
    page.set_default_timeout(3000)
    page.set_content(FAKE_MFR)
    atf._MFR_WAIT_MS, saved = 800, atf._MFR_WAIT_MS
    yield page
    atf._MFR_WAIT_MS = saved
    br.close()
    pw.stop()


def test_name_search_picks_the_exact_match(mfr_page, monkeypatch):
    monkeypatch.setattr(atf, "ask", lambda prompt: pytest.fail("an exact match needs no question"))
    assert atf._pick_manufacturer(mfr_page, "", "Aero Precision LLC") == {"mfr_code": "AP2", "mfr_atf_name": "AERO PRECISION LLC"}


def test_name_search_asks_when_not_sure(mfr_page, monkeypatch):
    asked = []
    monkeypatch.setattr(atf, "ask", lambda prompt: asked.append(prompt) or "1")
    # "PALMETTO STATE ARMORY" is listed with and without LLC: picking #1 must not click the LLC one
    got = atf._pick_manufacturer(mfr_page, "", "Palmetto")
    assert got == {"mfr_code": "PS1", "mfr_atf_name": "PALMETTO STATE ARMORY"}
    assert "PALMETTO STATE ARMORY LLC" in asked[0] and "RAWL, JAMES B DBA PALMETTO ARMAMENT" in asked[0]


def test_name_search_with_no_match_asks_for_another_spelling(mfr_page, monkeypatch):
    answers = iter(["aero", "2"])
    asked = []
    monkeypatch.setattr(atf, "ask", lambda prompt: asked.append(prompt) or next(answers))
    assert atf._pick_manufacturer(mfr_page, "", "Aeroo Precisionn")["mfr_code"] == "AP2"
    assert "shows no manufacturer" in asked[0]


def test_name_search_can_be_stopped(mfr_page, monkeypatch):
    monkeypatch.setattr(atf, "ask", lambda prompt: "q")
    with pytest.raises(RuntimeError, match="not chosen"):
        atf._pick_manufacturer(mfr_page, "", "Nothing Like It")


def test_name_search_tries_shorter_names(mfr_page, monkeypatch):
    asked = []
    monkeypatch.setattr(atf, "ask", lambda prompt: asked.append(prompt) or "2")
    # nothing lists "AERO PRECISION TACOMA"; "AERO PRECISION" does (INC and LLC), so it asks which
    assert atf._pick_manufacturer(mfr_page, "", "Aero Precision Tacoma")["mfr_code"] == "AP2"
    assert '"AERO PRECISION"' in asked[0].upper() and "AERO PRECISION LLC" in asked[0]


def test_name_search_accepts_a_pick_made_in_the_browser(mfr_page, monkeypatch):
    def by_hand(prompt):  # the 2026-11-19 practice run: the list never reached the script, the user verified it by hand
        mfr_page.evaluate("""() => { const box = document.querySelector('#manufacturerName input');
            box.value = 'AERO PRECISION LLC'; document.getElementById('manufacturerCodeFromName').value = 'AP2'; }""")
        return ""
    monkeypatch.setattr(atf, "ask", by_hand)
    assert atf._pick_manufacturer(mfr_page, "", "Nothing Like It") == {"mfr_code": "AP2", "mfr_atf_name": "AERO PRECISION LLC"}


def test_name_search_takes_a_name_the_site_verifies_without_a_list(mfr_page, monkeypatch):
    # the practice run: no suggestion list reached the script, but eForms verified the typed name (code AP2)
    mfr_page.evaluate("""() => { const panel = document.getElementById('panel');
      new MutationObserver(() => { if (panel.innerHTML) panel.innerHTML = ''; }).observe(panel, {childList: true});
      document.getElementById('verifyManufacturer').onclick = () => {
        const v = document.querySelector('#manufacturerName input').value.toUpperCase();
        document.getElementById('manufacturerCodeFromName').value = v === 'AERO PRECISION LLC' ? 'AP2' : ''; }; }""")
    monkeypatch.setattr(atf, "ask", lambda prompt: pytest.fail("eForms verified it; nothing to ask"))
    assert atf._pick_manufacturer(mfr_page, "", "AERO PRECISION LLC") == {"mfr_code": "AP2", "mfr_atf_name": "AERO PRECISION LLC"}


# eForms' firearm details for an SBR (mapped live 2026-11-19): Model an ng-select of ATF's list for the maker,
# Caliber a select; "not in the list" swaps both for text inputs
FAKE_MODEL = """<div id="f"><ng-select id="weaponModel"><div class="ng-value"></div><input type="text"></ng-select>
<div id="panel"></div><select id="weaponCaliber"><option value="">Select</option><option>5.56</option>
<option>300 BLACKOUT</option></select></div><input type="checkbox" id="descriptionNotListed">
<script>
const MODELS = ["17DM-15", "A15", "AC-9", "AERO PRECISION M4E1", "AP15", "AR15", "M4E1 PISTOL"];
const sel = document.getElementById('weaponModel'), q = sel.querySelector('input'), panel = document.getElementById('panel');
const show = () => { const hits = MODELS.filter(m => m.includes(q.value.toUpperCase()));
  panel.innerHTML = '<div class="ng-dropdown-panel">' + (hits.length ? hits.map(m => '<div class="ng-option">' + m + '</div>').join('')
    : '<div class="ng-option">No items found</div>') + '</div>'; };
sel.addEventListener('click', show); q.addEventListener('input', show);
panel.addEventListener('click', e => { const o = e.target.closest('.ng-option');
  if (o) { sel.querySelector('.ng-value').textContent = o.innerText; window.picked = o.innerText; panel.innerHTML = ''; } });
document.getElementById('descriptionNotListed').addEventListener('change', () => { document.getElementById('f').innerHTML =
  '<input id="weaponModel"><input id="weaponCaliber">'; });
</script>"""


@pytest.fixture
def model_page():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    br = pw.chromium.launch(headless=True)
    page = br.new_page()
    page.set_default_timeout(3000)
    page.set_content(FAKE_MODEL)
    yield page
    br.close()
    pw.stop()


def test_model_is_picked_from_the_list(model_page, monkeypatch):
    # "M4E1" also hits "M4E1 PISTOL"; only "AERO PRECISION M4E1" is the engraved model
    monkeypatch.setattr(atf, "ask", lambda prompt: pytest.fail("one entry matches; nothing to ask"))
    assert atf._pick_model(model_page, "M4E1", "5.56", nfa=True) == "AERO PRECISION M4E1"
    assert model_page.evaluate("window.picked") == "AERO PRECISION M4E1"
    assert model_page.locator("#weaponCaliber").input_value() == "5.56"
    assert not model_page.locator("#descriptionNotListed").is_checked()


def test_model_not_in_the_list_asks_then_types_it(model_page, monkeypatch):
    asked = []
    answers = iter(["", "none"])  # Enter alone is not "none": asked again
    monkeypatch.setattr(atf, "ask", lambda prompt: asked.append(prompt) or next(answers))
    assert atf._pick_model(model_page, "X99", "5.56", nfa=True) == ""
    assert "AP15" in asked[0] and len(asked) == 2  # nothing close: the whole list is shown
    assert model_page.locator("#descriptionNotListed").is_checked()
    assert model_page.locator("#weaponModel").input_value() == "X99"


def test_model_chosen_by_number(model_page, monkeypatch):
    asked = []
    monkeypatch.setattr(atf, "ask", lambda prompt: asked.append(prompt) or "1")  # AR15, the closest
    assert atf._pick_model(model_page, "AR-15 LOWER", "5.56", nfa=True) == "AR15"
    assert model_page.evaluate("window.picked") == "AR15"
    assert "  1. AR15" in asked[0] and "AP15" not in asked[0]


def test_mistyped_model_shows_the_closest_and_can_be_searched_again(model_page, monkeypatch):
    # PR #5: a fat-fingered model was typed in as "not listed" though eForms had it
    asked, answers = [], iter(["M4E1"])
    monkeypatch.setattr(atf, "ask", lambda prompt: asked.append(prompt) or next(answers))
    assert atf._pick_model(model_page, "M4E2", "5.56", nfa=True) == "AERO PRECISION M4E1"
    assert "Closest" in asked[0] and "AERO PRECISION M4E1" in asked[0] and len(asked) == 1
    assert model_page.evaluate("window.picked") == "AERO PRECISION M4E1"
    assert not model_page.locator("#descriptionNotListed").is_checked()


def test_model_pick_can_be_stopped(model_page, monkeypatch):
    monkeypatch.setattr(atf, "ask", lambda prompt: "q")
    with pytest.raises(RuntimeError, match="model not chosen"):
        atf._pick_model(model_page, "X99", "5.56", nfa=True)
    assert not model_page.locator("#descriptionNotListed").is_checked()


def test_model_with_a_caliber_not_listed_is_typed(model_page, monkeypatch):
    monkeypatch.setattr(atf, "ask", lambda prompt: pytest.fail("nothing to ask"))
    assert atf._pick_model(model_page, "M4E1", "6.5 GRENDEL", nfa=True) == ""
    assert model_page.locator("#weaponCaliber").input_value() == "6.5 GRENDEL"


LOCK_AS_LIVE = """([name, code]) => {  // live 2026-11-24: verified, the input is replaced by plain text
    const wrap = document.getElementById('manufacturerNameInput');
    window.savedInput = wrap.innerHTML;
    wrap.innerHTML = '<div class="manufacturerNameBox"> ' + name + ' </div>';
    document.getElementById('manufacturerCodeFromName').value = code;
    document.getElementById('clearManufacturer').onclick = () => { wrap.innerHTML = window.savedInput;
      document.getElementById('manufacturerCodeFromName').value = ''; rebind(); }; }"""


def test_popup_opening_with_the_marked_name_verified_keeps_it(mfr_page, monkeypatch):
    mfr_page.evaluate(LOCK_AS_LIVE, ["STAG ARMS", "NBC"])
    monkeypatch.setattr(atf, "ask", lambda prompt: pytest.fail("verified already; nothing to ask"))
    assert atf._pick_manufacturer(mfr_page, "", "Stag Arms") == {"mfr_code": "NBC", "mfr_atf_name": "STAG ARMS"}


def test_popup_opening_with_another_name_verified_clears_it(mfr_page, monkeypatch):
    mfr_page.evaluate(LOCK_AS_LIVE, ["AERO PRECISION INC", "AP1"])
    monkeypatch.setattr(atf, "ask", lambda prompt: pytest.fail("an exact match needs no question"))
    assert atf._pick_manufacturer(mfr_page, "", "Aero Precision LLC") == {"mfr_code": "AP2", "mfr_atf_name": "AERO PRECISION LLC"}


def test_a_name_eforms_verified_and_locked_is_kept(mfr_page, monkeypatch):
    # 2026-11-24 (STAG ARMS -> NBC): the typed name was verified, eForms greyed out the box, and the next search
    # waited 30 s to type into it. Verified as the marked name: kept, nothing typed.
    mfr_page.evaluate("""() => { const box = document.querySelector('#manufacturerName input');
        box.value = 'AERO PRECISION LLC'; box.disabled = true; document.getElementById('manufacturerCodeFromName').value = 'AP2'; }""")
    monkeypatch.setattr(atf, "ask", lambda prompt: pytest.fail("verified already; nothing to ask"))
    assert atf._pick_manufacturer(mfr_page, "", "Aero Precision LLC") == {"mfr_code": "AP2", "mfr_atf_name": "AERO PRECISION LLC"}


def test_a_locked_wrong_name_is_cleared_and_searched_again(mfr_page, monkeypatch):
    mfr_page.evaluate("""() => { const box = document.querySelector('#manufacturerName input');
        box.value = 'AERO PRECISION INC'; box.disabled = true; document.getElementById('manufacturerCodeFromName').value = 'AP1'; }""")
    monkeypatch.setattr(atf, "ask", lambda prompt: pytest.fail("an exact match needs no question"))
    assert atf._pick_manufacturer(mfr_page, "", "Aero Precision LLC") == {"mfr_code": "AP2", "mfr_atf_name": "AERO PRECISION LLC"}


def test_usa_is_eforms_united_states():
    assert atf._mfr_country("usa") == atf._mfr_country("") == atf._mfr_country("U.S.") == "UNITED STATES"
    assert atf._mfr_country("Germany") == "GERMANY"


def test_model_spelled_exactly_as_engraved_wins(model_page, monkeypatch):
    model_page.evaluate("MODELS.push('STAG-15', 'STAG 15')")
    monkeypatch.setattr(atf, "ask", lambda prompt: pytest.fail("one is spelled as engraved; nothing to ask"))
    assert atf._pick_model(model_page, "STAG-15", "5.56", nfa=True) == "STAG-15"


def in_process_rec(serial, eform, kind="SHORT BARRELED RIFLE"):
    return {"type": kind, "serial": serial, "caliber": "5.56", "overall_length": "30", "eform_id": eform,
            "filed_date": "11/24/2026", "submitted": ""}


def test_a_pending_sbr_in_process_on_eforms_is_reported(configured):
    # PR #5: an SBR submitted by hand stayed pending after Check approvals
    add_sbr()
    rows = atf.load_rows()
    rows[0]["status"] = "pending"
    atf.save_rows(rows)
    rep = atf.in_process_report([in_process_rec("XYZ123", "4000001"), in_process_rec("OTHER9", "4000002")], atf.load_rows())
    assert [row["serial_number"] for row, _ in rep["pending"]] == ["XYZ123"]
    assert [r["serial"] for r in rep["other"]] == ["OTHER9"] and rep["unknown"] == []


def test_a_queued_sbr_already_submitted_is_recorded_as_filed(configured):
    add_sbr()
    rep = atf.in_process_report([in_process_rec("XYZ123", "4000001")], atf.load_rows())
    assert [row["serial_number"] for row, _ in rep["queued"]] == ["XYZ123"]
    assert atf.record_in_process([rec for _, rec in rep["queued"]]) == ["XYZ123"]
    row = atf.load_rows()[0]
    assert (row["status"], row["eform_id"], row["filed_date"], row["type"]) == ("filed", "4000001", "11/24/2026", "SHORT BARRELED RIFLE")


def test_an_sbr_form_never_matches_a_suppressor_with_the_same_serial(configured):
    queue(("XYZ123", "9"))
    rep = atf.in_process_report([in_process_rec("XYZ123", "4000001")], atf.load_rows())
    assert rep["queued"] == [] and [r["serial"] for r in rep["other"]] == ["XYZ123"]
    assert atf.record_in_process([in_process_rec("XYZ123", "4000001")]) == []
    assert atf.load_rows()[0]["status"] == ""
