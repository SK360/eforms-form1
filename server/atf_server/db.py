"""The inventory database: one SQLite file (WAL mode), rules enforced by constraints and triggers.

Once the app is connected, the server is the only copy of the inventory (design doc: "Inventory server design"). Every
write is a named operation that runs in one transaction together with its history row; there is no "replace the
whole inventory" call. Phase 1 has one write: import, into an empty server. The schema version is PRAGMA user_version.
"""
import csv
import hashlib
import hmac
import io
import json
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The app's CSV columns (src/atf.py COLS / SBR_COLS): import and export are straight copies of the app's files.
COLS = ["caliber", "serial_number", "overall_length", "status", "application_number", "filed_date", "approved_date",
        "eform_id", "control_number", "conditions", "built", "design"]
SBR_COLS = ["serial_number", "caliber", "model", "barrel_length", "overall_length", "approved_date", "eform_id",
            "control_number", "conditions"]
STATUSES = ("queued", "pending", "filed", "approved", "disapproved", "skip")  # "" in the app's CSV = queued
PAIR_MINUTES, PAIR_TRIES = 5, 5
ISO = "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"

MIGRATIONS = {1: f"""
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE suppressors (
  id INTEGER PRIMARY KEY,
  serial TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (trim(serial) <> ''),
  caliber TEXT NOT NULL DEFAULT '',
  overall_length TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
  application_number TEXT UNIQUE CHECK (application_number IS NULL OR application_number <> ''),
  filed_date TEXT CHECK (filed_date IS NULL OR filed_date GLOB {ISO}),
  approved_date TEXT CHECK (approved_date IS NULL OR approved_date GLOB {ISO}),
  eform_id TEXT NOT NULL DEFAULT '',
  control_number TEXT NOT NULL DEFAULT '',
  conditions TEXT NOT NULL DEFAULT '',
  built INTEGER NOT NULL DEFAULT 0 CHECK (built IN (0, 1)),
  design TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  CHECK (built = 0 OR status = 'approved'));
CREATE TRIGGER suppressors_version AFTER UPDATE ON suppressors WHEN NEW.version <= OLD.version
BEGIN UPDATE suppressors SET version = OLD.version + 1 WHERE id = NEW.id; END;
CREATE TABLE sbrs (
  id INTEGER PRIMARY KEY,
  serial TEXT NOT NULL CHECK (trim(serial) <> ''),
  caliber TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  barrel_length TEXT NOT NULL DEFAULT '',
  overall_length TEXT NOT NULL DEFAULT '',
  approved_date TEXT CHECK (approved_date IS NULL OR approved_date GLOB {ISO}),
  eform_id TEXT UNIQUE CHECK (eform_id IS NULL OR eform_id <> ''),
  control_number TEXT NOT NULL DEFAULT '',
  conditions TEXT NOT NULL DEFAULT '');
CREATE TABLE approval_pdfs (
  id INTEGER PRIMARY KEY,
  eform_id TEXT UNIQUE CHECK (eform_id IS NULL OR eform_id <> ''),
  serial TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  size INTEGER NOT NULL,
  received_at TEXT NOT NULL);
CREATE TABLE history (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  device TEXT NOT NULL,
  operation TEXT NOT NULL,
  serial TEXT,
  before TEXT,
  after TEXT,
  request_id TEXT);
CREATE INDEX history_serial ON history (serial COLLATE NOCASE);
CREATE TRIGGER history_no_update BEFORE UPDATE ON history BEGIN SELECT RAISE(ABORT, 'history is append-only'); END;
CREATE TRIGGER history_no_delete BEFORE DELETE ON history BEGIN SELECT RAISE(ABORT, 'history is append-only'); END;
CREATE TABLE devices (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  last_seen TEXT,
  revoked_at TEXT);
"""}
SCHEMA_VERSION = max(MIGRATIONS)


class Refused(Exception):
    """An operation the rules don't allow (the API answers 400/409 with this text)."""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def serial_key(serial):
    """Natural order, as in the app: S9 < S10 < S100, letters compared case-insensitively."""
    return [int(t) if t.isdigit() else t.upper() for t in re.split(r"(\d+)", str(serial))]


