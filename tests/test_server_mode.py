"""The app with an inventory server (phase 2: reads only), against a small fake server over HTTPS with a test-only
certificates: test_ca*.pem = a CA and a server certificate it signed (servers now), test_server_*.pem = a self-signed
one (servers from before their CA). The real server's side of the same calls is in server/tests."""
import json
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import atf, pick, queue, ui
import remote

FIX = Path(__file__).parent / "fixtures"
SID = "f" * 32
ROWS = [{"caliber": "22", "serial_number": "A101", "overall_length": "12", "status": "approved", "application_number": "900000101",
         "filed_date": "08/10/2026", "approved_date": "10/15/2026", "eform_id": "1000101", "control_number": "", "conditions": "",
         "built": "yes", "design": "PCC Short", "version": 1},
        {"caliber": "9", "serial_number": "A136", "overall_length": "12", "status": "", "application_number": "", "filed_date": "",
         "approved_date": "", "eform_id": "", "control_number": "", "conditions": "", "built": "", "design": "", "version": 1}]
SBRS = [{"serial_number": "R1", "caliber": "9", "model": "PCC", "barrel_length": "8", "overall_length": "24",
         "approved_date": "09/02/2026", "eform_id": "2000001", "control_number": "", "conditions": ""}]
QUIT = lambda kw: "quit"  # noqa: E731


CA = (FIX / "test_ca.pem").read_text()


def _fingerprint(name="test_ca.pem"):
    return remote._fingerprint(ssl.PEM_cert_to_DER_cert((FIX / name).read_text()))


@pytest.fixture
def fake():
    yield from _fake(own_ca=True)


@pytest.fixture
def old_fake():
    """A server from before its CA: a self-signed certificate and no /ca.crt."""
    yield from _fake(own_ca=False)


