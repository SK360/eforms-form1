"""Shared test setup. Everything runs against FAKE data: a sandbox folder per test, the fake 5320.23 template in
tests/fixtures, and a fake eForms portal page in a headless browser. Nothing here touches eforms.atf.gov.

Run:  .venv/bin/python -m pytest -q        (Windows: .venv\\Scripts\\python -m pytest -q)
"""
import builtins
import io
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest
import questionary
import yaml
from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import atf  # noqa: E402
import ui  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "fake_5320.pdf"
TODAY = date.today().strftime("%m/%d/%Y")
# What read_template() must get from the fake template (it also carries a fake SSN that must never come back)
FIXTURE_ANSWERS = {
    "trust.name": "DOE FAMILY TRUST", "responsible_persons.0.home_address.state": "Ohio",
    "responsible_persons.0.dob": "01/02/1980", "firearm.model": "N/A", "cleo.department": "SPRINGFIELD POLICE DEPARTMENT",
    "cleo.official": "PAT EXAMPLE", "cleo.title": "CHIEF OF POLICE", "cleo.address.street": "200 MAIN ST",
    "cleo.address.city": "SPRINGFIELD", "cleo.address.state": "Ohio", "cleo.address.zip": "45502",
    "responsible_persons.0.place_of_birth.state": "Ohio"}
