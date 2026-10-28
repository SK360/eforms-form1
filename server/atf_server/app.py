"""The inventory server's HTTP API and web page (FastAPI).

Phase 1 is read-only apart from pairing: the page (the same phone.html the PC serves with Inventory > Open on my
phone), the inventory, a row's history, approval PDFs, a CSV export, and Devices. Every request except the page itself,
/api/hello, /api/pair and /s/<serial> needs a device key: `Authorization: Bearer <key>` (the app) or the cookie a
browser gets when it pairs. Request logs name the method, path, device and status, never keys or bodies.
"""
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from . import __version__, db

API_VERSION = 1
COOKIE = "atf_device"
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; "
       "frame-ancestors 'none'")
log = logging.getLogger("atf_server")


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


def create_app(data_dir, page=None, name="ATF inventory", fingerprint=""):
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
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.middleware("http")
    async def guard_and_log(request: Request, call_next):
        # Writes must be JSON: a form on another site can't send that without the browser asking first (CSRF).
        if request.method == "POST" and not request.headers.get("content-type", "").startswith("application/json"):
            response = JSONResponse({"error": "Send JSON."}, status_code=415)
        else:
            response = await call_next(request)
        for k, v in (("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store")):
            response.headers.setdefault(k, v)
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

    @app.get("/api/hello")
    def hello(request: Request, conn=Depends(get_db)):
        auth = request.headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get(COOKIE, "")
        return {"server_id": server_id, "name": name, "version": __version__, "api": API_VERSION,
                "paired": db.device_for_key(conn, key) is not None}

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
                "conditions": r["conditions"], "built": r["built"] == "yes", "design": r["design"],
                "pdf": r["status"] == "approved" and db.pdf_for(conn, r["serial_number"]) is not None}
               for r in db.rows(conn)]
        sbrs = [{"serial": r["serial_number"], "caliber": r["caliber"], "length": r["overall_length"], "status": "sbr",
                 "model": r["model"], "barrel": r["barrel_length"], "approved": r["approved_date"],
                 "eform": _last4(r["eform_id"]), "conditions": r["conditions"],
                 "pdf": db.pdf_for(conn, r["serial_number"]) is not None} for r in db.sbrs(conn)]
        return {"suppressors": sup, "sbrs": sbrs, "can_build": False,
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
