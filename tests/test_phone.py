"""The phone page (src/phone.py + phone.html) on made-up records: the API, and the page itself in a phone-sized
headless browser. The server listens on 127.0.0.1 here (the real one on the home network)."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlparse, parse_qs

import pytest

from conftest import atf, pick, ui

import phone

QUIT = lambda kw: "quit"  # noqa: E731


@pytest.fixture
def served(configured):
    """T1 built, T2 and A083 approved (not built), T6 filed, T7 queued, one SBR; T1's approval PDF saved."""
    rows = [("T1", "approved", "yes", "Rimfire Short", "Mark per 27 CFR 479.102"), ("T2", "approved", "", "", ""),
            ("A083", "approved", "", "", ""), ("T6", "filed", "", "", ""), ("T7", "", "", "", "")]
    atf.save_rows([{"serial_number": s, "caliber": "22", "overall_length": "12", "status": st, "built": b, "design": d,
                    "conditions": c, "approved_date": "10/15/2026" if st == "approved" else "",
                    "application_number": f"90000{n:04d}", "eform_id": f"100{n:04d}"}
                   for n, (s, st, b, d, c) in enumerate(rows, 1)])
    atf.save_sbrs([{"serial_number": "R9", "caliber": "9", "model": "PCC", "barrel_length": "8", "overall_length": "24",
                    "approved_date": "08/14/2026", "eform_id": "1000099", "control_number": "", "conditions": ""}])
    (atf.OUT / "approved").mkdir()
    (atf.OUT / "approved" / "T1.pdf").write_bytes(b"%PDF-1.4 fake")
    events = []
    server, link = phone.start(port=0, on_event=events.append, host="127.0.0.1")
    yield link, events
    server.shutdown()
    server.server_close()