def to_iso(date):
    """MM/DD/YYYY (the app's CSV) -> YYYY-MM-DD; "" -> None. Anything else is refused."""
    date = (date or "").strip()
    if not date:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise Refused(f"not a date: {date!r} (expected MM/DD/YYYY)")


def to_us(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%m/%d/%Y") if iso else ""


# ---------- Opening, migrating, backing up ----------

def connect(path):
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _statements(script):
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ""


def migrate(conn, backups=None):
    """Bring the schema up to date, each migration in one transaction. An existing database is backed up first; one
    made by a newer server is refused (this server would not know its rules)."""
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > SCHEMA_VERSION:
        raise Refused(f"The database is from a newer server (schema {have}, this server knows {SCHEMA_VERSION}). "
                      "Update the server.")
    if 0 < have < SCHEMA_VERSION and backups:
        backup(conn, backups, label=f"before-schema-{SCHEMA_VERSION}")
    for version in range(have + 1, SCHEMA_VERSION + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _statements(MIGRATIONS[version]):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    if not meta(conn, "server_id"):
        set_meta(conn, "server_id", uuid.uuid4().hex)


def backup(conn, folder, label=None, keep=30):
    """A consistent copy made with SQLite's online backup (safe while the server runs). Keeps the newest `keep`."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"inventory-{label or datetime.now().strftime('%Y-%m-%d')}.db"
    target = sqlite3.connect(str(dest))
    try:
        conn.backup(target)
    finally:
        target.close()
    for old in sorted(folder.glob("inventory-*.db"), key=lambda p: p.stat().st_mtime)[:-keep]:
        old.unlink()
    return dest


def meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (key, value))


# ---------- Reads ----------

def _app_row(r):
    """A suppressors row in the app's CSV shape ("" = queued, built "yes", dates MM/DD/YYYY) plus its version."""
    return {"caliber": r["caliber"], "serial_number": r["serial"], "overall_length": r["overall_length"],
            "status": "" if r["status"] == "queued" else r["status"].upper() if r["status"] == "skip" else r["status"],
            "application_number": r["application_number"] or "", "filed_date": to_us(r["filed_date"]),
            "approved_date": to_us(r["approved_date"]), "eform_id": r["eform_id"], "control_number": r["control_number"],
            "conditions": r["conditions"], "built": "yes" if r["built"] else "", "design": r["design"],
            "version": r["version"]}


def _app_sbr(r):
    return {"serial_number": r["serial"], "caliber": r["caliber"], "model": r["model"], "barrel_length": r["barrel_length"],
            "overall_length": r["overall_length"], "approved_date": to_us(r["approved_date"]), "eform_id": r["eform_id"] or "",
            "control_number": r["control_number"], "conditions": r["conditions"]}


def rows(conn):
    return sorted((_app_row(r) for r in conn.execute("SELECT * FROM suppressors")),
                  key=lambda r: serial_key(r["serial_number"]))


def row(conn, serial):
    r = conn.execute("SELECT * FROM suppressors WHERE serial = ?", (serial,)).fetchone()
    return _app_row(r) if r else None


def sbrs(conn):
    return sorted((_app_sbr(r) for r in conn.execute("SELECT * FROM sbrs")), key=lambda r: serial_key(r["serial_number"]))


def pdf_for(conn, serial):
    """(sha256, file name) of the approval PDF for a suppressor or SBR: by its eForm ID, else by serial; or None."""
    found = conn.execute("SELECT eform_id FROM suppressors WHERE serial = ? UNION ALL "
                         "SELECT eform_id FROM sbrs WHERE serial = ? COLLATE NOCASE", (serial, serial)).fetchall()
    for (eform,) in found:
        if eform:
            p = conn.execute("SELECT sha256, serial FROM approval_pdfs WHERE eform_id = ?", (eform,)).fetchone()
            if p:
                return p["sha256"], f"{p['serial'] or serial}.pdf"
    if found:
        p = conn.execute("SELECT sha256, serial FROM approval_pdfs WHERE serial = ? COLLATE NOCASE ORDER BY id DESC",
                         (serial,)).fetchone()
        if p:
            return p["sha256"], f"{p['serial']}.pdf"
    return None


def history(conn, serial):
    return [{"at": h["at"], "device": h["device"], "operation": h["operation"],
             "before": json.loads(h["before"]) if h["before"] else None, "after": json.loads(h["after"]) if h["after"] else None}
            for h in conn.execute("SELECT * FROM history WHERE serial = ? COLLATE NOCASE ORDER BY id", (serial,))]


def export_csv(conn):
    """The inventory as the app's suppressors.csv (serial order)."""
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows(conn))
    return out.getvalue()


def is_empty(conn):
    return not any(conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in ("suppressors", "sbrs", "approval_pdfs"))


# ---------- Import (into an empty server only) ----------

def import_inventory(conn, pdf_dir, suppressors, sbr_rows=(), pdfs=(), device="import"):
    """Load a whole inventory once: the app's suppressors.csv / sbrs.csv rows and approval PDFs
    ({"eform_id", "serial", "data"}). All or nothing; refused unless the server is empty. Returns the counts."""
    pdf_dir = Path(pdf_dir)
    at = now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not is_empty(conn):
            raise Refused("The server already holds an inventory; import only goes into an empty server.")
        for r in suppressors:
            r = {c: (r.get(c) or "").strip() for c in COLS}
            status = r["status"].lower() or "queued"
            if status not in STATUSES:
                raise Refused(f"{r['serial_number']}: unknown status {r['status']!r}")
            built = r["built"].lower() == "yes"
            if built and status != "approved":
                raise Refused(f"{r['serial_number']}: marked built but not approved")
            values = (r["serial_number"], r["caliber"], r["overall_length"], status, r["application_number"] or None,
                      to_iso(r["filed_date"]), to_iso(r["approved_date"]), r["eform_id"], r["control_number"],
                      r["conditions"], int(built), r["design"], at, device)
            try:
                conn.execute("INSERT INTO suppressors (serial, caliber, overall_length, status, application_number, "
                             "filed_date, approved_date, eform_id, control_number, conditions, built, design, updated_at, "
                             "updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            except sqlite3.IntegrityError as e:
                raise Refused(f"{r['serial_number'] or '(no serial)'}: {_why(e)}")
            _log(conn, at, device, "import", r["serial_number"], None, row(conn, r["serial_number"]))
        for r in sbr_rows:
            r = {c: (r.get(c) or "").strip() for c in SBR_COLS}
            try:
                conn.execute("INSERT INTO sbrs (serial, caliber, model, barrel_length, overall_length, approved_date, "
                             "eform_id, control_number, conditions) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (r["serial_number"], r["caliber"], r["model"], r["barrel_length"], r["overall_length"],
                              to_iso(r["approved_date"]), r["eform_id"] or None, r["control_number"], r["conditions"]))
            except sqlite3.IntegrityError as e:
                raise Refused(f"SBR {r['serial_number'] or '(no serial)'}: {_why(e)}")
            _log(conn, at, device, "import", r["serial_number"], None, r)
        stored = 0
        for p in pdfs:
            data = p["data"]
            if not data.startswith(b"%PDF"):
                raise Refused(f"approval PDF for {p.get('serial') or p.get('eform_id')}: not a PDF")
            digest = hashlib.sha256(data).hexdigest()
            target = pdf_dir / f"{digest}.pdf"
            if not target.exists():
                pdf_dir.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(target)
            try:
                conn.execute("INSERT INTO approval_pdfs (eform_id, serial, sha256, size, received_at) VALUES (?, ?, ?, ?, ?)",
                             (p.get("eform_id") or None, p.get("serial") or "", digest, len(data), at))
            except sqlite3.IntegrityError as e:
                raise Refused(f"approval PDF for eForm {p.get('eform_id')}: {_why(e)}")
            stored += 1
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"suppressors": len(suppressors), "sbrs": len(sbr_rows), "pdfs": stored}


