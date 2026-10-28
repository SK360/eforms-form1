"""Your inventory on your phone, on your home Wi-Fi: ./atf serve (menu: Inventory > Open on my phone).

A small web server in this program, only while you leave it running. Your phone opens the page from a QR code; the
link carries a random key made for this run, and every request without it is refused. The page shows the inventory
(suppressors and SBRs, approval details, the approval PDF) and can do ONE thing: mark an approved suppressor built
(with its design) or clear that, through the same atf.record_builds / clear_build as the menu (approved rows only).
Nothing goes to the internet; the page loads nothing from anywhere else.
"""
import hmac
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import atf

PAGE = Path(__file__).with_name("phone.html")
PORT = 8765


def lan_address():
    """This computer's address on the home network (no packet is sent: UDP connect only picks the route)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _last4(v):
    return f"…{v[-4:]}" if v else ""


def inventory():
    """What the page shows. Application numbers and eForm IDs as their last 4 digits (like the menu)."""
    sup = [{"serial": r["serial_number"], "caliber": r["caliber"], "length": r["overall_length"], "status": r["status"] or "queued",
            "filed": r["filed_date"], "approved": r["approved_date"], "application": _last4(r["application_number"]),
            "eform": _last4(r["eform_id"]), "conditions": r["conditions"], "built": atf.is_built(r), "design": r["design"],
            "pdf": r["status"] == "approved" and atf.approved_pdf_for(r) is not None}
           for r in sorted(atf.load_rows(), key=lambda r: atf.serial_key(r["serial_number"]))]
    sbrs = [{"serial": r["serial_number"], "caliber": r["caliber"], "length": r["overall_length"], "status": "sbr",
             "model": r["model"], "barrel": r["barrel_length"], "approved": r["approved_date"], "eform": _last4(r["eform_id"]),
             "conditions": r["conditions"], "pdf": atf.approved_pdf_for(r) is not None}
            for r in sorted(atf.load_sbrs(), key=lambda r: atf.serial_key(r["serial_number"]))]
    return {"suppressors": sup, "sbrs": sbrs}


def _row(serial):
    return next((r for r in atf.load_rows() + atf.load_sbrs() if r["serial_number"].upper() == serial.upper()), None)


def make_handler(key, on_event=lambda text: None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "atf"

        def log_message(self, *args):  # the default log would print the link, key included
            pass

        def _send(self, code, body, kind="application/json", extra=()):
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
            self.send_response(code)
            for k, v in (("Content-Type", kind), ("Content-Length", str(len(data))), ("Cache-Control", "no-store"),
                         ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"), *extra):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _allowed(self, url):
            given = (parse_qs(url.query).get("k") or [""])[0] or self.headers.get("X-Key", "")
            if hmac.compare_digest(given.encode(), key.encode()):
                return True
            self._send(403, {"error": "This link has expired: open the new one from the helper."})
            return False

        def do_GET(self):
            url = urlparse(self.path)
            if not self._allowed(url):
                return
            if url.path == "/":
                csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:"
                return self._send(200, PAGE.read_text(encoding="utf-8"), "text/html; charset=utf-8",
                                  [("Content-Security-Policy", csp)])
            if url.path == "/api/inventory":
                return self._send(200, inventory())
            if url.path.startswith("/pdf/"):
                row = _row(unquote(url.path[5:]))
                path = atf.approved_pdf_for(row) if row else None
                if path is None:
                    return self._send(404, {"error": "No saved approval PDF for that serial yet (Check approvals saves them)."})
                return self._send(200, path.read_bytes(), "application/pdf",
                                  [("Content-Disposition", f'inline; filename="{path.name}"')])
            self._send(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            if not self._allowed(url):
                return
            if url.path != "/api/build":
                return self._send(404, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 10000)) or b"{}")
                serial, built = str(body["serial"]), bool(body["built"])
                design = str(body.get("design") or "").strip()[:200]
            except (ValueError, KeyError, TypeError):
                return self._send(400, {"error": "bad request"})
            row = _row(serial)
            if row is None or row.get("status") != "approved":
                return self._send(400, {"error": f"{serial} isn't approved, so it can't be marked built."})
            try:
                if built:
                    atf.record_builds({row["serial_number"]: design})
                    on_event(f"{row['serial_number']} marked built" + (f" ({design})" if design else ""))
                else:
                    atf.clear_build(row["serial_number"])
                    on_event(f"{row['serial_number']} no longer marked built")
            except (ValueError, OSError) as e:  # OSError: the inventory is open in Excel (Windows)
                return self._send(409, {"error": f"Not saved: {e}"})
            self._send(200, {"ok": True})
    return Handler


def start(port=PORT, on_event=lambda text: None, host="0.0.0.0"):
    """Start serving in a background thread. Returns (server, link). Stop with server.shutdown()."""
    key = secrets.token_urlsafe(16)
    try:
        server = ThreadingHTTPServer((host, port), make_handler(key, on_event))
    except OSError:  # the usual port is taken: any free one
        server = ThreadingHTTPServer((host, 0), make_handler(key, on_event))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    address = "127.0.0.1" if host == "127.0.0.1" else lan_address()
    return server, f"http://{address}:{server.server_address[1]}/?k={key}"


def qr_text(link):
    """The link as a QR code made of text blocks, or None without the segno package (an older install)."""
    try:
        import io
        import segno
    except ImportError:
        return None
    out = io.StringIO()
    segno.make(link, error="l").terminal(out=out, compact=True)
    return out.getvalue()


def cmd_serve(args):
    server, link = start(on_event=lambda text: print(f"  phone: {text}"))
    print(qr_text(link) or "(Run the installer again to show a QR code here.)")
    print(f"Open on your phone (same Wi-Fi): {link}\nCtrl-C stops it.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