FIXTURE_SSN = "123-45-6789"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Empty data/out/forms folders; atf points at them. Restores atf.emit/ask (the menu swaps them)."""
    for d in ("data", "out", "forms", "files"):
        (tmp_path / d).mkdir()
    for name, sub in (("DATA", "data"), ("OUT", "out"), ("FORMS", "forms")):
        monkeypatch.setattr(atf, name, tmp_path / sub)
    monkeypatch.setattr(atf, "SUPP", tmp_path / "data" / "suppressors.csv")
    real_data = ROOT / "data"
    before = {f.name: f.stat().st_mtime for f in real_data.glob("*")} if real_data.exists() else {}
    monkeypatch.setattr(atf, "emit", atf.emit)
    monkeypatch.setattr(atf, "ask", atf.ask)
    monkeypatch.setattr(builtins, "input", lambda *a: "")
    yield tmp_path
    # guard: a test must never touch the real data/ folder (personal records)
    after = {f.name: f.stat().st_mtime for f in real_data.glob("*")} if real_data.exists() else {}
    assert after == before, f"a test changed the real data/ folder: {sorted(set(after.items()) ^ set(before.items()))}"


def fake_settings(root):
    f = root / "files"
    for name in ("prints.eft", "me.jpg", "trust.pdf", "printer.jpg"):
        (f / name).write_bytes(b"x")
    return {
        "trust": {"name": "DOE FAMILY TRUST"},
        "responsible_persons": [{"title": "Trustee", "dob": "01/02/1980", "sex": "Male", "race": "Asian",
                                 "place_of_birth": {"country": "United States of America", "state": "Ohio"},
                                 "home_address": {"state": "Ohio"}}],
        "cleo": {"department": "SPRINGFIELD POLICE DEPARTMENT", "official": "PAT EXAMPLE", "title": "CHIEF OF POLICE",
                 "address": {"street": "200 MAIN ST", "zip": "45502", "city": "Springfield", "county": "Clark", "state": "Ohio"}},
        "firearm": {"model": "N/A", "overall_length": "12", "description": ui.DEFAULT_DESCRIPTION},
        "files": {"trust_document": str(f / "trust.pdf"), "trust_document_desc": "TRUST", "eft": str(f / "prints.eft"),
                  "photo": str(f / "me.jpg")},
        "docs": [{"path": str(f / "printer.jpg"), "description": "3D PRINTER"}]}


@pytest.fixture
def configured(sandbox):
    """Sandbox with complete fake settings, the fake template and an empty queue."""
    (sandbox / "data" / "trust.yaml").write_text(yaml.safe_dump(fake_settings(sandbox), sort_keys=False), encoding="utf-8")
    shutil.copy(FIXTURE, sandbox / "forms" / "5320.23.pdf")
    atf.save_rows([])
    return sandbox


def queue(*rows):
    """queue(("S1", "9"), ("S2", "22", "filed", "123456789"))"""
    atf.save_rows([{"serial_number": r[0], "caliber": r[1], "status": r[2] if len(r) > 2 else "",
                    "application_number": r[3] if len(r) > 3 else ""} for r in rows])


@pytest.fixture
def screen(monkeypatch):
    """Captures what the menu prints."""
    buf = io.StringIO()
    monkeypatch.setattr(ui, "con", Console(file=buf, width=120, color_system=None))
    return buf


class Script:
    """Scripted answers for questionary prompts. An answer may be a value or f(kwargs) -> value."""

    def __init__(self):
        self.answers, self.asked, self.seen = [], [], {}

    def __call__(self, *answers):
        self.answers[:] = list(answers)

    def prompt(self, kind, msg, kw):
        script = self

        class Q:
            def ask(self):
                script.asked.append((kind, msg))
                script.seen[msg] = kw.get("default")
                assert script.answers, f"no scripted answer left for {kind} {msg!r}"
                a = script.answers.pop(0)
                a = a(kw) if callable(a) else a
                v = kw.get("validate")
                if v and kind in ("text", "path", "autocomplete") and a is not None:
                    r = v(a)
                    assert r is True, f"{msg!r} rejected {a!r}: {r}"
                return a
        return Q()


@pytest.fixture
def answers(monkeypatch):
    s = Script()
    for kind in ("select", "checkbox", "confirm", "text", "path", "autocomplete"):
        monkeypatch.setattr(questionary, kind, lambda msg, _k=kind, **kw: s.prompt(_k, msg, kw))
    return s


D = lambda kw: kw.get("default")  # noqa: E731  accept the offered default
TICKED = lambda kw: [c.value for c in kw["choices"] if c.checked]  # noqa: E731


def pick(title):
    def f(kw):
        for c in kw["choices"]:
            if str(c.title).startswith(title):
                return c.value
        raise AssertionError(f"no menu item {title!r} in {[c.title for c in kw['choices']]}")
    return f


# ---------- fake eForms portal ----------
FORM = """<div class="modal" id="aff"><button id="closeModalBtn" onclick="this.parentNode.remove()">I affirm</button></div>
<input type="checkbox" id="Trust">
<div class="modal" id="ebts">Fingerprint status: EBTS <button onclick="this.parentNode.remove()">Ok</button></div>
<input id="serialNumberMessage" value="SERIAL"><input id="caliber" value="CAL"><input id="businessName" value="DOE FAMILY TRUST">
<input type="checkbox" id="binary"><button id="nextBtn" onclick="document.getElementById('cert').style.display='block'">Next</button>
<div id="cert" style="display:none">The application has validated successfully <input type="checkbox" id="signed">
<button id="signAndSubmit" onclick="CONFIRM">Sign</button></div>
<div class="modal" id="done" style="display:none">Your form has been successfully submitted. Your application number is: APPNO
<button id="OkBtn" onclick="home()">Ok</button></div>"""

PORTAL_STEPS = [  # a miniature of config/mapping.yaml's steps, for the fake portal
    {"action": "section", "value": "Applicant type"}, {"selector": "#Trust", "action": "check"},
    {"action": "section", "value": "Responsible person"},
    {"action": "dismiss_dialog", "value": "300", "require": "Valid EBTS Package"},
    {"action": "section", "value": "Read-back check"},
    {"selector": "#serialNumberMessage", "action": "expect", "label": "Serial number", "value": "{serial_number}"},
    {"selector": "#caliber", "action": "expect", "label": "Caliber", "value": "{caliber}"},
    {"selector": "#businessName", "action": "expect", "label": "Trust name", "private": True, "value": "{trust_name}"}]


@pytest.fixture
def portal(monkeypatch):
    """portal(serials, ...) makes atf._browser open the fake portal headless. Returns a state dict:
    state["logged_out"] is True once the script clicked Log Out."""
    from playwright.sync_api import sync_playwright
    from playwright.sync_api._generated import Locator
    state = {}
    wait_for = Locator.wait_for
    monkeypatch.setattr(Locator, "wait_for", lambda self, timeout=None, state=None: wait_for(self, timeout=min(timeout or 5000, 5000), state=state))
    real_mapping = yaml.safe_load(atf.MAPPING.read_text(encoding="utf-8"))["portal"]
    monkeypatch.setattr(atf, "load_mapping", lambda: {"portal": {"steps": PORTAL_STEPS, "new_form_steps": real_mapping["new_form_steps"]}})
    logout = atf._logout

    def tracked_logout(page):
        logout(page)
        state["logged_out"] = page.title() == "LOGGEDOUT"
    monkeypatch.setattr(atf, "_logout", tracked_logout)

    def configure(serials, ebts="Valid EBTS Package", shown_caliber=None, confirm=True, link=True, start_home=True):
        confirm_js = "setTimeout(()=>document.getElementById('done').style.display='block',200)" if confirm else ""
        form = FORM.replace("EBTS", ebts).replace("CONFIRM", confirm_js)

        def browser():
            pw = sync_playwright().start()
            br = pw.chromium.launch(headless=True, handle_sigint=False)
            ctx = br.new_context()
            ctx.set_default_timeout(3000)  # the fake portal is instant; a missing element should fail fast
            page = ctx.new_page()
            page.set_content("<a href='#' onclick=\"document.title='LOGGEDOUT'\">Log Out</a><div id=app></div>")
            page.evaluate("""([form, serials, cal, link, startHome]) => {
              window.forms = 0;
              window.home = () => { document.getElementById('app').innerHTML = link ?
                  '<a id="FORM53201LinkId" href="#" onclick="newForm();return false">ATF Form 1 (5320.1)</a>' : '<p>home</p>'; };
              window.newForm = () => { const s = serials[window.forms++];
                  document.getElementById('app').innerHTML = form.replace('SERIAL', s[0]).replace('CAL', cal || s[1])
                      .replace('APPNO', '90000000' + window.forms); };
              startHome ? home() : newForm(); }""", [form, [list(s) for s in serials], shown_caliber, link, start_home])
            state["page"] = page
            return pw, ctx, page
        monkeypatch.setattr(atf, "_browser", browser)
        return state
    return configure