def _why(error):
    text = str(error)
    if "UNIQUE" in text and "serial" in text:
        return "the serial appears twice"
    if "UNIQUE" in text and "application_number" in text:
        return "the application number appears twice"
    if "UNIQUE" in text and "eform_id" in text:
        return "the eForm ID appears twice"
    return text


def _log(conn, at, device, operation, serial, before, after, request_id=None):
    conn.execute("INSERT INTO history (at, device, operation, serial, before, after, request_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (at, device, operation, serial, json.dumps(before) if before is not None else None,
                  json.dumps(after) if after is not None else None, request_id))


def read_app_folder(root):
    """An app folder's inventory, as import takes it: data/suppressors.csv, data/sbrs.csv, out/approved/ (index.csv maps
    eForm ID -> file; files without an entry count when they are named after a serial). Reads only."""
    root = Path(root)

    def table(path):
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    sup, sbr = table(root / "data" / "suppressors.csv"), table(root / "data" / "sbrs.csv")
    if not sup and not (root / "data" / "suppressors.csv").exists():
        raise Refused(f"No inventory at {root / 'data' / 'suppressors.csv'}")
    by_eform = {r.get("eform_id", "").strip(): r["serial_number"].strip() for r in sup + sbr if r.get("eform_id", "").strip()}
    serials = {r["serial_number"].strip().upper(): r["serial_number"].strip() for r in sup + sbr}
    folder, pdfs, used = root / "out" / "approved", [], set()
    for entry in table(folder / "index.csv"):
        path = folder / entry["file"]
        if path.is_file():
            stem = re.sub(r"_\d+$", "", path.stem)
            serial = by_eform.get(entry["eform_id"]) or serials.get(stem.upper(), "")
            pdfs.append({"eform_id": entry["eform_id"], "serial": serial, "data": path.read_bytes()})
            used.add(path.name)
    for path in sorted(folder.glob("*.pdf")) if folder.exists() else []:
        if path.name not in used and path.stem.upper() in serials:
            pdfs.append({"eform_id": None, "serial": serials[path.stem.upper()], "data": path.read_bytes()})
    return sup, sbr, pdfs


