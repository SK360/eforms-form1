"""Filing against the FAKE portal in a headless browser: submit gating, pending status, logout, starting new forms."""
import builtins
from types import SimpleNamespace

import pytest
import yaml

from conftest import TODAY, add_other_rp, atf, make_individual, queue

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


# ---------- other responsible persons: the real mapping's "Add Responsible Person" steps on a fake popup ----------
RP_PAGE = """<table><tr><td>Settlor</td><td>DOE</td><td><input type="image" id="0Edit"></td></tr>LEFTOVER</table>
<button id="addResponsiblePerson" onclick="document.getElementById('pop').style.display='block'">Add Responsible Person</button>
<div class="modal" id="pop" style="display:none">Add Responsible Person
  <select id="citizenType"><option>Select Citizenship ...</option><option>U.S.CITIZEN</option><option>NON U.S.CITIZEN</option></select>
  <select id="cadence"><option>Select Cadence ...</option><option>JR</option><option>SR</option></select>
  <input id="title"><input id="firstName"><input id="middleName"><input id="lastName"><input id="emailAddress">
  <select id="middleNameDetail"><option>Select Detail ...</option><option>NO MIDDLE NAME</option><option>INITIAL ONLY</option><option>FULL MIDDLE NAME</option></select>
  <input type="file" id="formFile"><input type="file" id="photoFile">
  <input type="file" id="eftFile" onchange="setTimeout(()=>document.getElementById('ebts').style.display='block',300)">
  <input id="socialNumber"><input id="street">
  <input id="zipCode" oninput="fillZip()"><select id="state"><option>Select a State ...</option></select>
  <select name="city"><option>Select a City ...</option></select>
  <input id="dateOfBirth"><select id="birthCountry"><option>Select a country ...</option><option>UNITED STATES OF AMERICA</option></select>
  <select id="birthState"><option>Select a state ...</option><option>OHIO</option></select>
  <select id="stateOfResidence"><option>Select a state ...</option><option>OHIO</option></select>
  <select id="sex"><option>Select a sex ...</option><option>FEMALE</option><option>MALE</option></select>
  <div id="raceEthnicity"><div class="ui-multiselect" onclick="document.getElementById('races').style.display='block'">Race</div></div>
  <button id="finishButton" onclick="saveRp()">Save &amp; Close</button><button id="cancelButton">Cancel</button>
</div>
<div class="modal" id="ebts" style="display:none">Validation Status: Valid EBTS Package <button onclick="this.parentNode.style.display='none'">Ok</button></div>
<ul class="ui-multiselect-panel" id="races" style="display:none">
  <li class="ui-multiselect-item" onclick="window.race=this.innerText">Asian</li><li class="ui-multiselect-item" onclick="window.race=this.innerText">White</li></ul>
<script>
  window.saved = [];
  const v = id => { const e = document.getElementById(id); return e.tagName === 'SELECT' ? e.options[e.selectedIndex].text : e.value; };
  function fillZip() { document.getElementById('state').add(new Option('OHIO'));
    document.querySelector("select[name='city']").add(new Option('SPRINGFIELD')); }
  function saveRp() {
    const rp = {}; for (const id of ['citizenType', 'title', 'firstName', 'middleNameDetail', 'middleName', 'lastName', 'cadence',
        'emailAddress', 'street', 'zipCode', 'state', 'dateOfBirth', 'birthState', 'stateOfResidence', 'sex']) rp[id] = v(id);
    rp.city = document.querySelector("select[name='city']").value; rp.race = window.race;
    rp.files = ['formFile', 'photoFile', 'eftFile'].map(id => document.getElementById(id).files.length);
    window.saved.push(rp);
    const n = document.querySelectorAll("input[id$='Edit']").length, tr = document.createElement('tr');
    tr.innerHTML = `<td>${rp.title}</td><td>${rp.lastName.toUpperCase()}</td><td><input type="image" id="${n}Edit"></td>`;
    document.querySelector('table').appendChild(tr);
    document.querySelectorAll('#pop input').forEach(e => e.value = '');   // the popup opens empty for the next one
    document.getElementById('pop').style.display = 'none'; }
</script>"""


def _other_rp_steps():
    steps = yaml.safe_load(atf.MAPPING.read_text(encoding="utf-8"))["portal"]["steps"]
    start = next(i for i, s in enumerate(steps) if s.get("over") == "other_rps")
    return steps[start:start + 2]                                     # the repeat block + the count check after it


