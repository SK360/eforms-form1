"""Check approvals (read-only): fake approved Form 1 PDFs (same layout as eForms' "View PDF", made-up values) and a
fake eForms dashboard with View Form / Submit Void buttons. The test fails if anything but the read-only controls is clicked."""
import csv
import json
from pathlib import Path
from urllib.parse import unquote

import pymupdf
import pytest

from conftest import TICKED, atf, pick, queue, ui  # noqa: F401

QUIT = lambda kw: "quit"  # noqa: E731


def approved_pdf(type_="SILENCER", serial="T201", caliber="22", length="12", barrel="", model="N/A",
                 approved=True, disapproved=False, date="08/15/2026", conditions="", date_x=505, date_top=718,
                 signed="01/01/2026", nine=True):
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
    if nine:                                                                    # item 9: "9." then "Date" (a separate label)
        label(484, 604, "9.")
    label(494, 604, "Date"), value(513, 615, signed)                            # applicant's signature date (not ATF's)
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
                                        "filed_date": "01/01/2026", "pdf_control_number": ""}
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
    assert row == {"eform_id": "1234567", "control_number": "20261234567", "finalized": "8/15/2026", "submitted": "8/3/2026"}
    # a submitted form (in process): created and submitted dates only, "Withdraw Form" instead of "Submit Void"
    assert atf._parse_result_row("1234568	20261234568		JOHN DOE	11/5/2026	11/6/2026		 View Form Withdraw Form")["submitted"] == "11/6/2026"
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
const BY = %s, PER = %d, LINKS = %d, MODE = %s, DRAW = %d;  // BY: status -> forms; LINKS page numbers shown at a time, in blocks or sliding
let STATUS = '', SHOWN = 0;  // DRAW: ms between rows (a table drawn row by row, as a slow PC might)
const app = () => document.getElementById('app');
function home() {
  app().innerHTML = `<select id="form-type-search"><option>Select...</option><option>ATF Form 1 (5320.1)</option><option>ATF Form 4 (5320.4)</option></select>
   <select id="form-status-search"><option>Select...</option><option>APPROVED</option><option>DISAPPROVED</option><option>RETURNED FOR CORRECTION</option>
   <option>RETURNED WITHOUT ACTION</option><option>SUBMITTED/IN PROCESS</option></select>
   <button id="queryBtn" onclick="search()">Search</button>
   <div id=results>No data to display 1 10 | Total Rows: 0</div>`;   // like eForms: an empty table before searching
}
function search() {
  STATUS = document.getElementById('form-status-search').value; fetch('/searched/' + encodeURIComponent(STATUS));
  const ok = document.getElementById('form-type-search').value.startsWith('ATF Form 1');
  window.hits = ok ? (BY[STATUS] || []) : []; setTimeout(() => showPage(1), 1500);   // results arrive after a delay, like the real portal
}
function showPage(n) {
  const token = ++SHOWN;
  // markup like a UI-library table: empty first cell, column titles inside cells, hidden duplicate buttons
  const T = t => `<span class="col-title">${t} </span>`;
  const rows = window.hits.slice((n - 1) * PER, n * PER).map(f => `<tr><td></td><td>${T('ID')}<span class="ng-star-inserted"><a id="eformId-link"> ${f.id} </a></span></td>
     <td>${T('Permit / Control Number')}${f.id.length > 6 ? '2026' + f.id + '0' : ''}</td><td>${T('Internal Control Number')}${f.icn}</td><td>${T('Created By')}JOHN DOE</td>
     <td>1/1/2026</td><td>${f.sub}</td><td>${f.fin}</td><td><button style="display:none" onclick="fetch('/clicked/hidden')">View Form</button>
     <button onclick="viewForm('${f.id}')">View Form</button> ${STATUS === 'APPROVED' ? `<button onclick="fetch('/clicked/void')">Submit Void</button>`
     : `<button onclick="fetch('/clicked/withdraw')">Withdraw Form</button>`}</td></tr>`);
  const pages = Math.ceil(window.hits.length / PER);
  let lo = MODE.startsWith('block') ? Math.floor((n - 1) / LINKS) * LINKS + 1 : Math.max(1, Math.min(n - Math.floor(LINKS / 2), pages - LINKS + 1));
  const nums = Array.from({length: Math.min(LINKS, pages - lo + 1)}, (_, i) => lo + i);
  const icon = (cls, to) => `<a href="#" class="ui-paginator-${cls}${to < 1 || to > pages || to === n ? ' ui-state-disabled' : ''}"
     onclick="${to >= 1 && to <= pages ? `showPage(${to});` : ''}return false"><span class="pi pi-caret" style="display:inline-block;width:12px;height:12px"></span></a>`;
  const links = icon('first', 1) + icon('prev', n - 1) + '<span class="ui-paginator-pages">' + nums.map(k => `<a href="#" class="ui-paginator-page${k === n && !MODE.includes('nomark') ? ' ui-state-active' : ''}"
     onclick="showPage(${k});return false">${k}</a>`).join(' ') + '</span>' + icon('next', n + 1) + icon('last', pages);
  document.getElementById('results').innerHTML = `<table><tr><th>ID</th><th>Permit / Control Number</th></tr>${DRAW ? rows.slice(0, 1).join('') : rows.join('')}</table>
     <div class="ui-paginator">${links} <input type="text" value="10"></div><span>10 | Total Rows: ${window.hits.length}</span>`;
  if (DRAW) rows.slice(1).forEach((r, i) => setTimeout(() => { if (token === SHOWN) document.querySelector('#results tbody').insertAdjacentHTML('beforeend', r); }, (i + 1) * DRAW));
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
    """dashboard(forms) -> state; forms = [(eform_id, finalized, pdf_bytes[, submitted])]. state['pdf_hits'], state['bad_clicks'],
    state['searches'] (each status searched). status: what the search finds them under (APPROVED; SUBMITTED/IN PROCESS rows
    have Withdraw Form, not Submit Void); others: {status: forms} found under other statuses."""
    from playwright.sync_api import sync_playwright
    state = {"pdf_hits": 0, "bad_clicks": [], "searches": []}
    monkeypatch.setattr(atf, "NOT_APPROVED_WAIT", 2)   # the fake's results come after 1.5 s
    logout = atf._logout

    def tracked_logout(page):
        logout(page)
        state["logged_out"] = page.title() == "LOGGEDOUT"
    monkeypatch.setattr(atf, "_logout", tracked_logout)

    def configure(forms, start="/home", per=10, links=5, mode="slide", status="APPROVED", others=None, draw=0):
        by = {status: forms, **(others or {})}
        monkeypatch.setattr(atf, "RESULTS_PER_PAGE", per)   # eForms shows 10; the paging tests use smaller pages
        pdfs = {f[0]: f[2] for fs in by.values() for f in fs}
        html = DASHBOARD % (json.dumps({st: [{"id": f[0], "fin": f[1], "sub": f[3] if len(f) > 3 else "1/2/2026",
                                            "icn": f[4] if len(f) > 4 else ""} for f in fs]
                                       for st, fs in by.items()}), per, links, json.dumps(mode), draw)

        def route(r):
            path = r.request.url.split("fake.test", 1)[1]
            if path.startswith("/pdf/"):
                state["pdf_hits"] += 1
                return r.fulfill(body=pdfs[path[5:]], content_type="application/pdf")
            if path.startswith("/searched/"):
                state["searches"].append(unquote(path[10:]))
                return r.fulfill(body="")
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
    assert dict(rep["skipped"]) == {} and rep["duplicates"] == []                                  # SBRs are recorded, not skipped
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

    def flaky(page, page_no=1, search=True, status="APPROVED", wait=30):
        if page_no == 2 and fails["left"]:
            fails["left"] -= 1
            raise RuntimeError("Locator.inner_text: Timeout 15000ms exceeded.\nCall log: ...")
        return real(page, page_no, search, status, wait)
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
    def boom(*args, **kw):
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
    for shown in ("5 approved Form 1s", "3 suppressor(s), 2 SBRs", "SBRs: 2 added", "Approved, matches your records (1)",
                  "Approved, NOT in your records (2)", "Filed, not approved yet: 1", "T204"):
        assert shown in out, shown
    assert "DOE" not in out.upper()
    assert answers.kw["Open the browser?"]["auto_enter"] is False   # 'y' alone doesn't start it: Enter does


NFA_KINDS = [("SHORT BARRELED RIFLE", "R1", "5.56"), ("SHORT BARRELED SHOTGUN", "G1", "12"), ("ANY OTHER WEAPON", "W1", "9"),
             ("DESTRUCTIVE DEVICE", "D1", "37"), ("MACHINEGUN", "M1", "9")]


def _nfa_record(kind, serial, eform_id="2000001", caliber="12"):
    return {"type": kind, "approved": True, "serial": serial, "approval_date": "10/30/2026", "eform_id": eform_id, "control_number": "",
            "conditions": "", "overall_length": "26", "caliber": caliber, "model": "X", "barrel_length": "14", "pdf": ""}


def test_every_other_type_is_read_and_kept_with_its_type(configured, tmp_path):
    queue(("T201", "22", "filed", "1"))
    records = []
    for n, (kind, serial, caliber) in enumerate(NFA_KINDS):
        f = tmp_path / f"{serial}.pdf"
        f.write_bytes(approved_pdf(kind, serial, caliber, "26", barrel="10.5"))
        r = atf.read_approved_pdf(f)
        assert (r["type"], r["serial"], r["caliber"]) == (kind, serial, caliber)
        records.append(dict(r, eform_id=f"200000{n}"))
    before = atf.SUPP.read_bytes()
    assert atf.record_sbrs(records) == 5 and atf.record_sbrs(records) == 0                         # no duplicates
    assert atf.apply_approvals(records) == ([], []) and atf.SUPP.read_bytes() == before            # never suppressors
    rows = atf.load_sbrs()
    assert {r["serial_number"]: r["type"] for r in rows} == {s: k for k, s, _ in NFA_KINDS}
    assert atf.nfa_counts(rows) == "1 SBR, 1 SBS, 1 AOW, 1 DD, 1 MG" and atf.nfa_label(rows) == "Other NFA items"
    assert dict(atf.approval_report(records, atf.load_rows(), atf.load_trust())["skipped"]) == {}


def test_a_type_that_cant_be_read_is_reported_not_recorded(configured):
    rec = _nfa_record("", "U1")
    assert atf.is_other_nfa(rec) is False and atf.record_sbrs([rec]) == 0 and atf.load_sbrs() == []
    assert dict(atf.approval_report([rec], [], atf.load_trust())["skipped"]) == {"unknown type": 1}
    assert atf.is_other_nfa({"type": " silencer "}) is False and atf.is_other_nfa(_nfa_record("MACHINEGUN", "M1")) is True


def test_older_rows_count_as_sbrs_until_check_approvals_corrects_them(configured):
    atf._sbrs_path().write_text("serial_number,caliber,model,barrel_length,overall_length,approved_date,eform_id,control_number,conditions\n"
                                "R1,12,X,14,26,09/02/2026,2000001,,\n", encoding="utf-8")       # a file from before types
    assert atf.load_sbrs()[0]["type"] == "SHORT BARRELED RIFLE" and atf.nfa_label(atf.load_sbrs()) == "SBRs"
    assert atf.record_sbrs([_nfa_record(" short  barreled shotgun ", "R1")]) == 0                  # the same eForm: corrected
    assert atf.load_sbrs()[0]["type"] == "SHORT BARRELED SHOTGUN" and len(atf.load_sbrs()) == 1
    assert atf.nfa_counts(atf.load_sbrs()) == "1 SBS" and atf.nfa_label(atf.load_sbrs()) == "SBSs"


def test_a_type_this_tool_doesnt_know_is_shown_as_printed(configured):
    assert atf.record_sbrs([_nfa_record("SOME NEW TYPE", "N1"), _nfa_record("SHORT BARRELED RIFLE", "R1", "2000002"),
                            _nfa_record("SHORT BARRELED RIFLE", "R2", "2000003")]) == 3
    rows = atf.load_sbrs()
    assert atf.nfa_counts(rows) == "2 SBRs, 1 SOME NEW TYPE" and atf.nfa_short("SOME NEW TYPE") == "SOME NEW TYPE"
    assert atf.nfa_label(rows) == "Other NFA items" and atf.nfa_label(rows[:1]) == "Other NFA items"    # only the new type: still generic
    assert atf.nfa_counts([]) == "0 other NFA items"


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


def test_item_9_is_the_filed_date(tmp_path):
    """Item 9 = the applicant's signature date, which in eForms is the day the Form 1 was signed and submitted."""
    f = tmp_path / "a.pdf"
    for signed, want in (("6/1/2026", "06/01/2026"), ("June 2, 2026", "06/02/2026"), ("", ""), ("N/A", "")):
        f.write_bytes(approved_pdf(signed=signed))
        assert atf.read_approved_pdf(f)["filed_date"] == want, signed
    f.write_bytes(approved_pdf(nine=False))                    # a "Date" that isn't item 9's: not taken
    r = atf.read_approved_pdf(f)
    assert (r["filed_date"], r["approval_date"]) == ("", "08/15/2026")


def test_filed_dates_are_filled_from_approvals_never_overwritten(configured, tmp_path):
    queue(("T1", "22", "filed"), ("T2", "22", "filed"))
    rows = atf.load_rows()
    rows[1]["filed_date"] = "05/20/2026"
    atf.save_rows(rows)
    found = []
    for serial in ("T1", "T2", "T3"):
        f = tmp_path / f"{serial}.pdf"
        f.write_bytes(approved_pdf(serial=serial, signed="05/01/2026"))
        found.append({"eform_id": f"100000{serial[-1]}", "control_number": "", "finalized": "", "pdf": str(f)})
    records, _ = atf.read_approved(found)
    assert atf.apply_approvals(records, import_unknown=True) == (["T1", "T2"], ["T3"])
    assert {r["serial_number"]: r["filed_date"] for r in atf.load_rows()} == {"T1": "05/01/2026", "T2": "05/20/2026", "T3": "05/01/2026"}
    assert atf.apply_approvals(records) == ([], [])             # nothing left to fill


def _approved_rows(*rows):
    atf.save_rows([{"serial_number": s, "caliber": "22", "overall_length": "12", "status": "approved", "filed_date": filed,
                    "approved_date": "10/15/2026", "eform_id": f"100000{n}"} for n, (s, filed) in enumerate(rows, 1)])


def test_saved_pdfs_fill_missing_filed_dates_without_logging_in(configured, answers, screen):
    """Approved rows filed by hand (no filed date): the menu fills them from the PDFs already saved, at start."""
    _approved_rows(("T1", ""), ("T2", "05/20/2026"), ("T3", ""), ("T4", ""), ("T5", ""))
    folder = atf.OUT / "approved"
    folder.mkdir()
    (folder / "T1.pdf").write_bytes(approved_pdf(serial="T1", signed="05/01/2026"))
    (folder / "T2.pdf").write_bytes(approved_pdf(serial="T2", signed="05/02/2026"))   # has one: kept
    (folder / "T3.pdf").write_bytes(approved_pdf(serial="T9", signed="05/03/2026"))   # someone else's serial: not used
    (folder / "eForm-1000005.pdf").write_bytes(approved_pdf(serial="T5", signed="05/05/2026"))
    (folder / "index.csv").write_text("eform_id,file\n1000005,eForm-1000005.pdf\n", encoding="utf-8")
    answers(QUIT)
    ui.menu()
    got = {r["serial_number"]: r["filed_date"] for r in atf.load_rows()}
    assert got == {"T1": "05/01/2026", "T2": "05/20/2026", "T3": "", "T4": "", "T5": "05/05/2026"}
    assert atf.backfill_from_approval_pdfs() == []


# ---------- disapproved and returned forms ----------
def filed(*rows):
    """filed(("T301", "filed", "11/01/2026"), ...): rows with a status and filed date."""
    atf.save_rows([{"serial_number": s, "caliber": "22", "status": st, "filed_date": d, "overall_length": "12",
                    "application_number": f"90000030{n}" if st else ""} for n, (s, st, d) in enumerate(rows, 1)])


def rejected_pdf(serial, signed, disapproved=False):
    return approved_pdf(serial=serial, approved=False, disapproved=disapproved, signed=signed)


REJECTED = {  # made-up forms eForms lists as not approved: (eForm ID, finalized, PDF (item 9 = the day it was signed), submitted)
    "DISAPPROVED": [("3000001", "11/12/2026", rejected_pdf("T301", "11/01/2026", True), "11/1/2026"),
                    ("3000003", "10/12/2026", rejected_pdf("T303", "10/01/2026", True), "10/1/2026")],   # an earlier filing
    "RETURNED WITHOUT ACTION": [("3000002", "11/13/2026", rejected_pdf("T302", "11/01/2026"), "11/1/2026")],
    "RETURNED FOR CORRECTION": [("3000004", "11/14/2026", rejected_pdf("T304", "11/02/2026"), "11/2/2026")],
}
WAITING = (("T301", "filed", "11/01/2026"), ("T302", "pending", "11/01/2026"), ("T303", "filed", "11/10/2026"),
           ("T304", "filed", "11/02/2026"), ("T305", "", ""))


def test_disapproved_and_returned_forms_are_found_and_marked(configured, dashboard):
    filed(*WAITING)
    state = dashboard(FORMS[:1], others=REJECTED)
    others = []
    found, problems = atf.fetch_approved(others)
    assert problems == [] and state["bad_clicks"] == [] and state["logged_out"]                    # read-only, logged out
    assert list(dict.fromkeys(state["searches"])) == ["APPROVED", *atf.NOT_APPROVED] and len(found) == 1   # (again after each PDF)
    assert sorted((r["status"], r["eform_id"]) for r in others) == [
        ("DISAPPROVED", "3000001"), ("DISAPPROVED", "3000003"), ("RETURNED FOR CORRECTION", "3000004"), ("RETURNED WITHOUT ACTION", "3000002")]
    saved = {p.relative_to(atf.OUT / "not-approved").as_posix() for p in (atf.OUT / "not-approved").rglob("*.pdf")}
    assert saved == {"disapproved/T301.pdf", "disapproved/T303.pdf", "returned-without-action/T302.pdf",
                     "returned-for-correction/T304.pdf"}
    assert not (atf.OUT / "approved" / "T301.pdf").exists()                                         # never among approvals

    records, more = atf.read_approved(others)
    rep = atf.not_approved_report(records, atf.load_rows())
    assert more == [] and rep["unsure"] == []
    assert [(row["serial_number"], rec["status"]) for row, rec in rep["disapproved"]] == [
        ("T301", "DISAPPROVED"), ("T302", "RETURNED WITHOUT ACTION")]                               # T303 was filed again since
    assert [row["serial_number"] for row, _ in rep["correction"]] == ["T304"]
    assert atf.record_disapprovals([row["serial_number"] for row, _ in rep["disapproved"]]) == ["T301", "T302"]
    status = {r["serial_number"]: r["status"] for r in atf.load_rows()}
    assert status == {"T301": "disapproved", "T302": "disapproved", "T303": "filed", "T304": "filed", "T305": ""}
    assert atf.record_disapprovals(["T301", "t302", "T303x"]) == []                                # already marked: nothing to do

    # the next run downloads nothing again, and a disapproved row no longer counts for the old form
    state["pdf_hits"] = 0
    others = []
    atf.fetch_approved(others)
    assert state["pdf_hits"] == 0 and len(others) == 4
    rep = atf.not_approved_report(atf.read_approved(others)[0], atf.load_rows())
    assert rep["disapproved"] == [] and [row["serial_number"] for row, _ in rep["correction"]] == ["T304"]


def test_no_extra_searches_while_nothing_is_waiting(configured, dashboard):
    filed(("T301", "approved", "10/01/2026"), ("T305", "", ""))
    state = dashboard(FORMS[:1], others=REJECTED)
    others = []
    atf.fetch_approved(others)
    assert set(state["searches"]) == {"APPROVED"} and others == []
    atf.fetch_approved()                                                                            # without `others`: never
    assert set(state["searches"]) == {"APPROVED"}


def test_a_failed_search_keeps_the_approvals(configured, dashboard, monkeypatch):
    filed(*WAITING)
    dashboard(FORMS[:1], others=REJECTED)
    real = atf._approved_results

    def broken(page, page_no=1, search=True, status="APPROVED", wait=30):
        if status == "RETURNED WITHOUT ACTION":
            raise RuntimeError("the page layout changed")
        return real(page, page_no, search, status, wait)
    monkeypatch.setattr(atf, "_approved_results", broken)
    others = []
    found, problems = atf.fetch_approved(others)
    assert len(found) == 1 and sorted(r["status"] for r in others) == ["DISAPPROVED", "DISAPPROVED", "RETURNED FOR CORRECTION"]
    assert problems == ["couldn't search returned without action Form 1s: the page layout changed"]


def test_which_forms_count_for_a_row():
    rows = [{"serial_number": s, "status": st, "filed_date": d} for s, st, d in (
        ("A1", "filed", "11/01/2026"), ("A2", "filed", ""), ("A3", "approved", "11/01/2026"), ("A4", "", ""), ("A5", "filed", "11/01/2026"))]
    rec = lambda serial, signed="", submitted="", status="DISAPPROVED", type_="SILENCER": {  # noqa: E731
        "serial": serial, "filed_date": signed, "submitted": submitted, "status": status, "type": type_, "eform_id": "3000009"}
    rep = atf.not_approved_report([rec("a1", submitted="11/1/2026"),                             # no item 9: the results list's date
                                   rec("A2", "11/01/2026"),                                      # the row has no filed date
                                   rec("A3", "11/01/2026"), rec("A4", "11/01/2026"),              # approved / never filed: not ours
                                   rec("A5", "11/01/2026", type_="SHORT BARRELED RIFLE"),         # another NFA type
                                   rec("A9", "11/01/2026")], rows)                                # not in the inventory
    assert [r["serial_number"] for r, _ in rep["disapproved"]] == ["A1"]
    assert [r["serial_number"] for r, _ in rep["unsure"]] == ["A2"] and rep["correction"] == []


def test_menu_reports_disapprovals_and_refiles_them(configured, dashboard, answers, screen, monkeypatch):
    filed(*WAITING)
    dashboard(FORMS[:1], others=REJECTED)
    calls = []
    monkeypatch.setattr(ui, "file_flow", lambda indices=None, force=False: calls.append((indices, force)))
    answers(pick("Check approvals"), True, False,                                  # open the browser; don't add T201
            pick("Advanced"), pick("Re-file"), pick("T302"), True, QUIT)
    ui.menu()
    out = screen.getvalue()
    for shown in ("Disapproved or returned by ATF (2)", "returned without action", "2 marked disapproved", "Advanced > Re-file",
                  "T304: returned for correction", "Filed, not approved yet: 2", "2 Form 1s disapproved by ATF"):
        assert shown in out, shown
    titles = [str(c.title) for c in answers.kw["Re-file which row?"]["choices"]]
    assert titles[:2] == ["T301  (caliber 22)  disapproved", "T302  (caliber 22)  disapproved"]   # first, then the filed ones
    assert "ATF disapproved or returned T302" in out and answers.kw["File it again?"]["auto_enter"] is False
    rows = atf.load_rows()
    assert calls == [([next(i for i, r in enumerate(rows) if r["serial_number"] == "T302")], True)]


def in_process_pdf(serial, caliber="22", length="12", signed="11/03/2026", type_="SILENCER"):
    """A submitted form's "View PDF" (made-up): the ATF copy's page 1 (item 4, item 9 signed, nothing ticked) + 2 more pages."""
    doc = pymupdf.open(stream=approved_pdf(type_, serial, caliber, length, approved=False, date="", signed=signed))
    for t in ("ATF Form 5320.1 (Form 1) instructions", "CLEO Copy"):
        doc.new_page(width=612, height=792).insert_text((40, 80), t, fontname="tiro", fontsize=9)
    return doc.tobytes()


IN_PROCESS = [  # (eForm ID, finalized, PDF, submitted): what eForms lists as SUBMITTED/IN PROCESS
    ("4000001", "", in_process_pdf("T401"), "11/3/2026"),               # filed with this tool (its CLEO copy was saved)
    ("4000002", "", in_process_pdf("T402", length='12"'), "11/3/2026"),  # filed by hand, but queued here: would be filed twice
    ("4000003", "", in_process_pdf("T403"), "11/3/2026"),               # pending here
    ("4000004", "", in_process_pdf("HAND1", "9", signed=""), "11/4/2026"),   # filed by hand, not in the records; no item 9
    ("4000005", "", in_process_pdf("R9", "5.56", "26", type_="SHORT BARRELED RIFLE"), "11/4/2026"),
    ("4000006", "", in_process_pdf("DUP1"), "11/4/2026"), ("4000007", "", in_process_pdf("DUP1"), "11/5/2026"),
]


def hand_filed_setup():
    filed(("T401", "filed", "11/03/2026"), ("T402", "", ""), ("T403", "pending", "11/03/2026"), ("T404", "", ""))
    (atf.OUT / "cleo").mkdir(parents=True, exist_ok=True)
    atf._save_form1_index({"4000001": "T401"})


def test_forms_filed_by_hand_are_found_in_process_and_recorded_as_filed(configured, dashboard):
    hand_filed_setup()
    state = dashboard(FORMS[:1], others={atf.SUBMITTED: IN_PROCESS})
    in_process = []
    found, problems = atf.fetch_approved(None, in_process)
    assert problems == [] and state["bad_clicks"] == [] and state["logged_out"]                  # read-only, logged out
    assert list(dict.fromkeys(state["searches"])) == ["APPROVED", atf.SUBMITTED] and len(found) == 1
    assert state["pdf_hits"] == 1 + 6                                                             # T401 is never opened
    folder = atf.OUT / "in-process"
    assert sorted(f.name for f in folder.glob("*.pdf")) == ["DUP1.pdf", "DUP1_2.pdf", "HAND1.pdf", "R9.pdf", "T402.pdf", "T403.pdf"]
    assert all(pymupdf.open(f).page_count == 1 for f in folder.glob("*.pdf"))                     # only page 1 is kept
    assert not (atf.OUT / "approved" / "T402.pdf").exists()

    records, more = atf.read_approved(in_process)
    rep = atf.in_process_report(records, atf.load_rows())
    assert more == [] and rep["duplicates"] == ["DUP1"] and [r["serial"] for r in rep["other"]] == ["R9"]
    assert [r["serial"] for r in rep["unknown"]] == ["HAND1"]
    assert [row["serial_number"] for row, _ in rep["queued"]] == ["T402"]
    assert [row["serial_number"] for row, _ in rep["pending"]] == ["T403"]
    assert atf.record_in_process(rep["unknown"] + [rec for _, rec in rep["queued"]]) == ["HAND1", "T402"]
    rows = {r["serial_number"]: r for r in atf.load_rows()}
    assert [r["serial_number"] for r in atf.load_rows()] == ["HAND1", "T401", "T402", "T403", "T404"]   # serial order
    assert {k: rows["HAND1"][k] for k in ("status", "caliber", "overall_length", "filed_date", "eform_id", "application_number")} == {
        "status": "filed", "caliber": "9", "overall_length": "12", "filed_date": "11/04/2026", "eform_id": "4000004",
        "application_number": ""}                                                                 # no item 9: the submitted date
    assert (rows["T402"]["status"], rows["T402"]["filed_date"], rows["T402"]["eform_id"]) == ("filed", "11/03/2026", "4000002")
    assert rows["T403"]["status"] == "pending" and rows["T404"]["status"] == ""                  # left alone
    assert "T402" not in [r["serial_number"] for r in atf.load_rows() if r["status"] not in atf.SKIP]   # File won't file it again
    assert atf.record_in_process(rep["unknown"]) == []                                            # filed now: nothing to do

    # the next run opens nothing again: recorded forms are skipped, the others are read from out/in-process/
    state["pdf_hits"] = 0
    in_process = []
    atf.fetch_approved(None, in_process)
    assert state["pdf_hits"] == 0 and sorted(r["eform_id"] for r in in_process) == ["4000003", "4000005", "4000006", "4000007"]


def test_forms_no_longer_in_process_are_removed_from_the_folder(configured, dashboard):
    hand_filed_setup()
    dashboard(FORMS[:1], others={atf.SUBMITTED: IN_PROCESS})
    atf.fetch_approved(None, [])
    dashboard(FORMS[:1], others={atf.SUBMITTED: IN_PROCESS[:3]})                                   # the rest were approved since
    atf.fetch_approved(None, [])
    folder = atf.OUT / "in-process"
    assert sorted(f.name for f in folder.glob("*.pdf")) == ["T402.pdf", "T403.pdf"]
    assert sorted(atf._approved_index(folder)) == ["4000002", "4000003"]


def test_a_failed_in_process_search_keeps_the_approvals(configured, dashboard, monkeypatch):
    hand_filed_setup()
    dashboard(FORMS[:1], others={atf.SUBMITTED: IN_PROCESS})
    real = atf._approved_results

    def broken(page, page_no=1, search=True, status="APPROVED", wait=30):
        if status == atf.SUBMITTED:
            raise RuntimeError("the page layout changed")
        return real(page, page_no, search, status, wait)
    monkeypatch.setattr(atf, "_approved_results", broken)
    in_process = []
    found, problems = atf.fetch_approved(None, in_process)
    assert len(found) == 1 and in_process == []
    assert problems == ["couldn't search submitted/in process Form 1s: the page layout changed"]


def test_menu_records_forms_filed_by_hand(configured, dashboard, answers, screen):
    hand_filed_setup()
    dashboard(FORMS[:1], others={atf.SUBMITTED: IN_PROCESS})
    answers(pick("Check approvals"), True, True, False, QUIT)        # open the browser; record them as filed; don't add T201
    ui.menu()
    out = screen.getvalue()
    for shown in ("In process on eForms, not filed in your records (2)", "queued: filing it would file it twice", "not in your records",
                  "T403: pending here and in process on eForms", "1 other NFA item(s) in process",
                  "serial DUP1 is on more than one submitted/in process Form 1", "2 suppressor(s) in process on eForms recorded as filed"):
        assert shown in out, shown
    assert "4000004" not in out                                                                   # no eForm IDs on screen
    q = next(k for k in answers.kw if k.startswith("Record the 2 suppressor(s)"))
    assert "HAND1, T402" in q
    status = {r["serial_number"]: r["status"] for r in atf.load_rows()}
    assert status == {"HAND1": "filed", "T401": "filed", "T402": "filed", "T403": "pending", "T404": ""}
    assert "Filed, not approved yet: 4" in out                                                    # HAND1 and T402 now count too


def broken_pdf(path, pages=3):
    """A PDF like eForms' submitted ones: its xref lists objects that aren't there ("cannot find object in xref")."""
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    spares = [doc.get_new_xref() for _ in range(5)]
    for x in spares:
        doc.update_object(x, "<< /Kind /Spare >>")
    doc.xref_set_key(doc.pdf_catalog(), "Spares", "[" + " ".join(f"{x} 0 R" for x in spares) + "]")
    raw = doc.tobytes()
    for x in spares:
        raw = raw.replace(f"\n{x} 0 obj".encode(), f"\n{x} 0 xbj".encode())
    path.write_bytes(raw)
    return path


def test_trimming_a_broken_pdf_prints_nothing_and_keeps_page_one(tmp_path, capfd):
    """First live run (2026-11-21): one "MuPDF error: ... cannot find object in xref" line per object filled the
    screen while in-process forms were trimmed, and select() could leave all pages after MuPDF's repair."""
    pymupdf.TOOLS.mupdf_display_errors(True)
    pdf = broken_pdf(tmp_path / "f.pdf")
    capfd.readouterr()
    atf._keep_first_page(pdf)
    out, err = capfd.readouterr()
    assert "MuPDF" not in out + err
    doc = pymupdf.open(pdf)
    assert doc.page_count == 1 and doc[0].get_text().strip() == "page 1"


def test_a_pdf_that_cant_be_trimmed_is_kept_whole(tmp_path):
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really a pdf")
    atf._keep_first_page(pdf)
    assert pdf.read_bytes() == b"%PDF-1.4 not really a pdf"


def test_a_table_drawn_row_by_row_is_read_whole(configured, dashboard):
    """A precaution, not seen live: a table drawn row by row must be read whole, not at its first row (that made 7
    forms look like 7 pages, and later pages came up short). The live "7 page(s)" (2026-11-22) was 6-digit IDs."""
    queue(("T201", "22", "filed", "1"))
    state = dashboard(FORMS[:7], draw=150)
    found, problems = atf.fetch_approved()
    assert problems == [] and state["bad_clicks"] == [] and state["pdf_hits"] == 7 and len(found) == 7
    state = dashboard(FORMS, draw=150)                  # 12 forms: page 2 is drawn row by row too
    found, problems = atf.fetch_approved()
    assert problems == [] and state["pdf_hits"] == 7 + 5 and len({f["eform_id"] for f in found}) == 12  # 7 saved before


def test_six_digit_eform_ids_are_read(configured, dashboard):
    """Live 2026-11-22 (a user's older forms): eForm IDs of 6 digits. Only 7-8 digit IDs were read, so 1 of his 7
    approved forms was seen ("7 approved Form 1(s) on 7 page(s)", then "couldn't get to results page 2") and his
    disapproved one not at all. IDs come from each row's ID link now; the serial in the Internal Control Number
    column (6 digits in a row) must not be taken for one."""
    queue(("T201", "22", "filed", "1"))
    old = [(f"10000{n}", "4/23/2019", older_approval_pdf(serial=f"2014{n}"), "4/1/2019", f"TD26112{n}") for n in range(1, 7)]
    state = dashboard(old + [FORMS[0]], others={"DISAPPROVED": [("100009", "", approved_pdf(serial="T201"), "1/2/2026")]})
    others = []
    found, problems = atf.fetch_approved(others)
    assert problems == [] and state["bad_clicks"] == []
    assert sorted(f["eform_id"] for f in found) == sorted([f"10000{n}" for n in range(1, 7)] + ["1000001"])
    assert [o["eform_id"] for o in others] == ["100009"] and state["pdf_hits"] == 8
    records, more = atf.read_approved(found)                # his 2007-form approvals: suppressors, with caliber + length
    rep = atf.approval_report(records, atf.load_rows(), atf.load_trust())
    assert more == [] and sorted((r["serial"], r["caliber"], r["overall_length"]) for r, _ in rep["unknown"]) == [
        (f"2014{n}", "30", "9") for n in range(1, 7)]
    assert atf.record_sbrs(records) == 0 and atf.load_sbrs() == []
    assert sorted(atf.apply_approvals(records, import_unknown=True)[1]) == [f"2014{n}" for n in range(1, 7)]


def test_rows_that_cant_be_read_are_reported_with_their_shape_only(configured, dashboard, monkeypatch):
    """A short read is a problem line naming a file that describes each row without its values, not "7 page(s)"."""
    real = atf._parse_result_row
    monkeypatch.setattr(atf, "_parse_result_row", lambda r: None if "1000002" in str(r) else real(r))
    state = dashboard(FORMS[:3])
    found, problems = atf.fetch_approved()
    assert len(problems) == 1 and "results page 1: eForms lists 3 form(s), only 2 could be read" in problems[0]
    assert state["pdf_hits"] == 2
    shapes = next((atf.OUT).glob("results_rows_*.txt")).read_text(encoding="utf-8")
    assert "ID link: 7 characters" in shapes and "buttons: View Form, Submit Void (+1 hidden)" in shapes
    assert "1000002" not in shapes and "JOHN" not in shapes and "8/15/2026" not in shapes


def older_approval_pdf(serial="20141", caliber="30", overall="9", approved=True):
    """ATF Form 1 "Revised September 2007" as eForms rendered a user's older approvals (6-digit eForm IDs, 2026-11-22),
    placed from his scan of the blank form and his clip of item 4 (pt = px / 1.9): mixed-case labels ("c. Caliber,"
    / "Gauge or" / "Size" / "(Specify)", "e. Of Barrel:", "f. Overall:", "g. Serial Number"), typed values in the same
    Times font. The type sits ~40 pt below its label; only the printed "(Specify)" is inside the old type window."""
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    label = lambda x, top, t: p.insert_text((x, top + 6.5), t, fontname="tiro", fontsize=8)  # noqa: E731
    value = lambda x, top, t: p.insert_text((x, top + 6), t, fontname="tiro", fontsize=7)    # noqa: E731
    label(42, 300, "4. Description of Firearm"), label(140, 300, "(complete items a through i)")
    label(42, 309, "a. Name and Location of Original Manufacturer of"), label(52, 318, "Firearm (Receiver)")
    label(247, 309, "b. Type of Firearm to be made"), label(256, 318, "(See instruction 1c)")
    label(361, 309, "c. Caliber,"), label(369, 318, "Gauge or"), label(369, 327, "Size"), label(370, 336, "(Specify)")
    label(416, 312, "d. Model")
    label(416, 335, "Length"), label(416, 345, "(Inches)"), label(451, 335, "e. Of Barrel:"), label(510, 335, "f. Overall:")
    label(416, 362, "g. Serial Number"), label(475, 362, "(See instruction 2i.)")
    label(42, 393, "h. Additional Description"), label(317, 393, "i. State Why You Intend To Make Firearm")
    value(39, 349, "FORM 1 REGISTRATION, UNITED STATES"), value(248, 349, "SILENCER"), value(362, 368, caliber)
    value(514, 351, overall), value(417, 378, serial), value(418, 322, "N/A"), value(317, 410, "ANY LAWFUL PURPOSE")
    label(519, 542, "9. Date"), value(521, 556, "04/01/2019")
    label(42, 573, "The space below is for the use of the Bureau of Alcohol, Tobacco, Firearms and Explosives")
    label(63, 607, "Approved"), label(100, 607, "(With the following conditions, if any)")
    label(337, 607, "Disapproved"), label(385, 607, "(For the following reasons)")
    # ATF ticks this form's boxes with a bold X (his clip of the decision block, 2026-11-22), not a ZapfDingbats check
    p.insert_text((47, 616) if approved else (319, 616), "X", fontname="hebo", fontsize=10)
    label(42, 665, "Authorized ATF Official"), label(528, 664, "Date"), value(530, 680, "04/23/2019")
    label(510, 750, "ATF Form 1 (5320.1)"), label(510, 758, "Revised September 2007")
    return doc.tobytes()


def test_older_approval_pdf_is_read_as_a_suppressor(tmp_path):
    f = tmp_path / "o.pdf"
    f.write_bytes(older_approval_pdf())
    r = atf.read_approved_pdf(f)
    assert (r["type"], r["serial"], r["caliber"], r["overall_length"], r["barrel_length"], r["approved"]) == (
        "SILENCER", "20141", "30", "9", "", True)
    assert (r["approval_date"], r["filed_date"], r["model"]) == ("04/23/2019", "04/01/2019", "N/A")
    f.write_bytes(older_approval_pdf(approved=False))       # the X in the Disapproved box
    assert atf.read_approved_pdf(f)["approved"] is False
    assert not atf.is_other_nfa(r)
    f.write_bytes(approved_pdf(type_="(Specify)"))           # no type anywhere: unreadable, never another NFA item
    r = atf.read_approved_pdf(f)
    assert r["type"] == "" and not atf.is_other_nfa(r)


def test_only_real_nfa_types_are_other_nfa_items():
    assert atf.is_other_nfa({"type": "SHORT BARRELED RIFLE"}) and atf.is_other_nfa({"type": "Short-barreled shotgun"})
    assert atf.is_other_nfa({"type": "ANY OTHER WEAPON"}) and atf.is_other_nfa({"type": "SOME NEW TYPE"})
    for kind in ("SILENCER", "(SPECIFY)", " ( Specify ) ", "", "  "):
        assert not atf.is_other_nfa({"type": kind}), kind


def test_suppressors_misread_as_other_nfa_items_are_taken_off_that_list(configured):
    """6 of a user's older suppressors were saved to data/sbrs.csv with the type "(SPECIFY)"; the next Check approvals
    reads them as SILENCER and takes them out (and records them as suppressors)."""
    base = {k: "" for k in atf.SBR_COLS}
    atf.save_sbrs([dict(base, serial_number="20141", type="(SPECIFY)", eform_id="100001"),
                   dict(base, serial_number="R1", type="SHORT BARRELED RIFLE", eform_id="1000004")])
    recs = [{"type": "SILENCER", "eform_id": "100001", "serial": "20141"}, {"type": "SHORT BARRELED RIFLE", "eform_id": "1000004"}]
    assert atf.drop_misread_nfa(recs) == 1
    assert [r["serial_number"] for r in atf.load_sbrs()] == ["R1"]
    assert atf.drop_misread_nfa(recs) == 0


def test_a_rename_windows_refuses_for_a_moment_is_retried(tmp_path, monkeypatch):
    src, dest = tmp_path / "a.part.pdf", tmp_path / "A101.pdf"
    src.write_bytes(b"x")
    real, calls = Path.replace, []

    def flaky(self, target):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "Access is denied")
        return real(self, target)
    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(atf.time, "sleep", lambda s: None)
    atf._replace(src, dest)
    assert dest.read_bytes() == b"x" and len(calls) == 3
    src.write_bytes(b"y")
    calls.clear()
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(PermissionError(13, "Access is denied")))
    with pytest.raises(PermissionError, match="open in a PDF viewer"):
        atf._replace(src, dest)
