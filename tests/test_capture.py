"""Advanced > Capture portal pages (atf.cmd_inspect): records pages to out/capture_*.jsonl, follows new tabs,
and keeps every PDF the portal sends (opened in a tab, or downloaded). Fake pages only."""
import builtins
import json

from conftest import FIXTURE, atf  # noqa: F401

PAGE = """<a href='#' onclick="document.title='LOGGEDOUT'">Log Out</a>
<table id=results><tr><td>900000001</td><td>APPROVED</td></tr></table>
<a id=view href="https://fake.test/form.pdf" target="_blank">View</a>
<a id=dl href="https://fake.test/download.pdf">Download</a>"""


def test_capture_records_pages_new_tabs_and_pdfs(sandbox, monkeypatch):
    from playwright.sync_api import sync_playwright
    pdf = FIXTURE.read_bytes()
    state = {}

    def browser():
        pw = sync_playwright().start()
        ctx = pw.chromium.launch(headless=True).new_context(accept_downloads=True)
        ctx.set_default_timeout(5000)
        ctx.route("https://fake.test/form.pdf", lambda r: r.fulfill(body=pdf, content_type="application/pdf"))
        ctx.route("https://fake.test/download.pdf", lambda r: r.fulfill(
            body=pdf, content_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="form1.pdf"'}))
        page = ctx.new_page()
        page.set_content(PAGE)
        state.update(ctx=ctx, page=page)
        return pw, ctx, page
    monkeypatch.setattr(atf, "_browser", browser)

    def user(prompt=""):
        step = state.setdefault("step", 0)
        state["step"] += 1
        page, ctx = state["page"], state["ctx"]
        if step == 1:  # record the results page, then open a form PDF in a new tab
            with ctx.expect_page():
                page.click("#view")
            page.wait_for_timeout(1000)  # headless Chromium downloads PDFs instead of showing its viewer
        if step == 2:  # record the newest tab, then download the PDF
            page.click("#dl")
            page.wait_for_timeout(1000)
        return {0: "", 1: "", 2: "q"}.get(step, "y")
    monkeypatch.setattr(builtins, "input", user)
    atf.cmd_inspect(None)

    log = [json.loads(line) for f in atf.OUT.glob("capture_*.jsonl") for line in f.read_text(encoding="utf-8").splitlines()]
    pages = [r for r in log if "url" in r and "controls" in r]
    pdfs = [r for r in log if r.get("event", "").startswith("pdf_")]
    assert len(pages) == 2 and "900000001" in pages[0]["pageText"] and len(pages[1]["controls"]) < len(pages[0]["controls"])  # 2nd record = the new tab
    assert len(pdfs) >= 2 and "pdf_download" in {r["event"] for r in pdfs}
    assert all((atf.OUT / "captures" / r["saved"].split("/")[-1].split("\\")[-1]).read_bytes() == pdf for r in pdfs)