def _run_on_fake_popup(leftover=""):
    from playwright.sync_api import sync_playwright
    reads = []
    atf.emit = lambda kind, text, **d: reads.append(d) if kind == "expect" else None
    row = atf.load_rows()[0]
    atf.stamp_all(row, TODAY)
    ctx_vals, bad = atf._row_context(atf.load_trust(), row), []
    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page()
        page.set_default_timeout(5000)
        page.set_content(RP_PAGE.replace("LEFTOVER", leftover))
        atf._run_steps(page, _other_rp_steps(), ctx_vals, False, bad, skip_tags={"ssn"})
        return page.evaluate("window.saved"), bad, reads


def test_adds_another_responsible_person(configured):
    queue(("S1", "9"))
    add_other_rp(middle_name="Q", suffix="JR")
    saved, bad, reads = _run_on_fake_popup()
    assert bad == [] and saved == [{
        "citizenType": "U.S.CITIZEN", "title": "Co-Trustee", "firstName": "Jane", "middleNameDetail": "INITIAL ONLY",
        "middleName": "Q", "lastName": "Roe", "cadence": "JR", "emailAddress": "jane@example.com", "street": "300 ELM ST",
        "zipCode": "45503", "state": "OHIO", "city": "SPRINGFIELD", "dateOfBirth": "03/04/1985", "birthState": "OHIO",
        "stateOfResidence": "OHIO", "sex": "FEMALE", "race": "White", "files": [1, 1, 1]}]
    assert [(r["label"], r["ok"], r["private"]) for r in reads] == [
        ("Responsible person 2 (list)", True, True), ("Responsible persons listed", True, False)]


def test_two_others_and_no_middle_name(configured):
    queue(("S1", "9"))
    add_other_rp()
    add_other_rp(first_name="Sam", last_name="Poe", template="5320.23_sam-poe.pdf")
    saved, bad, _ = _run_on_fake_popup()
    assert bad == [] and [(s["firstName"], s["middleNameDetail"], s["cadence"]) for s in saved] == [
        ("Jane", "NO MIDDLE NAME", "Select Cadence ..."), ("Sam", "NO MIDDLE NAME", "Select Cadence ...")]


def test_a_leftover_responsible_person_blocks_submit(configured):
    """The list must hold exactly you + the others in Settings: one left from an earlier attempt is a mismatch."""
    queue(("S1", "9"))
    add_other_rp()
    _, bad, _ = _run_on_fake_popup(leftover='<tr><td>Trustee</td><td>OLD</td><td><input type="image" id="1Edit"></td></tr>')
    assert [b[0] for b in bad] == ["tr:has([id='1Edit'])", "input[id$='Edit']"] and bad[1][1:] == ("2", "3")


# ---------- supporting documents: the portal saves each one in the background, then clears the form ----------
DOCS_PAGE = """<input type="file" id="file-upload" onchange="document.getElementById('addDocument').disabled = !this.files.length">
<input id="description"><button id="addDocument" disabled onclick="addDoc()">Add Document</button>
<table id="docs"></table>
<script>
  function addDoc() {   // like the live portal on 2026-10-22: listed ~2 s later, and THEN the form is cleared
    const f = document.getElementById('file-upload'), d = document.getElementById('description');
    setTimeout(() => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><a>${f.files[0] ? f.files[0].name : '?'}</a></td><td>${d.value}</td>`;
      document.getElementById('docs').appendChild(tr);
      f.value = ''; d.value = ''; document.getElementById('addDocument').disabled = true; }, 2000); }
</script>"""


def test_each_supporting_document_is_listed_before_the_next(configured):
    from playwright.sync_api import sync_playwright
    t = yaml.safe_load((atf.DATA / "trust.yaml").read_text(encoding="utf-8"))
    (configured / "files" / "it's filament.jpg").write_bytes(b"x")                 # a quote in the name, too
    t["docs"].append({"path": str(configured / "files" / "it's filament.jpg"), "description": "FILAMENT"})
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t), encoding="utf-8")
    queue(("S1", "9"))
    atf.stamp_all(atf.load_rows()[0], TODAY)
    steps = yaml.safe_load(atf.MAPPING.read_text(encoding="utf-8"))["portal"]["steps"]
    block = [s for s in steps if s.get("over") == "docs"]
    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page()
        page.set_default_timeout(5000)
        page.set_content(DOCS_PAGE)
        atf._run_steps(page, block, atf._row_context(atf.load_trust(), atf.load_rows()[0]), False, [])
        rows = page.evaluate("[...document.querySelectorAll('#docs tr')].map(r => r.innerText.replace(/\\s+/g, ' ').trim())")
    assert rows == ["printer.jpg 3D PRINTER", "it's filament.jpg FILAMENT"]
