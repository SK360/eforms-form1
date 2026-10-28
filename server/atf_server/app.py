"""The inventory server's HTTP API and web page (FastAPI).

The page (the same phone.html the PC serves with Inventory > Open on my phone), the inventory, a row's history,
approval PDFs, a CSV export, Devices, and the named operations (db.OPERATIONS: builds and queue edits; filing and
approvals come later). /api/events tells open pages when anything changed (server-sent events). Every request except the page itself,
/api/hello, /api/pair and /s/<serial> needs a device key: `Authorization: Bearer <key>` (the app) or the cookie a
browser gets when it pairs. Request logs name the method, path, device and status, never keys or bodies.
"""
import asyncio
import base64
import binascii
import hashlib
import logging
import secrets
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from . import __version__, db, tls

API_VERSION = 1
COOKIE = "atf_device"
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; "
       "frame-ancestors 'none'")
log = logging.getLogger("atf_server")
PRIVATE_PATHS = {f"/api/ops/{name}" for name in db.PRIVATE_OPS}


def find_page():
    """phone.html: copied next to this package in the Docker image, else the repo's src/phone.html."""
    here = Path(__file__).resolve().parent
    for p in (here / "phone.html", here.parents[1] / "src" / "phone.html"):
        if p.is_file():
            return p
    raise FileNotFoundError("phone.html not found (src/phone.html in the repo)")


class Unpaired(Exception):
    pass


def _last4(v):
    return f"…{v[-4:]}" if v else ""


