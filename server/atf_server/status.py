"""How the server itself is doing (GET /api/status, the page's Server screen, the app's Check my setup) and a whole
backup as one file (GET /api/backup; `restore` takes it back).

Status: version and whether a newer release is out, backups, the certificate's expiry, storage, approval PDFs the
database lists but the disk doesn't have, and `problems`: the few lines worth acting on, worded once here so the page
and the app say the same. Nothing about builds (no time of anything done to the inventory is reported here).

A backup file = a ZIP of inventory.db (SQLite's online backup, safe while serving) and every approval PDF, stored
under its checksum. The daily backups in <data>/backups are the database only and live on the server's own disk;
this one can leave it. The server's certificates and keys are NOT in it (a restored server on new hardware makes its
own: devices pair and trust it again).
"""
import hashlib
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import db, update

BACKUP_AGE_DAYS = 2   # the server makes one a day: older than this = the daily backup isn't working
LOW_DISK = 200 * 1024 * 1024
CERT_WARN_DAYS = 30   # it's renewed at a start once under tls.RENEW_DAYS; this close = it never was restarted
SHA_PDF = re.compile(r"approved/([0-9a-f]{64})\.pdf")


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _backups(folder):
    files = sorted(Path(folder).glob("inventory-*.db"), key=lambda p: p.stat().st_mtime) if Path(folder).is_dir() else []
    newest = files[-1] if files else None
    return {"count": len(files), "newest": newest.name if newest else None,
            "newest_at": _iso(newest.stat().st_mtime) if newest else None,
            "age_days": int((datetime.now().timestamp() - newest.stat().st_mtime) // 86400) if newest else None}


def _certificate(tls_dir):
    """The server certificate's expiry (None on plain HTTP or when it can't be read)."""
    path = Path(tls_dir) / "cert.pem"
    if not path.is_file():
        return None
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(path.read_bytes())
        left = cert.not_valid_after_utc - datetime.now(timezone.utc)
        return {"expires": cert.not_valid_after_utc.strftime("%Y-%m-%d"), "days_left": left.days}
    except Exception:
        return None


def status(data_dir, conn, version, api):
    data_dir = Path(data_dir)
    pdf_dir = data_dir / "approved"
    listed = db.pdfs(conn)
    missing = sorted({p["serial"] or p["eform_id"] for p in listed if not (pdf_dir / f"{p['sha256']}.pdf").is_file()})
    on_disk = [p for p in pdf_dir.glob("*.pdf")] if pdf_dir.is_dir() else []
    db_bytes = sum(p.stat().st_size for p in data_dir.glob("inventory.db*"))
    backups, cert = _backups(data_dir / "backups"), _certificate(data_dir / "tls")
    latest = update.available(data_dir, version)
    out = {"version": version, "api": api, "schema": db.SCHEMA_VERSION, "install": update.kind(),
           "update": {"version": latest["version"], "url": latest["url"]} if latest else None,
           "counts": {"suppressors": len(db.rows(conn)), "other": len(db.sbrs(conn)), "pdfs": len(listed),
                      "devices": sum(1 for d in db.devices(conn) if not d["revoked_at"])},
           "backups": backups, "certificate": cert,
           "storage": {"database_bytes": db_bytes, "pdf_bytes": sum(p.stat().st_size for p in on_disk),
                       "free_bytes": shutil.disk_usage(data_dir).free},
           "missing_pdfs": missing}
    problems = []
    if backups["count"] == 0:
        problems.append("No backup has been made yet (the server makes one a day).")
    elif backups["age_days"] >= BACKUP_AGE_DAYS:
        problems.append(f"The newest backup is {backups['age_days']} days old: the daily backup isn't working (see the server's log).")
    if cert and cert["days_left"] <= CERT_WARN_DAYS:
        problems.append(("The server's certificate has expired" if cert["days_left"] < 0 else
                         f"The server's certificate expires in {cert['days_left']} days") + ": restart the server to renew it.")
    if out["storage"]["free_bytes"] < LOW_DISK:
        problems.append(f"The server's disk is almost full ({out['storage']['free_bytes'] // (1024 * 1024)} MB free).")
    if missing:
        problems.append(f"{len(missing)} approval PDF{'s are' if len(missing) > 1 else ' is'} missing from the server's disk: "
                        + ", ".join(missing[:5]) + (" …" if len(missing) > 5 else "") + ". Check approvals sends them again.")
    out["problems"] = problems
    return out


# ---------- one file with everything ----------
README = """ATF inventory server backup, made {made} by server version {version}.

inventory.db   the database (inventory, history, paired devices)
approved/      every approval PDF, named by its SHA-256 checksum

To restore, on the server:   atf-server restore <this file>      (Docker: python -m atf_server restore <this file>)
The database there is backed up first. The server's certificates aren't in this file: a server on new hardware makes
its own, and each device pairs with it again.
"""


def write_backup(conn, pdf_dir, dest, version):
    """The whole server as one ZIP at `dest` (PDFs stored, not compressed: they already are). Returns its PDF count."""
    dest, pdf_dir = Path(dest), Path(pdf_dir)
    snap = dest.with_suffix(".db.tmp")
    target = sqlite3.connect(str(snap))
    try:
        conn.backup(target)
    finally:
        target.close()
    n = 0
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z:
            z.writestr("README.txt", README.format(made=datetime.now().strftime("%Y-%m-%d %H:%M"), version=version))
            z.write(snap, "inventory.db", compress_type=zipfile.ZIP_DEFLATED)
            for sha in sorted({p["sha256"] for p in db.pdfs(conn)}):
                if (pdf_dir / f"{sha}.pdf").is_file():
                    z.write(pdf_dir / f"{sha}.pdf", f"approved/{sha}.pdf")
                    n += 1
    finally:
        snap.unlink(missing_ok=True)
    return n


def restore_backup(conn, source, data_dir):
    """Server console: put a write_backup file back. Its PDFs are checked against their checksums and added where
    missing (none is replaced or removed), then the database is restored like a plain backup (the current one is
    backed up first). Returns (the saved current database, PDFs added)."""
    source, data_dir = Path(source), Path(data_dir)
    with zipfile.ZipFile(source) as z:
        names = z.namelist()
        if "inventory.db" not in names:
            raise db.Refused(f"{source.name} isn't an inventory backup.")
        tmp = data_dir / "restore.db.tmp"
        try:
            with z.open("inventory.db") as src, tmp.open("wb") as out:
                shutil.copyfileobj(src, out)
            wanted = [(name, m.group(1)) for name in names for m in [SHA_PDF.fullmatch(name)]
                      if m and not (data_dir / "approved" / f"{m.group(1)}.pdf").is_file()]
            for name, sha in wanted:  # all checked before any is written
                if hashlib.sha256(z.read(name)).hexdigest() != sha:
                    raise db.Refused(f"{source.name} is damaged (an approval PDF doesn't match its checksum); nothing was restored.")
            saved = db.restore(conn, tmp, data_dir / "backups")
            for name, sha in wanted:
                dest = data_dir / "approved" / f"{sha}.pdf"
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = dest.with_suffix(".tmp")
                part.write_bytes(z.read(name))
                part.replace(dest)
            return saved, len(wanted)
        finally:
            tmp.unlink(missing_ok=True)
