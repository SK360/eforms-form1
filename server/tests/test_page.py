"""The server's page in a real browser (a phone-sized Chromium and WebKit = Safari's engine) against the server running
on a real port: pairing, the inventory, history, read-only builds, Devices. Skipped where Playwright isn't installed."""
import socket
import threading
import time

import pytest

from conftest import PAGE, pair_device
from atf_server import db, tls


@pytest.fixture
def running(filled, data_dir, tls_dir):
    import uvicorn
    from atf_server.app import create_app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(data_dir, page=PAGE, ca=tls.ca_path(tls_dir)), host="127.0.0.1",
                                           port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_pair_and_browse(running, filled, engine, ca_fp):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = getattr(pw, engine).launch()
        except playwright.Error:
            pytest.skip(f"Playwright {engine} isn't installed")
        page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
        page.set_default_timeout(5000)
        page.goto(running + "/s/A101")                                  # a per-serial link, before pairing
        page.wait_for_selector("#pairbtn")
        assert "isn't paired" in page.inner_text("#listView") and not page.is_visible("#detailView")
        page.fill("#code", "000000")
        page.click("#pairbtn")
        page.wait_for_selector("#toast.show")
        assert "No pairing code" in page.inner_text("#toast")
        code, _ = db.new_pair_code(filled)
        page.fill("#code", code)
        page.fill("#devname", "Test phone")
        page.click("#pairbtn")
        page.wait_for_selector("#detailView .big")                     # paired: the page, opened at A101
        assert page.url.endswith("#A101") and page.is_visible("#detailView")
        detail = page.inner_text("#detailView")
        assert "PCC Short" in detail and "Read-only on the server for now" in detail and page.is_disabled("#sw")
        page.wait_for_selector("#hist .kv")
        assert "Added (import)" in page.inner_text("#hist")
        page.click("#back")
        page.wait_for_selector("#listView", state="visible")
        assert page.inner_text("#sum").startswith("38 suppressors · 8 built · 17 not built · 2 SBRs")
        page.fill("#q", "130")
        assert page.locator(".row .serial").all_inner_texts() == ["A130"]
        page.fill("#q", "")
        assert "Export CSV" in page.inner_text("#foot")

        pair_device(filled, "Old phone")
        page.click("#devlink")                                          # Devices
        page.wait_for_selector(".devs .kv")
        assert "Test phone (this one)" in page.inner_text(".devs") and ca_fp in page.inner_text("#detailView")
        assert page.locator("#trust").count() == 0                     # plain HTTP here: nothing to trust
        page.click("#addDevice")
        page.wait_for_selector("#paircode")
        assert len(page.inner_text("#paircode")) == 6
        old = page.locator(".devs .kv", has_text="Old phone").locator("button")
        old.click()
        assert old.inner_text() == "Tap again to remove"                # asks on the page first
        old.click()
        page.wait_for_function("() => !document.querySelector('.devs').innerText.includes('Old phone')")
        assert [d["name"] for d in db.devices(filled) if not d["revoked_at"]] == ["Test phone"]
        page.go_back()
        page.wait_for_selector("#listView", state="visible")

        mine = next(d for d in db.devices(filled) if d["name"] == "Test phone")
        db.revoke(filled, mine["id"], "test")                           # removed elsewhere: back to pairing
        page.reload()
        page.wait_for_selector("#pairbtn")
        browser.close()


def test_trust_this_server_over_https(https_running, ca_fp):
    """At the server's own HTTPS address (no tailnet), the pairing screen offers its CA to trust once."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(ignore_https_errors=True, viewport={"width": 390, "height": 844})
        page.set_default_timeout(5000)
        page.goto(https_running)
        page.wait_for_selector("#trust .fp", state="attached")         # folded; the fingerprint comes from /api/hello
        assert page.text_content("#trust .fp") == ca_fp
        page.click("#trust summary")
        assert "Certificate Trust Settings" in page.inner_text("#trust")
        with page.expect_download() as d:
            page.click("#cadl")
        assert tls.fingerprint_pem(open(d.value.path()).read()) == ca_fp
        browser.close()


@pytest.fixture
def https_running(filled, data_dir, tls_dir):
    import uvicorn
    from atf_server.app import create_app
    cert, key = tls.ensure_certificate(tls_dir)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(data_dir, page=PAGE, ca=tls.ca_path(tls_dir)), host="127.0.0.1",
                                           port=port, log_level="warning", ssl_certfile=str(cert), ssl_keyfile=str(key)))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"https://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)