def call(link, path, body=None, key=None):
    u = urlparse(link)
    k = parse_qs(u.query)["k"][0] if key is None else key
    req = urllib.request.Request(f"{u.scheme}://{u.netloc}{path}?k={k}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_key_is_required(served):
    link, _ = served
    for path in ("/", "/api/inventory", "/pdf/T1"):
        assert call(link, path, key="wrong")[0] == 403
        assert call(link, path, key="")[0] == 403
    assert call(link, "/api/build", {"serial": "T2", "built": True}, key="wrong")[0] == 403
    assert not atf.is_built(next(r for r in atf.load_rows() if r["serial_number"] == "T2"))


def test_inventory_shows_last_4_only(served):
    link, _ = served
    code, body, headers = call(link, "/api/inventory")
    data = json.loads(body)
    assert code == 200 and headers["Cache-Control"] == "no-store"
    assert [r["serial"] for r in data["suppressors"]] == ["A083", "T1", "T2", "T6", "T7"]   # serial order
    t1 = data["suppressors"][1]
    assert (t1["built"], t1["design"], t1["application"], t1["pdf"]) == (True, "Rimfire Short", "…0001", True)
    assert data["suppressors"][4]["status"] == "queued" and data["sbrs"][0]["model"] == "PCC"
    assert b"900000001" not in body                                  # never the full application number


def test_build_and_clear(served):
    link, events = served
    assert call(link, "/api/build", {"serial": "t2", "built": True, "design": "  PCC Short "})[0] == 200
    row = {r["serial_number"]: r for r in atf.load_rows()}["T2"]
    assert (row["built"], row["design"]) == ("yes", "PCC Short")
    assert call(link, "/api/build", {"serial": "T2", "built": False})[0] == 200
    assert not atf.is_built({r["serial_number"]: r for r in atf.load_rows()}["T2"])
    code, body, _ = call(link, "/api/build", {"serial": "T6", "built": True})
    assert code == 400 and "isn't approved" in json.loads(body)["error"]
    assert call(link, "/api/build", {"serial": "R9", "built": True})[0] == 400       # SBRs aren't builds
    assert call(link, "/api/build", {"built": True})[0] == 400
    assert events == ["T2 marked built (PCC Short)", "T2 no longer marked built"]


def test_pdf(served):
    link, _ = served
    code, body, headers = call(link, "/pdf/T1")
    assert code == 200 and body == b"%PDF-1.4 fake" and headers["Content-Type"] == "application/pdf"
    assert call(link, "/pdf/T2")[0] == 404 and call(link, "/pdf/NOPE")[0] == 404


def test_the_page_on_a_phone(served):
    from playwright.sync_api import sync_playwright
    link, _ = served
    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(5000)
        page.goto(link)
        page.wait_for_selector(".row")
        assert page.inner_text("#sum") == "5 suppressors · 1 built · 2 not built · 1 SBR"
        assert page.get_attribute("#q", "inputmode") == "numeric"
        page.fill("#q", "83")
        assert page.locator(".row .serial").all_inner_texts() == ["A083"]
        page.fill("#q", "rim")
        assert page.locator(".row .serial").all_inner_texts() == ["T1"]
        page.fill("#q", "")
        page.click(".chip[data-f='notbuilt']")
        assert page.locator(".row .serial").all_inner_texts() == ["A083", "T2"]
        page.click(".row[data-s='T2']")
        assert page.is_visible("#detailView") and not page.is_visible("#listView")    # one screen at a time on a phone
        page.click("#sw")
        page.wait_for_selector("#design")
        page.fill("#design", "Rimfire 12in")
        page.click("#save")
        page.wait_for_function("document.getElementById('toast').textContent.includes('saved as built')")
        assert {r["serial_number"]: r["design"] for r in atf.load_rows()}["T2"] == "Rimfire 12in"
        page.click("#back")
        page.wait_for_selector("#listView", state="visible")
        page.click(".chip[data-f='all']")
        page.click(".row[data-s='T6']")
        assert page.is_disabled("#sw") and "Only approved suppressors" in page.inner_text("#detailView")
        page.go_back()
        page.click(".row[data-s='T1']")
        assert "Approved with conditions" in page.inner_text("#detailView") and page.is_visible("text=Open")
        assert "900000" not in page.content()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_back_to_the_list(served, engine):
    """Phone back button/swipe and "‹ Inventory" both return to the list, also when the page was opened at a serial
    (no history entry of its own to go back to). WebKit = Safari's engine (skipped where it isn't installed)."""
    from playwright.sync_api import sync_playwright, Error
    link, _ = served
    with sync_playwright() as pw:
        try:
            browser = getattr(pw, engine).launch()
        except Error:
            pytest.skip(f"Playwright {engine} isn't installed")
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(5000)
        listed = lambda: page.wait_for_selector("#listView", state="visible") and not page.is_visible("#detailView")  # noqa: E731
        page.goto(link)
        page.wait_for_selector(".row")
        page.click(".row[data-s='T2']")
        assert page.is_visible("#detailView") and page.url.endswith("#T2")
        page.click("#back")
        assert listed() and "#" not in page.url
        page.click(".row[data-s='A083']")
        page.go_back()
        assert listed()
        page.go_forward()
        assert page.is_visible("#detailView") and "A083" in page.inner_text("#detailView")
        page.goto("about:blank")
        page.goto(link + "#T1")                                           # opened straight at a serial
        page.wait_for_selector("#back")
        page.click("#back")
        assert listed() and page.url.startswith("http") and "#" not in page.url
        page.evaluate("history.back = () => {}")                          # a browser whose back does nothing
        page.click(".row[data-s='T2']")
        page.click("#back")
        assert listed() and "#" not in page.url
        browser.close()


def test_menu_shares_and_stops(configured, answers, screen, monkeypatch):
    started = {}
    real = phone.start

    def local(**kw):
        started["server"], link = real(port=0, host="127.0.0.1", **kw)
        return started["server"], link
    monkeypatch.setattr(phone, "start", local)
    atf.save_rows([{"serial_number": "T1", "caliber": "22", "status": "approved"}])
    answers(pick("Inventory"), pick("Open on my phone"), "back", QUIT)
    ui.menu()
    out = screen.getvalue()
    assert "Your inventory on your phone" in out and "http://127.0.0.1:" in out and "?k=" in out
    with pytest.raises(OSError):                                       # Enter stopped it
        urllib.request.urlopen(f"http://127.0.0.1:{started['server'].server_address[1]}/", timeout=2)


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_unmark_built_on_the_page(served, engine):
    """Turning Built off asks first (on the page itself, not a browser pop-up), Keep leaves it, Clear clears it."""
    from playwright.sync_api import sync_playwright, Error
    link, _ = served
    with sync_playwright() as pw:
        try:
            browser = getattr(pw, engine).launch()
        except Error:
            pytest.skip(f"Playwright {engine} isn't installed")
        page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
        page.set_default_timeout(5000)
        page.goto(link + "#T1")
        page.wait_for_selector("#sw[aria-checked='true']")
        page.tap("#sw")
        page.tap("#keep")
        assert page.get_attribute("#sw", "aria-checked") == "true" and atf.is_built(atf.load_rows()[0])
        page.tap("#sw")
        page.tap("#clear")
        page.wait_for_selector("#sw[aria-checked='false']")
        assert page.get_attribute("#sw", "aria-checked") == "false"
        assert {r["serial_number"]: r["built"] for r in atf.load_rows()}["T1"] == ""
        browser.close()
