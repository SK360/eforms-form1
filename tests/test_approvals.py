"""Check approvals (read-only): fake approved Form 1 PDFs (same layout as eForms' "View PDF", made-up values) and a
fake eForms dashboard with View Form / Submit Void buttons. The test fails if anything but the read-only controls is clicked."""
import csv
import json

import pymupdf
import pytest

from conftest import TICKED, atf, pick, queue, ui  # noqa: F401

QUIT = lambda kw: "quit"  # noqa: E731


def approved_pdf(type_="SILENCER", serial="T201", caliber="22", length="12", barrel="", model="N/A",
                 approved=True, disapproved=False, date="08/15/2026", conditions="", date_x=505, date_top=718):
    """A one-page stand-in for an approved Form 1 PDF: labels and values at the positions eForms uses."""
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    if conditions:  # drawn FIRST, as in eForms' PDFs: a plain search for "Approved" finds this banner before the checkbox
        p.insert_text((30, 698), "APPROVED WITH CONDITIONS - SEE CONDITIONS ON THE LAST PAGE OF PDF", fontname="hebo", fontsize=11)
    label = lambda x, top, t: p.insert_text((x, top + 6.5), t, fontname="tiro", fontsize=8)  # noqa: E731
    value = lambda x, top, t, font="helv": p.insert_text((x, top + 6), t, fontname=font, fontsize=7)  # noqa: E731
    label(246, 377, "a. Type of firearm to be made"), label(246, 387, "(See definition 1.c)")
    label(364, 377, "c. Caliber or gauge"), label(364, 387, "(specify one)")
    label(450, 378, "d. Model (as marked on firearm)"), label(450, 405, "e. Barrel length"), label(516, 405, "f.  Overall length")
    label(248, 435, "g. Serial number"), label(40, 463, "h. Additional description")
    label(494, 604, "Date"), value(513, 615, "01/01/2026")                       # applicant's signature date (not ATF's)
    label(43, 657, "Approved (with the following conditions, if any)"), label(262, 657, "Disapproved (for the following reasons)")
    label(486, 708, "Date")
    value(281, 408, type_), value(398, 407, caliber), value(452, 391, model), value(520, 419, length)
    if barrel:
        value(453, 419, barrel)
    value(252, 448, serial, font="cour")
    if approved:
        p.insert_text((30, 668), "4", fontname="zadb", fontsize=10)
    if disapproved:
        p.insert_text((249, 668), "4", fontname="zadb", fontsize=10)
    value(date_x, date_top, date)
    if conditions:  # older approvals: a conditions page at the end (the banner is drawn first, see above)
        last = doc.new_page(width=612, height=792)
        last.insert_text((40, 80), conditions, fontname="helv", fontsize=9)
        last.insert_text((40, 120), "Approval Conditions", fontname="hebo", fontsize=12)
        last.insert_text((40, 140), "U.S. Department of Justice", fontname="helv", fontsize=9)
        last.insert_text((400, 160), "Control Number: 2026000777", fontname="helv", fontsize=9)   # 10 digits: older format
    return doc.tobytes()