def create_app(data_dir, page=None, name="ATF inventory", ca=None):
    """ca: the server's CA certificate (tls.py), offered at /ca.crt for phones to trust; None = plain HTTP (tests)."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path, pdf_dir = data_dir / "inventory.db", data_dir / "approved"
    conn = db.connect(db_path)
    try:
        db.migrate(conn, data_dir / "backups")
        server_id = db.meta(conn, "server_id")
    finally:
        conn.close()
    page = Path(page) if page else find_page()
    ca_pem = Path(ca).read_text(encoding="ascii") if ca else ""
    fingerprint = tls.fingerprint_pem(ca_pem) if ca_pem else ""
    app = FastAPI(title=name, version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    @contextmanager
    def opened():
        c = db.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    def get_db():
        with opened() as c:
            yield c

    def device(request: Request, conn=Depends(get_db)):
        auth = request.headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get(COOKIE, "")
        d = db.device_for_key(conn, key)
        if d is None:
            raise Unpaired()
        request.state.device = d["name"]
        return d

    @app.exception_handler(Unpaired)
    def unpaired(request, exc):
        return JSONResponse({"error": "This device isn't paired with the inventory server.", "pair": True}, status_code=401)

    @app.exception_handler(db.Refused)
    def refused(request, exc):
        if isinstance(exc, db.Conflict):
            return JSONResponse({"error": str(exc), "rows": exc.rows}, status_code=409)
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.middleware("http")
    async def guard_and_log(request: Request, call_next):
        # Writes must be JSON: a form on another site can't send that without the browser asking first (CSRF).
        kind = request.headers.get("content-type", "")
        # (an approval PDF upload is application/pdf: a form on another site can't send that either)
        if request.method == "POST" and not (kind.startswith("application/json")
                                             or (request.url.path == "/api/import/pdf" and kind == "application/pdf")):
            response = JSONResponse({"error": "Send JSON."}, status_code=415)
        else:
            response = await call_next(request)
        for k, v in (("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store")):
            response.headers.setdefault(k, v)
        if request.url.path not in PRIVATE_PATHS:  # a build leaves no record of when, log lines included
            log.info("%s %s %s %s", request.method, request.url.path, response.status_code,
                     getattr(request.state, "device", "-"))
        return response

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Content-Security-Policy": CSP})

    @app.get("/s/{serial}")
    def serial_link(serial: str):
        """A permanent address per suppressor (later printed as a QR label): opens the page at that serial."""
        return RedirectResponse(f"/#{quote(serial, safe='')}", status_code=302)

    @app.get("/ca.crt")
    def ca_certificate():
        """The server's CA, to trust once on a phone (no pairing needed: a certificate is public; its key never leaves
        the server). iPhone: Safari offers it as a profile; Android: Settings > Security > install a CA certificate."""
        if not ca_pem:
            return JSONResponse({"error": "This server runs without HTTPS."}, status_code=404)
        return Response(ca_pem, media_type="application/x-x509-ca-cert",
                        headers={"Content-Disposition": 'attachment; filename="atf-inventory-ca.crt"'})

    @app.get("/api/hello")
    def hello(request: Request, conn=Depends(get_db)):
        auth = request.headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get(COOKIE, "")
        return {"server_id": server_id, "name": name, "version": __version__, "api": API_VERSION,
                "paired": db.device_for_key(conn, key) is not None, "ca_fingerprint": fingerprint}

    @app.post("/api/pair")
    def pair(request: Request, body: dict, conn=Depends(get_db)):
        device_id, key = db.pair(conn, body.get("code", ""), body.get("name", ""))
        request.state.device = str(body.get("name", ""))[:40]
        response = JSONResponse({"device_id": device_id, "key": key, "server_id": server_id})
        response.set_cookie(COOKIE, key, max_age=10 * 365 * 24 * 3600, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https")
        return response

    @app.get("/api/inventory")
    def inventory(me=Depends(device), conn=Depends(get_db)):
        """What the page shows (the same shape as the PC's phone page): application numbers and eForm IDs as last 4."""
        sup = [{"serial": r["serial_number"], "caliber": r["caliber"], "length": r["overall_length"],
                "status": r["status"].lower() or "queued", "filed": r["filed_date"], "approved": r["approved_date"],
                "application": _last4(r["application_number"]), "eform": _last4(r["eform_id"]),
                "conditions": r["conditions"], "built": r["built"] == "yes", "design": r["design"], "version": r["version"],
                "pdf": r["status"] == "approved" and db.pdf_for(conn, r["serial_number"]) is not None}
               for r in db.rows(conn)]
        sbrs = [{"serial": r["serial_number"], "caliber": r["caliber"], "length": r["overall_length"], "status": "sbr",
                 "model": r["model"], "barrel": r["barrel_length"], "approved": r["approved_date"],
                 "eform": _last4(r["eform_id"]), "conditions": r["conditions"],
                 "pdf": db.pdf_for(conn, r["serial_number"]) is not None} for r in db.sbrs(conn)]
        return {"suppressors": sup, "sbrs": sbrs, "can_build": True,
                "server": {"name": name, "id": server_id, "fingerprint": fingerprint, "device": me["name"]}}

    @app.get("/api/rows")
    def all_rows(me=Depends(device), conn=Depends(get_db)):
        """The whole inventory in the app's CSV shape (full numbers), each suppressor with its version."""
        return {"suppressors": db.rows(conn), "sbrs": db.sbrs(conn)}

    @app.get("/api/rows/{serial}")
    def one_row(serial: str, me=Depends(device), conn=Depends(get_db)):
        r = db.row(conn, serial)
        return r if r else JSONResponse({"error": f"No suppressor {serial}."}, status_code=404)

    @app.get("/api/history/{serial}")
    def row_history(serial: str, me=Depends(device), conn=Depends(get_db)):
        return {"serial": serial, "history": db.history(conn, serial)}

    @app.get("/pdf/{serial}")
    def pdf(serial: str, me=Depends(device), conn=Depends(get_db)):
        found = db.pdf_for(conn, serial)
        if not found or not (pdf_dir / f"{found[0]}.pdf").is_file():
            return JSONResponse({"error": "No approval PDF for that serial on the server."}, status_code=404)
        return FileResponse(pdf_dir / f"{found[0]}.pdf", media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{quote(found[1])}"'})

    @app.get("/export.csv")
    def export(me=Depends(device), conn=Depends(get_db)):
        return Response(db.export_csv(conn), media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="inventory_{datetime.now():%Y-%m-%d}.csv"'})

    incoming = data_dir / "incoming"  # approval PDFs uploaded for an import, one request each

    @app.post("/api/import/pdf")
    async def import_pdf(request: Request, me=Depends(device)):
        """One approval PDF for the coming import, streamed to disk (a 137 MB inventory in one request was too much
        for a 512 MB container). Returns its checksum, which the import then names."""
        incoming.mkdir(parents=True, exist_ok=True)
        tmp, digest, size, head = incoming / f"upload-{secrets.token_hex(8)}.tmp", hashlib.sha256(), 0, b""
        try:
            with tmp.open("wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 64 * 1024 * 1024:
                        raise db.Refused("That PDF is too large (over 64 MB).")
                    head = head or chunk[:4]
                    digest.update(chunk)
                    f.write(chunk)
            if head != b"%PDF":
                raise db.Refused("That isn't a PDF.")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(incoming / f"{digest.hexdigest()}.pdf")
        return {"sha256": digest.hexdigest(), "size": size}

    @app.post("/api/import")
    def do_import(body: dict, me=Depends(device), conn=Depends(get_db)):
        """The app's move to the server: the whole inventory at once ({suppressors, sbrs} in the app's CSV shape;
        pdfs: [{eform_id, serial, sha256}] uploaded first with /api/import/pdf, or {data: base64} for small ones),
        into an empty server only, all or nothing."""
        files = []
        for p in body.get("pdfs") or []:
            item = {"eform_id": p.get("eform_id"), "serial": p.get("serial") or ""}
            if "data" in p:
                try:
                    item["data"] = base64.b64decode(p["data"], validate=True)
                except (TypeError, ValueError, binascii.Error):
                    raise db.Refused("An approval PDF in the import couldn't be read.")
            else:
                path = incoming / f"{p.get('sha256')}.pdf"
                if not (len(str(p.get("sha256"))) == 64 and path.is_file()):
                    raise db.Refused(f"The approval PDF for {item['serial'] or item['eform_id']} wasn't uploaded.")
                item["path"] = path
            files.append(item)
        try:
            return db.import_inventory(conn, pdf_dir, body.get("suppressors") or [], body.get("sbrs") or [], files,
                                       device=me["name"])
        finally:
            shutil.rmtree(incoming, ignore_errors=True)  # stored (or refused): the uploads aren't needed any more

    @app.post("/api/approval-pdfs")
    def approval_pdfs(body: dict, me=Depends(device), conn=Depends(get_db)):
        """Check approvals: new approval PDFs uploaded first with /api/import/pdf ([{eform_id, serial, sha256}])."""
        items = []
        for p in body.get("pdfs") or []:
            path = incoming / f"{p.get('sha256')}.pdf"
            if not (len(str(p.get("sha256"))) == 64 and path.is_file()):
                raise db.Refused(f"The approval PDF for {p.get('serial') or p.get('eform_id')} wasn't uploaded.")
            items.append({"eform_id": str(p.get("eform_id") or ""), "serial": str(p.get("serial") or ""), "path": path})
        try:
            return {"added": db.add_approval_pdfs(conn, pdf_dir, items, me["name"])}
        finally:
            for item in items:
                item["path"].unlink(missing_ok=True)

    @app.post("/api/import/undo")
    def undo_import(me=Depends(device), conn=Depends(get_db)):
        """Take back the last import while nothing has changed since (the app found a difference, or a try-out), and
        drop any uploaded PDFs an unfinished move left."""
        shutil.rmtree(incoming, ignore_errors=True)
        db.undo_import(conn, pdf_dir)
        return {"ok": True}

    @app.get("/api/pdfs")
    def list_pdfs(me=Depends(device), conn=Depends(get_db)):
        return {"pdfs": db.pdfs(conn)}

    @app.get("/api/pdfs/{sha}")
    def pdf_file(sha: str, me=Depends(device)):
        path = pdf_dir / f"{sha}.pdf"
        if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha) or not path.is_file():
            return JSONResponse({"error": "No such PDF."}, status_code=404)
        return FileResponse(path, media_type="application/pdf")

    @app.post("/api/ops/{name}")
    def operation(name: str, body: dict, me=Depends(device), conn=Depends(get_db)):
        """One named change (db.OPERATIONS), recorded in the history under this device's name."""
        return db.operate(conn, name, body, me["name"])

    @app.get("/api/events")
    async def events(request: Request, me=Depends(device)):
        """Server-sent events: "change" whenever anything changed (db.last_change: any device, the console too), so
        open pages reload. A comment line every 15 s keeps proxies from closing it."""
        async def stream():
            with opened() as c:
                seen = db.last_change(c)
            yield f"event: hello\ndata: {seen}\n\n"
            quiet = 0
            while not await request.is_disconnected():
                await asyncio.sleep(1)
                with opened() as c:
                    now = db.last_change(c)
                if now != seen:
                    seen, quiet = now, 0
                    yield f"event: change\ndata: {now}\n\n"
                else:
                    quiet += 1
                    if quiet >= 15:
                        quiet = 0
                        yield ": still here\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/devices")
    def devices(me=Depends(device), conn=Depends(get_db)):
        return {"devices": db.devices(conn), "you": me["id"], "fingerprint": fingerprint}

    @app.post("/api/devices/code")
    def pair_code(me=Depends(device), conn=Depends(get_db)):
        code, expires = db.new_pair_code(conn)
        return {"code": code, "expires": expires, "minutes": db.PAIR_MINUTES}

    @app.post("/api/devices/{device_id}/revoke")
    def revoke(device_id: int, me=Depends(device), conn=Depends(get_db)):
        db.revoke(conn, device_id, me["name"])
        return {"ok": True}

    return app