def _fake(own_ca):
    """A fake inventory server on 127.0.0.1 (HTTPS). Pairing code 123456 gives key "k1" (device 7)."""
    state = {"calls": [], "server_id": SID}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, kind="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authed(self):
            if self.headers.get("Authorization") == "Bearer k1":
                return True
            self._send(401, {"error": "not paired", "pair": True})

        def do_GET(self):
            state["calls"].append(("GET", self.path))
            if self.path == "/api/hello":
                return self._send(200, {"server_id": state["server_id"], "name": "Test server", "version": "0.1.0", "api": 1})
            if self.path == "/ca.crt" and own_ca:
                return self._send(200, CA.encode(), "application/x-x509-ca-cert")
            if not self._authed():
                return
            if self.path == "/api/rows":
                return self._send(200, {"suppressors": ROWS, "sbrs": SBRS})
            if self.path == "/pdf/A101":
                return self._send(200, b"%PDF-1.4 server copy", "application/pdf")
            self._send(404, {"error": "not found"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append(("POST", self.path, body))
            if self.path == "/api/pair":
                if body.get("code") != "123456":
                    return self._send(400, {"error": "That code is wrong."})
                return self._send(200, {"key": "k1", "device_id": 7, "server_id": state["server_id"]})
            if self._authed() and self.path == "/api/devices/7/revoke":
                return self._send(200, {"ok": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    name = "test_ca_server" if own_ca else "test_server"
    ctx.load_cert_chain(FIX / f"{name}_cert.pem", FIX / f"{name}_key.pem")
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["address"] = f"https://127.0.0.1:{server.server_address[1]}"
    state["server"] = server
    yield state
    server.shutdown()
    server.server_close()


def _dead_address():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"https://127.0.0.1:{s.getsockname()[1]}"


def connect(fake, addresses=None):
    atf.save_server_settings({"server_id": SID, "name": "Test server", "addresses": addresses or [fake["address"]],
                              "fingerprint": _fingerprint(), "ca": CA, "key": "k1", "device_id": 7})


def test_addresses():
    assert remote.normalize("192.168.1.138") == "https://192.168.1.138:8765"
    assert remote.normalize(" atf-inventory.tail1234.ts.net/ ") == "https://atf-inventory.tail1234.ts.net:443"
    assert remote.normalize("https://inv.home.arpa") == "https://inv.home.arpa:443"
    assert remote.normalize("10.0.0.5:9000") == "https://10.0.0.5:9000"
    with pytest.raises(remote.ServerError):
        remote.normalize("http://192.168.1.138")


def test_probe_and_pair(fake):
    found = remote.probe(fake["address"])
    assert found["fingerprint"] == _fingerprint() and found["ca"] == CA and found["server_id"] == SID and found["api"] == 1
    with pytest.raises(remote.ServerError, match="wrong"):
        remote.pair(found, "000000", "PC")
    assert remote.pair(found, " 123456 ", "PC") == {"key": "k1", "device_id": 7, "server_id": SID}
    with pytest.raises(remote.ServerError, match="No inventory server"):
        remote.probe(_dead_address(), timeout=2)


def test_only_the_paired_server_is_used(fake):
    other_ca = (FIX / "test_server_cert.pem").read_text()             # a certificate this server's wasn't signed by
    with pytest.raises(remote.Unreachable, match="isn't the one this computer paired with"):
        remote.Server(SID, [fake["address"]], "AA:BB", "k1", ca=other_ca).rows()
    first_dead = remote.Server(SID, [_dead_address(), fake["address"]], _fingerprint(), "k1", timeout=2, ca=CA)
    assert first_dead.rows() == (ROWS, SBRS) and first_dead.address == fake["address"]
    fake["server_id"] = "0" * 32                                   # a different server at that address now
    remote.Server.working.clear()
    with pytest.raises(remote.Unreachable, match="different server"):
        remote.Server(SID, [fake["address"]], _fingerprint(), "k1", ca=CA).rows()
    fake["server_id"] = SID
    with pytest.raises(remote.ServerError, match="doesn't know this computer"):
        remote.Server(SID, [fake["address"]], _fingerprint(), "wrong key", ca=CA).rows()


def test_an_older_server_is_pinned_by_its_certificate(old_fake):
    found = remote.probe(old_fake["address"])
    assert found["ca"] is None and found["fingerprint"] == _fingerprint("test_server_cert.pem")
    assert remote.pair(found, "123456", "PC")["key"] == "k1"
    assert remote.Server(SID, [old_fake["address"]], found["fingerprint"], "k1").rows() == (ROWS, SBRS)
    with pytest.raises(remote.Unreachable, match="isn't the one"):
        remote.Server(SID, [old_fake["address"]], "AA:BB", "k1").rows()


def test_reads_come_from_the_server(fake, configured):
    queue(("S1", "9"))                                             # this computer's own inventory
    local = atf.SUPP.read_bytes()
    connect(fake)
    assert [r["serial_number"] for r in atf.load_rows()] == ["A101", "A136"]
    assert atf.load_rows()[1]["status"] == "" and atf.is_built(atf.load_rows()[0])
    assert [r["serial_number"] for r in atf.load_sbrs()] == ["R1"]
    assert atf.SERVER_STATE["online"] and atf.SERVER_STATE["address"] == fake["address"]
    with pytest.raises(atf.ServerReadOnly):
        atf.save_rows(atf.load_rows())
    with pytest.raises(atf.ServerReadOnly):
        atf.record_builds({"A101": "Other"})
    assert atf.backfill_filed_dates() is not None
    pdf = atf.approved_pdf_for(atf.load_rows()[0])
    assert pdf.read_bytes() == b"%PDF-1.4 server copy" and pdf.parent == atf.OUT / "server-pdfs"
    assert atf.approved_pdf_for(atf.load_rows()[1]) is None
    assert atf.SUPP.read_bytes() == local                          # never touched
    atf.forget_server()
    assert [r["serial_number"] for r in atf.load_rows()] == ["S1"]


def test_offline_shows_the_last_data_seen(fake, configured):
    connect(fake)
    atf.load_rows()
    fake["server"].shutdown()
    fake["server"].server_close()
    atf._FETCHED.clear()
    remote.Server.working.clear()
    rows = atf.load_rows()
    assert [r["serial_number"] for r in rows] == ["A101", "A136"]
    assert atf.SERVER_STATE["online"] is False and atf.SERVER_STATE["seen"] and "No answer" in atf.SERVER_STATE["problem"]
    assert atf.approved_pdf_for(rows[0]) is None                   # never fetched, can't be now
    atf._server_cache_path().unlink()
    atf._FETCHED.clear()
    with pytest.raises(remote.ServerError):
        atf.load_rows()


def test_check_setup_names_the_server(fake, configured):
    connect(fake)
    line = next(c for c in atf.check_setup() if c[1] == "Inventory server")
    assert line[0] == "ok" and "2 suppressors, 1 SBRs" in line[2]
    connect(fake, [_dead_address()])
    remote.Server.working.clear()
    line = next(c for c in atf.check_setup() if c[1] == "Inventory server")
    assert line[0] == "fail" and "No answer" in line[2]


def test_menu_connects_shows_the_server_and_switches_back(fake, configured, answers, screen):
    queue(("S1", "9"))
    local = atf.SUPP.read_bytes()
    answers(pick("Settings"), pick("Where your inventory lives"), "connect", fake["address"], True, "", "PC", "123456",
            lambda kw: "quit" if not any(str(c.title).startswith(("Add suppressors", "File ", "Check approvals", "Advanced"))
                                         for c in kw["choices"]) else "unexpected")
    ui.menu()
    out = screen.getvalue()
    assert _fingerprint() in out and "Connected:" in out and "2 suppressors, 1 SBRs" in out
    assert "A101" in out and "On your server" in out and "read-only" in out
    saved = atf.server_settings()
    assert saved["key"] == "k1" and saved["addresses"] == [fake["address"]] and saved["fingerprint"] == _fingerprint()
    assert saved["ca"] == CA and "certificate authority fingerprint" in out
    assert atf.SUPP.read_bytes() == local

    answers(pick("Inventory"), lambda kw: "back" if not any(str(c.title).startswith(("Record builds", "Edit or remove",
                                                                                     "Open on my phone")) for c in kw["choices"]) else "unexpected",
            pick("Settings"), pick("Where your inventory lives"), "local", True, QUIT)
    ui.menu()
    assert atf.server_settings() is None and ("POST", "/api/devices/7/revoke", {}) in fake["calls"]
    assert "This computer's inventory is used again" in screen.getvalue()
    assert [r["serial_number"] for r in atf.load_rows()] == ["S1"]


def test_menu_refuses_a_certificate_that_doesnt_match(fake, configured, answers, screen):
    answers(pick("Settings"), pick("Where your inventory lives"), "connect", fake["address"], False, QUIT)
    ui.menu()
    assert atf.server_settings() is None and "Not connected" in screen.getvalue()
    assert not any(c[0] == "POST" for c in fake["calls"])          # never paired


def test_menu_when_the_server_is_gone(fake, configured, answers, screen):
    connect(fake, [_dead_address()])
    answers(QUIT)
    ui.menu()
    assert "can't be reached" in screen.getvalue()