def test_read_approved_pdf(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(approved_pdf())
    assert atf.read_approved_pdf(f) == {"type": "SILENCER", "serial": "T201", "caliber": "22", "model": "N/A", "barrel_length": "",
                                        "overall_length": "12", "approved": True, "approval_date": "08/15/2026", "conditions": "",
                                        "pdf_control_number": ""}
    f.write_bytes(approved_pdf("SHORT BARRELED RIFLE", "R1", "5.56", "26", barrel="10.5", model="X15"))
    r = atf.read_approved_pdf(f)
    assert (r["type"], r["barrel_length"], r["overall_length"], r["caliber"]) == ("SHORT BARRELED RIFLE", "10.5", "26", "5.56")
    f.write_bytes(approved_pdf(approved=False, disapproved=True))
    assert atf.read_approved_pdf(f)["approved"] is False


def test_newer_approval_date(tmp_path):
    # approvals from Oct 2026 on: the date is spelled out and sits ~4.5 pt below the "Date" label (older: ~10 pt)
    f = tmp_path / "n.pdf"
    f.write_bytes(approved_pdf(date="October 15, 2026", date_x=504, date_top=712.8))
    assert atf.read_approved_pdf(f)["approval_date"] == "10/15/2026"


def test_approved_with_conditions(tmp_path):
    f = tmp_path / "c.pdf"
    f.write_bytes(approved_pdf(serial="X1", date="02/14/2026", conditions="NFA FIREARM MUST COMPLY WITH MARKING REQUIREMENTS"))
    r = atf.read_approved_pdf(f)
    assert (r["approved"], r["approval_date"], r["conditions"]) == (True, "02/14/2026", "NFA FIREARM MUST COMPLY WITH MARKING REQUIREMENTS")
    assert r["pdf_control_number"] == "2026000777"
    rec, _ = atf.read_approved([{"eform_id": "1", "control_number": "", "finalized": "", "pdf": str(f)}])
    assert rec[0]["control_number"] == "2026000777" and "pdf_control_number" not in rec[0]
    rep = atf.approval_report([{**r, "eform_id": "1"}], [], {"firearm": {"overall_length": "12"}})
    assert rep["unknown"][0][1] == ["with conditions: NFA FIREARM MUST COMPLY WITH MARKING REQUIREMENTS"]  # no length noise


def test_parse_result_row():
    # the shape of a real results row (made-up numbers): eForm ID, control #, ICN (empty), name, 3 dates, actions
    row = atf._parse_result_row("1234567\t20261234567\t\tJOHN DOE\t8/3/2026\t8/3/2026\t8/15/2026\t View Form Submit Void")
    assert row == {"eform_id": "1234567", "control_number": "20261234567", "finalized": "8/15/2026"}
    assert atf._parse_result_row("ID 12345678 Permit / Control Number 20269999999 1/1/2026 1/2/2026 4/23/2026")["eform_id"] == "12345678"
    assert atf._parse_result_row("1234567\t2026123456\t\tJOHN DOE\t2/1/2026\t2/1/2026\t2/14/2026")["control_number"] == "2026123456"
    assert atf._parse_result_row("No data to display") is None


def test_safe_click_refuses_anything_unexpected():
    class Btn:
        def __init__(self, text):
            self.text, self.clicked = text, False

        def inner_text(self, timeout=None):
            return self.text

        def click(self):
            self.clicked = True
    for text, expected in (("Submit Void", "View Form"), ("Submit Void", "Submit Void"), ("Save", "Save"), ("View Form", "View PDF")):
        b = Btn(text)
        with pytest.raises(RuntimeError, match="refused"):
            atf._safe_click(b, expected)
        assert not b.clicked
    b = Btn(" View  Form ")
    atf._safe_click(b, "View Form")
    assert b.clicked


DASHBOARD = """<a href='#' onclick="document.title='LOGGEDOUT'">Log Out</a><div id=app></div><script>
const FORMS = %s, PER = %d, LINKS = %d, MODE = %s;  // LINKS page numbers shown at a time, in blocks or sliding
const app = () => document.getElementById('app');
function home() {
  app().innerHTML = `<select id="form-type-search"><option>Select...</option><option>ATF Form 1 (5320.1)</option><option>ATF Form 4 (5320.4)</option></select>
   <select id="form-status-search"><option>Select...</option><option>APPROVED</option><option>DISAPPROVED</option></select>
   <button id="queryBtn" onclick="search()">Search</button>
   <div id=results>No data to display 1 10 | Total Rows: 0</div>`;   // like eForms: an empty table before searching
}
function search() {
  const ok = document.getElementById('form-type-search').value.startsWith('ATF Form 1') && document.getElementById('form-status-search').value === 'APPROVED';
  window.hits = ok ? FORMS : []; setTimeout(() => showPage(1), 1500);   // results arrive after a delay, like the real portal
}
function showPage(n) {
  // markup like a UI-library table: empty first cell, column titles inside cells, hidden duplicate buttons
  const T = t => `<span class="col-title">${t} </span>`;
  const rows = window.hits.slice((n - 1) * PER, n * PER).map(f => `<tr><td></td><td>${T('ID')}<span class="ng-star-inserted"><a id="eformId-link"> ${f.id} </a></span></td>
     <td>${T('Permit / Control Number')}2026${f.id}0</td><td></td><td>${T('Created By')}JOHN DOE</td>
     <td>1/1/2026</td><td>1/2/2026</td><td>${f.fin}</td><td><button style="display:none" onclick="fetch('/clicked/hidden')">View Form</button>
     <button onclick="viewForm('${f.id}')">View Form</button> <button onclick="fetch('/clicked/void')">Submit Void</button></td></tr>`).join('');
  const pages = Math.ceil(window.hits.length / PER);
  let lo = MODE.startsWith('block') ? Math.floor((n - 1) / LINKS) * LINKS + 1 : Math.max(1, Math.min(n - Math.floor(LINKS / 2), pages - LINKS + 1));
  const nums = Array.from({length: Math.min(LINKS, pages - lo + 1)}, (_, i) => lo + i);
  const icon = (cls, to) => `<a href="#" class="ui-paginator-${cls}${to < 1 || to > pages || to === n ? ' ui-state-disabled' : ''}"
     onclick="${to >= 1 && to <= pages ? `showPage(${to});` : ''}return false"><span class="pi pi-caret" style="display:inline-block;width:12px;height:12px"></span></a>`;
  const links = icon('first', 1) + icon('prev', n - 1) + '<span class="ui-paginator-pages">' + nums.map(k => `<a href="#" class="ui-paginator-page${k === n && !MODE.includes('nomark') ? ' ui-state-active' : ''}"
     onclick="showPage(${k});return false">${k}</a>`).join(' ') + '</span>' + icon('next', n + 1) + icon('last', pages);
  document.getElementById('results').innerHTML = `<table><tr><th>ID</th><th>Permit / Control Number</th></tr>${rows}</table>
     <div class="ui-paginator">${links} <input type="text" value="10"></div><span>10 | Total Rows: ${window.hits.length}</span>`;
}
function viewForm(id) {
  app().innerHTML = `<button id="saveBtn" onclick="fetch('/clicked/save')">Save</button><button id="cancelBtn">Cancel</button>
    <button id="viewFormBtn" onclick="viewPdf('${id}')">View PDF</button><button id="goHomeBtn" onclick="home()">Go Home</button>
    <button id="nextBtn" onclick="fetch('/clicked/next')">Next</button>`;
}
function viewPdf(id) {
  fetch('/pdf/' + id).then(r => r.blob()).then(b => window.open(URL.createObjectURL(new Blob([b], {type: 'application/pdf'}))));
}
home();
</script>"""


@pytest.fixture
def dashboard(monkeypatch):
    """dashboard(forms) -> state; forms = [(eform_id, finalized, pdf_bytes)]. state['pdf_hits'], state['bad_clicks']."""
    from playwright.sync_api import sync_playwright
    state = {"pdf_hits": 0, "bad_clicks": []}
    logout = atf._logout

    def tracked_logout(page):
        logout(page)
        state["logged_out"] = page.title() == "LOGGEDOUT"
    monkeypatch.setattr(atf, "_logout", tracked_logout)

    def configure(forms, start="/home", per=10, links=5, mode="slide"):
        pdfs = {f[0]: f[2] for f in forms}
        html = DASHBOARD % (json.dumps([{"id": f[0], "fin": f[1]} for f in forms]), per, links, json.dumps(mode))

        def route(r):
            path = r.request.url.split("fake.test", 1)[1]
            if path.startswith("/pdf/"):
                state["pdf_hits"] += 1
                return r.fulfill(body=pdfs[path[5:]], content_type="application/pdf")
            if path.startswith("/clicked/"):
                state["bad_clicks"].append(path)
                return r.fulfill(body="")
            if path.startswith("/adfs/"):  # the ATF login page: no Log Out link, no search form
                return r.fulfill(body="<h1>Sign in</h1><input id=user><input id=pass type=password>", content_type="text/html")
            return r.fulfill(body=html, content_type="text/html")

        def browser():
            pw = sync_playwright().start()
            ctx = pw.chromium.launch(headless=True).new_context(accept_downloads=True)
            ctx.set_default_timeout(5000)
            ctx.route("https://fake.test/**", route)
            page = ctx.new_page()
            page.goto("https://fake.test" + start)
            state["page"] = page
            return pw, ctx, page
        monkeypatch.setattr(atf, "_browser", browser)
        return state
    return configure


FORMS = [  # 12 approved forms over 2 result pages: suppressors (matching, differing, unknown) and SBRs
    ("1000001", "8/15/2026", approved_pdf(serial="T201")),
    ("1000002", "8/15/2026", approved_pdf(serial="T202", caliber="22")),           # records say 9
    ("1000003", "8/15/2026", approved_pdf(serial="T203", length="13")),            # settings say 12
    ("1000004", "9/13/2026", approved_pdf("SHORT BARRELED RIFLE", "R1", "5.56", "26", barrel="10.5")),
    ("1000005", "9/13/2026", approved_pdf("SHORT BARRELED RIFLE", "R2", "5.56", "26", barrel="10.5")),
] + [(f"10000{n:02d}", "4/23/2026", approved_pdf(serial=f"OLD{n}")) for n in range(6, 13)]  # approved, not in records


def test_check_approvals_end_to_end(configured, dashboard):
    queue(("T201", "22", "filed", "1"), ("T202", "9", "filed", "2"), ("T203", "22", "filed", "3"), ("T204", "22", "filed", "4"), ("T205", "22"))
    state = dashboard(FORMS)
    found, problems = atf.fetch_approved()
    records, more = atf.read_approved(found)
    rep = atf.approval_report(records, atf.load_rows(), atf.load_trust())
    assert problems == [] and more == [] and state["logged_out"] and state["bad_clicks"] == []   # read-only
    assert len(records) == 12 and state["pdf_hits"] == 12
    names = sorted(f.name for f in (atf.OUT / "approved").glob("*.pdf"))
    assert names == sorted(["T201.pdf", "T202.pdf", "T203.pdf", "R1.pdf", "R2.pdf"] + [f"OLD{n}.pdf" for n in range(6, 13)])  # by serial
    assert [r["serial"] for r, _ in rep["matched"]] == ["T201"]
    assert {r["serial"]: n for r, n in rep["differs"]} == {"T202": ["caliber 22 (records: 9)"], "T203": ["overall length 13 (settings: 12)"]}
    assert sorted(r["serial"] for r, _ in rep["unknown"]) == [f"OLD{n}" for n in range(10, 13)] + [f"OLD{n}" for n in range(6, 10)]
    assert dict(rep["skipped"]) == {"SHORT BARRELED RIFLE": 2} and rep["duplicates"] == []
    assert [r["serial_number"] for r in rep["waiting"]] == ["T204"]                                # T205 not filed yet

    # approvals are recorded in the inventory; SBRs never are; unknown suppressors only when importing
    updated, added = atf.apply_approvals(records)
    assert sorted(updated) == ["T201", "T202", "T203"] and added == []
    rows = {r["serial_number"]: r for r in atf.load_rows()}
    assert rows["T201"]["status"] == "approved" and rows["T201"]["approved_date"] == "08/15/2026" and rows["T201"]["eform_id"] == "1000001"
    assert rows["T204"]["status"] == "filed" and rows["T205"]["status"] == ""
    assert atf.apply_approvals(records) == ([], [])                                                # idempotent
    updated, added = atf.apply_approvals(records, import_unknown=True)
    assert updated == [] and sorted(added) == sorted(f"OLD{n}" for n in range(6, 13))
    order = [r["serial_number"] for r in atf.load_rows()]
    assert order == [f"OLD{n}" for n in range(6, 13)] + ["T201", "T202", "T203", "T204", "T205"]   # natural serial order
    assert not any(s.startswith("R") for s in order)                                               # SBRs not in suppressors.csv
    assert atf.record_sbrs(records) == 2 and atf.record_sbrs(records) == 0                         # SBR inventory, no duplicates
    sbrs = atf.load_sbrs()
    assert [(r["serial_number"], r["caliber"], r["barrel_length"], r["overall_length"], r["approved_date"]) for r in sbrs] == [
        ("R1", "5.56", "10.5", "26", "08/15/2026"), ("R2", "5.56", "10.5", "26", "08/15/2026")]

    # second run: everything is already saved, nothing is downloaded again
    state["pdf_hits"] = 0
    found, problems = atf.fetch_approved()
    assert len(found) == 12 and state["pdf_hits"] == 0 and problems == []


def test_results_page_that_wont_load(configured, dashboard, monkeypatch):
    """Live 2026-10-19: the page-2 link didn't show up within 15 s and the traceback lost the whole report. Now it starts
    over once; if the page still won't load it stops paging, says so, and reports every PDF saved so far."""
    state, fails = dashboard(FORMS), {"left": 0}
    real = atf._approved_results

    def flaky(page, page_no=1, search=True):
        if page_no == 2 and fails["left"]:
            fails["left"] -= 1
            raise RuntimeError("Locator.inner_text: Timeout 15000ms exceeded.\nCall log: ...")
        return real(page, page_no, search)
    monkeypatch.setattr(atf, "_approved_results", flaky)

    fails["left"] = 99                                            # page 2 never loads: page 1 is still saved and reported
    found, problems = atf.fetch_approved()
    assert len(found) == 10 and state["logged_out"] and len(problems) == 1
    assert problems[0].startswith("stopped at results page 2 of 2: Locator.inner_text: Timeout")

    fails["left"] = 1                                             # fails once: the retry gets the rest
    found, problems = atf.fetch_approved()
    assert len(found) == 12 and problems == [] and state["pdf_hits"] == 12

    fails["left"] = 99                                            # everything saved: the report still covers all 12
    found, problems = atf.fetch_approved()
    assert len(found) == 12 and len(problems) == 1 and len({r["eform_id"] for r in found}) == 12
    records, more = atf.read_approved(found)
    assert len(records) == 12 and more == []


@pytest.mark.parametrize("mode", ["block", "slide", "block-nomark"])
def test_paginator_shows_only_some_pages(configured, dashboard, mode):
    """Live 2026-10-19: 66 forms on 7 pages and the "6" link never showed up. Here: 12 forms, 2 per page, 3 page
    numbers shown at a time, in blocks (1-3, then the icon-only next arrow) or sliding; current page marked or not."""
    state = dashboard(FORMS, per=2, links=3, mode=mode)
    found, problems = atf.fetch_approved()
    assert problems == [] and len(found) == 12 and state["pdf_hits"] == 12 and state["bad_clicks"] == [] and state["logged_out"]
    assert sorted(r["eform_id"] for r in found) == sorted(f[0] for f in FORMS)


def test_pager_ignores_eform_id_links():
    """Live 2026-10-19: each results row's eForm ID is a link too (<span><a id="eformId-link"> 1234567 </a></span>);
    it was taken for the paginator and nothing was found. Markup shaped like the live page (made-up IDs)."""
    from playwright.sync_api import sync_playwright
    icon = lambda c: f'<a href="#" class="ui-paginator-{c}"><span style="display:inline-block;width:9px;height:9px"></span></a>'  # noqa: E731
    html = ("<table>" + "".join(f'<tr><td><span class="ng-star-inserted"><a id="eformId-link"> 1000000{i} </a></span></td>'
                                f"<td><button>View Form</button></td></tr>" for i in range(3)) + "</table>"
            + '<div class="ui-paginator">' + icon("first") + icon("prev") + '<span class="ui-paginator-pages">'
            + " ".join(f'<a href="#" class="ui-paginator-page{" ui-state-active" if k == 1 else ""}">{k}</a>' for k in range(1, 6))
            + "</span>" + icon("next") + icon("last") + '<input type="text" value="10"></div><span>10 | Total Rows: 66</span>')
    with sync_playwright() as pw:
        page = pw.chromium.launch(headless=True).new_page()
        page.set_content(html)
        pg = page.evaluate(atf._PAGER_JS)
        assert (pg["nums"], pg["current"], pg["next"]) == ([1, 2, 3, 4, 5], 1, True)
        assert "eformId" not in pg["html"] and page.locator("a[data-atf-next]").get_attribute("class") == "ui-paginator-next"


def test_waits_until_logged_in(configured, dashboard, monkeypatch):
    """Enter pressed while still on the ATF login page: warn and ask again (it used to time out with a traceback)."""
    state = dashboard(FORMS[:1], start="/adfs/ls?SAMLRequest=x")
    said, prompts = [], []
    monkeypatch.setattr(atf, "emit", lambda kind, text, **d: said.append((kind, text)))

    def user(prompt):
        prompts.append(prompt)
        if len(prompts) == 2:  # the user finishes logging in after the warning
            state["page"].goto("https://fake.test/home")
        return ""
    monkeypatch.setattr(atf, "ask", user)
    found, problems = atf.fetch_approved()
    assert len(prompts) == 2 and len(found) == 1 and problems == [] and state["logged_out"]
    assert any(k == "warn" and "still on the ATF login page" in t for k, t in said)


def test_q_at_login_stops_cleanly(configured, dashboard, monkeypatch):
    dashboard(FORMS[:1], start="/adfs/ls?SAMLRequest=x")
    said, answers_ = [], iter(["", "q"])
    monkeypatch.setattr(atf, "emit", lambda kind, text, **d: said.append((kind, text)))
    monkeypatch.setattr(atf, "ask", lambda prompt: next(answers_))
    with pytest.raises(SystemExit, match="Stopped before anything was done"):
        atf.fetch_approved()
    assert ("info", "Not logged in to eForms, so there was nothing to log out of.") in said
    assert not any("log out manually" in t for _, t in said)


def test_menu_survives_an_unexpected_error(configured, answers, screen, monkeypatch):
    def boom():
        raise RuntimeError("Locator.select_option: Timeout 30000ms exceeded.\nCall log: ...")
    monkeypatch.setattr(atf, "fetch_approved", boom)
    answers(pick("Check approvals"), True, QUIT)
    ui.menu()
    out = screen.getvalue()
    assert "Something went wrong: Locator.select_option: Timeout 30000ms exceeded." in out and "Traceback" not in out
    assert "RuntimeError" in (atf.OUT / "last_error.txt").read_text(encoding="utf-8")


def test_pending_row_is_resolved_by_an_approval(configured, tmp_path):
    queue(("T201", "22", "pending"))
    f = tmp_path / "a.pdf"
    f.write_bytes(approved_pdf(serial="T201"))
    records, _ = atf.read_approved([{"eform_id": "1000001", "control_number": "", "finalized": "", "pdf": str(f)}])
    atf.apply_approvals(records)
    assert atf.load_rows()[0]["status"] == "approved"


def test_duplicate_serial_is_flagged(configured):
    recs = [{"type": "SILENCER", "serial": "S1", "caliber": "22", "overall_length": "12", "approved": True},
            {"type": "SILENCER", "serial": "s1", "caliber": "22", "overall_length": "12", "approved": True}]
    assert atf.approval_report(recs, [], atf.load_trust())["duplicates"] == ["S1"]


def test_menu_shows_the_report(configured, dashboard, answers, screen):
    queue(("T201", "22", "filed", "1"), ("T204", "22", "filed", "4"))
    dashboard(FORMS[:5])
    answers(pick("Check approvals"), True, True, QUIT)       # yes: open the browser; yes: add the unknown ones
    ui.menu()
    out = screen.getvalue()
    assert "Inventory updated: 1 approval(s) recorded, 2 suppressor(s) added" in out
    for shown in ("5 approved Form 1s", "3 suppressor(s), 2 SBR(s)", "SBR inventory: 2 added", "Approved, matches your records (1)",
                  "Approved, NOT in your records (2)", "Filed, not approved yet: 1", "T204"):
        assert shown in out, shown
    assert "DOE" not in out.upper()
    assert answers.kw["Open the browser?"]["auto_enter"] is False   # 'y' alone doesn't start it: Enter does


def test_lengths_are_filled_and_checked(configured, tmp_path):
    queue(("T1", "22", "filed"), ("T2", "22", "filed"))
    rows = atf.load_rows()
    rows[1]["overall_length"] = "6.5"
    atf.save_rows(rows)
    found = []
    for serial, length in (("T1", "12"), ("T2", "7"), ("T3", "4.7")):
        f = tmp_path / f"{serial}.pdf"
        f.write_bytes(approved_pdf(serial=serial, length=length))
        found.append({"eform_id": f"100000{serial[-1]}", "control_number": "", "finalized": "", "pdf": str(f)})
    records, _ = atf.read_approved(found)
    rep = atf.approval_report(records, atf.load_rows(), atf.load_trust())
    assert {r["serial"]: n for r, n in rep["differs"]} == {"T2": ["overall length 7 (records: 6.5)"]}
    atf.apply_approvals(records, import_unknown=True)
    lengths = {r["serial_number"]: r["overall_length"] for r in atf.load_rows()}
    assert lengths == {"T1": "12", "T2": "6.5", "T3": "4.7"}   # gap filled from the PDF; a recorded length is never overwritten


def test_old_id_named_pdfs_are_renamed_by_serial(configured):
    folder = atf.OUT / "approved"
    folder.mkdir()
    (folder / "1234567.pdf").write_bytes(approved_pdf(serial="T301"))
    (folder / "1234568.pdf").write_bytes(approved_pdf(serial="T301"))             # same serial twice: kept, not overwritten
    (folder / "1234569.pdf").write_bytes(b"%PDF-1.4 unreadable")
    records, problems = atf.read_approved()
    assert sorted(f.name for f in folder.glob("*.pdf")) == ["T301.pdf", "T301_2.pdf", "eForm-1234569.pdf"]
    assert sorted(r["eform_id"] for r in records) == ["1234567", "1234568"] and len(problems) == 1
    index = (folder / "index.csv").read_text(encoding="utf-8")
    assert "1234567,T301.pdf" in index and "1234568,T301_2.pdf" in index