# ---------- Devices and pairing ----------

def new_pair_code(conn):
    """A 6-digit code for pairing one device, valid PAIR_MINUTES; replaces any earlier code."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    expires = (datetime.now(timezone.utc) + timedelta(minutes=PAIR_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_meta(conn, "pair_code", json.dumps({"hash": _hash(code), "expires": expires, "failures": 0}))
    return code, expires


def pair(conn, code, name):
    """Trade a valid pairing code for a new device key (returned once; only its hash is kept). A code works once and
    stops working after PAIR_TRIES wrong tries."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:40] or "Device"
    conn.execute("BEGIN IMMEDIATE")
    try:
        state = json.loads(meta(conn, "pair_code") or "null")
        if not state or state["expires"] < now():
            raise Refused("No pairing code is active. Make one under Devices on a paired device, or with the "
                          "server's pair-code command.")
        if not hmac.compare_digest(state["hash"], _hash(str(code).strip())):
            state["failures"] += 1
            if state["failures"] >= PAIR_TRIES:
                conn.execute("DELETE FROM meta WHERE key = 'pair_code'")
            else:
                set_meta(conn, "pair_code", json.dumps(state))
            conn.execute("COMMIT")
            raise Refused("That code is wrong." + (" It no longer works; make a new one." if state["failures"] >= PAIR_TRIES else ""))
        conn.execute("DELETE FROM meta WHERE key = 'pair_code'")
        key = secrets.token_urlsafe(32)
        cur = conn.execute("INSERT INTO devices (name, key_hash, created_at, last_seen) VALUES (?, ?, ?, ?)",
                           (name, _hash(key), now(), now()))
        _log(conn, now(), name, "pair_device", None, None, {"device": name})
        conn.execute("COMMIT")
        return cur.lastrowid, key
    except Refused:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def device_for_key(conn, key):
    if not key:
        return None
    d = conn.execute("SELECT * FROM devices WHERE key_hash = ? AND revoked_at IS NULL", (_hash(key),)).fetchone()
    if d and (d["last_seen"] or "") < (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"):
        conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (now(), d["id"]))
    return dict(d) if d else None


def devices(conn):
    return [{"id": d["id"], "name": d["name"], "created_at": d["created_at"], "last_seen": d["last_seen"],
             "revoked_at": d["revoked_at"]} for d in conn.execute("SELECT * FROM devices ORDER BY id")]


def revoke(conn, device_id, by):
    conn.execute("BEGIN IMMEDIATE")
    try:
        d = conn.execute("SELECT name FROM devices WHERE id = ? AND revoked_at IS NULL", (device_id,)).fetchone()
        if not d:
            raise Refused("No such device (or it's already removed).")
        conn.execute("UPDATE devices SET revoked_at = ? WHERE id = ?", (now(), device_id))
        _log(conn, now(), by, "revoke_device", None, {"device": d["name"]}, None)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
