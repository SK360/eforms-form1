#!/usr/bin/env python3
"""ATF eForms Form 1 helper (suppressors, filed by a trust). See CLAUDE.md for the full picture.

Normally run through the menu: ./atf (src/ui.py). These commands are the plain CLI underneath it:
  fill-5320 [row] [--date MM/DD/YYYY]
        Stamp caliber (4d), serial (4e), signature date and CLEO line onto the user's completed
        forms/5320.23.pdf -> out/5320.23_<serial>.pdf. No row: all unfiled rows. Other responsible persons in the
        trust: their own template (forms/5320.23_<name>.pdf) -> out/5320.23_<serial>.<name>.pdf (their CLEO line is kept).
        CLEO copies (forms/5320.23.cleo.pdf etc., if set) -> out/cleo/5320.23_<serial>[.<name>].cleo.pdf (no serial on them).
  fill-form1 [row] [--dry] [--auto-affirm] [--skip-ssn] [--submit] [--capture] [--force] [--all] [--count N] [--cleo]
        Open eForms, wait for the user to log in, open a new Form 1, then run the steps in
        data/mapping.yaml. No row: the first row in data/suppressors.csv not marked filed.
        --submit        also verify + certify + Sign & Submit (only if every read-back matched);
                        the user still types the emailed code and PIN, then the row is marked filed.
        --auto-affirm   click the "I affirm" popup (the user's affirmation).
        --skip-ssn      skip the pause for typing your SSN (it's optional; leaving it blank may delay processing).
        --dry           print the steps with their values; no browser.
        --capture       after the steps, record each screen the user clicks through to out/capture_*.jsonl.
        --cleo          after the batch, save each filed Form 1's CLEO copy (read-only) to out/cleo/.
  check-approved
        Read-only: download approved Form 1 PDFs (new ones only) and compare suppressors with your records.
  check-setup
        Check settings, files (found, readable, not online-only, the right kind), 5320.23 templates (test-stamped)
        and the browser, without opening eForms. Exit 1 if something must be fixed.
  update [--check] [--yes]
        Update this folder to the newest release on GitHub (src/update.py): git checkouts with git, ZIP installs by
        downloading it. Your data/, forms/ and out/ are never touched. --check only says whether one is out.
  serve
        Your inventory on your phone, on the same Wi-Fi, until Ctrl-C (src/phone.py): a QR code and a link with a key
        made for this run. The page can mark approved suppressors built (with their design) or clear that.
  inspect
        Open eForms and dump the controls/dialog text of whatever page the user is on (to map new steps).
  fields <pdf>
        List AcroForm field names in a PDF (only useful for a fresh, fillable ATF 5320.23).
  --version
        Print the version (see CHANGELOG.md).
"""
import argparse
import base64
import csv
import json
import os
import re
from datetime import date, datetime
import sys
import time
from pathlib import Path

import yaml
from pypdf import PdfReader

__version__ = "1.6.3"  # bump with each release; CHANGELOG.md says what changed

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT, FORMS = ROOT / "data", ROOT / "out", ROOT / "forms"
PROFILE = ROOT / ".browser-profile"
EFORMS_URL = "https://eforms.atf.gov/"


# ---------- Output hooks (src/ui.py swaps these for the menu's styled output) ----------
def emit(kind, text, **data):
    """kind: step | info | ok | warn | error | banner | section | row | expect | done. The CLI just prints."""
    print(text)


def ask(prompt):
    return input(prompt)


def load_trust():
    return yaml.safe_load((DATA / "trust.yaml").read_text(encoding="utf-8-sig"))


MAPPING = ROOT / "config" / "mapping.yaml"  # shared portal steps (tracked); personal values live in data/trust.yaml


def load_mapping():
    return yaml.safe_load(MAPPING.read_text(encoding="utf-8-sig"))


# ---------- Inventory (data/suppressors.csv: one row per suppressor, queued -> filed -> approved) ----------
SUPP = DATA / "suppressors.csv"
COLS = ["caliber", "serial_number", "overall_length", "status", "application_number", "filed_date", "approved_date",
        "eform_id", "control_number", "conditions", "built", "design"]
# overall_length: inches, per suppressor (empty = the settings default, firearm.overall_length, is used and then recorded)
# status: "" queued | pending (Sign & Submit clicked, confirmation never read) | filed | approved | disapproved
# built: "yes" once made ("" = not yet; only approved rows are ever marked built, see record_build).
# design: what it is, free text (e.g. "PCC Short").
# Other NFA items (an SBR made from an existing receiver, ...) queue in the same file so filing, pending and resolving
# work the same; these columns stay empty for suppressors. type = the portal's #productType ("" = SILENCER).
# mfr_* = the ORIGINAL manufacturer as ATF lists it (code verified in the portal; name + address go in 5320.23 item 4b),
# model as engraved, barrel_length in inches, description (item 4h, 120 max), photo = optional markings photo.
# Once approved they leave this file for data/sbrs.csv (record_sbrs). On an inventory server they're in its
# suppressors table (schema 5, server API 5: NFA_SERVER_API), which takes them out of the queue the same way.
NFA_QUEUE_COLS = ["type", "mfr_code", "mfr_name", "mfr_address", "mfr_country", "model", "barrel_length", "unit",
                  "description", "photo"]
QUEUE_COLS = COLS + NFA_QUEUE_COLS
NFA_SERVER_API = 5  # the first inventory-server API that holds queued SBRs etc.


def nfa_server_problem():
    """In server mode: why the server can't hold queued SBRs etc. (too old, or can't be reached), else None."""
    import remote
    if not server_settings():
        return None
    try:
        api = _server().get("/api/hello").get("api") or 1
    except remote.ServerError as e:
        return f"your inventory server can't be reached ({e})"
    if api < NFA_SERVER_API:
        return "your inventory server is too old to hold SBRs and other NFA items: update it (atf-server update on the server)"
    return None


def is_nfa_row(row):
    """A queued row for something other than a suppressor (SBR, SBS, AOW...)."""
    kind = (row.get("type") or "").strip().upper()
    return bool(kind) and kind != "SILENCER"


def nfa_row_problems(row):
    """What a queued SBR (etc.) row still needs before it can be filed; [] = ready. Form 1 instruction K: one caliber
    or gauge (another designation goes in the description), lengths with the stock extended, the existing serial."""
    if not is_nfa_row(row):
        return []
    need = [("mfr_name", "the manufacturer's name (as marked)"),
            ("mfr_address", "the manufacturer's city and state (as marked; 5320.23 item 4b)"), ("model", "the model as engraved"),
            ("caliber", "the caliber or gauge"), ("barrel_length", "the barrel length"), ("overall_length", "the overall length")]
    probs = [f"{row['serial_number']}: {what} is missing" for key, what in need if not (row.get(key) or "").strip()]
    if re.search(r"\bMULTI\b", row.get("caliber", ""), re.I):
        probs.append(f"{row['serial_number']}: caliber must be ONE caliber (put MULTI in the description)")
    if len(row.get("description", "")) > 120:
        probs.append(f"{row['serial_number']}: description is over 120 characters (the portal's limit)")
    photo = (row.get("photo") or "").strip()
    if photo and not Path(photo).is_file():
        probs.append(f"{row['serial_number']}: markings photo not found ({Path(photo).name})")
    elif photo and Path(photo).stat().st_size > 3 * 1024 * 1024:
        probs.append(f"{row['serial_number']}: markings photo is over 3 MB (the portal's limit per file)")
    why = nfa_server_problem()
    if why:
        probs.append(f"{row['serial_number']}: {why}")
    return probs


def load_rows():
    if server_settings():
        rows = [dict(r) for r in _server_inventory()[0]]
    else:
        with SUPP.open(newline="", encoding="utf-8-sig") as f:  # -sig: tolerate a BOM (Excel on Windows)
            rows = list(csv.DictReader(f))
    for r in rows:
        for c in QUEUE_COLS:  # older files have fewer columns
            r[c] = (r.get(c) or "").strip()
        r["status"] = r["status"].lower()
    return rows


def is_built(row):
    return row.get("built", "").lower() == "yes"


def record_builds(designs):
    """Mark suppressors as built, each with what it is: {serial: design} (or change the design of built ones). Only
    APPROVED rows: a Form 1 must be approved before the suppressor is made. Saves once; returns the serials updated;
    raises ValueError if any serial is unknown or not approved (then nothing is saved). With a server: one operation,
    all or nothing, based on the versions last read (another device's change since = nothing saved, ServerError)."""
    if server_settings():
        _server_op("set_built", {"items": [{"serial": s, "design": d.strip(), "version": _seen_version(s)}
                                           for s, d in designs.items()]})
        return list(designs)
    rows = load_rows()
    by = {r["serial_number"].upper(): r for r in rows}
    bad = [s for s in designs if (by.get(s.upper()) or {}).get("status") != "approved"]
    if bad:
        raise ValueError(f"not approved (or not in the inventory): {', '.join(bad)}")
    for s, design in designs.items():
        by[s.upper()].update(built="yes", design=design.strip())
    save_rows(rows)
    return list(designs)


def record_build(serials, design=""):
    """record_builds with the same design for every serial."""
    return record_builds({s: design for s in serials})


def caliber_key(caliber):
    """Calibers compare case-insensitively and without a leading dot (".22" = "22")."""
    return caliber.strip().upper().lstrip(".")


def available_for_build(rows, caliber, length):
    """Approved, not built suppressors of `caliber` approved at `length` inches or LONGER (an approved 12" is good for a
    5" build), in serial order: the first one is the next to use. Rows with no recorded length are left out."""
    want = caliber_key(caliber)
    fits = [r for r in rows if r["status"] == "approved" and not is_built(r) and caliber_key(r["caliber"]) == want
            and _num(r["overall_length"]) is not None and _num(r["overall_length"]) >= float(length)]
    return sorted(fits, key=lambda r: serial_key(r["serial_number"]))


def clear_build(serial):
    """Undo a build record (marked built by mistake)."""
    if server_settings():
        _server_op("clear_built", {"serial": serial, "version": _seen_version(serial)})
        return
    rows = load_rows()
    for r in rows:
        if r["serial_number"].upper() == serial.upper():
            r.update(built="", design="")
    save_rows(rows)


def add_queued(new_rows):
    """Add queued suppressors ({serial_number, caliber, overall_length}); the caller has checked the serials are new.
    Other NFA items (is_nfa_row) carry NFA_QUEUE_COLS too (on a server: one with API NFA_SERVER_API or newer)."""
    if server_settings():
        nfa = any(is_nfa_row(r) for r in new_rows)
        why = nfa_server_problem() if nfa else None
        if why:
            raise ServerReadOnly(f"Not added: {why}.")
        keys = ["serial_number", "caliber", "overall_length"] + (NFA_QUEUE_COLS if nfa else [])
        _server_op("add_queued", {"rows": [{k: r.get(k, "") for k in keys} for r in new_rows]})
        return
    save_rows(load_rows() + [dict(r, status="") for r in new_rows])


def edit_queued(serial, changes, version=None):
    """Change a queued row's serial_number / caliber / overall_length (an SBR etc. also its NFA_QUEUE_COLS). A new
    manufacturer name drops the code saved from the last eForms lookup, so the next filing looks the new name up."""
    if "mfr_name" in changes and "mfr_code" not in changes:
        changes = dict(changes, mfr_code="")
    if server_settings():
        _server_op("edit_queued", {"serial": serial, "changes": changes, "version": version})
        return
    rows = load_rows()
    r = next(r for r in rows if r["serial_number"].upper() == serial.upper() and r["status"] == "")
    r.update(changes)
    save_rows(rows)


def remove_queued(serial, version=None):
    if server_settings():
        _server_op("remove_queued", {"serial": serial, "version": version})
        return
    save_rows([r for r in load_rows() if not (r["serial_number"].upper() == serial.upper() and r["status"] == "")])


def serials_in(spec, rows):
    """Serials named by `spec` ("A101-A110, A115"): single serials and inclusive ranges in natural order, among `rows`.
    Returns (serials in serial order, problems)."""
    names = {r["serial_number"].upper(): r["serial_number"] for r in rows}
    out, problems = set(), []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        m = re.fullmatch(r"(\S+?)\s*(?:-|–|to)\s*(\S+)", part) if part.upper() not in names else None
        if m:
            lo, hi = sorted((serial_key(m[1]), serial_key(m[2])))
            hits = [n for n in names.values() if lo <= serial_key(n) <= hi and re.sub(r"\d", "", n).upper() == re.sub(r"\d", "", m[1]).upper()]
            out.update(hits) if hits else problems.append(f"nothing in {part}")
        elif part.upper() in names:
            out.add(names[part.upper()])
        else:
            problems.append(f"{part} isn't one of them")
    return sorted(out, key=serial_key), problems


def serial_key(serial):
    """Natural order: S9 < S10 < S100, letters compared case-insensitively."""
    return [int(t) if t.isdigit() else t.upper() for t in re.split(r"(\d+)", str(serial))]


def backfill_filed_dates(rows=None):
    """Fill filed_date for filed rows from out/submissions.csv (the filing log). Saves only if something changed."""
    log = OUT / "submissions.csv"
    rows = load_rows() if rows is None else rows
    if server_settings():  # the server's records aren't changed from here (yet)
        return rows
    if not log.exists() or not any(r["status"] in ("filed", "pending", "approved") and not r["filed_date"] for r in rows):
        return rows
    first = {}
    with log.open(newline="", encoding="utf-8-sig") as f:
        for rec in csv.DictReader(f):
            first.setdefault(rec["serial_number"].strip().upper(), rec["timestamp"][:10])
    changed = False
    for r in rows:
        iso = first.get(r["serial_number"].upper())
        if iso and not r["filed_date"] and r["status"] in ("filed", "pending", "approved"):
            r["filed_date"] = datetime.strptime(iso, "%Y-%m-%d").strftime("%m/%d/%Y")
            changed = True
    if changed:
        save_rows(rows)
    return rows


def _server_fills_filed_dates():
    """Server API 3 on: record_approvals may fill an empty filed_date (older servers refuse the field)."""
    return (_server().get("/api/hello").get("api") or 1) >= 3


def backfill_from_approval_pdfs():
    """Fill an empty filed_date of approved suppressors (e.g. filed by hand before this tool) from item 9 of their
    approval PDF already saved in out/approved/ (no eForms login). A recorded filed date is never changed; the PDF's
    serial must be the row's. Server mode: one record_approvals operation (needs server API 3). Returns the serials."""
    rows = load_rows()
    todo = [r for r in rows if r["status"] == "approved" and not r["filed_date"]]
    if not todo:
        return []
    fills = {}
    for r in todo:
        path = _saved_approved_pdf(r)
        if path is None:
            continue
        try:
            rec = read_approved_pdf(path)
        except Exception:  # an unreadable PDF: Check approvals reports those
            continue
        if rec["serial"].strip().upper() == r["serial_number"].strip().upper() and "SILENCER" in rec["type"].upper() and rec["filed_date"]:
            fills[r["serial_number"]] = rec["filed_date"]
    if not fills:
        return []
    if server_settings():
        if not SERVER_STATE.get("online") or not _server_fills_filed_dates():  # offline copy / older server: next time
            return []
        _server_op("record_approvals", {"updates": [{"serial": s, "fields": {"filed_date": d}} for s, d in fills.items()], "added": []})
        return list(fills)
    for r in rows:
        if r["serial_number"] in fills:
            r["filed_date"] = fills[r["serial_number"]]
    save_rows(rows)
    return list(fills)


def _date(text):
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def wait_days(row, today=None):
    """How long ATF took, in days: filed -> approved for an approved row; filed -> today ("so far") while filed or
    pending. None without the dates (or for queued/disapproved rows)."""
    filed = _date(row.get("filed_date"))
    if filed is None:
        return None
    if row.get("status") == "approved":
        done = _date(row.get("approved_date"))
        return (done - filed).days if done and done >= filed else None
    if row.get("status") in ("filed", "pending"):
        return max(((today or date.today()) - filed).days, 0)
    return None


def approval_waits(rows, today=None, days=365):
    """Filed -> approved, for the approvals of the last `days` days (all approvals if none is that recent):
    {"count", "median", "average", "shortest", "longest", "recent"} in days, or None without any."""
    today = today or date.today()
    done = [(r, wait_days(r)) for r in rows if r.get("status") == "approved"]
    done = [(r, w) for r, w in done if w is not None]
    recent = [(r, w) for r, w in done if (today - _date(r["approved_date"])).days <= days]
    pick = recent or done
    if not pick:
        return None
    waits = sorted(w for _, w in pick)
    mid = len(waits) // 2
    median = waits[mid] if len(waits) % 2 else (waits[mid - 1] + waits[mid]) / 2
    half_up = lambda x: int(x + 0.5)  # noqa: E731 (like the phone page's Math.round; round() would give 98 for 98.5)
    return {"count": len(waits), "median": half_up(median), "average": half_up(sum(waits) / len(waits)),
            "shortest": waits[0], "longest": waits[-1], "recent": bool(recent)}


def save_rows(rows):
    if server_settings():
        raise ServerReadOnly(SERVER_READ_ONLY)
    tmp = SUPP.with_suffix(".csv.tmp")  # write-then-rename so a crash can't leave a half-written queue
    cols = QUEUE_COLS if any(is_nfa_row(r) for r in rows) else COLS  # suppressor-only files keep their old header
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(SUPP)


def log_submission(item, num):
    """Append to out/submissions.csv (the audit log of filed applications)."""
    OUT.mkdir(exist_ok=True)
    log = OUT / "submissions.csv"
    new = not log.exists()
    with log.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "serial_number", "caliber", "application_number"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), item["serial_number"], item["caliber"], num])
    return log


# status "pending" = Sign & Submit was clicked but the confirmation was never read: it may be filed.
# Such rows are skipped until you check the dashboard and resolve them (menu: Resolve a pending row).
SKIP = ("filed", "pending", "approved", "disapproved")  # never (re)filed without --force


def _warn_pending(rows):
    pend = [f"row {i + 1} ({r['serial_number']})" for i, r in enumerate(rows) if r["status"] == "pending"]
    if pend:
        emit("warn", f"!! PENDING (possibly filed, confirmation never read): {', '.join(pend)}. Check the eForms dashboard,\n"
                     "   then set status to 'filed' (with the application number) or blank in data/suppressors.csv.")


def _check_row_num(rows, n):
    if not 1 <= n <= len(rows):
        sys.exit(f"Row {n} does not exist; data/suppressors.csv has rows 1-{len(rows)}.")


def pick_row(rows, n, force=False):
    """1-based row number, or the first row neither filed nor pending when n is None."""
    _warn_pending(rows)
    if n is None:
        for i, r in enumerate(rows):
            if r["status"] not in SKIP:
                return i
        sys.exit("No unfiled rows left in data/suppressors.csv.")
    _check_row_num(rows, n)
    r = rows[n - 1]
    if r["status"] in SKIP and not force:
        sys.exit(f"Row {n} ({r['serial_number']}) is {r['status']} (app {r['application_number'] or '?'}). Use --force to override.")
    return n - 1


def _token_match(want, got):
    """Case-insensitive whole-token match: '22' matches '22' or 'CAL 22' but not '223'; 'S101' not 'S1015'."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(str(want).strip())}(?![A-Za-z0-9])", got or "", re.I) is not None


# ---------- Other NFA items (data/sbrs.csv): approved SBRs, SBSs, ..., filled by Check approvals; never filed here ----------
# ---------- Inventory server (optional: server/, connected in Settings > Where your inventory lives) ----------
# data/server.yaml = the paired server (id, addresses, its CA and fingerprint, this computer's key). While it
# exists, the inventory is the server's: load_rows / load_sbrs read it; builds and queue edits are the server's named
# operations (record_builds, clear_build, add_queued, edit_queued, remove_queued); whole-file saves are refused
# (save_rows / save_sbrs raise ServerReadOnly: filing and approvals come later). This computer's CSVs aren't used. When the
# server can't be reached, the last data seen (data/server_cache.json) is shown, marked offline and read-only.
SERVER_READ_ONLY = ("Your inventory is on your server: this can't be saved as a whole file. Please report it (it's a bug); "
                    "nothing was changed.")
SERVER_STATE = {}  # after a read: {"online": bool, "address", "problem", "seen"}
_FETCHED = {}


class ServerReadOnly(Exception):
    pass


def _server_path():  # looked up when used (tests point DATA elsewhere)
    return DATA / "server.yaml"


def _server_cache_path():
    return DATA / "server_cache.json"


def server_settings():
    p = _server_path()
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or None) if p.exists() else None


def save_server_settings(settings):
    p = _server_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(settings, sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        p.chmod(0o600)  # holds this computer's key
    except OSError:
        pass
    _FETCHED.clear()


def forget_server():
    """Back to this computer's inventory: the server settings and the offline copy go (the CSVs were never changed)."""
    for p in (_server_path(), _server_cache_path()):
        if p.exists():
            p.unlink()
    _FETCHED.clear()
    SERVER_STATE.clear()


def _server(settings=None):
    import remote
    s = settings or server_settings()
    return remote.Server(s["server_id"], s["addresses"], s["fingerprint"], s["key"], ca=s.get("ca"))


def server_status():
    """The server's own report (version, newer release, backups, problems), or None: unreachable, or too old."""
    import remote
    try:
        return _server().status()
    except remote.ServerError:
        return None


def save_server_backup(keep=5):
    """Fetch one file with everything on the server (database + every approval PDF) into out/server-backups/, so a
    copy exists off the server (its own daily backups sit on its own disk, without the PDFs). Keeps the newest
    `keep`. Returns the file; remote.ServerError if the server can't be reached or is too old."""
    import zipfile
    data = _server().backup()
    folder = OUT / "server-backups"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"atf-inventory-backup-{datetime.now():%Y-%m-%d-%H%M%S}.zip"
    tmp = dest.with_suffix(".zip.tmp")
    tmp.write_bytes(data)
    try:
        with zipfile.ZipFile(tmp) as z:  # a whole, readable file with a database in it, or it isn't kept
            if "inventory.db" not in z.namelist() or z.testzip():
                raise zipfile.BadZipFile("incomplete")
    except zipfile.BadZipFile:
        tmp.unlink()
        import remote
        raise remote.ServerError("The backup from your server arrived damaged; nothing was saved. Try again.")
    tmp.replace(dest)
    for old in sorted(folder.glob("atf-inventory-backup-*.zip"))[:-keep]:
        old.unlink()
    return dest


def _server_op(name, body):
    """One named change on the server (server/atf_server/db.py OPERATIONS), with a fresh request id. Filing results
    still waiting in the retry file go first."""
    import uuid
    import remote
    if _outbox_path().exists():
        outbox_replay()
    try:
        return _server().post(f"/api/ops/{name}", dict(body, request_id=uuid.uuid4().hex))
    except remote.Unreachable as e:
        raise remote.ServerError(f"Not saved: your server can't be reached. {e}")
    finally:
        _FETCHED.clear()  # the next read shows what the server has now


# The retry file (data/outbox.jsonl): a filing result the server hasn't confirmed yet. Written (and flushed to disk)
# BEFORE it's sent, removed only once the server confirms it; sent again before every server call and at every
# start. Its request id makes a repeat harmless. While it isn't empty, filing doesn't start.
def _outbox_path():
    return DATA / "outbox.jsonl"


def outbox():
    p = _outbox_path()
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()] if p.exists() else []


def _outbox_write(entries):
    p = _outbox_path()
    if not entries:
        p.unlink(missing_ok=True)
        return
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    tmp.replace(p)


def _outbox_add(entry):
    p = _outbox_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())


def outbox_replay():
    """Send the waiting filing results. Returns those still waiting (unreachable, or refused: then with "problem")."""
    import remote
    waiting = outbox()
    if not waiting or not server_settings():
        return waiting
    left = []
    for n, e in enumerate(waiting):
        try:
            _server().post(f"/api/ops/{e['op']}", e["body"])
            _FETCHED.clear()
        except remote.Unreachable:
            left += waiting[n:]
            break
        except remote.ServerError as err:
            left.append(dict(e, problem=str(err)))
    _outbox_write(left)
    return left


def _seen_version(serial):
    """The row's version as last read from the server (None = not read: the server then doesn't check it)."""
    for r in (_FETCHED.get("data") or ([], []))[0]:
        if r["serial_number"].upper() == serial.upper():
            return r.get("version")
    return None


def _server_inventory():
    """(suppressors, sbrs) from the server; reused for 2 s (one screen reads both). Unreachable: the last data seen."""
    import time
    import remote
    if _FETCHED and time.monotonic() - _FETCHED["at"] < 2:
        return _FETCHED["data"]
    if _outbox_path().exists():  # a filing result waiting to reach the server goes first
        outbox_replay()
    server = _server()
    try:
        data = server.rows()
    except remote.ServerError as e:
        cache = _server_cache_path()
        if not cache.exists():
            SERVER_STATE.update(online=False, address=None, problem=str(e), seen=None)
            raise
        saved = json.loads(cache.read_text(encoding="utf-8"))
        SERVER_STATE.update(online=False, address=None, problem=str(e), seen=saved["seen"])
        data = (saved["suppressors"], saved["sbrs"])
    else:
        seen = datetime.now().strftime("%m/%d/%Y %H:%M")
        tmp = _server_cache_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"seen": seen, "suppressors": data[0], "sbrs": data[1]}), encoding="utf-8")
        tmp.replace(_server_cache_path())
        SERVER_STATE.update(online=True, address=server.address, problem=None, seen=seen)
    _FETCHED.update(at=time.monotonic(), data=data)
    return data


# ---------- Moving the inventory to the server and back (phase 4) ----------
# Moving: this computer's CSVs and approval PDFs go to an EMPTY server in one import; the app reads everything back and
# compares every field of every row and every PDF's checksum; any difference = the import is taken back and nothing
# here changes. Only on an exact match are the CSVs renamed (suppressors.moved-to-server-<date>.csv: kept, never
# deleted) and the server used. try_only does all of it and then takes the import back. Copying back writes the
# server's inventory into this computer's CSVs (any non-empty one renamed first) and fetches PDFs it doesn't have.

def _read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_csv(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: serial_key(r["serial_number"])))
    tmp.replace(path)


def _move_aside(path, word):
    """suppressors.csv -> suppressors.<word>-<date>.csv (-2, -3 ... if taken). Returns the new name."""
    stem = f"{path.stem}.{word}-{datetime.now():%Y-%m-%d}"
    dest, n = path.with_name(stem + path.suffix), 2
    while dest.exists():
        dest, n = path.with_name(f"{stem}-{n}{path.suffix}"), n + 1
    path.replace(dest)
    return dest.name


def local_for_move():
    """This computer's own inventory, read from its files whatever mode it's in: (suppressors, sbrs, pdfs), the PDFs
    as {eform_id, serial, path} (out/approved/index.csv, plus files named after a serial that aren't in it)."""
    sup = [{c: (r.get(c) or "").strip() for c in QUEUE_COLS} for r in _read_csv(SUPP)]
    sbrs = [{c: (r.get(c) or "").strip() for c in SBR_COLS} for r in _read_csv(_sbrs_path())]
    folder = OUT / "approved"
    by_eform = {r["eform_id"]: r["serial_number"] for r in sup + sbrs if r["eform_id"]}
    serials = {r["serial_number"].upper(): r["serial_number"] for r in sup + sbrs}
    pdfs, used = [], set()
    for eform, name in (_approved_index(folder).items() if folder.exists() else []):
        path = folder / name
        if path.is_file():
            stem = re.sub(r"_\d+$", "", path.stem)
            pdfs.append({"eform_id": eform, "serial": by_eform.get(eform) or serials.get(stem.upper(), ""), "path": path})
            used.add(name)
    for path in sorted(folder.glob("*.pdf")) if folder.exists() else []:
        if path.name not in used and path.stem.upper() in serials:
            pdfs.append({"eform_id": None, "serial": serials[path.stem.upper()], "path": path})
    return sup, sbrs, pdfs


def _norm(rows, cols):
    out = {}
    for r in rows:
        n = {}
        for c in cols:
            v = (r.get(c) or "").strip()
            if c in ("status", "built"):
                v = v.lower()
            if c.endswith("_date") and v:
                try:
                    v = datetime.strptime(v, "%m/%d/%Y").strftime("%m/%d/%Y")
                except ValueError:
                    pass
            n[c] = (v or NFA_DEFAULT_TYPE) if c == "type" and cols is SBR_COLS else v
        out[(n["serial_number"].upper(), n["eform_id"] if cols is SBR_COLS else "")] = n
    return out


def _differences(here, there, cols, what):
    """Every field that differs between this computer's rows and the server's (by serial; SBRs by serial + eForm ID)."""
    a, b = _norm(here, cols), _norm(there, cols)
    problems = []
    for k in sorted(set(a) | set(b), key=lambda k: serial_key(k[0])):
        if k not in b:
            problems.append(f"{what} {a[k]['serial_number']}: not on the server")
        elif k not in a:
            problems.append(f"{what} {b[k]['serial_number']}: on the server but not on this computer")
        else:
            problems += [f"{what} {a[k]['serial_number']}: {c} is {a[k][c]!r} here, {b[k][c]!r} on the server"
                         for c in cols if a[k][c] != b[k][c]]
    return problems


def move_to_server(try_only=False, progress=lambda text: None):
    """Move this computer's inventory to the (empty, connected) server; see above. The approval PDFs go one per
    request first (all of them in one request was too much for a small container), then the rows in one import that
    names them by checksum. progress(text) is told how far it got. Returns {suppressors, built, sbrs, pdfs, problems,
    moved, renamed}."""
    import hashlib
    import remote
    if not server_settings():
        raise remote.ServerError("Connect to your server first (Settings > Where your inventory lives).")
    if any(is_nfa_row(r) for r in _read_csv(SUPP)):
        why = nfa_server_problem()
        if why:
            raise ServerReadOnly(f"Your queue has an SBR or other NFA item and {why}. Nothing was moved.")
    server = _server()
    sup, sbrs, pdfs = local_for_move()
    if not sup and not sbrs:
        raise remote.ServerError("There's no inventory on this computer to move.")
    counts = {"suppressors": len(sup), "built": sum(1 for r in sup if is_built(r)), "sbrs": len(sbrs), "nfa": nfa_counts(sbrs),
              "pdfs": len(pdfs)}
    sent = []
    try:
        for n, p in enumerate(pdfs, 1):
            progress(f"Sending approval PDFs ({n}/{len(pdfs)})…")
            data = p["path"].read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if server.post("/api/import/pdf", data, timeout=60)["sha256"] != digest:
                raise remote.ServerError(f"The approval PDF {p['path'].name} arrived damaged. Nothing was moved.")
            sent.append((p, digest))
        progress("Sending the inventory…")
        server.post("/api/import", {"suppressors": sup, "sbrs": sbrs, "pdfs": [
            {"eform_id": p["eform_id"], "serial": p["serial"], "sha256": d} for p, d in sent]}, timeout=300)
    except BaseException:
        try:  # an unfinished move: drop what was uploaded (an import that did land is taken back too)
            server.post("/api/import/undo", {}, timeout=60)
        except remote.ServerError:
            pass
        raise
    progress("Checking every row and PDF on the server…")
    try:
        got_sup, got_sbrs = server.rows()
        problems = _differences(sup, got_sup, QUEUE_COLS, "Suppressor") + _differences(sbrs, got_sbrs, SBR_COLS, "Other NFA item")
        if sorted((p["eform_id"] or "", d) for p, d in sent) != sorted((p["eform_id"], p["sha256"]) for p in server.get("/api/pdfs")["pdfs"]):
            problems.append("Approval PDFs: what the server stored isn't what was sent")
    except BaseException:
        server.post("/api/import/undo", {}, timeout=60)
        raise
    finally:
        _FETCHED.clear()
    if problems or try_only:
        server.post("/api/import/undo", {}, timeout=60)
        return dict(counts, problems=problems, moved=False, renamed=[])
    renamed = [_move_aside(p, "moved-to-server") for p in (SUPP, _sbrs_path()) if p.exists()]
    return dict(counts, problems=[], moved=True, renamed=renamed)


def copy_from_server():
    """Write the server's inventory into this computer's CSVs (a non-empty one is renamed first: .replaced-by-server-
    <date>.csv) and fetch the approval PDFs this computer doesn't have; then read it back and compare. Doesn't
    disconnect. Returns {suppressors, sbrs, pdfs (fetched), renamed, problems}."""
    import hashlib
    server = _server()
    sup, sbrs = server.rows()
    listed = server.get("/api/pdfs")["pdfs"]
    renamed = []
    sup_cols = QUEUE_COLS if any(is_nfa_row(r) for r in sup) else COLS  # suppressor-only files keep their old header
    for path, rows, cols in ((SUPP, sup, sup_cols), (_sbrs_path(), sbrs, SBR_COLS)):
        if _read_csv(path):
            renamed.append(_move_aside(path, "replaced-by-server"))
        _write_csv(path, cols, rows)
    folder = OUT / "approved"
    folder.mkdir(parents=True, exist_ok=True)
    index = _approved_index(folder)
    have = {hashlib.sha256(f.read_bytes()).hexdigest() for f in folder.glob("*.pdf")}
    fetched = 0
    for p in listed:
        if p["sha256"] in have:
            continue
        data = server.pdf_file(p["sha256"])
        base = re.sub(r"[^A-Za-z0-9._-]", "_", p["serial"]) or f"eForm-{p['eform_id'] or p['sha256'][:12]}"
        name, n = f"{base}.pdf", 2
        while (folder / name).exists():
            name, n = f"{base}_{n}.pdf", n + 1
        (folder / name).write_bytes(data)
        if p["eform_id"]:
            index[p["eform_id"]] = name
        have.add(p["sha256"])
        fetched += 1
    _save_approved_index(folder, index)
    problems = (_differences(_read_csv(SUPP), sup, QUEUE_COLS, "Suppressor")
                + _differences(_read_csv(_sbrs_path()), sbrs, SBR_COLS, "Other NFA item"))
    return {"suppressors": len(sup), "sbrs": len(sbrs), "nfa": nfa_counts(sbrs), "pdfs": fetched, "renamed": renamed, "problems": problems}


def _sbrs_path():  # looked up when used (not at import), so it always follows DATA
    return DATA / "sbrs.csv"


SBR_COLS = ["serial_number", "type", "caliber", "model", "barrel_length", "overall_length", "approved_date", "eform_id",
            "control_number", "conditions"]
# The Form 1 types other than SILENCER, as the portal's #productType lists them and the approval PDF prints them
# (short name, plural). data/sbrs.csv holds them all (the name is from when it held only SBRs); a row with no type was
# saved before the type was kept, when only SBRs got in.
NFA_TYPES = {"SHORT BARRELED RIFLE": ("SBR", "SBRs"), "SHORT BARRELED SHOTGUN": ("SBS", "SBSs"), "ANY OTHER WEAPON": ("AOW", "AOWs"),
             "DESTRUCTIVE DEVICE": ("DD", "DDs"), "MACHINEGUN": ("MG", "MGs")}
NFA_DEFAULT_TYPE = "SHORT BARRELED RIFLE"


def _loose(text):
    """Letters only, upper case: "Short-barreled rifle" -> "SHORTBARRELEDRIFLE"."""
    return re.sub(r"[^A-Z]", "", (text or "").upper())


def _known_type(text):
    """The type name (loose form) that `text` contains: SILENCER or one of NFA_TYPES; "" for none."""
    loose = _loose(text)
    return next((k for k in ["SILENCER"] + [_loose(t) for t in NFA_TYPES] if k in loose), "")


def is_other_nfa(record):
    """An approved Form 1 for anything but a suppressor (a type this tool doesn't know is kept as printed). A type that
    couldn't be read is neither: it gets reported. Nor is a printed hint in brackets: an older PDF was once read as
    "(SPECIFY)" and 6 suppressors were saved as other NFA items (2026-11-22)."""
    kind = " ".join((record.get("type") or "").upper().split())
    return bool(_loose(kind)) and _known_type(kind) != "SILENCER" and not re.fullmatch(r"\(.*\)", kind)


def nfa_short(kind):
    """SBR, SBS, ... (a type this tool doesn't know: as printed)."""
    return NFA_TYPES.get(kind, (kind,))[0]


def nfa_counts(rows):
    """"3 SBRs, 1 SBS" for rows of data/sbrs.csv, in the order of NFA_TYPES."""
    from collections import Counter
    n = Counter(r.get("type") or NFA_DEFAULT_TYPE for r in rows)
    known = [k for k in NFA_TYPES if n[k]] + sorted(k for k in n if k not in NFA_TYPES)
    return ", ".join(f"{n[k]} {NFA_TYPES[k][0 if n[k] == 1 else 1] if k in NFA_TYPES else k}" for k in known) or "0 other NFA items"


def nfa_label(rows):
    """What to call these rows on a screen: "SBRs" while they're all SBRs, else "Other NFA items"."""
    kinds = {r.get("type") or NFA_DEFAULT_TYPE for r in rows}
    return NFA_TYPES[kinds.pop()][1] if len(kinds) == 1 and kinds <= set(NFA_TYPES) else "Other NFA items"


def load_sbrs():
    if server_settings():
        rows = [dict(r) for r in _server_inventory()[1]]
    elif not _sbrs_path().exists():
        return []
    else:
        with _sbrs_path().open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    for r in rows:
        for c in SBR_COLS:
            r[c] = (r.get(c) or "").strip()
        r["type"] = r["type"] or NFA_DEFAULT_TYPE
    return rows


def save_sbrs(rows):
    if server_settings():
        raise ServerReadOnly(SERVER_READ_ONLY)
    tmp = _sbrs_path().with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SBR_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: serial_key(r["serial_number"])))
    tmp.replace(_sbrs_path())


def record_sbrs(records):
    """Add/refresh approved non-suppressors (SBRs, SBSs, ...) in data/sbrs.csv (keyed by eForm ID, else serial). Returns
    how many were added."""
    rows = load_sbrs()
    key = lambda r: r["eform_id"] or r["serial_number"].upper()  # noqa: E731
    by = {key(r): r for r in rows}
    added, changed = 0, False
    for rec in records:
        if not is_other_nfa(rec) or not rec["approved"] or not rec["serial"].strip():
            continue
        new = {"serial_number": rec["serial"].strip(), "type": " ".join(rec["type"].upper().split()), "caliber": rec["caliber"], "model": rec["model"],
               "barrel_length": rec["barrel_length"], "overall_length": rec["overall_length"],
               "approved_date": rec["approval_date"], "eform_id": rec["eform_id"],
               "control_number": rec.get("control_number", ""), "conditions": rec.get("conditions", "")}
        old = by.get(key(new))
        if old is None:
            rows.append(dict(new, _changed=True))
            by[key(new)] = rows[-1]
            added += 1
            changed = True
        elif any(v and old.get(k) != v for k, v in new.items()):
            old.update({k: v for k, v in new.items() if v}, _changed=True)
            changed = True
    if changed and server_settings():
        send = [r for r in rows if r.get("_changed")]
        # A server from before types (API 1) would drop the type and show the row as an SBR: don't let it.
        if any(r["type"] != NFA_DEFAULT_TYPE for r in send) and (_server().get("/api/hello").get("api") or 1) < 2:
            raise ServerReadOnly("Your inventory server needs updating before it can record this type of firearm: "
                                 f"{nfa_counts([r for r in send if r['type'] != NFA_DEFAULT_TYPE])}. "
                                 "Update it, then run Check approvals again.")
        _server_op("record_sbrs", {"rows": send})
    elif changed:
        save_sbrs(rows)
    # filed by this tool: done once its approval is in data/sbrs.csv (a server takes it out of its queue in record_sbrs)
    if not server_settings() and SUPP.exists():
        approved = {r["serial_number"].upper() for r in rows}
        queue = load_rows()
        keep = [r for r in queue if not (is_nfa_row(r) and r["status"] in ("filed", "pending")
                                         and r["serial_number"].upper() in approved)]
        if len(keep) != len(queue):
            save_rows(keep)
    return added


def drop_misread_nfa(records):
    """Take out of data/sbrs.csv the rows whose eForm ID is now read as a SUPPRESSOR: older approvals once read with the
    type "(SPECIFY)" were saved there (a user's 6, 2026-11-22). Local inventory only (the server has no remove; its
    rows stay). Returns how many were removed."""
    if server_settings():
        return 0
    sup = {r["eform_id"] for r in records if _known_type(r.get("type")) == "SILENCER" and r.get("eform_id")}
    rows = load_sbrs()
    keep = [r for r in rows if not (r.get("eform_id") and r["eform_id"] in sup)]
    if len(keep) != len(rows):
        save_sbrs(keep)
    return len(rows) - len(keep)


def approved_pdf_for(row):
    """The saved approved Form 1 PDF for an inventory row (via out/approved/index.csv, else <serial>.pdf), or None.
    With a server: the server's copy, fetched into out/server-pdfs/ (the copy there is used while it's unreachable)."""
    if server_settings():
        import remote
        path = OUT / "server-pdfs" / (re.sub(r"[^A-Za-z0-9._-]", "_", row["serial_number"]) + ".pdf")
        try:
            data = _server().pdf(row["serial_number"])
        except remote.ServerError:
            return path if path.is_file() else None
        if data is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path
    return _saved_approved_pdf(row)


def _saved_approved_pdf(row):
    """The approval PDF Check approvals saved on THIS computer (out/approved/), whatever the mode, or None."""
    folder = OUT / "approved"
    name = _approved_index(folder).get(row.get("eform_id", "")) if folder.exists() else None
    for candidate in filter(None, [name, f"{row['serial_number']}.pdf"]):
        if (folder / candidate).is_file():
            return folder / candidate
    return None


EXPORT_COLS = [("Serial", "serial_number", 10), ("Caliber", "caliber", 9), ("Length (in)", "overall_length", 11),
               ("Status", "status", 11), ("Filed", "filed_date", 11), ("Approved", "approved_date", 11),
               ("Days to approve", "_wait", 15), ("Conditions", "conditions", 60), ("Application #", "application_number", 14), ("eForm ID", "eform_id", 11),
               ("Control #", "control_number", 14), ("Built", "built", 7), ("Design", "design", 30)]


def export_inventory(dest=None):
    """Write the inventory to an .xlsx (serial order, bold frozen header, filters). Returns the path."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    dest = Path(dest or OUT / f"inventory_{datetime.now():%Y-%m-%d}.xlsx")
    dest.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventory"
    ws.append([c[0] for c in EXPORT_COLS])
    for row in sorted(load_rows(), key=lambda r: serial_key(r["serial_number"])):
        row = dict(row, _wait=wait_days(row) if row["status"] == "approved" else None)  # filed -> approved
        vals = [row[c[1]] if c[1] == "_wait" else row[c[1]] or ("queued" if c[1] == "status" else "") for c in EXPORT_COLS]
        ws.append([float(v) if c[1] == "overall_length" and re.fullmatch(r"\d+(\.\d+)?", v) else v for c, v in zip(EXPORT_COLS, vals)])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for i, (_, _, width) in enumerate(EXPORT_COLS, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    sbrs = load_sbrs()
    if sbrs:
        cols = [("Serial", "serial_number", 10), ("Type", "type", 26), ("Caliber/gauge", "caliber", 13), ("Model", "model", 14), ("Barrel (in)", "barrel_length", 11),
                ("Overall (in)", "overall_length", 11), ("Approved", "approved_date", 11), ("Conditions", "conditions", 60),
                ("eForm ID", "eform_id", 11), ("Control #", "control_number", 14)]
        ws2 = wb.create_sheet(nfa_label(sbrs))
        ws2.append([c[0] for c in cols])
        for row in sorted(sbrs, key=lambda r: serial_key(r["serial_number"])):
            ws2.append([float(row[c[1]]) if c[1].endswith("length") and re.fullmatch(r"\d+(\.\d+)?", row[c[1]]) else row[c[1]]
                        for c in cols])
        for cell in ws2[1]:
            cell.font = Font(bold=True)
        for i, (_, _, width) in enumerate(cols, 1):
            ws2.column_dimensions[ws2.cell(row=1, column=i).column_letter].width = width
        ws2.freeze_panes = "A2"
        ws2.auto_filter.ref = ws2.dimensions
    wb.save(dest)
    return dest


# ---------- Backups (automatic; menu: Settings > Backups; CLI: backup) ----------
# The records (inventory, settings, filing log, approval index) live only in this folder. Two kinds of copy:
#  - records ZIPs (a few KB: the CSV/YAML/JSON files only), out/backups/records-<date>.zip, written on a day the
#    records changed (rewritten while they keep changing that day, so an earlier day survives a bad change); the
#    newest BACKUP_KEEP_DAYS kept;
#  - with a backup folder (`backup_folder` in trust.yaml: a USB drive, a cloud folder), everything is copied to
#    <folder>/eForms Form 1 backup/files/ (data, forms, the approval PDFs, ...: only new or changed files, nothing is
#    ever deleted there) plus the same records ZIPs in records/. A whole copy every day would not fit: the approval
#    PDFs and CLEO copies run to hundreds of MB, and they never change once saved.
# Run by the menu each time the home screen is shown (cheap: compares file sizes and times); never raises.
BACKUP_KEEP_DAYS = 60
BACKUP_DIR = "eForms Form 1 backup"
BACKUP_STALE_DAYS = 7  # the home screen warns when the backup folder hasn't been reached for this long
_BACKUP_SKIP = {"server.yaml", "server_cache.json", ".DS_Store"}  # the server pairing key (never copied) and a cache
_RECORD_SUFFIXES = (".csv", ".yaml", ".yml", ".json", ".jsonl")
BACKUP_README = """eForms Form 1 helper: backup

files/    a copy of the helper's data, forms and out folders (your records, settings, approval PDFs, CLEO copies).
          Nothing is ever deleted here; a changed file is replaced by its newest version.
files/elsewhere/
          files your settings use that sit outside the helper's folder (fingerprints, photo, trust document...).
records/  just the records (inventory, settings, filing log), one ZIP for each day they changed, the newest {keep}.

To put it back on a new computer: install the helper, then copy the data, forms and out folders from files/ into
the helper's folder. Choose the files from files/elsewhere again in Settings (or put them back where they were).
To go back to an earlier day's records, unzip one from records/ into the helper's folder (replace when asked).

This holds personal files (fingerprints, photo, trust document, signed 5320.23s): keep it private.
"""


def backup_folder(trust=None):
    """The backup folder from the settings (a str), or None."""
    if trust is None:
        try:
            trust = load_trust() or {}
        except FileNotFoundError:
            return None
    return str(trust.get("backup_folder") or "").strip() or None


def _settings_files(trust):
    """(label, path) of the files the settings point to (fingerprints, photos, trust document, documents). Labels
    name no one."""
    f = trust.get("files") or {}
    out = [("your-fingerprint-file", f.get("eft")), ("your-photo", f.get("photo")), ("trust-document", f.get("trust_document"))]
    out += [(f"document-{i}", d.get("path")) for i, d in enumerate(trust.get("docs") or [], 1)]
    for n, p in enumerate(other_rps(trust), 2):
        out += [(f"responsible-person-{n}-fingerprint-file", p.get("eft")), (f"responsible-person-{n}-photo", p.get("photo"))]
    return [(label, Path(str(p))) for label, p in out if p]


def _backup_sources(trust):
    """(path, name) of every file a backup copies: data/ (minus the server pairing key and its cache), forms/, the
    filing log, out/approved/, out/cleo/ (minus the mail packets, made from the rest), and files the settings use
    from outside those folders (as elsewhere/<label>-<name>)."""
    out = []

    def add(base, label):
        if base.is_file():
            out.append((base, label))
        elif base.is_dir():
            for p in sorted(base.rglob("*")):
                if (p.is_file() and p.name not in _BACKUP_SKIP and not p.name.endswith((".tmp", ".update-tmp"))
                        and not (label == "out/cleo" and p.name.startswith("CLEO packet "))):
                    out.append((p, f"{label}/{p.relative_to(base).as_posix()}"))

    add(DATA, "data")
    add(FORMS, "forms")
    add(OUT / "submissions.csv", "out/submissions.csv")
    add(OUT / "approved", "out/approved")
    add(OUT / "cleo", "out/cleo")
    inside = [DATA.resolve(), FORMS.resolve()]
    for label, p in _settings_files(trust):
        try:
            rp = p.expanduser().resolve()
            if rp.is_file() and not any(rp == d or d in rp.parents for d in inside):
                out.append((rp, f"elsewhere/{label}-{rp.name}"))
        except OSError:
            continue
    return out


def _signature(files):
    import hashlib
    h = hashlib.sha256()
    for p, name in files:
        st = p.stat()
        h.update(f"{name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()


def _backup_state_path():
    return OUT / "backups" / "state.json"


def _backup_state():
    try:
        return json.loads(_backup_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_backup_state(state):
    path = _backup_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _records_zip(folder, records, today):
    """Write <folder>/records-<today>.zip (replacing today's), checked; prune all but the newest BACKUP_KEEP_DAYS."""
    import zipfile
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"records-{today}.zip"
    tmp = folder / f"records-{today}.zip.tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p, name in records:
            z.write(p, name)
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None or len(z.namelist()) != len(records):
            raise OSError("the records backup didn't read back")
    os.replace(tmp, dest)
    for old in sorted(folder.glob("records-????-??-??.zip"))[:-BACKUP_KEEP_DAYS]:
        old.unlink()
    return dest


def _copy_file(src, dest):
    """Copy keeping the modification time (so the next run sees it unchanged); tmp + replace, never half a file."""
    import shutil
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copyfile(src, tmp)
    st = src.stat()
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, dest)


def _mirror(files, root):
    """Copy each file to root/files/<name> unless a copy of the same size and time is there (2 s slack: FAT/exFAT
    drives keep times in 2 s steps). Never deletes. Returns (files copied, bytes copied)."""
    n = size = 0
    for p, name in files:
        dest = root / "files" / name
        st = p.stat()
        try:
            ds = dest.stat()
            if ds.st_size == st.st_size and abs(ds.st_mtime - st.st_mtime) <= 2:
                continue
        except FileNotFoundError:
            pass
        _copy_file(p, dest)
        n, size = n + 1, size + st.st_size
    return n, size


def run_backups(force=False, now=None):
    """Make the backups that are due (see above). force = check every file in the backup folder even if nothing
    changed here (Back up now). Returns {"records": path|None, "folder": "ok"|"missing"|None, "copied": (files, bytes),
    "error": str|None}; never raises."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    res = {"records": None, "folder": None, "copied": (0, 0), "error": None}
    try:
        try:
            trust = load_trust() or {}
        except FileNotFoundError:
            trust = {}
        files = _backup_sources(trust)
        records = [(p, name) for p, name in files if name.endswith(_RECORD_SUFFIXES) and not name.startswith("elsewhere/")]
        if not records:
            return res
        state = _backup_state()
        rsig = _signature(records)
        if rsig != state.get("records"):
            res["records"] = _records_zip(OUT / "backups", records, today)
            state["records"] = rsig
            _save_backup_state(state)
        folder = backup_folder(trust)
        if folder:
            base = Path(folder).expanduser()
            if not base.is_dir():
                res["folder"] = "missing"
                return res
            root = base / BACKUP_DIR
            fsig = _signature(files)
            if force or fsig != state.get("folder_sig") or state.get("folder") != str(root):
                res["copied"] = _mirror(files, root)
                _records_zip(root / "records", records, today)
                readme = root / "README.txt"
                text = BACKUP_README.format(keep=BACKUP_KEEP_DAYS)
                if not readme.exists() or readme.read_text(encoding="utf-8", errors="replace") != text:
                    readme.write_text(text, encoding="utf-8")
                state.update(folder_sig=fsig, folder=str(root))
            state["folder_ok"] = now.isoformat(timespec="seconds")
            _save_backup_state(state)
            res["folder"] = "ok"
    except Exception as e:  # e.g. a drive pulled out mid-copy, a file Excel has locked: tried again next time
        res["error"] = str(e).splitlines()[0] if str(e) else type(e).__name__
    return res


def backup_stale_days(now=None):
    """Days since the backup folder was last reached, when a folder is set and that's BACKUP_STALE_DAYS or more
    (never reached since it was chosen = counted from then); else None."""
    if not backup_folder():
        return None
    state = _backup_state()
    since = state.get("folder_ok") or state.get("folder_set")
    try:
        days = ((now or datetime.now()) - datetime.fromisoformat(since)).days if since else None
    except ValueError:
        return None
    return days if days is not None and days >= BACKUP_STALE_DAYS else None


def note_backup_folder_set(now=None):
    """Settings > Backups chose a folder: the stale-folder warning counts from now until it's first reached."""
    state = _backup_state()
    for k in ("folder_ok", "folder_sig", "folder"):
        state.pop(k, None)
    state["folder_set"] = (now or datetime.now()).isoformat(timespec="seconds")
    _save_backup_state(state)


def cmd_backup(args):
    res = run_backups(force=True)
    print(f"records: {res['records'] or 'unchanged since the last backup'}")
    if res["folder"] == "ok":
        print(f"backup folder: {res['copied'][0]} file(s) copied")
    elif res["folder"] == "missing":
        print("backup folder: not found (is the drive connected?)")
    else:
        print("backup folder: none set (Settings > Backups)")
    if res["error"]:
        print("problem:", res["error"])
        sys.exit(1)


# ---------- 5320.23 PDF ----------
# Each responsible person signs their own 5320.23. Yours is forms/5320.23.pdf; another responsible person's template is
# forms/5320.23_<tag>.pdf (`template` in their settings entry) and is stamped to out/5320.23_<serial>.<tag>.pdf.
def _rp_tag(person):
    """'5320.23_jane-doe.pdf' -> 'jane-doe' (None = you)."""
    return Path(person["template"]).stem.split("_", 1)[-1] if person else None


def _find_5320(serial, warn=False, person=None):
    """Newest generated 5320.23 for a serial (5320.23_<serial>.pdf, or the older 5320.23_<row>_<serial>.pdf);
    with person (another responsible person's settings entry), theirs: 5320.23_<serial>.<tag>.pdf."""
    pattern = f"5320.23_{serial}.{_rp_tag(person)}.pdf" if person else f"5320.23*_{serial}.pdf"
    hits = sorted(OUT.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not hits:
        who = f" of the responsible person titled {person.get('title', '')!r}" if person else ""  # (no names on screen)
        sys.exit(f"No generated 5320.23{who} for serial {serial}; run fill-5320 first.")
    if warn and len(hits) > 1:
        emit("info", f"({len(hits)} 5320.23 files for {serial}; using the newest: {hits[-1].name})")
    return hits[-1]


def _check_5320(path, item, today, cleo=False):
    """Problems with a generated 5320.23: serial not on page 1, or signature date not today.
    cleo (a CLEO copy): the caliber must be on page 1 and the serial on NEITHER page (ATF leaves it off the CLEO's copy)."""
    import pymupdf
    doc = pymupdf.open(path)
    probs = []
    if cleo:
        if not _token_match(item["caliber"], doc[0].get_text()):
            probs.append(f"caliber {item['caliber']} not found in {path.name}")
        if any(_token_match(item["serial_number"], pg.get_text()) for pg in doc):
            probs.append(f"{path.name} shows the serial (the CLEO copy must not)")
    elif not _token_match(item["serial_number"], doc[0].get_text()):
        probs.append(f"serial {item['serial_number']} not found in {path.name}")
    if today not in doc[1].get_text():
        probs.append(f"{path.name} is not dated today ({today}); regenerate it if filing today")
    return probs


def cmd_fields(args):
    for name, f in (PdfReader(args.pdf).get_fields() or {}).items():
        print(f"{name}\t{f.get('/FT')}\t{f.get('/_States_', '')}")


def stamp_5320(row, when, out_dir=None, person=None, cleo=False):
    """Stamp one row's caliber (4d), serial (4e), signature date and CLEO line onto your completed 5320.23. For an SBR or
    other NFA item (is_nfa_row) also the type (4a), the ORIGINAL manufacturer's name and address (4b) and the model (4c),
    which the template has as SILENCER / your trust / N/A.
    Old values are redacted (text removed), photo/signature untouched. Returns (path, keys not found after).
    person: another responsible person's settings entry: their template, and their CLEO line is left as they made it
    (item 5 is the CLEO where THEY live; the CLEO in your settings is yours).
    cleo: the CLEO copy instead (cleo_template(); it has no item 4e, so no serial) -> out/cleo/...cleo.pdf."""
    import pymupdf
    out_dir = Path(out_dir or (_cleo_dir() if cleo else OUT))
    out_dir.mkdir(parents=True, exist_ok=True)
    # (page, key) -- located by searching the template, so layout changes are caught
    nfa = is_nfa_row(row)
    targets = ([(0, "4a"), (0, "4b"), (0, "4c")] if nfa else []) + [(0, "4d")] + ([] if cleo else [(0, "4e")]) \
        + [(1, "date")] + ([] if person else [(0, "cleo")])
    new = {"4d": row["caliber"], "4e": row["serial_number"], "date": when}
    if nfa:
        new.update({"4a": row["type"].upper(), "4c": row.get("model") or "N/A",
                    "4b": "\n".join(x for x in (row.get("mfr_name", ""), row.get("mfr_address", "")) if x.strip())})
    if not person:
        cl = load_trust()["cleo"]
        new["cleo"] = f"{cl['official']}, {cl['title']}"
    doc = pymupdf.open(cleo_template(person) if cleo else FORMS / (person["template"] if person else "5320.23.pdf"))
    boxes = {}
    for pg, k in targets:
        boxes[k] = _find_value_box(doc[pg], k)
        doc[pg].add_redact_annot(pymupdf.Rect(boxes[k]) + (-1, -1, 30, 1), fill=(1, 1, 1))
    for pg in (0, 1):
        doc[pg].apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    for pg, k in targets:
        x0, y0, _, y1 = boxes[k]
        if k == "4b":  # one line each, starting where the first typed line was
            for n, line in enumerate(str(new[k]).splitlines()):
                doc[pg].insert_text((x0, y0 + 8.5 + 9 * n), line, fontname="helv", fontsize=8)
            continue
        doc[pg].insert_text((x0, y1 - 2.5), str(new[k]), fontname="helv", fontsize=8)
    dest = out_dir / (f"5320.23_{row['serial_number']}" + (f".{_rp_tag(person)}" if person else "") + (".cleo" if cleo else "") + ".pdf")
    doc.save(dest)
    chk = pymupdf.open(dest)  # re-read: each new value must be in the text layer where it was stamped
    missing = [k for pg, k in targets
               if not chk[pg].search_for(str(new[k]).splitlines()[0] if new[k] else "", clip=pymupdf.Rect(boxes[k]) + (-2, -6, 250, 6))]
    return dest, missing


def stamp_all(row, when, out_dir=None):
    """Stamp the 5320.23 of every responsible person (you first) for one row: [(path, keys not found after)]."""
    return [stamp_5320(row, when, out_dir)] + [stamp_5320(row, when, out_dir, p) for p in other_rps(load_trust())]


# ---------- CLEO notification copies ----------
# The CLEO gets copies too: of the Form 1 (eForms builds it: the last pages of a SUBMITTED form's "View PDF", marked
# "CLEO Copy") and of each responsible person's 5320.23 (the generator's "CLEO copy": ATF's CLEO-copy pages, with no
# SSN/UPIN, no photo box and no item 4e). Neither shows the serial: ATF keeps it from the CLEO, so nothing made here for
# the CLEO may show it either. Everything is optional: filing works without it. Files: out/cleo/.
def _cleo_dir():
    return OUT / "cleo"


def cleo_template(person=None):
    """The CLEO copy of a 5320.23 template: forms/5320.23.cleo.pdf (yours), forms/5320.23_<tag>.cleo.pdf (theirs)."""
    return FORMS / (f"{Path(person['template']).stem}.cleo.pdf" if person else "5320.23.cleo.pdf")


def copy_kind(path):
    """'ATF' or 'CLEO' from a 5320.23's page-1 footer ("ATF Copy" / "CLEO Copy"); None if it has neither."""
    import pymupdf
    lines = {" ".join(ln.split()) for ln in pymupdf.open(path)[0].get_text().splitlines()}
    return "CLEO" if "CLEO Copy" in lines else "ATF" if "ATF Copy" in lines else None


def stamp_cleo_all(row, when):
    """Stamp the CLEO copy of every responsible person who has one (you first) for one row, dated `when`.
    Returns [(n, path or None, problems)]: n = 1 for you, 2.. for the others. Never raises: CLEO copies are optional."""
    out = []
    for n, person in enumerate([None] + other_rps(load_trust()), 1):
        if not cleo_template(person).is_file():
            continue
        try:
            if copy_kind(cleo_template(person)) != "CLEO":  # e.g. the ATF copy: it would show the CLEO an SSN and serial
                out.append((n, None, ["its CLEO-copy template isn't a CLEO copy (Settings > CLEO copies)"]))
                continue
            dest, missing = stamp_5320(row, when, person=person, cleo=True)
            out.append((n, dest, [f"could not place: {', '.join(missing)}"] if missing else _check_5320(dest, row, when, cleo=True)))
        except (Exception, SystemExit) as e:
            out.append((n, None, [str(getattr(e, "code", None) or e).splitlines()[0]]))
    return out


def cmd_fill_5320(args):
    from datetime import date
    rows = load_rows()
    when = args.date or date.today().strftime("%m/%d/%Y")
    if args.row is not None:
        _check_row_num(rows, args.row)
    for n, row in enumerate(rows, 1):
        if args.row is not None and n != args.row:
            continue
        if args.row is None and row["status"] in SKIP:
            continue
        for dest, missing in stamp_all(row, when):
            print("wrote", dest, f"  !! NOT FOUND after stamping: {', '.join(missing)}" if missing else "")
        for _, dest, probs in stamp_cleo_all(row, when):
            print("wrote", dest, f"  !! {'; '.join(probs)}" if probs else "") if dest else print("  !! CLEO copy:", "; ".join(probs))
    print("Verify each PDF visually before use.")


def _find_value_box(page, key):
    """Bbox of the existing typed value: below the 4d/4e label, or above the signature 'Date' label.
    Works on templates made with the 5320.23 generator (its text is in Nimbus fonts); see CLAUDE.md."""
    if key == "cleo":  # item 5 "NAME, TITLE" line: right of the agency name, above its (garbled-font) label
        official = _near(page, "Agency or Department Name", (250, 450), (-25, 0))
        if official:
            return official[0]["bbox"]
        sys.exit("Could not find the CLEO official's name/title (item 5) on forms/5320.23.pdf. "
                 "Was it made with the 5320.23 generator?")
    if key == "4b":  # maker name + address: up to 3 typed lines under its label, returned as one box
        lab = page.search_for("Name and Address of Maker")
        if not lab:
            sys.exit("label for 4b not found")
        lines = [sp["bbox"] for sp in _near(page, "Name and Address of Maker", (-40, 40), (0, 45))]
        if not lines:
            sys.exit("could not locate existing value for 4b")
        return (min(b[0] for b in lines), min(b[1] for b in lines), max(b[2] for b in lines), max(b[3] for b in lines))
    word = {"4a": "Type of Firearm", "4c": "Firearm Model", "4d": "Caliber", "4e": "Serial Number", "date": "Date"}[key]
    hits = page.search_for(word)
    if not hits:
        sys.exit(f"label for {key} not found")
    spans = [sp for blk in page.get_text("dict")["blocks"] for ln in blk.get("lines", []) for sp in ln["spans"]
             if "Nimbus" in sp["font"] and sp["text"].strip()]
    for label in hits:  # a label can occur more than once (the blank form has a 2nd "Date"): use the one with a value
        best = None
        for sp in spans:
            r = sp["bbox"]
            if abs(r[0] - label.x0) > 40:
                continue
            dy = (label.y0 - r[3]) if key == "date" else (r[1] - label.y1)  # date sits above its label, 4d/4e below
            if 0 <= dy < 30 and (best is None or dy < best[0]):
                best = (dy, r)
        if best:
            return best[1]
    sys.exit(f"could not locate existing value for {key}")


US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
             "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
             "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
             "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
             "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
             "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
             "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
             "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
             "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}
# Place of birth: the portal's Birth Country dropdown spells the US "UNITED STATES OF AMERICA"; Birth State is asked
# only for it (born abroad, e.g. on a military base: the state stays empty, as eForms allows; issue #6).
US_COUNTRY = "UNITED STATES OF AMERICA"
_US_SPELLINGS = {"", US_COUNTRY, "UNITED STATES", "USA", "U.S.A.", "US", "U.S.", "AMERICA"}


def born_in_us(person):
    """True unless the person's place_of_birth names another country (no country = the US: settings saved before
    1.6.2 always meant it)."""
    return str(((person or {}).get("place_of_birth") or {}).get("country") or "").strip().upper() in _US_SPELLINGS


def birth_country(person):
    """The Birth Country option to pick in the portal, uppercased as it lists them."""
    return US_COUNTRY if born_in_us(person) else str(person["place_of_birth"]["country"]).strip().upper()


def birth_state(person):
    """The Birth State option ("" when born outside the US: the portal leaves it empty)."""
    return str(((person or {}).get("place_of_birth") or {}).get("state") or "").strip().upper() if born_in_us(person) else ""


# Firearm description (item 4h): at most 100 characters. The Form 1 PDF eForms has made since about October 2026 prints
# only the first 100, though the portal's box takes more (the old default, 102 long, came out "...IS APPROV"; issue #7).
DESCRIPTION_MAX = 100


def description_problem(trust):
    """Why the firearm description is too long, else ""."""
    desc = str(((trust or {}).get("firearm") or {}).get("description") or "").strip()
    if len(desc) <= DESCRIPTION_MAX:
        return ""
    end = " ".join(desc[:DESCRIPTION_MAX].split()[-4:])
    return (f"the firearm description is {len(desc)} characters: the Form 1 PDF eForms makes prints only the first "
            f"{DESCRIPTION_MAX} (it would end \"...{end}\"); shorten it in Settings")


STREET_SUFFIXES = {"ST", "STREET", "RD", "ROAD", "AVE", "AVENUE", "AV", "DR", "DRIVE", "BLVD", "LN", "LANE", "WAY", "CT", "COURT",
                   "CIR", "CIRCLE", "PL", "PLACE", "HWY", "HIGHWAY", "PIKE", "PKWY", "PARKWAY", "TER", "TERRACE", "TRL",
                   "TRAIL", "SQ", "LOOP", "RUN", "ROW", "XING", "EXT"}


def _near(page, label, dx, dy):
    """Typed values (generator text: Nimbus fonts) whose top-left lies within dx/dy points of a printed label,
    top to bottom. The form's own labels are in other fonts; some are font-garbled, so anchor on readable ones."""
    hits = page.search_for(label)
    if not hits:
        return []
    lab = hits[0]
    vals = [sp for blk in page.get_text("dict")["blocks"] for ln in blk.get("lines", []) for sp in ln["spans"]
            if "Nimbus" in sp["font"] and sp["text"].strip()
            and dx[0] <= sp["bbox"][0] - lab.x0 <= dx[1] and dy[0] <= sp["bbox"][1] - lab.y0 <= dy[1]]
    return sorted(vals, key=lambda sp: (sp["bbox"][1], sp["bbox"][0]))


def _split_address(line):
    """'1 MAIN ST SPRINGFIELD OH 12345' -> street, city, state name, zip (best effort; the user confirms)."""
    m = re.match(r"^(.*?)\s+([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$", line.strip().upper())
    if not m:
        return {}
    rest, st, zp = m.groups()
    words = rest.split()
    cut = max((i for i, w in enumerate(words) if w.strip(".,") in STREET_SUFFIXES), default=None)
    street, city = (" ".join(words[:cut + 1]), " ".join(words[cut + 1:])) if cut is not None else (rest, "")
    return {"street": street.strip(" ,"), "city": city.strip(" ,"), "state": US_STATES.get(st, ""), "zip": zp}


def read_template(path):
    """Answers the generator printed on a signed 5320.23, as {settings path: value}, to prefill Settings.
    Reads only what the settings need. Never the SSN/UPIN (item 3f): it isn't read, and anything SSN-shaped is dropped."""
    import pymupdf
    doc = pymupdf.open(path)
    p1, p2 = doc[0], doc[1]
    first = lambda spans: spans[0]["text"].strip() if spans else ""  # noqa: E731
    out = {}
    applicant = _near(p1, "Full Legal Name and Address of Applicant", (-30, 260), (8, 45))
    out["trust.name"] = first(applicant)
    rp = _near(p1, "3b.", (-300, -10), (8, 45))  # item 3a (its label is font-garbled): left of 3b, same rows
    if rp:
        out["responsible_persons.0.home_address.state"] = _split_address(rp[-1]["text"]).get("state", "")
    out["responsible_persons.0.dob"] = first([sp for sp in _near(p1, "3g.", (-5, 60), (0, 25))
                                              if re.fullmatch(r"\d{2}/\d{2}/\d{4}", sp["text"].strip())])
    out["firearm.model"] = first(_near(p1, "4c.", (-5, 100), (5, 25)))
    out["cleo.department"] = first(_near(p1, "Agency or Department Name", (-5, 250), (-25, 0)))
    official = first(_near(p1, "Agency or Department Name", (250, 450), (-25, 0)))
    if ", " in official:
        out["cleo.official"], out["cleo.title"] = (x.strip() for x in official.rsplit(", ", 1))
    lines = [sp["text"].strip() for sp in _near(p1, "Full Mailing Address", (-5, 300), (-30, 0))]
    addr = _split_address(lines[-1]) if lines else {}
    if len(lines) > 1 and addr:  # "STREET" / "CITY ST ZIP" on separate lines: the city is the whole last-line prefix
        addr["street"], addr["city"] = " ".join(lines[:-1]), " ".join(filter(None, [addr["street"], addr["city"]]))
    elif lines:  # one line: split street from city at the last street suffix (best effort; the user confirms)
        addr = _split_address(lines[0])
    out.update({f"cleo.address.{k}": v for k, v in addr.items()})
    birth = first(_near(p2, "State of Birth", (20, 250), (0, 15))).upper()
    out["responsible_persons.0.place_of_birth.state"] = next((v for v in US_STATES.values() if v.upper() == birth), "")
    country = first(_near(p2, "Country of Birth", (20, 250), (0, 15))).upper()  # 9c
    if country and country not in _US_SPELLINGS:  # born abroad: no state of birth
        out["responsible_persons.0.place_of_birth.country"] = country
        out.pop("responsible_persons.0.place_of_birth.state")
    return {k: v for k, v in out.items() if v and not re.search(r"\d{3}-?\d{2}-?\d{4}", v)}


NAME_SUFFIXES = ["JR", "SR", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]  # the portal's "Cadence" options


def read_person(path):
    """Another responsible person's answers on their signed 5320.23, as {key in their settings entry: value}:
    name (3a), home address (3a), email (3c), DOB, state of birth. Never the SSN (see read_template)."""
    import pymupdf
    p1 = pymupdf.open(path)[0]
    pre = read_template(path)
    out = {k.split(".", 2)[2]: v for k, v in pre.items() if k.startswith("responsible_persons.0.")}
    lines = [sp["text"].strip() for sp in _near(p1, "3b.", (-300, -10), (8, 45))]  # name / street / city ST zip
    words = lines[0].replace(",", " ").split() if lines else []
    if words and words[-1].strip(".").upper() in NAME_SUFFIXES and len(words) > 2:
        out["suffix"] = words.pop().strip(".").upper()
    if len(words) >= 2:
        out["first_name"], out["last_name"], out["middle_name"] = words[0], words[-1], " ".join(words[1:-1])
    addr = _split_address(lines[-1]) if len(lines) > 1 else {}
    if len(lines) > 2 and addr:  # "STREET" / "CITY ST ZIP": the city is the whole last-line prefix
        addr["street"], addr["city"] = " ".join(lines[1:-1]), " ".join(filter(None, [addr["street"], addr["city"]]))
    out.update({f"home_address.{k}": v for k, v in addr.items()})
    email = [sp["text"].strip() for sp in _near(p1, "3c.", (-5, 250), (5, 30)) if "@" in sp["text"]]
    if email:
        out["email"] = email[0]
    return {k: v for k, v in out.items() if v and not re.search(r"\d{3}-?\d{2}-?\d{4}", v)}


# ---------- Browser ----------
def _browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    # handle_sigint=False: Ctrl-C must not kill the browser, or the finally-logout can't run and the account locks
    ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport=None, handle_sigint=False)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(EFORMS_URL)
    return pw, ctx, page


def _dump_page(page):
    return page.evaluate("""() => {
            const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
            const lab = e => ((e.labels && e.labels[0] ? e.labels[0].innerText : e.getAttribute('aria-label')) || '').trim();
            const widgets = [...document.querySelectorAll('[role=combobox],[role=listbox],[role=option],[class*=select],[class*=dropdown],[class*=multi],[class*=race],[class*=ethnic],[id*=race i],[id*=ethnic i]')]
                .filter(vis).slice(0,40).map(e => ({tag: e.tagName.toLowerCase(), id: e.id, cls: String(e.className).slice(0,80), role: e.getAttribute('role') || '', text: e.innerText.trim().slice(0,50)}));
            const labels = [...document.querySelectorAll('label,legend')].filter(vis).map(e => ({for: e.htmlFor, text: e.innerText.trim().slice(0,70)})).filter(x => x.text);
            const dialogs = [...document.querySelectorAll('.modal, .modal-content, [role=dialog], [role=alertdialog], .ui-dialog, [class*=modal], [class*=popup]')]
                .filter(vis).map(e => e.innerText.trim().replace(/\\n{2,}/g, ' | ').slice(0, 2500)).filter(t => t.length > 15);
            // the question a field answers: the nearest enclosing block with some text besides the field's own
            const ctx = e => { const own = (e.innerText || e.value || '').trim().length;
                for (let p = e.parentElement, i = 0; p && i < 8; p = p.parentElement, i++) {
                    const t = (p.innerText || '').trim();
                    if (t.length > 1500) break;
                    if (t.length > own + 12) return t.replace(/\\s*\\n\\s*/g, ' | ').slice(0, 500);
                }
                return ''; };
            return {url: location.href, title: document.title, dialogs: [...new Set(dialogs)], pageText: document.body.innerText.replace(/\\n{2,}/g, " | ").slice(0, 20000), widgets, labels,
              controls: [...document.querySelectorAll('input,select,textarea,button,a[href],[role=button],[role=tab]')]
                .filter(vis).map(e => {
                  const o = {tag: e.tagName.toLowerCase(), id: e.id, name: e.name || undefined, type: e.type || undefined,
                             label: lab(e), text: ['BUTTON','A'].includes(e.tagName) || e.getAttribute('role') ? e.innerText.trim().slice(0,60) : undefined};
                  if (e.tagName === 'A') o.href = e.getAttribute('href');
                  if (e.disabled) o.disabled = true;
                  if (['radio','checkbox'].includes(e.type)) o.checked = e.checked;
                  if (['radio','checkbox'].includes(e.type)) o.value = e.value;
                  if (e.tagName === 'SELECT') { o.options = [...e.options].map(x => x.value && x.value !== x.text ? `${x.text} [${x.value}]` : x.text).slice(0,40);
                                                o.selected = e.selectedIndex >= 0 ? e.options[e.selectedIndex].text : undefined; }
                  if (['INPUT','SELECT','TEXTAREA'].includes(e.tagName)) o.context = ctx(e);
                  return Object.fromEntries(Object.entries(o).filter(([k,v]) => v !== undefined && v !== ''));
                })};
        }""")


def _capture_loop(page, save_to=None, ctx=None):
    """Press Enter to record the current page (the newest tab, if ctx is given); 'q' quits.
    With save_to, each record is appended there and a one-line summary is printed; otherwise the dump is printed."""
    while ask("> ").strip().lower() != "q":
        cur = ctx.pages[-1] if ctx is not None and ctx.pages else page
        d = _dump_page(cur)
        if not save_to:
            print(json.dumps(d, indent=1))
            continue
        _log_capture(save_to, d)
        print(f"  recorded {d['url']}  ({len(d['controls'])} controls, {len(d['dialogs'])} dialog(s)) -> {Path(save_to).name}")


def _log_capture(save_to, record):
    with open(save_to, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(), **record}) + "\n")


def _keep_pdfs(ctx, folder, save_to):
    """While capturing: save every PDF the portal sends (a download, or one opened in the viewer) and log its URL."""
    folder.mkdir(parents=True, exist_ok=True)
    seen = set()

    def keep(kind, url, write, name="portal.pdf"):
        path = folder / f"{datetime.now():%H%M%S_%f}_{re.sub(r'[^A-Za-z0-9._-]', '_', name)}"
        try:
            write(path)
        except Exception as e:
            print(f"  (could not save a PDF from {url}: {str(e).splitlines()[0]})")
            return
        _log_capture(save_to, {"event": f"pdf_{kind}", "url": url, "saved": str(path)})
        print(f"  saved PDF ({kind}): {path.name}")

    def on_response(r):
        if "pdf" in (r.headers.get("content-type") or "").lower() and r.url not in seen:
            seen.add(r.url)
            keep("response", r.url, lambda path: path.write_bytes(r.body()))

    def on_download(d):
        keep("download", d.url, d.save_as, d.suggested_filename or "download.pdf")

    def hook(pg):
        pg.on("download", on_download)
    ctx.on("response", on_response)
    for pg in ctx.pages:
        hook(pg)
    ctx.on("page", hook)


def _logout(page):
    """Best-effort click on the portal's Log Out link so the single-session lock is released."""
    try:
        page.get_by_text("Log Out", exact=False).first.click(timeout=3000)
        page.wait_for_timeout(1500)
        emit("info", "Logged out of eForms.")
    except Exception:
        if _on_login_page(page):
            emit("info", "Not logged in to eForms, so there was nothing to log out of.")
        else:
            emit("warn", "Could not find a Log Out link; log out manually in the browser before closing it.")


def _on_login_page(page):
    try:
        return "/adfs/" in page.url.lower() or "login" in page.url.lower()
    except Exception:
        return False


def _wait_for_login(page, prompt):
    """Ask the user to log in and press Enter; go on only once the browser shows a logged-in eForms page (one with a
    Log Out link). Pressing Enter while still on the ATF login page used to end in a 30 s timeout and a traceback.
    Another tab of the same browser that is logged in is used instead. Returns the page to use; q stops."""
    while True:
        if ask(prompt).strip().lower() == "q":
            raise SystemExit("Stopped before anything was done on eForms.")
        for tab in [page] + [t for t in page.context.pages if t is not page]:
            try:
                tab.get_by_text("Log Out", exact=False).first.wait_for(state="attached", timeout=5000 if tab is page else 500)
                if tab is not page:
                    tab.bring_to_front()
                return tab
            except Exception:
                pass
        emit("warn", "The browser isn't logged in to eForms yet" + (" (it's still on the ATF login page)" if _on_login_page(page) else "")
             + ". Finish logging in, MFA included, until your eForms home page shows, then press Enter (or type q to stop).")


def _tick(page, selector):
    """Check a checkbox/radio even when the real input is hidden behind a styled label.
    The portal hides its real inputs, so a visible label is clicked straight away, like a person would. Trying
    Playwright's check() first cost 4 s on every tick (it waits for the hidden input to become clickable)."""
    loc = page.locator(selector)
    if loc.is_checked():  # waits for the element; a radio or box already ticked is left alone (a click would untick)
        return
    el_id = loc.get_attribute("id")
    lab = page.locator(f"label[for='{el_id}']") if el_id else None
    if lab is not None and lab.count() and lab.first.is_visible():
        lab.first.click()
    else:
        try:
            loc.check(timeout=4000)
        except Exception:
            loc.evaluate("e => e.click()")
    for _ in range(20):  # the page may take a moment to show the tick
        if loc.is_checked():
            return
        page.wait_for_timeout(100)
    raise RuntimeError(f"could not tick {selector}")


def _set_pending(rows, idx, length, force=False):
    """Mark a row pending just before Sign & Submit. With a server, only its confirmation counts: this raises if it
    doesn't come, and the caller then doesn't click Sign & Submit."""
    r, today = rows[idx], datetime.now().strftime("%m/%d/%Y")
    length = r.get("overall_length") or length
    if server_settings():  # mfr_code: an SBR's code found on eForms this time (the server keeps it for a re-file)
        _server_op("mark_pending", {"serial": r["serial_number"], "version": r.get("version"), "filed_date": today,
                                    "overall_length": length, "force": bool(force), "mfr_code": r.get("mfr_code", "")})
        r.update(status="pending", filed_date=today, overall_length=length)
        return
    r.update(status="pending", filed_date=today, overall_length=length)
    save_rows(rows)


def _set_filed(rows, idx, num):
    """Record the application number (out/submissions.csv was written first). With a server: into the retry file,
    then sent. Returns False if the server hasn't confirmed it yet (it stays in the retry file)."""
    import uuid
    rows[idx].update(status="filed", application_number=num)
    if not server_settings():
        save_rows(rows)
        return True
    serial = rows[idx]["serial_number"]
    _outbox_add({"op": "mark_filed", "serial": serial, "number": num,
                 "body": {"serial": serial, "application_number": num, "request_id": uuid.uuid4().hex}})
    return not [e for e in outbox_replay() if e["serial"] == serial]


def resolve_pending(serial, number=None):
    """Resolve a pending row after checking the dashboard: filed (with its application number, also logged to
    out/submissions.csv) or, with no number, not filed (back in the queue)."""
    rows = load_rows()
    i = next(n for n, r in enumerate(rows) if r["serial_number"].upper() == serial.upper())
    if number:
        log_submission(rows[i], number)
        if server_settings():
            _server_op("mark_filed", {"serial": rows[i]["serial_number"], "application_number": number})
            return
        rows[i].update(status="filed", application_number=number, filed_date=rows[i]["filed_date"] or datetime.now().strftime("%m/%d/%Y"))
    elif server_settings():
        _server_op("unmark_pending", {"serial": rows[i]["serial_number"]})
        return
    else:
        rows[i]["status"] = ""
    save_rows(rows)


def _submit(page, item, bad, rows, idx, length="", force=False):
    """Opt-in: tick the certification and click Sign & Submit. You still enter the emailed code and your PIN."""
    if bad:
        emit("error", "*** NOT submitting: see the problem(s) above. ***"); return
    # Read-back above was clean: tick the verify box and move to the Certify page, where validation is reported.
    emit("section", "--- Certify ---", name="Certify")
    _tick(page, "#binary")
    page.locator("#nextBtn").click()
    try:
        page.locator("#signed").wait_for(timeout=20000)
    except Exception:
        emit("error", "*** NOT submitting: never reached the Certify page. ***"); return
    # Go on as soon as the portal says so (it used to be a fixed 1.5 s, which a slow validation would also have missed).
    try:
        page.get_by_text("The application has validated successfully").first.wait_for(state="attached", timeout=15000)
    except Exception:
        emit("error", "*** NOT submitting: Certify page does not say 'validated successfully'. ***"); return
    _tick(page, "#signed")
    # From here on the application may get filed: mark the row pending first, so a crash, timeout or Ctrl-C
    # can't leave it looking unfiled (and get it filed twice by the next run). With a server, only once it confirmed.
    try:
        _set_pending(rows, idx, length, force)
    except Exception as e:
        emit("error", f"*** NOT submitting: PENDING couldn't be recorded ({str(e).splitlines()[0]}). Nothing was filed. ***")
        return
    page.locator("#signAndSubmit").click()
    emit("banner", "Signed & submitted. In the browser: enter the 6-digit code from your email and click Verify,\n"
                   "then enter your eForms PIN and click Submit. Waiting up to 15 min for the confirmation...")
    try:
        page.get_by_text("successfully submitted").first.wait_for(timeout=15 * 60 * 1000)
    except Exception as e:
        emit("error", f"*** No confirmation seen ({str(e).splitlines()[0]}). It MAY have been submitted. ***\n"
                      f"Row {idx + 1} is marked PENDING. Check the dashboard, then resolve it (menu: Resolve a pending row).")
        return
    txt = page.locator(".modal, [role=dialog]").filter(has_text="successfully submitted").first.inner_text()
    m = re.search(r"application number is:?\s*(\d+)", txt)
    num = m.group(1) if m else "UNKNOWN"
    log = log_submission(item, num)
    if _set_filed(rows, idx, num):
        emit("done", f"SUBMITTED. Application number {num} logged to {log}; row {idx + 1} marked filed.",
             serial=item["serial_number"], number=num)
    else:
        emit("done", f"SUBMITTED. Application number {num} logged to {log}.", serial=item["serial_number"], number=num)
        emit("error", f"*** Your server hasn't recorded it yet: kept in {_outbox_path()} and sent again automatically. "
                      "The batch stops here. ***")
    try:
        page.locator("#OkBtn").click(timeout=3000)
    except Exception:
        pass


# ---------- Approved Form 1s (read-only) ----------
def _mmddyyyy(text):
    """Approval dates as MM/DD/YYYY: older approvals print 08/15/2026, newer ones (Oct 2026) "October 15, 2026"."""
    m = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", text.strip())
    if m:
        try:
            return datetime.strptime(f"{m[1][:3]} {m[2]} {m[3]}", "%b %d %Y").strftime("%m/%d/%Y")
        except ValueError:
            pass
    return text


def _us_date(text):
    """A date as MM/DD/YYYY (zero-padded), from MM/DD/YYYY or "October 15, 2026"; "" if it isn't one."""
    try:
        return datetime.strptime(_mmddyyyy(text or ""), "%m/%d/%Y").strftime("%m/%d/%Y")
    except ValueError:
        return ""


def read_approved_pdf(path):
    """Item 4 and ATF's decision block from an approved Form 1 PDF as eForms renders it ("View PDF").
    Values are located relative to the printed labels. The label font is learned from the labels themselves and
    ignored (in eForms' PDFs labels are TimesNewRomanPSMT; typed values are Helvetica, the serial "TimesNewRoman")."""
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)  # newer eForms PDFs have dangling refs ("cannot find object in xref"): harmless
    # Opened from bytes, so no file handle stays open (Windows can't rename or replace an open file).
    page = pymupdf.open("pdf", Path(path).read_bytes())[0]
    spans = [sp for blk in page.get_text("dict")["blocks"] for ln in blk.get("lines", []) for sp in ln["spans"] if sp["text"].strip()]
    label_fonts = {sp["font"] for sp in spans if re.search(r"Serial number|Caliber or gauge|Overall length|Type of firearm", sp["text"])}

    def value(label, dx, dy, pick=None):
        hits = [h for h in page.search_for(label) if pick is None or pick(h)]
        if not hits:
            return ""
        lab = hits[0]
        vals = sorted((sp for sp in spans if sp["font"] not in label_fonts and "Zapf" not in sp["font"]
                       and not re.fullmatch(r"\(.*\)", sp["text"].strip())  # printed hints like "(Specify)", never values
                       and dx[0] <= sp["bbox"][0] - lab.x0 <= dx[1] and dy[0] <= sp["bbox"][1] - lab.y0 <= dy[1]),
                      key=lambda sp: (sp["bbox"][1], sp["bbox"][0]))
        return " ".join(sp["text"].strip() for sp in vals)

    # ATF's decision line: "[ ] Approved (with ...)   [ ] Disapproved (for ...)". Find "Approved" ON that line: a banner
    # "APPROVED WITH CONDITIONS - SEE CONDITIONS ON THE LAST PAGE OF PDF" can also be on the page (search ignores case).
    dis = page.search_for("Disapproved")
    line_y = dis[0].y0 if dis else None
    approved = [h for h in page.search_for("Approved") if line_y is not None and abs(h.y0 - line_y) < 4 and h.x0 < dis[0].x0 - 30]

    def ticked(hits):  # a mark just left of the label, only drawn when the box is ticked: a ZapfDingbats check (newer
        # approvals) or a bold "X" (the Sept 2007 form of older approvals: a user's, 2026-11-22, all read as not ticked)
        return bool(hits) and any(("Zapf" in sp["font"] and 0 < hits[0].x0 - sp["bbox"][0] < 20
                                   or sp["text"].strip() in ("X", "x") and 0 < hits[0].x0 - sp["bbox"][0] < 30)
                                  and abs(sp["bbox"][1] - hits[0].y0) < 8 for sp in spans)
    below = (lambda h: h.y0 > line_y + 30) if line_y is not None else None
    # Item 9: "9. Date" right of the applicant's signature = the day it was signed, which in eForms is the day it was
    # submitted (checked on saved approvals 2026-11-05: the same day as the filing log for every one filed by this tool).
    nines = page.search_for("9.")
    item9 = lambda h: any(abs(n.y0 - h.y0) < 3 and 0 < h.x0 - n.x0 < 20 for n in nines)  # noqa: E731
    conditions = ""
    if page.search_for("APPROVED WITH CONDITIONS") and page.parent.page_count > 1:  # listed on the last page, above its heading
        last = [ln.strip() for ln in page.parent[-1].get_text().splitlines() if ln.strip()]
        cut = next((i for i, ln in enumerate(last) if ln.lower() == "approval conditions"), len(last))
        conditions = " ".join(last[:cut]) or "see the last page of the PDF"
    kind = value("Type of firearm", (-60, 120), (20, 40))
    if not _known_type(kind):
        # Older approvals (a user's, 2026-11-22: 6-digit eForm IDs) lay item 4 out differently: the type came out as
        # "(SPECIFY)", so 6 suppressors were listed as other NFA items. The typed type is the only span on page 1 that is
        # exactly a type name in capitals; the printed text spells them in lower case.
        kind = next((sp["text"].strip() for sp in spans if _known_type(sp["text"]) and sp["text"].strip().isupper()
                     and _known_type(sp["text"]) == _loose(sp["text"])), kind)
    return {"type": kind, "serial": value("g. Serial number", (-10, 120), (5, 22)),
            # The Sept 2007 form (older approvals) labels these "c. Caliber, / Gauge or / Size", "e. Of Barrel:" and
            # "f. Overall:"; its caliber sits ~58 pt below the label, under the printed "(Specify)", in a ~50 pt column.
            "caliber": value("c. Caliber or gauge", (-10, 75), (20, 40)) or value("c. Caliber,", (-10, 45), (35, 78)),
            "model": value("d. Model", (-10, 60), (5, 20)),
            "barrel_length": value("e. Barrel length", (-10, 50), (5, 22)) or value("e. Of Barrel:", (-10, 50), (5, 25)),
            "overall_length": value("Overall length", (-15, 50), (5, 22)) or value("f. Overall:", (-10, 50), (5, 25)),
            "approved": ticked(approved) and not ticked(dis),
            "approval_date": _mmddyyyy(value("Date", (-5, 80), (2, 20), pick=below)), "conditions": conditions,
            "filed_date": _us_date(value("Date", (-12, 80), (2, 20), pick=item9)),  # 2007 form: "9. Date" is one label
            "pdf_control_number": next((m.group(1) for pg in page.parent for m in [re.search(r"Control\s*Number:\s*(\d{10,11})\b", pg.get_text())] if m), "")}


# Read-only browsing: only these exact button texts are ever clicked, and never anything that could change a form.
UNSAFE = re.compile(r"void|withdraw|delete|remove|save|submit|sign|cancel|amend|copy|edit|abandon", re.I)


def _safe_click(loc, expected):
    """Click a control only if its visible text is exactly `expected` and harmless."""
    text = " ".join((loc.inner_text(timeout=15000) or "").split())
    if text != expected or UNSAFE.search(text):
        raise RuntimeError(f"refused to click {text!r} (expected {expected!r})")
    loc.click()


# The eForms "View PDF" button builds the PDF in the page (a Blob) and opens it in a new tab. Remember the Blob, read it.
_HOOK_JS = """() => { if (window.__atfHooked) return; window.__atfHooked = true; const make = URL.createObjectURL;
  URL.createObjectURL = function (obj) { if (obj instanceof Blob) window.__atfLastBlob = obj; return make.apply(this, arguments); }; }"""
_READ_BLOB_JS = """async () => { const b = window.__atfLastBlob; window.__atfLastBlob = null; if (!b) return null;
  const u = new Uint8Array(await b.arrayBuffer()); let s = "";
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s); }"""


# Each result row, whatever the table markup: climb from each "View Form" button to the smallest ancestor that holds
# exactly one "View Form" button and is a table row, holds the row's eForm ID link (`eformId-link`) or a 7+ digit
# number. Returns {text, id}: id = that link's text ("" without one). Each button is tagged data-atf-row=<i> (in this
# browser only) so the click below hits exactly that row's button.
_ROW_TEXTS_JS = """() => {
  const shown = e => e.getClientRects().length > 0;  // hidden elements still report their text, so check visibility
  const views = root => [...root.querySelectorAll('button')].filter(b => shown(b) && b.innerText.trim() === 'View Form');
  const link = el => el.querySelector('[id="eformId-link"]');
  document.querySelectorAll('[data-atf-row]').forEach(b => b.removeAttribute('data-atf-row'));
  return views(document).map((b, i) => {
    b.setAttribute('data-atf-row', i); let el = b;
    while (el.parentElement && !((el.tagName === 'TR' || link(el) || /\\d{7}/.test(el.innerText)) && views(el).length === 1))
      el = el.parentElement;
    const a = link(el);
    return {text: el.innerText, id: a ? a.innerText.trim() : ''}; }); }"""


def _parse_result_row(row):
    """eForm ID, Permit/Control number (10 digits on older forms, 11 on newer), finalized date (last of 3). `row` =
    {text, id} from _ROW_TEXTS_JS (or plain text). The ID is the row's ID link, any length (older forms have 6 digits:
    live 2026-11-22, only 7-8 were read); without a link, a 7-8 digit number in the text (not shorter: a serial in the
    Internal Control Number column, e.g. TD261122, has 6 digits in a row)."""
    text, link = (row.get("text") or "", row.get("id") or "") if isinstance(row, dict) else (row, "")
    eid = re.fullmatch(r"\d{4,9}", link) or re.search(r"(?<!\d)\d{7,8}(?!\d)", text)
    if not eid:
        return None
    control = re.search(r"(?<!\d)\d{10,11}(?!\d)", text)
    dates = re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text)  # created, submitted, finalized (none yet while in process)
    return {"eform_id": eid.group(), "control_number": control.group() if control else "", "finalized": dates[-1] if dates else "",
            "submitted": dates[1] if len(dates) > 1 else ""}


RESULTS_PER_PAGE = 10  # the eForms results table's rows per page (its "10" box); fewer = the last page, or still drawing


def _approved_results(page, page_no=1, search=True, status="APPROVED", wait=30):
    """Search Form 1 + APPROVED (or `status`; search=False: stay on the results page shown), go to results page `page_no`;
    returns (total rows, [{eform_id, control_number, finalized}]). Search only from a home page WITHOUT results: a
    table still showing is read as the new results, and the search coming back then resets it to page 1. `wait`:
    seconds to wait for result rows (an empty search looks the same as a slow one until then)."""
    def rows():
        return [r for r in (_parse_result_row(t) for t in page.evaluate(_ROW_TEXTS_JS)) if r]

    def settle():
        # A precaution, not seen live: if a slow PC drew the table row by row, a read at the first row would come up
        # short (now a problem line). Read until a full page shows, or the rows are unchanged for 0.5 s. (A user's
        # "7 approved Form 1(s) on 7 page(s)", 2026-11-22, was 6-digit eForm IDs, not this.)
        nonlocal current
        same = 0
        for _ in range(40):
            if not current or len(current) >= RESULTS_PER_PAGE or same >= 2:
                break
            page.wait_for_timeout(250)
            now = rows()
            same = same + 1 if [r["eform_id"] for r in now] == [r["eform_id"] for r in current] else 0
            current = now
    current = rows()
    if search:
        page.locator("#form-type-search").select_option(label="ATF Form 1 (5320.1)")
        page.locator("#form-status-search").select_option(label=status)
        _safe_click(page.locator("#queryBtn"), "Search")
        # The home page already shows an empty table ("Total Rows: 0") before any search, and results arrive a moment
        # after Search: wait for result rows rather than for the text (up to `wait` s; then there really are none).
        current = rows()
        for _ in range(int(wait * 4)):
            if current:
                break
            page.wait_for_timeout(250)
            current = rows()
        settle()
    total = int((re.search(r"Total Rows:\s*(\d+)", page.inner_text("body")) or [0, 0])[1])
    if total and not current:
        raise RuntimeError(f"eForms says {total} {status.lower()} form(s) but no result rows could be read; the page layout "
                           f"may have changed{_save_row_shapes(page, status)}")
    def click(loc, expected):
        nonlocal current
        first = current[0]["eform_id"] if current else ""
        _safe_click(loc, expected)
        for _ in range(40):  # wait until the table shows the new page
            page.wait_for_timeout(250)
            current = rows()
            if current and current[0]["eform_id"] != first:
                break
        settle()

    # The paginator shows only some page numbers (live 2026-10-19: "6" of 7 never showed up). Click the target when
    # shown; else the highest shown number below it; else the icon-only arrow right after the numbers (next).
    reached, clicked = page_no == 1, 1
    for _ in range(page_no + 5):
        if reached:
            break
        pg = page.evaluate(_PAGER_JS)
        if pg["current"] == page_no or page_no in pg["nums"]:
            if pg["current"] != page_no:
                click(page.locator(f'a[data-atf-page="{page_no}"]'), str(page_no))
            reached = True
            break
        lower = max((k for k in pg["nums"] if k < page_no), default=0)
        if lower > clicked:
            click(page.locator(f'a[data-atf-page="{lower}"]'), str(lower))
            clicked = lower
        elif pg["next"]:
            click(page.locator("a[data-atf-next]"), "")
        else:
            break
    if not reached:
        raise RuntimeError(f"couldn't get to results page {page_no}{_save_pager(page)}")
    total = int(re.search(r"Total Rows:\s*(\d+)", page.inner_text("body")).group(1))
    return total, current


# The results table's paginator: tag the shown page-number links (data-atf-page=<n>) and the icon-only arrow right
# after the last one (data-atf-next); report the numbers, the current page (if the paginator marks it) and its HTML.
_PAGER_JS = """() => {
  const shown = e => e.getClientRects().length > 0;
  document.querySelectorAll('[data-atf-page],[data-atf-next]').forEach(a => { a.removeAttribute('data-atf-page'); a.removeAttribute('data-atf-next'); });
  // page numbers are 1-3 digits; the results rows' eForm IDs (7-8 digits) are links too (live 2026-10-19)
  const links = [...document.querySelectorAll('a')].filter(a => shown(a) && /^\\d{1,3}$/.test(a.innerText.trim()));
  if (!links.length) return {nums: [], current: null, next: false, html: ''};
  // the smallest box holding every page number and an icon-only link after them (PrimeNG: numbers in their own span)
  let box = links[0].parentElement;
  const after = b => { const all = [...b.querySelectorAll('a')].filter(shown); return all.slice(all.indexOf(links[links.length - 1]) + 1).some(a => !a.innerText.trim()); };
  for (let i = 0; i < 8 && box.parentElement && (!links.every(a => box.contains(a)) || !after(box)); i++) box = box.parentElement;
  const nums = [], isCur = e => /active|highlight|current|selected/i.test((e.className || '') + ' ' + ((e.parentElement || {}).className || '')) || e.getAttribute('aria-current');
  let current = null;
  links.filter(a => box.contains(a)).forEach(a => { const n = +a.innerText.trim(); a.setAttribute('data-atf-page', n); nums.push(n); if (isCur(a)) current = n; });
  const all = [...box.querySelectorAll('a')].filter(shown), last = all.indexOf(links.filter(a => box.contains(a)).pop());
  const nxt = all.slice(last + 1).find(a => !a.innerText.trim());
  const off = nxt && /disabled/i.test((nxt.className || '') + ' ' + ((nxt.parentElement || {}).className || '') + (nxt.getAttribute('aria-disabled') || ''));
  if (nxt && !off) nxt.setAttribute('data-atf-next', '1');
  return {nums, current, next: !!(nxt && !off), html: box.outerHTML.slice(0, 20000)}; }"""


# What each shown table row looks like, WITHOUT its values (users send these files): the ID link's length, the
# lengths of its digit groups, its visible button labels. For results rows that couldn't be read.
_ROW_SHAPES_JS = """() => {
  const shown = e => e.getClientRects().length > 0;
  return [...document.querySelectorAll('tr')].filter(shown).map(tr => {
    const a = tr.querySelector('[id="eformId-link"]'), all = [...tr.querySelectorAll('button')];
    const runs = (tr.innerText.match(/\\d+/g) || []).map(d => d.length);
    const buttons = all.filter(shown).map(b => b.innerText.trim().replace(/\\d/g, '#').slice(0, 30));
    const hidden = all.length - all.filter(shown).length;
    return 'ID link: ' + (a ? a.innerText.trim().length + ' characters' : 'none') + '; digit groups (lengths): '
      + (runs.join(' ') || 'none') + '; buttons: ' + (buttons.join(', ') || 'none') + (hidden ? ' (+' + hidden + ' hidden)' : '');
  }).filter(s => !/^ID link: none; digit groups \\(lengths\\): none; buttons: none$/.test(s)); }"""


def _save_row_shapes(page, status):
    """Save _ROW_SHAPES_JS's lines for debugging; returns ' (... saved in ...)' or ''."""
    try:
        lines = page.evaluate(_ROW_SHAPES_JS)
        f = OUT / f"results_rows_{datetime.now():%Y%m%d_%H%M%S}.txt"
        f.write_text(f"Check approvals, {status.lower()}: what each table row on the page looks like, without its values\n"
                     "(lengths and button labels only; nothing personal).\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return f" (what the rows look like, without their values, is saved in {f})"
    except Exception:
        return ""


def _page_count(total, rows):
    """Results pages for `total` forms. eForms shows RESULTS_PER_PAGE a page; page 1's rows count only when more (a
    short read must not make more pages: live 2026-11-22, 1 of 7 rows read = "7 page(s)")."""
    return -(-total // max(len(rows), RESULTS_PER_PAGE))


def _save_pager(page):
    """Save the paginator's HTML (page numbers only, no form data) for debugging; returns ' (saved to ...)' or ''."""
    try:
        html = page.evaluate(_PAGER_JS)["html"]
        f = OUT / f"paginator_{datetime.now():%Y%m%d_%H%M%S}.html"
        f.write_text(html, encoding="utf-8")
        return f" (its page links are saved in {f})"
    except Exception:
        return ""


def _fetch_form_pdf(page, ctx, row, dest):
    """Open one approved form (View Form), have the portal build its PDF (View PDF), save it, go back home."""
    texts = page.evaluate(_ROW_TEXTS_JS)
    idx = next((i for i, t in enumerate(texts) if (_parse_result_row(t) or {}).get("eform_id") == row["eform_id"]), None)
    if idx is None:
        raise RuntimeError("its row is no longer on the results page")
    _safe_click(page.locator(f'button[data-atf-row="{idx}"]'), "View Form")
    page.locator("#viewFormBtn").wait_for(timeout=30000)
    page.evaluate(_HOOK_JS)
    _safe_click(page.locator("#viewFormBtn"), "View PDF")
    page.wait_for_function("() => !!window.__atfLastBlob", timeout=90000)
    data = base64.b64decode(page.evaluate(_READ_BLOB_JS) or "")
    page.wait_for_timeout(500)
    for tab in ctx.pages:  # close the PDF tab the portal opened
        if tab != page:
            tab.close()
    if not data.startswith(b"%PDF"):
        raise RuntimeError("the portal returned something that isn't a PDF")
    dest.write_bytes(data)


def _go_home(page, reload=False):
    """Back to the home page; reload=True: a fresh one (no results table) even when already there."""
    if reload:
        page.goto(re.match(r"https?://[^/]+", page.url).group() + "/home")
        page.locator("#form-type-search").wait_for(timeout=30000)
        return
    if page.locator("#form-type-search").is_visible():  # already there (e.g. the form never opened)
        return
    try:
        _safe_click(page.locator("#goHomeBtn"), "Go Home")
        page.locator("#form-type-search").wait_for(timeout=15000)
    except Exception:  # reload the home page of the site we're on
        page.goto(re.match(r"https?://[^/]+", page.url).group() + "/home")
        page.locator("#form-type-search").wait_for(timeout=30000)


# The Form 1's CLEO copy: eForms puts it in every SUBMITTED form's "View PDF" (pages marked "CLEO Copy" after the ATF
# copy and the instructions; live capture 2026-11-06). An approved form's PDF is the 3-page registrant copy only, so it
# has to be fetched while the form is in process. The CLEO pages don't show the serial: it's read from the ATF copy.
SUBMITTED = "SUBMITTED/IN PROCESS"


def _form1_cleo_path(serial):
    return _cleo_dir() / f"Form1_{re.sub(r'[^A-Za-z0-9._-]', '_', serial)}.cleo.pdf"


def _form1_index():
    """out/cleo/form1_index.csv: eForm ID -> serial of every submitted form already opened (so it isn't opened again)."""
    idx = _cleo_dir() / "form1_index.csv"
    if not idx.exists():
        return {}
    with idx.open(newline="", encoding="utf-8-sig") as f:
        return {r["eform_id"]: r["serial"] for r in csv.DictReader(f)}


def _save_form1_index(index):
    with (_cleo_dir() / "form1_index.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["eform_id", "serial"])
        w.writerows(sorted(index.items()))


def _cleo_pages(doc):
    return [i for i, pg in enumerate(doc) if "CLEO Copy" in {" ".join(ln.split()) for ln in pg.get_text().splitlines()}]


def _serial_words(page, serial):
    """Where `serial` is printed as a whole word on the page (case ignored; A101 doesn't match A1015)."""
    import pymupdf
    want = serial.strip().upper()
    return [pymupdf.Rect(w[:4]) for w in page.get_text("words") if w[4].strip(".,;:()[]").upper() == want]


def _redact_serial(doc, serial):
    """Blank out the serial wherever it's printed as a word in `doc` (the CLEO's copies never show it: ATF leaves item 4e
    off them, and our internal control number = the serial must not bring it back). Returns how many were removed."""
    import pymupdf
    n = 0
    for page in doc:
        hits = _serial_words(page, serial)
        for r in hits:
            page.add_redact_annot(r, fill=(1, 1, 1))
        if hits:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
            n += len(hits)
    if any(_serial_words(page, serial) for page in doc):
        raise RuntimeError("its CLEO copy shows the serial and it couldn't be removed")
    return n


def save_form1_cleo(pdf, eform_id, index):
    """Keep the CLEO-copy pages of a submitted form's PDF as out/cleo/Form1_<serial>.cleo.pdf. Returns the serial."""
    import pymupdf
    serial = (read_approved_pdf(pdf)["serial"] or "").strip()  # item 4g of the ATF copy (page 1; same layout)
    doc = pymupdf.open("pdf", Path(pdf).read_bytes())  # no open file handle: the file is deleted next
    pages = _cleo_pages(doc)
    if not pages:
        raise RuntimeError("its PDF has no CLEO copy pages")
    if not serial:
        raise RuntimeError("its serial couldn't be read")
    out = pymupdf.open()
    for i in pages:
        out.insert_pdf(doc, from_page=i, to_page=i)
    removed = _redact_serial(out, serial)
    if removed:
        emit("warn", f"    the Form 1 CLEO copy of {serial} showed the serial {removed} time(s) (the internal control "
                     "number?); blanked out, since the CLEO's copies never show it")
    _cleo_dir().mkdir(parents=True, exist_ok=True)
    out.save(_form1_cleo_path(serial), garbage=3, deflate=True)
    index[eform_id] = serial
    _save_form1_index(index)
    return serial


def _fetch_form1_cleo(page, ctx, wanted):
    """In a logged-in session, READ-ONLY: save the Form 1 CLEO copy of each serial in `wanted` ({serial: filed date})
    that isn't saved yet. Opens only submitted forms not opened before, and when the filed dates are known only
    those submitted on one of them. Returns ({serial: path}, problems)."""
    folder = _cleo_dir()
    folder.mkdir(parents=True, exist_ok=True)
    got = {s: _form1_cleo_path(s) for s in wanted if _form1_cleo_path(s).is_file()}
    need = {s.upper(): s for s in wanted if s not in got}
    if not need:
        return got, []
    index, problems = _form1_index(), []
    dates = {_us_date(d) for s, d in wanted.items() if s.upper() in need}
    _go_home(page, reload=True)  # a home page without results: Search only from there
    total, rows = _approved_results(page, status=SUBMITTED)
    pages = _page_count(total, rows)
    for n in range(1, pages + 1):
        if not need:
            break
        if n > 1:
            _, rows = _results_page(page, n, search=False, status=SUBMITTED)  # on from page n-1, which is showing
        todo = [r for r in rows if r["eform_id"] not in index and ("" in dates or _us_date(r["submitted"]) in dates)]
        for r in todo:
            if not need:
                break
            emit("step", f"getting the CLEO copy of eForm …{r['eform_id'][-4:]}")
            tmp = folder / f".{r['eform_id']}.part.pdf"
            try:
                _fetch_form_pdf(page, ctx, r, tmp)
                serial = save_form1_cleo(tmp, r["eform_id"], index)
                if serial.upper() in need:
                    got[need.pop(serial.upper())] = _form1_cleo_path(serial)
            except Exception as e:
                problems.append(f"eForm …{r['eform_id'][-4:]}: {str(e).splitlines()[0]}")
                emit("warn", f"could not get the CLEO copy of eForm …{r['eform_id'][-4:]}: {str(e).splitlines()[0]}")
            finally:
                if tmp.exists():  # the whole form (ATF copy included): only the CLEO pages are kept
                    tmp.unlink()
            _go_home(page)
            _results_page(page, n, status=SUBMITTED)  # back to the same results page for the next row
    if need:
        problems.append(f"no submitted form found for {', '.join(sorted(need.values(), key=serial_key))}")
    return got, problems


def fetch_cleo_form1s(wanted):
    """Log in and fetch the Form 1 CLEO copies of `wanted` ({serial: filed date}); logs out whatever happens."""
    pw, ctx, page = _browser()
    try:
        page = _wait_for_login(page, "Log in (MFA included) and wait for the eForms home page, then press Enter. From here the script only reads... ")
        emit("section", "--- CLEO copies of the Form 1s ---", name="CLEO copies of the Form 1s")
        return _fetch_form1_cleo(page, ctx, wanted)
    finally:
        _shutdown(page, ctx, pw)


_STATE_ABBR = {v: k for k, v in US_STATES.items()}


def _cleo_address(person=None):
    """The CLEO's mailing address lines: yours from Settings, another responsible person's from their CLEO copy."""
    if person is None:
        cl = load_trust().get("cleo") or {}
        a = cl.get("address") or {}
        vals = {"official": cl.get("official", ""), "title": cl.get("title", ""), "department": cl.get("department", ""),
                "street": a.get("street", ""), "city": a.get("city", ""), "state": a.get("state", ""), "zip": str(a.get("zip", ""))}
    else:
        try:
            pre = read_template(cleo_template(person))
        except Exception:
            pre = {}
        vals = {k: pre.get(f"cleo.{k}", "") for k in ("official", "title", "department")}
        vals.update({k: pre.get(f"cleo.address.{k}", "") for k in ("street", "city", "state", "zip")})
    city = " ".join(filter(None, [f"{vals['city']}," if vals["city"] else "", _STATE_ABBR.get(vals["state"], vals["state"]), vals["zip"]]))
    return [x for x in (", ".join(filter(None, [vals["official"], vals["title"]])), vals["department"], vals["street"], city) if x.strip()]


def _cleo_cover(doc, address, count, filed, what, theirs):
    """Page 1 of a packet: the address where a #10 window envelope shows it, then a plain notification. No serials."""
    p = doc.new_page(width=612, height=792)
    p.insert_text((72, 96), datetime.now().strftime("%B %d, %Y").replace(" 0", " "), fontname="helv", fontsize=11)
    for i, line in enumerate(address or ["(the CLEO's address: see item 5 of the enclosed copies)"]):
        p.insert_text((72, 158 + 14 * i), line.upper(), fontname="helv", fontsize=11)
    p.insert_text((72, 266), f"Re: notification of ATF Form 1 application{'s' if count > 1 else ''}", fontname="hebo", fontsize=11)
    apps = f"{count} application{'s' if count > 1 else ''}"
    body = (f"This is notification that {apps} to make and register an NFA firearm (ATF Form 1) "
            f"{'were' if count > 1 else 'was'} filed with ATF on {filed}"
            + (", on which I am a responsible person" if theirs else "") + f". Enclosed for each application: {what}.")
    p.insert_textbox((72, 284, 540, 420), body, fontname="helv", fontsize=11)


def cleo_packets(serials):
    """One PDF to mail per CLEO for these filed serials: yours (Form 1 CLEO copies + your 5320.23 CLEO copies) and one
    per other responsible person with a CLEO copy (theirs, for THEIR CLEO). Each: a cover page, then for each serial
    in serial order its Form 1 copy and its 5320.23 copy. Returns [(n, path, applications, missing)] (n 1 = yours)."""
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    trust = load_trust()
    rows = {r["serial_number"].upper(): r for r in load_rows()}
    serials = sorted(serials, key=serial_key)
    individual = applicant_type(trust) == "individual"
    filed = sorted({rows[s.upper()]["filed_date"] for s in serials if rows.get(s.upper(), {}).get("filed_date")},
                   key=lambda d: datetime.strptime(d, "%m/%d/%Y"))
    when, out, included = datetime.now().strftime("%Y-%m-%d %H%M"), [], set()
    for n, person in enumerate([None] + ([] if individual else other_rps(trust)), 1):
        tag = f".{_rp_tag(person)}" if person else ""
        want_5320 = not individual and cleo_template(person).is_file()
        if person is not None and not want_5320:
            continue
        apps, missing, kinds = [], [], set()
        for s in serials:
            pieces = []
            if person is None:
                f1 = _form1_cleo_path(s)
                pieces.append(f1) if f1.is_file() else missing.append(f"{s}: Form 1 copy")
            if want_5320:
                c = _cleo_dir() / f"5320.23_{s}{tag}.cleo.pdf"
                pieces.append(c) if c.is_file() else missing.append(f"{s}: 5320.23 copy")
            kinds.update("Form 1" if pc.name.startswith("Form1_") else "5320.23" for pc in pieces)
            if pieces:
                apps.append(pieces)
                included.add(s)
        if not apps:
            out.append((n, None, 0, missing))
            continue
        whose = "my" if person else "the responsible person's"  # plain ASCII: the cover's built-in font has no curly quotes
        what = " and ".join(filter(None, [
            "the CLEO copy of ATF Form 1" if "Form 1" in kinds else "",
            f"the CLEO copy of {whose} Form 5320.23 (responsible person questionnaire)" if "5320.23" in kinds else ""]))
        dates = ", ".join(filed[:-1]) + " and " + filed[-1] if len(filed) > 1 else "".join(filed)
        doc = pymupdf.open()
        _cleo_cover(doc, _cleo_address(person), len(apps), dates or "the date shown on the enclosed copies",
                    what, theirs=person is not None)
        for pieces in apps:
            for pc in pieces:
                doc.insert_pdf(pymupdf.open(pc))
        dest = _cleo_dir() / f"CLEO packet {when}{tag}.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc.save(dest, garbage=3, deflate=True)
        out.append((n, dest, len(apps), missing))
    if included:
        _log_packet(sorted(included), next(path for _, path, _, _ in out if path))
    return out


def icn_is_serial(trust=None):
    """Put the suppressor's serial in the Form 1's Internal Control Number box (Application page; the default), so the
    eForms dashboard can be searched and read by serial (its results list shows eForm IDs, not serials). Off
    (`icn_serial: false`) = the box is left empty, e.g. for someone who numbers their forms their own way."""
    return (trust if trust is not None else load_trust()).get("icn_serial", True) is not False


def cleo_packet_after_batch(trust=None):
    """Make the CLEO packet right after each batch (the default), or only when asked (Advanced > Make CLEO packets),
    e.g. one packet for several batches. Either way the Form 1 CLEO copies are saved after each batch: eForms only has
    them while the forms are in process."""
    return (trust if trust is not None else load_trust()).get("cleo_packet_after_batch", True) is not False


# out/cleo/packets.csv: which serials went into which packet, so "Make CLEO packets" can offer the ones in none yet.
def _packets_log():
    log = _cleo_dir() / "packets.csv"
    if not log.exists():
        return {}
    with log.open(newline="", encoding="utf-8-sig") as f:
        return {r["serial"]: r["packet"] for r in csv.DictReader(f)}


def _log_packet(serials, path):
    log = {**_packets_log(), **{s: Path(path).name for s in serials}}
    with (_cleo_dir() / "packets.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["serial", "packet"])
        w.writerows(sorted(log.items(), key=lambda kv: serial_key(kv[0])))


def cleo_unpacked(rows=None):
    """Filed forms (still at ATF) that aren't in any CLEO packet yet, in serial order."""
    done = _packets_log()
    return [r for r in sorted(rows if rows is not None else load_rows(), key=lambda r: serial_key(r["serial_number"]))
            if r["status"] in ("filed", "pending") and r["serial_number"] not in done]


# out/approved/: PDFs named by serial (A101.pdf); index.csv maps eForm ID -> file, because the results list only shows
# eForm IDs and that is how a later run knows which forms it already has.
def _approved_index(folder):
    idx = folder / "index.csv"
    if not idx.exists():
        return {}
    with idx.open(newline="", encoding="utf-8-sig") as f:
        return {r["eform_id"]: r["file"] for r in csv.DictReader(f)}


def _save_approved_index(folder, index):
    with (folder / "index.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["eform_id", "file"])
        w.writerows(sorted(index.items(), key=lambda kv: serial_key(kv[1])))


def _replace(src, dest, tries=6):
    """src.replace(dest), retried for a few seconds on Windows' "Access is denied" (WinError 5: a virus scanner or a
    PDF viewer holding a file for a moment; a user's run, 2026-11-22). The last failure is raised, naming the file."""
    for i in range(tries):
        try:
            return src.replace(dest)
        except PermissionError as e:
            if i == tries - 1:
                raise PermissionError(f"couldn't save {dest.name} in {dest.parent} (is it open in a PDF viewer? close it and "
                                      f"run Check approvals again): {e.strerror or e}") from None
            time.sleep(0.5 * (i + 1))


def _file_by_serial(folder, index, eform_id, pdf):
    """Rename a downloaded approval PDF to <serial>.pdf (A101.pdf; A101_2.pdf if that serial already has another
    form; eForm-<id>.pdf if the serial can't be read) and record it in the index. Returns the new path."""
    try:
        serial = read_approved_pdf(pdf)["serial"].strip()
    except Exception:
        serial = ""
    base = re.sub(r"[^A-Za-z0-9._-]", "_", serial) if serial else f"eForm-{eform_id}"
    taken = {v for k, v in index.items() if k != eform_id}
    name, n = f"{base}.pdf", 2
    while name in taken:
        name, n = f"{base}_{n}.pdf", n + 1
    dest = folder / name
    if Path(pdf) != dest:
        _replace(Path(pdf), dest)
    index[eform_id] = name
    return dest


def _migrate_approved_names(folder):
    """Older runs saved <eForm ID>.pdf: rename those to serials and index them."""
    index = _approved_index(folder)
    legacy = sorted((f for f in folder.glob("*.pdf") if re.fullmatch(r"\d{6,9}", f.stem) and f.stem not in index),
                    key=lambda f: int(f.stem))  # deterministic: the older form keeps the plain name
    for f in legacy:
        _file_by_serial(folder, index, f.stem, f)
    if legacy:
        _save_approved_index(folder, index)
    return index


# Form 1s ATF didn't approve, by eForms' "Form Status" search. Disapproved and returned without action are finished:
# the serial has to be filed again (a new application). Returned for correction is reported only.
NOT_APPROVED = ("DISAPPROVED", "RETURNED WITHOUT ACTION", "RETURNED FOR CORRECTION")
CORRECTION = "RETURNED FOR CORRECTION"
# These searches usually find nothing, and an empty result can't be told from a slow one: wait less than for approvals.
NOT_APPROVED_WAIT = 15


def _not_approved_dir(status):
    """out/not-approved/<status>/ (e.g. returned-without-action), resolved at call time."""
    return OUT / "not-approved" / re.sub(r"[^a-z]+", "-", status.lower()).strip("-")


def fetch_approved(others=None, in_process=None):
    """READ-ONLY: list approved Form 1s on eForms and save each one's PDF to out/approved/<serial>.pdf (already
    saved ones are skipped). Returns (results rows found, problems). Logs out at the end, whatever happens.
    `in_process` (a list): also search SUBMITTED/IN PROCESS (forms filed by hand outside this tool are found only
    there until they're approved; `_fetch_in_process`) and add those result rows to it.
    `others` (a list): while any suppressor is filed or pending, also search each NOT_APPROVED status, save those
    PDFs to out/not-approved/<status>/ and add their result rows (with "status") to it."""
    folder = OUT / "approved"
    folder.mkdir(parents=True, exist_ok=True)
    found, problems = [], []
    pw, ctx, page = _browser()
    try:
        page = _wait_for_login(page, "Log in (MFA included) and wait for the eForms home page, then press Enter. From here the script only reads... ")
        _fetch_status(page, ctx, "APPROVED", folder, found, problems)
        if in_process is not None:
            before = len(problems)
            try:
                _go_home(page, reload=True)  # never Search while results show
                _fetch_in_process(page, ctx, in_process, problems)
            except Exception as e:  # the approvals above are kept whatever happens here
                if len(problems) == before:
                    problems.append(f"couldn't search submitted/in process Form 1s: {str(e).splitlines()[0]}")
                emit("error", "couldn't search submitted/in process Form 1s")
        if others is not None and _anything_waiting():
            for status in NOT_APPROVED:
                before = len(problems)
                try:
                    _go_home(page, reload=True)  # never Search while results show
                    sub = _not_approved_dir(status)
                    sub.mkdir(parents=True, exist_ok=True)
                    got = []
                    _fetch_status(page, ctx, status, sub, got, problems, wait=NOT_APPROVED_WAIT)
                    others += [{**r, "status": status} for r in got]
                except Exception as e:  # the approvals above are kept whatever happens here
                    if len(problems) == before:
                        problems.append(f"couldn't search {status.lower()} Form 1s: {str(e).splitlines()[0]}")
                    emit("error", f"couldn't search {status.lower()} Form 1s")
    finally:
        _shutdown(page, ctx, pw)
    return found, problems


def _anything_waiting():
    """Is any suppressor filed or pending (the only ones a disapproval can be for)? False if the inventory can't be read."""
    try:
        return any(r["status"] in ("filed", "pending") for r in load_rows())
    except Exception:
        return False


def _in_process_dir():
    """out/in-process/: page 1 (the ATF copy's item 4 and item 9) of each submitted form opened, resolved at call time."""
    return OUT / "in-process"


def _keep_first_page(pdf):
    """Cut a downloaded form down to its first page: all that's read from it (item 4, item 9). eForms' submitted
    PDFs have dangling refs, so MuPDF's messages are switched off here (first live run, 2026-11-21: one "cannot find
    object in xref" line per object filled the screen; read_approved_pdf switches them off too, but runs after this).
    If trimming fails or gives no readable page, the whole PDF is kept: a bigger file, nothing lost."""
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    try:
        src = pymupdf.open("pdf", Path(pdf).read_bytes())  # no open file handle (Windows renames it next)
        if src.page_count <= 1:
            return
        out = pymupdf.open()
        out.insert_pdf(src, from_page=0, to_page=0)  # not select(): a broken xref makes MuPDF repair, undoing it
        data = out.tobytes(garbage=3, deflate=True)
        src.close()
        check = pymupdf.open("pdf", data)
        if check.page_count != 1 or not check[0].get_text().strip():
            return
    except Exception:
        return
    Path(pdf).write_bytes(data)


def _tracked_eform_ids():
    """eForm IDs of submitted forms already in your records, so they're not opened again: a row's own eForm ID (an
    approval, or a form filed by hand and recorded from in process), and forms whose CLEO copy was saved
    (out/cleo/form1_index.csv) for a serial that is filed, pending or approved here."""
    try:
        rows = load_rows()
    except Exception:
        return set()
    status = {r["serial_number"].strip().upper(): r["status"] for r in rows}
    ids = {r["eform_id"] for r in rows if r["eform_id"]}
    return ids | {e for e, s in _form1_index().items() if status.get(s.strip().upper()) in ("filed", "pending", "approved")}


def _fetch_in_process(page, ctx, in_process, problems):
    """READ-ONLY: search SUBMITTED/IN PROCESS and save page 1 of each form not opened before and not already in your
    records to out/in-process/<serial>.pdf (own index.csv), adding those result rows to `in_process`. Files of forms
    no longer in process are removed once every results page was read (their approval is in out/approved/)."""
    folder = _in_process_dir()
    folder.mkdir(parents=True, exist_ok=True)
    listed = set()
    complete = _fetch_status(page, ctx, SUBMITTED, folder, in_process, problems, skip=_tracked_eform_ids(), first_page=True,
                             seen=listed, wait=30 if _anything_waiting() else NOT_APPROVED_WAIT)
    if complete:
        index = _approved_index(folder)
        gone = {e: n for e, n in index.items() if e not in listed}
        for name in gone.values():
            try:
                (folder / name).unlink()
            except OSError:
                pass
        if gone:
            _save_approved_index(folder, {e: n for e, n in index.items() if e not in gone})


def _fetch_status(page, ctx, status, folder, found, problems, wait=30, skip=frozenset(), first_page=False, seen=None):
    """Search Form 1s with this status and save each result's PDF into `folder` (by serial, with its index.csv;
    already saved ones are skipped), page after page. Adds the result rows (with "pdf") to `found`. `skip`: eForm IDs
    neither opened nor added; `first_page`: keep only page 1 of each PDF; `seen` (a set): gets every eForm ID listed.
    Returns True if every results page was read."""
    index = _migrate_approved_names(folder)
    label = status.lower()
    emit("section", f"--- Searching {label} Form 1s ---", name=f"Searching {label} Form 1s")
    total, rows = _approved_results(page, status=status, wait=wait)
    pages, per, complete = _page_count(total, rows), max(len(rows), RESULTS_PER_PAGE), True
    emit("info", f"{total} {label} Form 1(s) on {pages} page(s)")
    which = "" if status == "APPROVED" else f"{label} "
    for n in range(1, pages + 1):
        try:
            if n > 1:
                emit("section", f"--- Results page {n} ---", name=f"Results page {n} of {pages}")
                _, rows = _results_page(page, n, search=False, status=status, wait=wait)  # on from page n-1, which is showing
            listed = min(per, total - (n - 1) * per)
            if len(rows) < listed:  # rows eForms shows that can't be read: say so (and seen is incomplete)
                complete = False
                msg = (f"{which}results page {n}: eForms lists {listed} form(s), only {len(rows)} could be read"
                       f"{_save_row_shapes(page, status)}")
                problems.append(msg)
                emit("error", msg)
            if seen is not None:
                seen.update(r["eform_id"] for r in rows)
            _fetch_page_rows(page, ctx, n, rows, folder, index, found, problems, status, wait, skip, first_page)
        except Exception as e:  # can't get to a results page: report what is saved instead of losing the whole run
            msg = f"stopped at {which}results page {n} of {pages}: {str(e).splitlines()[0]}"
            problems.append(msg + " (the report below also covers every PDF saved earlier; run Check approvals again)")
            emit("error", msg)  # ✖ on the section instead of a later ✔
            have = {r["eform_id"] for r in found}
            found += [{"eform_id": eid, "control_number": "", "finalized": "", "pdf": str(folder / name)}
                      for eid, name in sorted(index.items(), key=lambda kv: serial_key(kv[1]))
                      if eid not in have and eid not in skip and (folder / name).exists()]
            return False
    return complete


def _results_page(page, n, search=True, status="APPROVED", wait=30):
    """Results page n (search=False: page on from the results shown), starting over from a freshly loaded home page
    once if it fails."""
    try:
        return _approved_results(page, n, search, status, wait)
    except Exception as e:
        emit("step", f"results page {n} didn't load ({str(e).splitlines()[0]}); trying once more")
        _go_home(page, reload=True)
        return _approved_results(page, n, status=status, wait=wait)


def _fetch_page_rows(page, ctx, n, rows, folder, index, found, problems, status="APPROVED", wait=30, skip=frozenset(),
                     first_page=False):
    """Save the PDF of every row on results page n that isn't saved yet (nor in `skip`)."""
    for row in rows:
        if row["eform_id"] in skip:
            continue
        known = index.get(row["eform_id"])
        if known and (folder / known).exists():  # saved on an earlier run
            found.append({**row, "pdf": str(folder / known)})
            continue
        emit("step", f"downloading eForm …{row['eform_id'][-4:]}")
        try:
            tmp = folder / f".{row['eform_id']}.part.pdf"
            _fetch_form_pdf(page, ctx, row, tmp)
            if first_page:
                _keep_first_page(tmp)
            dest = _file_by_serial(folder, index, row["eform_id"], tmp)
            _save_approved_index(folder, index)
            found.append({**row, "pdf": str(dest)})
            emit("info", f"saved {dest.name}")
        except Exception as e:
            problems.append(f"eForm …{row['eform_id'][-4:]}: {str(e).splitlines()[0]}")
            emit("warn", f"could not get the PDF for eForm …{row['eform_id'][-4:]}: {str(e).splitlines()[0]}")
        _go_home(page)
        _results_page(page, n, status=status, wait=wait)  # back to the same results page for the next row


def read_approved(found=None):
    """Read approved-Form-1 PDFs (`found` from fetch_approved; default: every PDF already in out/approved/).
    Returns (records, problems)."""
    if found is None:
        folder = OUT / "approved"
        index = _migrate_approved_names(folder) if folder.exists() else {}
        found = [{"eform_id": eid, "control_number": "", "finalized": "", "pdf": str(folder / name)}
                 for eid, name in sorted(index.items(), key=lambda kv: serial_key(kv[1])) if (folder / name).exists()]
    records, problems = [], []
    for row in found:
        try:
            rec = {**row, **read_approved_pdf(row["pdf"])}
            rec["control_number"] = rec.get("control_number") or rec.pop("pdf_control_number", "")
            rec.pop("pdf_control_number", None)
            records.append(rec)
        except Exception as e:
            problems.append(f"eForm …{row['eform_id'][-4:]}: unreadable PDF ({str(e).splitlines()[0]})")
    return records, problems


def apply_approvals(records, import_unknown=False):
    """Record approvals in the inventory (data/suppressors.csv): for each approved SUPPRESSOR whose serial is there,
    set status approved + approval date, eForm ID, control number, conditions (a pending row is resolved this way too).
    With import_unknown, approved suppressors missing from the inventory are added as approved rows and the file is
    re-sorted by serial. Other types (record_sbrs) are never touched. Returns (serials updated, serials added)."""
    rows = load_rows()
    by = {r["serial_number"].upper(): r for r in rows}
    updated, added = [], []
    for rec in records:
        if "SILENCER" not in rec["type"].upper() or not rec["approved"] or not rec["serial"].strip():
            continue
        info = {"status": "approved", "approved_date": rec["approval_date"], "eform_id": rec["eform_id"],
                "control_number": rec.get("control_number", ""), "conditions": rec.get("conditions", "")}
        row = by.get(rec["serial"].strip().upper())
        if row is not None and not row["overall_length"] and rec["overall_length"]:
            info["overall_length"] = rec["overall_length"]  # only fills a gap; a differing length is reported, not overwritten
        if row is not None and not row["filed_date"] and rec.get("filed_date"):
            info["filed_date"] = rec["filed_date"]  # the same: e.g. filed by hand before this tool; never overwritten
        if row is not None:
            if any(row[k] != v for k, v in info.items() if v or k == "status"):
                row.update({k: v for k, v in info.items() if v or k == "status"})
                updated.append(row["serial_number"])
        elif import_unknown:
            row = {"caliber": rec["caliber"], "serial_number": rec["serial"].strip(), "overall_length": rec["overall_length"],
                   "application_number": "", "filed_date": rec.get("filed_date", ""), **info}
            rows.append(row)
            by[row["serial_number"].upper()] = row
            added.append(row["serial_number"])
    if server_settings():  # the same changes, as one operation on the server
        if updated or added:
            fields = ("status", "approved_date", "eform_id", "control_number", "conditions", "overall_length", "filed_date")
            if not _server_fills_filed_dates():  # an older server refuses filed_date: record the rest, fill dates later
                fields = fields[:-1]
            before = {r["serial_number"].upper(): r for r in load_rows()}
            changes = [{"serial": s, "fields": {k: by[s.upper()][k] for k in fields if by[s.upper()][k] != before[s.upper()][k]}}
                       for s in updated]
            changes = [c for c in changes if c["fields"]]
            updated = [c["serial"] for c in changes]
            if changes or added:
                _server_op("record_approvals", {"updates": changes, "added": [by[s.upper()] for s in added]})
        return updated, added
    if added:
        rows.sort(key=lambda r: serial_key(r["serial_number"]))
    if updated or added:
        save_rows(rows)
    return updated, added


def not_approved_report(records, rows):
    """Form 1s eForms lists as DISAPPROVED, RETURNED WITHOUT ACTION or RETURNED FOR CORRECTION (read_approved of
    fetch_approved's `others`), matched with your suppressors by serial. A form counts only for a row that is filed or
    pending, and only if it is that row's CURRENT filing: signed (item 9; else the results list's submitted date) on or
    after the row's filed date. An older form of a serial that was filed again since is left alone (eForms keeps
    listing it). Returns {"disapproved": [(row, rec)] (to be marked disapproved: re-file them), "correction":
    [(row, rec)] (ATF wants changes; nothing is marked), "unsure": [(row, rec)] (a date is missing; nothing is marked)}."""
    mine = {r["serial_number"].strip().upper(): r for r in rows}
    rep = {"disapproved": [], "correction": [], "unsure": []}
    for rec in sorted(records, key=lambda r: serial_key(r["serial"])):
        row = mine.get(rec["serial"].strip().upper()) if rec["serial"].strip() and not is_other_nfa(rec) else None
        if row is None or row["status"] not in ("filed", "pending"):
            continue
        signed, filed = _date(rec.get("filed_date")) or _date(rec.get("submitted")), _date(row["filed_date"])
        if signed is None or filed is None:
            rep["unsure"].append((row, rec))
        elif signed >= filed:
            rep["correction" if rec["status"] == CORRECTION else "disapproved"].append((row, rec))
    return rep


def record_disapprovals(serials):
    """Mark these suppressors disapproved (only while filed or pending), so Advanced > Re-file offers them. Returns
    the serials changed."""
    rows = load_rows()
    by = {r["serial_number"].strip().upper(): r for r in rows}
    todo = list(dict.fromkeys(by[s.strip().upper()]["serial_number"] for s in serials
                              if s.strip().upper() in by and by[s.strip().upper()]["status"] in ("filed", "pending")))
    if not todo:
        return []
    if server_settings():
        _server_op("record_approvals", {"updates": [{"serial": s, "fields": {"status": "disapproved"}} for s in todo], "added": []})
        return todo
    for s in todo:
        by[s.strip().upper()]["status"] = "disapproved"
    save_rows(rows)
    return todo


def in_process_report(records, rows):
    """Submitted/in-process Form 1s (read_approved of fetch_approved's `in_process`) compared with your records by
    serial: forms filed by hand outside this tool show up here long before they're approved. Returns
    {"unknown": [rec] (suppressors not in your records: add them as filed), "queued": [(row, rec)] (queued or
    disapproved here but already submitted on eForms: filing them would file them twice; mark them filed),
    "pending": [(row, rec)] (pending here and in process on eForms: Resolve them as filed), "duplicates": [serial]
    (on more than one in-process form; left out of the above), "other": [rec] (other NFA types: listed once approved),
    "unreadable": [rec] (no type or serial could be read)}. Forms of serials already filed or approved here are left out.
    An SBR (etc.) in your queue is matched the same way ("queued", "pending"; PR #5: one filed by hand stayed pending);
    one that isn't in your queue stays in "other" (recorded from its approval, record_sbrs)."""
    from collections import Counter
    mine = {r["serial_number"].strip().upper(): r for r in rows if not is_nfa_row(r)}
    nfa_mine = {r["serial_number"].strip().upper(): r for r in rows if is_nfa_row(r)}
    rep = {"unknown": [], "queued": [], "pending": [], "duplicates": [], "other": [], "unreadable": []}
    sup, nfa = [], []
    for rec in records:
        if is_other_nfa(rec):
            (nfa if rec["serial"].strip().upper() in nfa_mine else rep["other"]).append(rec)
        elif "SILENCER" in rec["type"].upper() and rec["serial"].strip():
            sup.append(rec)
        else:
            rep["unreadable"].append(rec)
    counts = Counter(r["serial"].strip().upper() for r in sup)
    counts.update(f"NFA {r['serial'].strip().upper()}" for r in nfa)
    rep["duplicates"] = sorted({s.removeprefix("NFA ") for s, n in counts.items() if n > 1}, key=serial_key)
    for rec, nfa_rec in sorted([(r, False) for r in sup] + [(r, True) for r in nfa], key=lambda x: serial_key(x[0]["serial"])):
        key = rec["serial"].strip().upper()
        if counts[f"NFA {key}" if nfa_rec else key] > 1:
            continue
        row = (nfa_mine if nfa_rec else mine).get(key)
        if row is None:
            rep["unknown"].append(rec)
        elif row["status"] in ("", "disapproved"):
            rep["queued"].append((row, rec))
        elif row["status"] == "pending":
            rep["pending"].append((row, rec))
    return rep


def _in_process_fields(rec):
    """What a submitted form says about its suppressor: filed (signed, item 9; else the results list's submitted date)."""
    return {"status": "filed", "filed_date": rec.get("filed_date") or _us_date(rec.get("submitted")),
            "eform_id": rec["eform_id"], "caliber": rec["caliber"].strip(),
            "overall_length": (re.search(r"\d+(?:\.\d+)?", rec["overall_length"] or "") or [""])[0]}


def record_in_process(records):
    """Record suppressors found submitted/in process on eForms (in_process_report's "unknown" and "queued") as FILED:
    new rows for serials not in your inventory, and queued or disapproved rows changed to filed, so they're waiting
    for approval here and never filed again. Filed date and eForm ID from the form; no application number (eForms
    doesn't list it); a recorded caliber or length is kept. Returns the serials recorded."""
    rows = load_rows()
    by = {(is_nfa_row(r), r["serial_number"].strip().upper()): r for r in rows}  # an SBR only matches an SBR row
    todo = []
    for rec in records:
        f, row = _in_process_fields(rec), by.get((is_other_nfa(rec), rec["serial"].strip().upper()))
        if row is None and is_other_nfa(rec):  # not in the queue: recorded once approved (record_sbrs)
            continue
        if row is not None and row["status"] not in ("", "disapproved"):
            continue
        todo.append((rec["serial"].strip(), f, row))
    if not todo:
        return []
    if server_settings():
        if (_server().get("/api/hello").get("api") or 1) < 4:
            raise ServerReadOnly("Your inventory server is too old to record forms filed outside this helper: update it "
                                 "(atf-server update on the server), then run Check approvals again.")
        _server_op("record_in_process", {"rows": [{"serial_number": row["serial_number"] if row else serial, **f}
                                                   for serial, f, row in todo]})
        return [row["serial_number"] if row else serial for serial, f, row in todo]
    for serial, f, row in todo:
        if row is None:
            row = {c: "" for c in COLS}
            row["serial_number"] = serial
            rows.append(row)
        row.update({"status": "filed", "filed_date": f["filed_date"], "eform_id": f["eform_id"]})
        for k in ("caliber", "overall_length"):
            row[k] = row[k] or f[k]
    if any(row is None for _s, _f, row in todo):  # new rows: in serial order, as apply_approvals does (never while filing)
        rows.sort(key=lambda r: serial_key(r["serial_number"]))
    save_rows(rows)
    return [row["serial_number"] if row else serial for serial, f, row in todo]


def send_approval_pdfs(records):
    """With a server: upload the approval PDFs it doesn't have yet (by eForm ID). Returns how many it added."""
    import hashlib
    if not server_settings():
        return 0
    server = _server()
    have = {p["eform_id"] for p in server.get("/api/pdfs")["pdfs"]}
    sent = []
    for rec in records:
        path = Path(rec.get("pdf") or "")
        if rec.get("eform_id") and rec["eform_id"] not in have and path.is_file():
            data = path.read_bytes()
            if server.post("/api/import/pdf", data, timeout=60)["sha256"] != hashlib.sha256(data).hexdigest():
                raise RuntimeError(f"The approval PDF {path.name} arrived damaged.")
            sent.append({"eform_id": rec["eform_id"], "serial": (rec.get("serial") or "").strip(),
                         "sha256": hashlib.sha256(data).hexdigest()})
            have.add(rec["eform_id"])
    return server.post("/api/approval-pdfs", {"pdfs": sent}, timeout=120)["added"] if sent else 0


def _num(s):
    try:
        return float(re.sub(r"[^\d.]", "", str(s)))
    except ValueError:
        return None


def approval_report(records, rows, trust):
    """Compare approved SUPPRESSORS (type SILENCER) with your records by serial. Other types go to record_sbrs;
    "skipped" counts only forms whose type couldn't be read, which nothing records."""
    from collections import Counter
    mine = {r["serial_number"].strip().upper(): r for r in rows}
    want_len = str((trust.get("firearm") or {}).get("overall_length") or "")
    sup = [r for r in records if "SILENCER" in r["type"].upper()]
    counts = Counter(r["serial"].strip().upper() for r in sup)
    rep = {"matched": [], "differs": [], "unknown": [], "skipped": Counter("unknown type" for r in records if r not in sup and not is_other_nfa(r)),
           "duplicates": sorted(s for s, n in counts.items() if n > 1)}
    for r in sorted(sup, key=lambda r: r["serial"]):
        notes = []
        if not r["approved"]:
            notes.append("Approved box not ticked on the PDF")
        if r.get("conditions"):
            notes.append(f"with conditions: {r['conditions'][:70]}{'…' if len(r['conditions']) > 70 else ''}")
        m = mine.get(r["serial"].strip().upper())
        if m is None:  # not one of yours (e.g. filed before this tool): just list it
            rep["unknown"].append((r, notes))
            continue
        filed_len, source = (m["overall_length"], "records") if m["overall_length"] else (want_len, "settings")
        if filed_len and _num(r["overall_length"]) != _num(filed_len):
            notes.append(f"overall length {r['overall_length'] or '?'} ({source}: {filed_len})")
        if not _token_match(m["caliber"], r["caliber"]):
            notes.append(f"caliber {r['caliber'] or '?'} (records: {m['caliber']})")
        rep["differs" if notes else "matched"].append((r, notes))
    approved = set(counts)
    rep["waiting"] = [r for r in rows if r["status"] in ("filed", "pending") and r["serial_number"].strip().upper() not in approved]
    return rep


def cmd_check_approved(_):
    """CLI: read-only check of approved Form 1s against data/suppressors.csv (suppressors only)."""
    others, in_process = [], []
    found, problems = fetch_approved(others, in_process)
    records, more = read_approved(found)
    rejected, more_rejected = read_approved(others)
    submitted, more_submitted = read_approved(in_process)
    rep = approval_report(records, load_rows(), load_trust())
    updated, added = apply_approvals(records, import_unknown=getattr(_, "import_unknown", False))
    nrep = not_approved_report(rejected, load_rows())
    marked = record_disapprovals([row["serial_number"] for row, _rec in nrep["disapproved"]])
    print(f"Recorded {len(updated)} approval(s) in data/suppressors.csv; added {len(added)} approved suppressor(s); "
          f"{record_sbrs(records)} new other NFA item(s) in data/sbrs.csv ({drop_misread_nfa(records)} misread suppressor(s) "
          f"taken out of it); {len(marked)} marked disapproved.")
    for key, title in (("disapproved", "Disapproved or returned by ATF (file them again: Advanced > Re-file)"),
                       ("correction", "Returned for correction (nothing marked: see eForms)"),
                       ("unsure", "Not approved, but couldn't tell if it's the current filing (nothing marked)")):
        if nrep[key]:
            print(f"\n{title}: {len(nrep[key])}")
            for row, rec in nrep[key]:
                print(f"  {row['serial_number']:12} {rec['status'].lower():24} eForm {rec['eform_id']}  filed {row['filed_date'] or '?'}")
    rep["waiting"] = [r for r in rep["waiting"] if r["serial_number"] not in marked]
    problems += more_rejected
    print(f"\n{len(records)} approved Form 1(s); suppressors: {sum(1 for r in records if 'SILENCER' in r['type'].upper())}; "
          f"other NFA items: {sum(1 for r in records if is_other_nfa(r))}; skipped: {dict(rep['skipped']) or 'none'}")
    for title, key in (("Approved and in your records", "matched"), ("Approved, but check these", "differs"),
                       ("Approved, NOT in your records", "unknown")):
        print(f"\n{title}: {len(rep[key])}")
        for r, notes in rep[key]:
            print(f"  {r['serial']:12} cal {r['caliber']:6} len {r['overall_length']:5} approved {r['approval_date']}  {'; '.join(notes)}")
    irep = in_process_report(submitted, load_rows())
    problems += more_submitted
    print(f"\n{len(submitted)} submitted/in process Form 1(s) checked (ones already in your records are skipped)")
    for title, items in (("In process on eForms, NOT in your records (filed by hand?)", irep["unknown"]),
                         ("In process on eForms, but queued or disapproved here (filing it would file it twice)",
                          [rec for _row, rec in irep["queued"]])):
        if items:
            print(f"\n{title}: {len(items)}")
            for r in items:
                print(f"  {r['serial']:12} cal {r['caliber']:6} len {r['overall_length']:5} filed {_in_process_fields(r)['filed_date'] or '?'}  eForm {r['eform_id']}")
    for row, rec in irep["pending"]:
        print(f"  {row['serial_number']}: pending here and in process on eForms (eForm {rec['eform_id']}): resolve it as filed")
    for s in irep["duplicates"]:
        print(f"!! serial {s} is on more than one submitted/in process Form 1")
    if irep["unknown"] or irep["queued"]:
        if getattr(_, "import_unknown", False):
            got = record_in_process(irep["unknown"] + [rec for _row, rec in irep["queued"]])
            print(f"Recorded {len(got)} as filed: {', '.join(got)}")
        else:
            print("(not recorded; --import-unknown records them as filed)")
    rep["waiting"] = [r for r in approval_report(records, load_rows(), load_trust())["waiting"] if r["serial_number"] not in marked]
    print(f"\nFiled, not approved yet: {len(rep['waiting'])}  {', '.join(r['serial_number'] for r in rep['waiting'])}")
    for s in rep["duplicates"]:
        print(f"!! serial {s} appears on more than one approved Form 1")
    for x in problems + more:
        print(f"!! {x}")
    print(f"\nPDFs: {OUT / 'approved'}")


def cmd_inspect(_):
    """Record portal pages (and any PDFs the portal sends) to out/ for mapping new or changed steps. Read-only."""
    OUT.mkdir(exist_ok=True)
    dest = OUT / f"capture_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    pw, ctx, page = _browser()
    _keep_pdfs(ctx, OUT / "captures", dest)
    out = True
    try:
        print("Log in and go to the page you want recorded, then press Enter here to record it ('q' when done).\n"
              f"Each Enter records the newest tab; PDFs the portal sends are saved to out/captures/. Log: out/{dest.name}")
        _capture_loop(page, dest, ctx)
        out = ask("Log out of eForms before closing? [Y/n] ").strip().lower() != "n"
    finally:
        _shutdown(page, ctx, pw, logout=out)


# Settings the shared mapping needs (data/trust.yaml). (path, description) -- used by the setup wizard and prechecks.
# Filing as a trust (default; the 5320.23 carries the responsible person's answers) or as an individual (the
# applicant page asks questions 11.A-11.M; answers are saved in settings and confirmed before every batch).
QUESTIONS_11 = [  # ATF's wording on the eForms applicant page (captured 2026-10-11)
    ("11.A", "Do you intend to make any firearm listed on this form for sale or other disposition to any person "
             "described in questions 11.c through 11.l, or a person described in question 11.m who does not fall under "
             "an exception?"),
    ("11.B", "Do you intend to sell or otherwise dispose of any firearm listed on this form in furtherance of any felony "
             "or other offense punishable by imprisonment for a term of more than one year, a Federal crime of terrorism, "
             "or a drug trafficking offense?"),
    ("11.C", "Are you under indictment or information in any court for a felony, or any other crime, for which the judge "
             "could imprison you for more than one year? (See definition 1.N)"),
    ("11.D", "Have you ever been convicted in any court for a felony, or any other crime, for which the judge could have "
             "imprisoned you for more than one year, even if you received a shorter sentence including probation? "
             "(See definition 1.N)"),
    ("11.E", "Are you a fugitive from justice? (See definition 1.T)"),
    ("11.F", "Are you an unlawful user of, or addicted to, marijuana or any depressant, stimulant, narcotic drug, or any "
             "other controlled substance? Warning: The use or possession of marijuana remains unlawful under Federal law "
             "regardless of whether it has been legalized or decriminalized for medicinal or recreational purposes in the "
             "state where you reside."),
    ("11.G", "Have you ever been adjudicated as a mental defective OR have you ever been committed to a mental "
             "institution? (See definition 1.O and 1.P)"),
    ("11.H", "Have you been discharged from the Armed Forces under dishonorable conditions?"),
    ("11.I", "Are you subject to a court order restraining you from harassing, stalking, or threatening your child or an "
             "intimate partner or child of such partner? (See definition 1.Q)"),
    ("11.J", "Have you ever been convicted in any court of a misdemeanor crime of domestic violence? (See definition 1.R)"),
    ("11.K", "Have you ever renounced United States citizenship?"),
    ("11.L", "Are you an alien illegally or unlawfully in the United States?"),
    ("11.M.1", "Are you an alien who has been admitted to the United States under a nonimmigrant visa?"),
]
# 11.M.2 ("If 'Yes', do you fall within any of the exceptions...") only offers N/A once 11.M.1 is No.

# Race/ethnicity: eForms' checkbox list (seen live 2026-11-20); more than one may be ticked. Settings keep one as a
# string (as before) or several as a list. The old "Alaska Native" spelling (before 1.5.2) never matched eForms.
RACES = ["American Indian or Alaskan Native", "Asian", "Black or African American", "Hispanic or Latino",
         "Middle Eastern/North African", "Native Hawaiian or Other Pacific Islander", "White", "Unknown"]


def races(person):
    """A person's race setting as a list of eForms options (a string or a list in trust.yaml; old spelling fixed)."""
    val = (person or {}).get("race") or []
    out = []
    for r in ([val] if isinstance(val, str) else val):
        r = re.sub(r"(?i)\balaska native\b", "Alaskan Native", str(r).strip())
        known = next((k for k in RACES if k.lower() == r.lower()), r)
        if known and known not in out:
            out.append(known)
    return out


COMMON_REQUIRED = [
    ("responsible_persons.0.dob", "date of birth"), ("responsible_persons.0.sex", "sex"),
    ("responsible_persons.0.race", "race"), ("responsible_persons.0.home_address.state", "state of residence"),
    ("cleo.department", "CLEO agency"), ("cleo.official", "CLEO official's name"), ("cleo.title", "CLEO official's title"),
    ("cleo.address.street", "CLEO street"), ("cleo.address.zip", "CLEO ZIP"), ("cleo.address.city", "CLEO city"),
    ("cleo.address.county", "CLEO county"), ("cleo.address.state", "CLEO state"),
    ("firearm.description", "firearm description"), ("files.eft", "fingerprint file (.eft)"),
    ("files.photo", "photo of you (eForms requires it)"),
]
TRUST_REQUIRED = [("trust.name", "trust name"), ("responsible_persons.0.title", "your title in the trust (e.g. Settlor)"),
                  ("files.trust_document", "trust document PDF")]
REQUIRED = TRUST_REQUIRED + COMMON_REQUIRED  # (trust filing; kept for callers that list everything)
# Another responsible person in the trust (responsible_persons[1:]): nothing comes from an eForms account, so the
# name and address are typed into the portal's "Add Responsible Person" popup. middle_name, suffix, email are optional.
OTHER_RP_REQUIRED = [
    ("title", "title in the trust"), ("first_name", "first name"), ("last_name", "last name"), ("dob", "date of birth"),
    ("sex", "sex"), ("race", "race"), ("home_address.street", "street address"),
    ("home_address.zip", "ZIP"), ("home_address.city", "city"), ("home_address.state", "state of residence"),
    ("eft", "fingerprint file (.eft)"), ("photo", "photo"), ("template", "signed 5320.23 template"),
]


def applicant_type(trust):
    """'trust' (default, also for settings saved before individuals were supported) or 'individual'."""
    return "individual" if str((trust or {}).get("applicant_type") or "").lower() == "individual" else "trust"


def answers_11(trust):
    """Saved answers to questions 11.A-11.M.1 as {id: 'No'|'Yes'} (individual filing)."""
    saved = ((trust or {}).get("individual") or {}).get("answers") or {}
    return {q: str(saved.get(q) or "").strip().title() for q, _ in QUESTIONS_11}


def other_rps(trust):
    """The trust's other responsible persons (settings entries after yours). Individuals have none."""
    return list((trust or {}).get("responsible_persons") or [])[1:] if applicant_type(trust) == "trust" else []


def _get(d, path):
    for k in path.split("."):
        if isinstance(d, list):
            d = d[int(k)] if k.isdigit() and int(k) < len(d) else None
        else:
            d = (d or {}).get(k)
    return d


def settings_problems(trust=None):
    """Missing settings or files, as readable strings (empty list = ready to file)."""
    if trust is None:
        if not (DATA / "trust.yaml").exists():
            return ["no settings yet (data/trust.yaml)"]
        trust = load_trust()
    kind = applicant_type(trust)
    need = COMMON_REQUIRED + (TRUST_REQUIRED if kind == "trust" else [])
    probs = [f"missing {desc} ({path})" for path, desc in need if not str(_get(trust, path) or "").strip()]
    you = (trust.get("responsible_persons") or [{}])[0]
    if born_in_us(you) and not birth_state(you):  # born abroad: no state (issue #6)
        probs.append("missing state of birth (responsible_persons.0.place_of_birth.state)")
    probs += [description_problem(trust)] if description_problem(trust) else []
    if kind == "individual":
        ans = answers_11(trust)
        blank = [q for q, a in ans.items() if a not in ("No", "Yes")]
        if blank:
            probs.append(f"questions not answered: {', '.join(blank)} (Settings)")
        elif any(a == "Yes" for a in ans.values()):
            probs.append("an answer to questions 11.A-11.M.1 is Yes: this helper only files when every answer is No; "
                         "file this application yourself in eForms")
    paths = [("files.eft", "fingerprint file"), ("files.photo", "photo")]
    paths += [("files.trust_document", "trust document")] if kind == "trust" else []
    paths += [(f"docs.{i}.path", f"supporting document {i + 1}") for i in range(len(trust.get("docs") or []))]
    for path, desc in paths:
        v = _get(trust, path)
        if v and not Path(v).is_file():
            probs.append(f"{desc} not found: {v}")
    if kind == "trust" and not (FORMS / "5320.23.pdf").is_file():
        probs.append("no 5320.23 template at forms/5320.23.pdf")
    for n, person in enumerate(other_rps(trust), 2):  # numbered, not named: these lines show on the menu's home screen
        probs += [f"responsible person {n}: missing {desc}" for path, desc in OTHER_RP_REQUIRED
                  if not str(_get(person, path) or "").strip()]
        if born_in_us(person) and not birth_state(person):
            probs.append(f"responsible person {n}: missing state of birth")
        for key, desc in (("eft", "fingerprint file"), ("photo", "photo")):
            if person.get(key) and not Path(person[key]).is_file():
                probs.append(f"responsible person {n}: {desc} not found: {person[key]}")
        if person.get("template") and not (FORMS / person["template"]).is_file():
            probs.append(f"responsible person {n}: no 5320.23 template at forms/{person['template']}")
    return probs


# ---------- Check my setup (menu: Settings > Check my setup; CLI: check-setup) ----------
FILE_KINDS = {"eft": ((b"1.001:",), "an EBTS fingerprint file (they start with 1.001:)"),
              "photo": ((b"\xff\xd8\xff", b"\x89PNG"), "a JPG or PNG picture"),
              "pdf": ((b"%PDF",), "a PDF")}
CLOUD_FOLDERS = ("OneDrive", "iCloud", "Mobile Documents", "CloudStorage", "Dropbox", "Google Drive")


def template_problems(person=None, cleo=False):
    """Test-stamp your 5320.23 template (or another responsible person's; cleo: its CLEO copy) with a dummy serial in a
    temporary folder: a list of problems (empty = it stamps correctly)."""
    import tempfile
    from datetime import date
    today = date.today().strftime("%m/%d/%Y")
    path = cleo_template(person) if cleo else FORMS / (person["template"] if person else "5320.23.pdf")
    try:
        kind = copy_kind(path)
    except Exception:
        kind = None  # unreadable: stamping below says why
    if cleo and kind == "ATF":
        return ["this is the ATF copy; choose the CLEO copy the generator made with it"]
    if not cleo and kind == "CLEO":
        return ["this is the CLEO copy; choose the ATF copy (the CLEO copy goes in Settings > CLEO copies)"]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            item = {"serial_number": "TEST123", "caliber": "22"}
            dest, missing = stamp_5320(item, today, out_dir=tmp, person=person, cleo=cleo)
            return ([f"could not place: {', '.join(missing)}"] if missing else _check_5320(dest, item, today, cleo=cleo))
    except SystemExit as e:
        return [str(e.code)]


def _online_only(st):
    """A cloud placeholder (OneDrive "online-only", iCloud "optimized storage"): the file isn't really on this computer,
    and eForms may get it too late (the fingerprint check once timed out like that)."""
    attrs = getattr(st, "st_file_attributes", 0)  # Windows: OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
    return bool(attrs & (0x1000 | 0x40000 | 0x400000) or getattr(st, "st_flags", 0) & 0x40000000)  # macOS: SF_DATALESS


def _check_file(label, value, kind=None):
    """(level, label, detail, path) for one file from the settings, or None when it isn't set (a missing setting is
    reported with the other settings). Online-only files are not read (reading would download them)."""
    if not str(value or "").strip():
        return None
    p = Path(value)
    if not p.is_file():
        return ("fail", label, "not found (choose it again in Settings)", str(p))
    st = p.stat()
    if _online_only(st):
        return ("warn", label, "online-only (OneDrive/iCloud): set it to always stay on this device", str(p))
    if not st.st_size:
        return ("fail", label, "the file is empty", str(p))
    try:
        with p.open("rb") as f:
            head = f.read(16)
    except OSError as e:
        return ("fail", label, f"can't be read ({e.strerror or e})", str(p))
    if kind and not head.startswith(FILE_KINDS[kind][0]):
        return ("warn", label, f"doesn't look like {FILE_KINDS[kind][1]}", str(p))
    size = f"{st.st_size / 1e6:.1f} MB" if st.st_size >= 1e6 else f"{max(1, round(st.st_size / 1e3))} KB"
    return ("ok", label, f"found, {size}", str(p))


def _chromium_path():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        return pw.chromium.executable_path


LINUX_DEPS = "sudo .venv/bin/python -m playwright install-deps chromium"


def _chromium_start_problem():
    """Linux only (elsewhere Chromium brings what it needs): None if Chromium starts, else why not. A fresh Linux
    machine can have Chromium installed but lack the system libraries it needs (install.sh installs them via apt)."""
    if not sys.platform.startswith("linux"):
        return None
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
        return None
    except Exception as e:
        if "missing dependencies" in str(e) or "error while loading shared libraries" in str(e):
            return f"installed, but system libraries are missing: run {LINUX_DEPS} (Debian/Ubuntu)"
        return f"installed, but can't start ({str(e).splitlines()[0] if str(e) else type(e).__name__})"


def check_setup():
    """Everything a filing needs, checked without opening eForms: [(level ok|warn|fail, label, detail, path)].
    Labels and details name no one and show no paths (the menu's screen gets shared); `path` is for the CLI."""
    out = []
    v = sys.version_info
    out.append(("ok" if (3, 9) <= v[:2] <= (3, 14) else "warn", "Python", f"{v[0]}.{v[1]}.{v[2]}"
                + ("" if (3, 9) <= v[:2] <= (3, 14) else " (3.9-3.14 are supported; run the installer again)"), sys.executable))
    try:
        exe = _chromium_path()
        problem = _chromium_start_problem() if exe and Path(exe).exists() else None
        out.append(("fail", "Browser (Chromium)", problem, exe) if problem else
                   ("ok", "Browser (Chromium)", "installed", exe) if exe and Path(exe).exists() else
                   ("fail", "Browser (Chromium)", "not installed: run the installer again", exe or ""))
    except Exception as e:
        out.append(("fail", "Browser (Chromium)", f"can't start ({str(e).splitlines()[0] if str(e) else type(e).__name__}): "
                    "run the installer again", ""))
    cloud = next((c for c in CLOUD_FOLDERS if c.lower() in str(ROOT).lower()), None)
    out.append(("warn", "Helper folder", f"inside {cloud}: its files can become online-only; a folder outside it is safer",
                str(ROOT)) if cloud else ("ok", "Helper folder", "not in a synced cloud folder", str(ROOT)))
    if not (DATA / "trust.yaml").exists():
        return out + [("fail", "Settings", "no settings yet: choose Settings in the menu", str(DATA / "trust.yaml"))]
    trust = load_trust()
    kind = applicant_type(trust)
    missing = [p for p in settings_problems(trust) if not re.search(r"not found|no 5320\.23 template", p)]
    out.append(("fail", "Settings", "; ".join(missing), str(DATA / "trust.yaml")) if missing else
               ("ok", "Settings", f"complete ({'a trust' if kind == 'trust' else 'an individual'})", str(DATA / "trust.yaml")))
    f = trust.get("files") or {}
    files = [("Your fingerprint file", f.get("eft"), "eft"), ("Your photo", f.get("photo"), "photo")]
    files += [("Trust document", f.get("trust_document"), "pdf")] if kind == "trust" else []
    files += [(f"Supporting document {i}", d.get("path"), None) for i, d in enumerate(trust.get("docs") or [], 1)]
    out += [c for c in (_check_file(*x) for x in files) if c]
    for n, person in enumerate([None] + other_rps(trust) if kind == "trust" else [], 1):
        who = "Your" if person is None else f"Responsible person {n}:"
        if person is not None:
            out += [c for c in (_check_file(f"{who} fingerprint file", person.get("eft"), "eft"),
                                _check_file(f"{who} photo", person.get("photo"), "photo")) if c]
        name = "5320.23.pdf" if person is None else person.get("template")
        c = _check_file(f"{who} 5320.23 template", name and str(FORMS / name), "pdf")
        if c and c[0] == "ok":  # readable: does it stamp?
            try:
                probs = template_problems(person)
            except Exception as e:
                probs = [str(e).splitlines()[0] if str(e) else type(e).__name__]
            c = (("fail", c[1], "; ".join(probs), c[3]) if probs else
                 ("ok", c[1], "stamps correctly (test serial, today's date)", c[3]))
        out += [c] if c else []
        cl = cleo_template(person) if person is None or person.get("template") else None
        if cl is not None and not cl.is_file():  # optional: filing works without it
            out.append(("warn", f"{who} 5320.23 CLEO copy", "not set: no CLEO copy is made (Settings > CLEO copies)", str(cl)))
        elif cl is not None:
            c = _check_file(f"{who} 5320.23 CLEO copy", str(cl), "pdf")
            if c[0] == "ok":
                try:
                    probs = template_problems(person, cleo=True)
                except Exception as e:
                    probs = [str(e).splitlines()[0] if str(e) else type(e).__name__]
                c = (("fail", c[1], "; ".join(probs), c[3]) if probs else
                     ("ok", c[1], "stamps correctly (caliber and date; no serial, as ATF prints it)", c[3]))
            out.append(c)
    out.append(_backup_check(trust))
    if server_settings():
        import remote
        s = server_settings()
        try:
            server = _server(s)
            sup, sbrs = server.rows()
            out.append(("ok", "Inventory server", f"reachable at {server.address} · {len(sup)} suppressors, {nfa_counts(sbrs)}",
                        server.address))
            st = server.status()  # None from a server older than the status report
            if st:
                behind = st.get("update") or {}
                for problem in st.get("problems") or []:
                    out.append(("warn", "Inventory server", problem, None))
                out.append(("warn" if behind else "ok", "Inventory server version",
                            f"{st['version']}" + (f": version {behind['version']} is out (on the server: atf-server update)"
                                                  if behind else ""), None))
        except remote.ServerError as e:
            out.append(("fail", "Inventory server", str(e), ", ".join(s["addresses"])))
    elif SUPP.exists():
        rows = load_rows()
        pending = [r["serial_number"] for r in rows if r["status"] == "pending"]
        try:
            with SUPP.open("r+b"):  # Excel on Windows locks a file it has open; nothing is written
                pass
            out.append(("warn", "Inventory", f"{len(pending)} pending: resolve {'it' if len(pending) == 1 else 'them'} from the "
                        "menu before filing more", str(SUPP)) if pending else ("ok", "Inventory", f"{len(rows)} suppressor{'s' if len(rows) != 1 else ''}", str(SUPP)))
        except OSError:
            out.append(("fail", "Inventory", "can't be saved: is it open in Excel? Close it", str(SUPP)))
    return out


def _backup_check(trust):
    folder = backup_folder(trust)
    if not folder:
        return ("ok", "Backups", "a daily copy of your records on this computer (optional: Settings > Backups adds a "
                "USB drive or cloud folder)", str(OUT / "backups"))  # optional: never counted as something to look at
    last = _backup_state().get("folder_ok")
    when = f"last copied {last[:10]}" if last else "not copied yet"
    if not Path(folder).expanduser().is_dir():
        return ("warn", "Backups", f"your backup folder isn't there (is the drive connected?); {when}", folder)
    return ("ok", "Backups", f"to your backup folder, {when}", folder)


def cmd_check_setup(args):
    marks = {"ok": "OK  ", "warn": "WARN", "fail": "FIX "}
    res = check_setup()
    for level, label, detail, path in res:
        print(f"{marks[level]} {label}: {detail}" + (f"\n       {path}" if path else ""))
    if any(r[0] == "fail" for r in res):
        sys.exit(1)


_NO_UPIN = re.compile(r"^(n/?a|none|no|-+|0+)?$", re.I)


def upin(trust):
    """The individual's UPIN (item 13), or "" when there is none. "N/A", "none", "no" and the like count as none: a
    user wrote N/A there (2026-11-16) and it would have been typed into the portal's UPIN box."""
    val = str((trust.get("individual") or {}).get("upin") or "").strip()
    return "" if _NO_UPIN.match(val) else val


def _row_context(trust, item):
    """Placeholder values for config/mapping.yaml steps for one suppressor row."""
    rp = trust["responsible_persons"][0]
    f, cl, fa = trust.get("files", {}), trust["cleo"], trust.get("firearm", {})
    kind = applicant_type(trust)
    out_pdf = _find_5320(item["serial_number"]) if kind == "trust" else ""  # individuals have no 5320.23
    tr = trust.get("trust") or {}
    ans = answers_11(trust)
    ctx = {**{f"trust_{k}": v for k, v in tr.items() if not isinstance(v, dict)},
           **{f"trust_{k}": v for k, v in tr.get("address", {}).items()}, **item,
           "applicant_type": kind,
           "answers": [{"q_id": q, "q_answer": a} for q, a in ans.items()] + [{"q_id": "11.M.2", "q_answer": "N/A"}],
           "upin": upin(trust),
           "icn": item["serial_number"] if icn_is_serial(trust) else "",
           "rp_title": rp.get("title", ""), "rp_dob": rp["dob"], "rp_race": ", ".join(races(rp)), "rp_races": [{"rp_race": r} for r in races(rp)],
           "rp_birth_country": birth_country(rp), "rp_birth_state": birth_state(rp),
           "rp_residence_state": rp["home_address"]["state"].upper(), "rp_sex": rp["sex"].upper(),
           "cleo_department": cl["department"], "cleo_official": cl["official"], "cleo_title": cl["title"],
           "cleo_street": cl["address"]["street"], "cleo_zip": str(cl["address"]["zip"]),
           # dropdowns: the portal lists these uppercased
           "cleo_city": cl["address"]["city"].upper(), "cleo_county": cl["address"]["county"].upper(),
           "cleo_state": cl["address"]["state"].upper(),
           "model": str(fa.get("model") or "N/A"),
           "overall_length": item.get("overall_length") or str(fa.get("overall_length") or "12"), "description": fa["description"],
           "form_5320_path": str(out_pdf), "photo_path": f.get("photo"), "eft_path": f.get("eft"),
           "trust_doc_path": f.get("trust_document"), "trust_doc_desc": f.get("trust_document_desc") or "TRUST",
           # file names as the portal lists them once added, quoted for a :has-text() selector
           "trust_doc_file": json.dumps(Path(f.get("trust_document") or "").name),
           "docs": [{"doc_path": d["path"], "doc_desc": d["description"], "doc_file": json.dumps(Path(d["path"]).name)}
                    for d in trust.get("docs") or []]}
    if is_nfa_row(item):  # an SBR etc.: the receiver's own maker/model, typed as "not in the list" (see mapping.yaml)
        photo = item.get("photo", "").strip()
        ctx.update({"model": item["model"] or "N/A", "description": item.get("description", ""),
                    "docs": ctx["docs"] + ([{"doc_path": photo, "doc_desc": "PHOTO OF RECEIVER MARKINGS",
                                             "doc_file": json.dumps(Path(photo).name)}] if photo else [])})
    ctx.update({"nfa": "yes" if is_nfa_row(item) else "", "product_type": (item.get("type") or "SILENCER").upper(),
                "mfr_code": item.get("mfr_code") or ("" if is_nfa_row(item) else "FMI"), "_found": {}, "mfr_country": _mfr_country(item.get("mfr_country")),
                "unit": item.get("unit") or "CALIBER", "barrel_length": item.get("barrel_length", "")})
    others = [_other_rp_context(p, n, item) for n, p in enumerate(other_rps(trust), 2)]
    ctx.update({"other_rps": others, "has_other_rps": "yes" if others else "", "rp_count": str(1 + len(others))})
    return ctx


def _other_rp_context(person, n, item):
    """Placeholders for one other responsible person (mapping `repeat: other_rps`); the same rp_* names as yours."""
    middle, addr = str(person.get("middle_name") or "").strip(), person["home_address"]
    detail = "NO MIDDLE NAME" if not middle else "INITIAL ONLY" if len(middle.strip(".")) == 1 else "FULL MIDDLE NAME"
    return {"rp_n": str(n), "rp_edit": f"{n - 1}Edit",  # row n-1 of the portal's list (yours is 0Edit)
            "rp_title": person["title"], "rp_first_name": person["first_name"], "rp_middle_detail": detail,
            "rp_middle_name": middle, "rp_last_name": person["last_name"], "rp_suffix": str(person.get("suffix") or ""),
            "rp_email": str(person.get("email") or ""), "rp_street": addr["street"], "rp_zip": str(addr["zip"]),
            # dropdowns: the portal lists these uppercased
            "rp_city": addr["city"].upper(), "rp_state": addr["state"].upper(), "rp_residence_state": addr["state"].upper(),
            "rp_dob": person["dob"], "rp_race": ", ".join(races(person)), "rp_races": [{"rp_race": r} for r in races(person)], "rp_sex": person["sex"].upper(),
            "rp_birth_country": birth_country(person), "rp_birth_state": birth_state(person),
            "form_5320_path": str(_find_5320(item["serial_number"], person=person)),
            "photo_path": person["photo"], "eft_path": person["eft"]}


def _norm_name(text):
    return re.sub(r"[^A-Z0-9 ]", "", re.sub(r"\s+", " ", (text or "").upper())).strip()


def _mfr_country(c):
    """eForms' #country lists "UNITED STATES"; a receiver marked USA (or nothing) means that."""
    c = str(c or "").strip().upper()
    return "UNITED STATES" if c in _US_SPELLINGS else c


_MFR_WAIT_MS = 8000  # how long eForms' name search may take to list anything
_MFR_SUFFIXES = {"INC", "INCORPORATED", "LLC", "LC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "LP",
                 "LLP", "PLLC", "GMBH", "AG", "SA", "SPA"}


def _mfr_queries(name):
    """What to type into eForms' manufacturer search, best first. The search matches any part of a name, so a name
    without punctuation and company suffix lists the marked one and its variants in one go: "SIG SAUER INC." listed
    nothing (2026-11-25, the period) and cost a wait before "SIG SAUER" did. Then shorter, a word at a time."""
    words = _norm_name(name).split()
    while len(words) > 1 and words[-1] in _MFR_SUFFIXES:
        words.pop()
    return [" ".join(words[:n]) for n in range(len(words), 0, -1)] or [name]


def _pick_manufacturer(page, code, name, tries=3):
    """Firearm popup 1. With a code (FMI for a suppressor, or one saved earlier): By Manufacturer Code. Without one (an
    SBR etc.): eForms' own name search, the way a person does it (mapped live 2026-11-19): type the name into the
    PrimeNG autocomplete `p-autocomplete#manufacturerName input` (real keystrokes; it matches any part of a name),
    read `.ui-autocomplete-panel li` (no hits for the full name: shorter, a word at a time). An exact match (ignoring case/punctuation) is picked;
    otherwise you choose from the list in the terminal, type another spelling, or pick + verify it in the browser
    yourself and press Enter (the code is then read from the page).
    Then Verify Manufacturer; the code is read from #manufacturerCodeFromName. Returns {mfr_code, mfr_atf_name}."""
    if code:
        _tick(page, "#byCode")
        page.locator("#manufacturerCode").fill(code)
        page.locator("#verifyManufacturer").click()
        page.wait_for_timeout(1500)
        return {"mfr_code": code}
    _tick(page, "#byName")
    box = page.locator("#manufacturerName input")
    # PrimeNG's suggestion list; newer PrimeNG names it p-autocomplete-*, and an ARIA listbox in any version
    items = page.locator(".ui-autocomplete-panel li, .p-autocomplete-panel li, "
                         "p-autocomplete [role=listbox] [role=option], body > [role=listbox] [role=option]")

    def shown_code():
        try:
            return (page.locator("#manufacturerCodeFromName").input_value(timeout=2000) or "").strip()
        except Exception:
            return ""

    # verified, eForms swaps the input for plain text: <div class="manufacturerNameBox"> STAG ARMS </div> (live 2026-11-24;
    # the popup can even open that way, with the last manufacturer verified on this account)
    name_box = page.locator("#manufacturerNameInput .manufacturerNameBox")

    def shown_name():
        try:
            if name_box.count():
                return (name_box.first.inner_text(timeout=2000) or "").strip()
            if box.count():
                return (box.input_value(timeout=2000) or "").strip()
        except Exception:
            pass
        return ""

    def verified_by_hand():  # eForms shows a verified code (it took the name, or the user picked it by hand)
        found = shown_code()
        if not found:
            return None
        shown = shown_name()
        emit("step", f"    manufacturer verified: {shown} (code {found})")
        return {"mfr_code": found, "mfr_atf_name": shown}

    def verify():  # Verify Manufacturer, then wait for the code (eForms can be slow; 2026-11-24 a run timed out)
        page.locator("#verifyManufacturer").click()
        for _ in range(max(2, _MFR_WAIT_MS // 500)):
            page.wait_for_timeout(500)
            if shown_code():
                return

    def unlock():
        """Once a name is verified, eForms replaces the name box with plain text (2026-11-24, STAG ARMS -> NBC: the next
        fill then waited 30 s for a box that was gone). Click Clear to get the box back."""
        if box.count() and box.is_enabled(timeout=2000) and not shown_code():
            return
        clear = page.locator("#clearManufacturer, button:has-text('Clear')").first
        if clear.count():
            clear.click()
        try:
            box.wait_for(state="visible", timeout=5000)
        except Exception:
            raise RuntimeError("eForms shows a verified manufacturer and Clear did not bring the name box back")
        if not box.is_enabled(timeout=2000):
            raise RuntimeError("eForms locked the manufacturer name box and Clear did not unlock it")

    def search(q):
        done = verified_by_hand()  # verified already (by eForms or by hand): keep it if it is the marked name
        if done and _norm_name(done["mfr_atf_name"]) == _norm_name(name):
            return done
        if done:
            emit("step", f"    eForms has {done['mfr_atf_name']} verified, not {name}: clearing it")
        unlock()
        box.fill("", timeout=5000)
        box.press_sequentially(q, delay=60)
        try:
            items.first.wait_for(timeout=_MFR_WAIT_MS)
        except Exception:
            pass
        page.wait_for_timeout(500)  # the list can grow as more results arrive
        return [t.strip() for t in items.all_inner_texts() if t.strip()]

    # "AERO PRECISION LLC" -> "AERO PRECISION" (lists INC and LLC; the exact one is picked) -> "AERO"
    queries = _mfr_queries(name)
    query, picked, names = queries[0], None, []
    for _ in range(tries):
        for query in queries:
            names = search(query)
            if isinstance(names, dict):  # already verified as the marked name
                return names
            if names:
                break
        exact = [n for n in names if _norm_name(n) == _norm_name(name)]
        if len(exact) == 1:
            picked = exact[0]
            emit("step", f"    manufacturer: {picked} (exact match)")
            break
        if not names:  # no list reached us; the site may still take the typed name as is (2026-11-19: it showed AP2)
            unlock()
            box.fill("", timeout=5000)
            box.press_sequentially(name, delay=60)
            page.wait_for_timeout(1000)
            page.keyboard.press("Escape")
            verify()
        done = verified_by_hand()  # the page already shows a verified code: nothing to ask
        if done:
            return done
        hint = ("Or pick it in the browser, click Verify Manufacturer, then press Enter here. q stops: ")
        if names:
            listing = "\n".join(f"  {i}. {n}" for i, n in enumerate(names, 1))
            ans = ask(f"eForms lists these manufacturers for \"{query}\". Which one is marked on the receiver?\n{listing}\n"
                      "Type its number, or another spelling to search again. " + hint).strip()
        else:
            ans = ask(f"eForms shows no manufacturer for \"{name}\" (also tried shorter). Type another spelling. "
                      + hint).strip()
        if ans.lower() == "q":
            raise RuntimeError("manufacturer not chosen")
        if not ans:  # Enter: the user chose it in the browser
            done = verified_by_hand()
            if done:
                return done
            continue  # nothing verified there: search again
        if ans.isdigit() and names and 1 <= int(ans) <= len(names):
            picked = names[int(ans) - 1]
            break
        queries = [ans]
    if picked is None:
        raise RuntimeError(f"no eForms manufacturer chosen for {name!r}")
    # exact text: "PALMETTO STATE ARMORY" must not hit "PALMETTO STATE ARMORY LLC"
    items.filter(has_text=re.compile("^\\s*" + re.escape(picked) + "\\s*$")).first.click()
    verify()
    found = shown_code()
    if not found:
        raise RuntimeError(f"eForms did not verify {picked!r} (no manufacturer code shown)")
    emit("step", f"    manufacturer code: {found}")
    return {"mfr_code": found, "mfr_atf_name": picked}


def _model_matches(option, model):
    """An eForms model entry for the engraved model: the same, or the maker's name before it ("AERO PRECISION M4E1")."""
    o, m = _norm_name(option), _norm_name(model)
    return bool(m) and (o == m or o.endswith(" " + m))


def _close_models(options, model, n=8):
    """eForms model entries that look like a mistyped `model`, best first: compared whole and by their tail (the
    maker's name often comes first, "AERO PRECISION M4E1")."""
    import difflib
    key = lambda t: re.sub(r"[^A-Z0-9]", "", t.upper())  # noqa: E731  "STAG-15" = "STAG 15"
    m = key(model)

    def score(option):
        words = _norm_name(option).split()
        tails = {" ".join(words[i:]) for i in range(len(words))}
        return max(difflib.SequenceMatcher(None, m, key(t)).ratio() for t in tails)

    scored = sorted(((score(o), o) for o in options), key=lambda x: -x[0])
    return [o for sc, o in scored if sc >= 0.6][:n]


def _pick_model(page, model, caliber, nfa):
    """Firearm popup 2, model and caliber. A silencer (FMI): plain text inputs, typed. An SBR etc.: Model is an
    ng-select of ATF's list for that maker, Caliber a select (mapped live 2026-11-19). The model is typed into the
    ng-select's search and an entry for it picked (one match: picked; several or none: asked, the list shown); the
    caliber is picked from its list. Not in the lists: tick "My item description is not in the list" and type both
    (eForms then warns on the certify page that ATF must validate it). Returns the eForms model name picked, or ""."""
    if not nfa:
        _fill(page, page.locator("#weaponModel"), model, "#weaponModel")
        _fill(page, page.locator("#weaponCaliber"), caliber, "#weaponCaliber")
        return ""
    picked = ""
    sel_box = page.locator("ng-select#weaponModel")
    if sel_box.count():
        options = page.locator(".ng-dropdown-panel .ng-option")

        def listed(q):
            sel_box.click()
            box = sel_box.locator("input")
            box.fill("")
            if q:
                box.press_sequentially(q, delay=40)
            page.wait_for_timeout(600)
            return [t.strip() for t in options.all_inner_texts()
                    if t.strip() and not re.match(r"(?i)no items found", t.strip())]

        query = model
        while True:
            names = listed(query)
            hits = [n for n in names if _model_matches(n, query)]
            # "STAG-15" and "STAG 15" are both listed: the one spelled exactly as engraved wins (case aside)
            literal = [n for n in hits if n.upper() == query.strip().upper()]
            if len(hits) > 1 and len(literal) == 1:
                hits = literal
            if len(hits) == 1:
                picked = hits[0]
                break
            everything = listed("")
            if not everything:
                break  # no list at all: typed in as not listed
            # No match is usually a typo (PR #5: a fat-fingered model went in as "not listed" though it was listed):
            # the closest entries first, never typed in as not listed without being told to
            close = hits or _close_models(everything, query)
            shown = close or everything
            listing = "\n".join(f"  {i}. {n}" for i, n in enumerate(shown, 1))
            head = (f"No eForms model matches \"{query}\" exactly. Closest for this manufacturer:" if close and not hits
                    else f"eForms lists these models for this manufacturer; the receiver is engraved \"{model}\":")
            ans = ask(f"{head}\n{listing}\nType the number of the matching one, another spelling to search again, "
                      "\"none\" to type it in as not listed (ATF then checks it by hand), or q to stop: ").strip()
            if ans.isdigit() and 1 <= int(ans) <= len(shown):
                picked = shown[int(ans) - 1]
                break
            if ans.lower() == "q":
                page.keyboard.press("Escape")
                raise RuntimeError("model not chosen")
            if ans.lower() == "none":
                if _norm_name(query) != _norm_name(model):
                    emit("warn", f"    typing the queued model \"{model}\" in as not listed (fix a typo with Edit or "
                                 "remove a queued item)")
                break
            if ans:
                query = ans
        if picked:
            listed(query if picked in names else "")
            options.filter(has_text=re.compile("^\\s*" + re.escape(picked) + "\\s*$")).first.click()
            try:
                _pick(page.locator("select#weaponCaliber"), caliber, wait_ms=5000, grace_ms=1500)
            except NoSuchOption as e:
                emit("warn", f"    caliber {e}; typing model and caliber in as not listed")
                picked = ""
            else:
                emit("step", f"    model: {picked} (from eForms' list), caliber {caliber}")
        else:
            page.keyboard.press("Escape")
    if not picked:
        _tick(page, "#descriptionNotListed")
        page.wait_for_timeout(800)
        _fill(page, page.locator("#weaponModel"), model, "#weaponModel")
        _fill(page, page.locator("#weaponCaliber"), caliber, "#weaponCaliber")
        emit("step", f"    model {model} typed in as not listed")
    return picked


_current_step = ""
_recent_steps = []  # the last steps run (with their values), saved with a failing page


def _run_steps(page, steps, ctx_vals, dry, bad, skip_tags=()):
    """Execute mapping steps. Raises on failure; read-back mismatches are appended to `bad`.
    Steps whose `tag` is in skip_tags are left out (e.g. tag: ssn with --skip-ssn). With dry, page may be None."""
    global _current_step, _picks
    for s in steps:  # {selector, action: fill|select|check|click|upload|..., value: "{key}" or literal}
        act = s["action"]
        if s.get("only") and s["only"] != ctx_vals.get("applicant_type", "trust"):  # trust-only / individual-only
            continue  # (before formatting: an individual has no {trust_name})
        val = str(s.get("value", "")).format(**ctx_vals) if s.get("value") else None
        sel = s["selector"].format(**ctx_vals) if s.get("selector") else None  # e.g. :has-text('{rp_race}')
        if act == "section":  # progress marker only
            emit("section", f"--- {val} ---", name=val)
            continue
        if act == "check_picks":  # run the sub-steps picking dropdowns quickly, then check them all (_check_picks)
            outer, _picks = _picks, []
            try:
                _run_steps(page, s["steps"], ctx_vals, dry, bad, skip_tags)
                if not dry:
                    _check_picks(page, _picks)
            finally:
                _picks = outer
            continue
        if act == "repeat":  # run the sub-steps once per item of a list setting (e.g. docs)
            for item in ctx_vals.get(s["over"]) or []:
                _run_steps(page, s["steps"], {**ctx_vals, **item}, dry, bad, skip_tags)
            continue
        _current_step = sel or act
        if s.get("if_set") and not str(ctx_vals.get(s["if_set"]) or "").strip():  # e.g. a UPIN only if you have one
            continue
        if act == "pick_manufacturer":  # by code if known, else eForms' own name search (see _pick_manufacturer)
            code = str(ctx_vals.get("mfr_code") or "").strip()
            emit("step", f"pick_manufacturer {'code ' + code if code else 'by name ' + str(ctx_vals.get('mfr_name', ''))}")
            if not dry:
                found = _pick_manufacturer(page, code, str(ctx_vals.get("mfr_name") or ""))
                if not code and isinstance(ctx_vals.get("_found"), dict):
                    ctx_vals["_found"].update(found)
            continue
        if act == "pick_model":  # from eForms' lists if there, else typed as not listed (see _pick_model)
            emit("step", f"pick_model {ctx_vals.get('model', '')} / {ctx_vals.get('caliber', '')}")
            if not dry:
                _pick_model(page, str(ctx_vals.get("model") or ""), str(ctx_vals.get("caliber") or ""),
                            bool(ctx_vals.get("nfa")))
            continue
        if s.get("tag") in skip_tags:
            emit("step", f"(skipping {s['tag']} step)")
            continue
        emit("step", f"{act} {sel or ''} {val or ''}")
        _recent_steps[:] = _recent_steps[-24:] + [f"{act} {sel or ''} {val or ''}".strip()]
        if dry:
            continue
        loc = page.locator(sel) if sel else None
        if s.get("skip_if_disabled") and loc is not None and (loc.is_disabled() or not loc.is_editable()):
            emit("step", "    (field is locked/prefilled; skipping)")
            continue
        if s.get("optional"):  # click it if it shows up within 3 s, otherwise carry on
            try:
                loc.first.click(timeout=3000)
            except Exception:
                emit("step", "    (not present; skipping)")
            continue
        if act in ("fill", "select"):
            try:
                (_fill if act == "fill" else _select)(page, loc, val, sel)
            except NoSuchOption as e:  # fix: where in the settings that value comes from
                if not s.get("fix"):
                    raise
                raise NoSuchOption(f"{e}. {s['fix']}") from None
        elif act == "check": _tick(page, sel)
        elif act == "click": loc.click()
        elif act == "upload": loc.set_input_files(val)
        elif act == "pause": ask(f"  >> {str(s.get('note', 'Do this step manually in the browser')).format(**ctx_vals)} then press Enter... ")
        elif act == "expect":  # compare a read-only field against the intended value; never changes anything
            got = str(loc.count()) if s.get("count") else _shown_value(loc)  # count: how many elements match
            # exact: whole-value match (e.g. citizenship "US", which a token match would also find in "NON-US")
            ok = got.lower() == val.strip().lower() if s.get("exact") else _token_match(val, got)
            emit("expect", f"    {'OK ' if ok else 'MISMATCH'} expected {val!r} got {got!r}",
                 label=str(s.get("label", sel)).format(**ctx_vals), want=val, got=got, ok=ok, private=s.get("private", False))
            if not ok:
                bad.append((sel, val, got))
        elif act == "dismiss_dialog":  # print the dialog's text (so warnings stay visible), then click its OK
            need = s.get("require")  # e.g. "Valid EBTS Package": anything else blocks --submit
            limit = int(val or 1500)
            if need:
                # Wait (up to `value` ms) for a dialog saying it. The fingerprint check can take a while (slow
                # connection, a cloud-only OneDrive file); 1.5 s once wasn't enough. Other open dialogs, such as the
                # responsible-person form itself, are not this message and are left alone.
                waited = 0
                while True:
                    dlgs = _dump_page(page).get("dialogs", [])
                    hit = [t for t in dlgs if _token_match(need, t)]
                    if hit or waited >= limit:
                        break
                    page.wait_for_timeout(250)
                    waited += 250
                hint = s.get("about", "")  # e.g. "EBTS": also show dialogs mentioning it (an Invalid message)
                shown = hit or [t for t in dlgs if hint and hint.lower() in t.lower()]
            else:
                page.wait_for_timeout(limit)
                dlgs = shown = hit = _dump_page(page).get("dialogs", [])
            for t in shown:
                emit("info", f"    DIALOG: {t[:400]}")
            if need and not hit:
                emit("error", f"    !! no dialog said {need!r}" + ("" if shown else f" within {limit // 1000} s")
                     + ": this application will NOT be submitted")
                bad.append(("dialog", need, " | ".join(t[:120] for t in shown) or "(no such dialog)"))
            elif not dlgs:
                emit("info", "    (no dialog appeared)")
            btn = page.locator(".modal button:visible, [role=dialog] button:visible").filter(
                has_text=re.compile(r"^\s*(ok|okay)\s*$", re.I))
            if shown and btn.count():
                btn.first.click()
                emit("step", "    clicked OK")
            elif shown:
                emit("warn", "    !! that message has no OK button I recognise; close just that message yourself")
                ask("    press Enter when closed... ")
        elif act == "press": page.keyboard.press(val)
        elif act == "wait": page.wait_for_timeout(int(val or 1000))
        elif act == "wait_for": loc.first.wait_for(state=s.get("state", "attached"), timeout=int(val or 30000))


_SELECTED_JS = ("e => e.tagName === 'SELECT' ? ((e.options[e.selectedIndex] || {}).text || '').trim()"
                " : ('value' in e ? (e.value || '') : (e.innerText || '')).trim()")  # (e.g. a row of a list: its text)


def _shown_value(loc):
    """What a field shows: a dropdown's selected option text (its hidden value may be a code), else its value
    (an element that has no value, such as a table row: its text)."""
    return (loc.evaluate(_SELECTED_JS) or "").strip()


_picks = None  # inside a `check_picks` block: [(selector, locator, value)] picked quickly, checked together at the end


class NoSuchOption(RuntimeError):
    """A dropdown doesn't offer the value from the settings (e.g. a misspelled CLEO county)."""


_PLACEHOLDER = re.compile(r"^(-+|select\b|choose\b|please\b)", re.I)


def _pick(loc, val, wait_ms=30000, grace_ms=3000):
    """Choose a dropdown option by its label (else its value). Dropdowns filled by the page (the CLEO city/county
    from the ZIP) may still be loading: wait up to `wait_ms` for any options, then `grace_ms` for the wanted one.
    Not offered = NoSuchOption naming the choices (Playwright alone waited 30 s and said only "Timeout": a CLEO
    county typed "MONORE" for MONROE, 2026-11-15)."""
    want = val.strip().lower()
    waited, real_since = 0, None
    while True:
        opts = loc.evaluate("e => [...e.options].map(o => [o.text.trim(), o.value])") or []
        label = next((t for t, _ in opts if t.lower() == want), None)
        if label is not None:
            return loc.select_option(label=label)
        value = next((v for _, v in opts if v.strip().lower() == want), None)
        if value is not None:
            return loc.select_option(value=value)
        real = [t for t, _ in opts if t and not _PLACEHOLDER.match(t)]
        if real and real_since is None:
            real_since = waited
        if waited >= wait_ms or (real_since is not None and waited - real_since >= grace_ms):
            import difflib  # a long list (e.g. 250 countries): the nearest spellings say more than the first dozen
            close = difflib.get_close_matches(val.strip().upper(), [t.upper() for t in real], n=5, cutoff=0.5)
            close = [next(t for t in real if t.upper() == c) for c in close]
            shown = (f"closest: {', '.join(close)}; {len(real)} choices in all" if len(real) > 12 and close else
                     ", ".join(real[:12]) + (f" and {len(real) - 12} more" if len(real) > 12 else ""))
            raise NoSuchOption(f"{val!r} isn't one of the choices on the page"
                               + (f" ({shown})" if real else " (it shows none)"))
        loc.page.wait_for_timeout(250)
        waited += 250


def _kept(loc, val):
    want = val.strip().lower()
    return want in (_shown_value(loc).lower(), (loc.input_value() or "").strip().lower())


def _select(page, loc, val, sel=""):
    """Pick a dropdown option. Inside a check_picks block it is checked later with the others; elsewhere it is
    checked once right away (a pick that didn't take at all fails the step instead of going on blank)."""
    _pick(loc, val)
    if _picks is not None:
        _picks.append((sel, loc, val))
        return
    if not _kept(loc, val):
        page.wait_for_timeout(500)
        if not _kept(loc, val):
            raise RuntimeError(f"the page shows {_shown_value(loc)!r} instead of {val!r}")


def _fill(page, loc, val, sel=""):
    """Type into a field. The same id can also be a dropdown, or a wrapper around the real input (the trust's "Add
    Responsible Person" popup: #title was not an <input> on the first live try): pick the option / fill the inner field."""
    tag = loc.evaluate("e => e.isContentEditable ? 'textarea' : e.tagName.toLowerCase()")
    if tag in ("input", "textarea"):
        loc.fill(val)
    elif tag == "select":
        _select(page, loc, val, sel)
    else:
        inner = loc.locator("input:not([type=hidden]), textarea, select")
        if not inner.count():
            raise RuntimeError(f"{sel} is a <{tag}> with no field inside it")
        _fill(page, inner.first, val, sel)


def _save_failure(page):
    """Record the page a step failed on, to fix the step: out/failed_<time>.jsonl (fields, labels, dialogs, like a
    capture) and .html (the open dialogs, else the page). Holds page text with your details: keep it to yourself."""
    try:
        OUT.mkdir(exist_ok=True)
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        _log_capture(OUT / f"failed_{stamp}.jsonl", {**_dump_page(page), "failed_step": _current_step, "last_steps": _recent_steps})
        html = page.evaluate("""() => { const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
            const els = [...document.querySelectorAll('.modal, [role=dialog], .ui-dialog, [class*=modal]')].filter(vis);
            return els.filter(e => !els.some(o => o !== e && o.contains(e))).map(e => e.outerHTML).join('\\n<hr>\\n'); }""")
        (OUT / f"failed_{stamp}.html").write_text(html or page.content(), encoding="utf-8")
        emit("info", f"(this page was saved to out/failed_{stamp}.jsonl and .html, to fix the step)")
    except Exception as e:  # never let the record hide the failure itself
        emit("warn", f"could not save the failing page ({str(e).splitlines()[0] if str(e) else type(e).__name__})")


def _check_picks(page, picks, rounds=2):
    """Read back every answer picked in a check_picks block; re-pick the ones the page lost. On the individual's
    applicant page the answers were once all lost (the page was still loading the profile and reset them)."""
    for n in range(rounds + 1):
        page.wait_for_timeout(1000)
        lost = [(sel, loc, val) for sel, loc, val in picks if not _kept(loc, val)]
        if not lost:
            return
        names = ", ".join(sel for sel, _, _ in lost)
        if n == rounds:
            raise RuntimeError(f"the page keeps losing these answers: {names}")
        emit("warn", f"    {len(lost)} answer(s) didn't stick ({names}); picking them again")
        for _, loc, val in lost:
            _pick(loc, val)


def _shutdown(page, ctx, pw, logout=True):
    try:
        if logout:
            _logout(page)
    finally:
        for close in (ctx.close, pw.stop):
            try:
                close()
            except Exception:
                pass


def _affirm(page, args):
    if args.auto_affirm:
        # Opt-in: --auto-affirm (or the menu's confirmation) is YOUR affirmation that each application is a legitimate Form 1.
        try:
            page.locator("#closeModalBtn").click(timeout=5000)
            emit("step", "Clicked 'I affirm'.")
        except Exception:
            emit("step", "No 'I affirm' popup found; continuing.")


def _file_one(page, args, trust, steps, rows, idx):
    """Fill (and with --submit, submit) one row. Returns True if the row ended up marked filed.
    On failure it waits for Enter and exits; the caller's finally logs out."""
    item = rows[idx]
    ctx_vals = _row_context(trust, item)
    emit("row", f"\n=== Row {idx + 1}: serial {item['serial_number']}, caliber {item['caliber']} ===",
         serial=item["serial_number"], caliber=item["caliber"])
    _affirm(page, args)
    bad = []
    try:
        _run_steps(page, steps, ctx_vals, args.dry, bad, skip_tags={"ssn"} if args.skip_ssn else ())
    except Exception as e:
        emit("error", f"\n*** STEP FAILED: {_current_step}: {str(e).splitlines()[0]} ***")
        if page is not None:
            _save_failure(page)
        ask("Browser left open on the failing page. Look at it, then press Enter to log out and close... ")
        sys.exit(1)
    found = ctx_vals.get("_found") or {}
    if found.get("mfr_code") and not item.get("mfr_code") and is_nfa_row(item):  # found by name: a re-file uses the code
        item.update(mfr_code=found["mfr_code"])
        if not server_settings():  # a server gets it with mark_pending (saving it now would change the row's version)
            save_rows(rows)
    if bad:
        emit("error", f"\n*** {len(bad)} PROBLEM(S) (above): DO NOT tick the verification box. ***")
    if args.submit:
        try:
            _submit(page, item, bad, rows, idx, ctx_vals["overall_length"], force=getattr(args, "force", False))
        except Exception as e:
            emit("error", f"\n*** SUBMIT STEP FAILED: {str(e).splitlines()[0]} ***")
            if rows[idx]["status"] == "pending":
                emit("error", f"Sign & Submit may have gone through: row {idx + 1} is marked PENDING. Check the dashboard.")
            else:
                emit("info", "Sign & Submit was not clicked; nothing was filed.")
            ask("Browser left open. Press Enter to log out and close... ")
            sys.exit(1)
    else:
        emit("info", "\nFilled. REVIEW everything in the browser. Submit yourself. Not submitting.")
    return rows[idx]["status"] == "filed"


def _start_form(page, portal, args, serial, first):
    """Get a fresh Form 1 on the applicant-type page via portal.new_form_steps (home page -> Form 1 link).
    Falls back to asking you to do it by hand. Returns False if you chose to stop."""
    nf = portal.get("new_form_steps")
    if not nf:
        if first:  # the login prompt already asked you to open it
            return True
    else:
        ready = next((s["selector"] for s in nf if s["action"] == "wait_for"), None)
        if first and ready and page.locator(ready).count():
            emit("info", "A Form 1 is already open on the applicant-type page; using it.")
            return True
        emit("info", f"\nStarting a new Form 1 for {serial} (Ctrl-C stops and logs out)...")
        try:
            _run_steps(page, nf, {}, False, [])
            if not args.auto_affirm:
                ask("Click the \"I affirm\" popup in the browser, then press Enter... ")
            return True
        except Exception as e:  # fall back to doing it by hand rather than ending the batch
            emit("warn", f"    !! could not start it automatically ({str(e).splitlines()[0]})")
    affirm = "" if args.auto_affirm else ' (click "I affirm")'
    ans = ask(f"\nNext: {serial}. In the SAME window go to the home page, start a NEW Form 1 and stop on the applicant-type page"
              f"{affirm}, then press Enter (or type q to stop here)... ")
    return ans.strip().lower() != "q"


def file_rows(rows, todo, args):
    """Fill (and with args.submit, submit) the rows at indices `todo` in one browser session.
    args needs: dry, submit, auto_affirm, skip_ssn, capture. With dry, only prints the steps (no browser)."""
    trust, mapping = load_trust(), load_mapping()
    portal = mapping.get("portal") or {}
    steps = portal.get("steps") or []
    if not steps:
        sys.exit("data/mapping.yaml has no portal.steps yet; use `inspect` to find selectors.")
    skip = {"ssn"} if args.skip_ssn else ()
    probs = settings_problems(trust)
    if probs:
        sys.exit("Settings incomplete (./atf > Settings):\n  " + "\n  ".join(probs))
    # Check every row's inputs BEFORE opening the browser (an exit after login would skip the logout).
    today, probs = datetime.now().strftime("%m/%d/%Y"), []
    nfa_probs = [p for i in todo for p in nfa_row_problems(rows[i])]
    if nfa_probs:
        sys.exit("Not ready to file:\n  " + "\n  ".join(nfa_probs))
    for i in todo:
        _row_context(trust, rows[i])
        if applicant_type(trust) == "trust":
            probs += _check_5320(_find_5320(rows[i]["serial_number"], warn=True), rows[i], today)
            for person in other_rps(trust):
                probs += _check_5320(_find_5320(rows[i]["serial_number"], person=person), rows[i], today)
    if args.dry:
        for i in todo:
            emit("row", f"\n=== Row {i + 1}: serial {rows[i]['serial_number']} (dry run) ===",
                 serial=rows[i]["serial_number"], caliber=rows[i]["caliber"])
            _run_steps(None, steps, _row_context(trust, rows[i]), True, [], skip_tags=skip)
        return
    if probs:
        emit("warn", "5320.23 problems:\n  " + "\n  ".join(probs))
        if ask("Continue anyway? [y/N] ").strip().lower() != "y":
            sys.exit(1)
    if args.submit and server_settings():
        _server_precheck(rows, todo, getattr(args, "force", False))
    pw, ctx, page = _browser()
    try:
        if portal.get("new_form_steps"):
            page = _wait_for_login(page, "Log in (MFA included) and wait for the eForms home page, then press Enter (the script opens the Form 1)... ")
        else:
            page = _wait_for_login(page, "Log in (MFA included), open a NEW Form 1 and stop on the applicant-type page (Trust/Individual choice). Without --auto-affirm, click the \"I affirm\" popup yourself. Then press Enter... ")
        filed_now = {}
        for n, idx in enumerate(todo):
            if not _start_form(page, portal, args, rows[idx]["serial_number"], first=not n):
                break
            if _file_one(page, args, trust, steps, rows, idx):
                filed_now[rows[idx]["serial_number"]] = rows[idx].get("filed_date") or today
            elif args.submit:
                emit("warn", f"Row {idx + 1} was not filed; stopping the batch.")
                break
            if args.submit and outbox():
                emit("warn", "A filing result hasn't reached your server yet; stopping the batch before the next row.")
                break
        if args.submit and getattr(args, "cleo", False) and filed_now:
            # Same session (eForms allows one), read-only. Never changes a filing result: a failure is a warning.
            emit("section", "--- CLEO copies of the Form 1s ---", name="CLEO copies of the Form 1s")
            try:
                got, probs = _fetch_form1_cleo(page, ctx, filed_now)
                emit("info", f"Form 1 CLEO copies saved: {len(got)} of {len(filed_now)}")
                for p in probs:
                    emit("warn", f"CLEO copy: {p} (Advanced > Make CLEO packets tries again while the forms are in process)")
            except Exception as e:
                emit("warn", f"Couldn't get the Form 1 CLEO copies ({str(e).splitlines()[0]}); "
                             "Advanced > Make CLEO packets tries again while the forms are in process.")
        if args.capture:
            OUT.mkdir(exist_ok=True)
            dest = OUT / f"capture_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
            print(f"CAPTURE MODE: do the remaining steps by hand; press Enter at each new screen to record it ('q' to finish). Saving to {dest}")
            _capture_loop(page, dest)
        ask("Press Enter to log out and close the browser... ")
    finally:  # runs on errors, sys.exit and Ctrl-C too: eForms allows one session, so always log out
        _shutdown(page, ctx, pw)


def _server_precheck(rows, todo, force=False):
    """Before the browser opens, with a server: it must answer, nothing may be waiting in the retry file, and every
    row to file must still be queued THERE (another device may have filed or changed it). Exits otherwise."""
    import remote
    try:
        waiting = outbox_replay()
        if waiting:
            sys.exit(f"A filing result hasn't reached your server yet ({', '.join(e['serial'] for e in waiting)}): "
                     "filing doesn't start until it has. Check the server, then try again.")
        fresh = {r["serial_number"].upper(): r for r in _server().rows()[0]}
    except remote.ServerError as e:
        sys.exit(f"Your inventory server can't be reached, so filing doesn't start: {e}")
    for i in todo:
        there = fresh.get(rows[i]["serial_number"].upper())
        if there is None or (there["status"] != "" and not force) or there.get("version") != rows[i].get("version"):
            sys.exit(f"{rows[i]['serial_number']} changed on your server since this list was read (another device?). "
                     "Nothing was filed; open File again.")


def cmd_fill_form1(args):
    """CLI: fill (and optionally submit) one row, or loop through unfiled rows in one browser session."""
    rows = load_rows()
    if args.all or args.count:
        if args.row or args.capture:
            sys.exit("--all/--count cannot be combined with a row number or --capture.")
        _warn_pending(rows)
        todo = [i for i, r in enumerate(rows) if r["status"] not in SKIP]
        todo = todo[:args.count] if args.count else todo
        if not todo:
            sys.exit("No unfiled rows left in data/suppressors.csv.")
        print("Queue:", ", ".join(rows[i]["serial_number"] for i in todo))
    else:
        todo = [pick_row(rows, args.row, args.force)]
    file_rows(rows, todo, args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"eForms Form 1 helper {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fields"); p.add_argument("pdf"); p.set_defaults(fn=cmd_fields)
    p = sub.add_parser("fill-5320"); p.add_argument("row", type=int, nargs="?"); p.add_argument("--date"); p.set_defaults(fn=cmd_fill_5320)
    p = sub.add_parser("inspect"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("serve", help="your inventory on your phone (same Wi-Fi) until Ctrl-C"); p.set_defaults(fn=lambda a: __import__("phone").cmd_serve(a))
    p = sub.add_parser("check-setup", help="check settings, files, templates and the browser (no eForms)"); p.set_defaults(fn=cmd_check_setup)
    p = sub.add_parser("update", help="update this folder to the newest release (asks first)"); p.add_argument("--check", action="store_true", help="only say whether a newer version is out (exit 2 if so)"); p.add_argument("--yes", action="store_true", help="don't ask before updating"); p.set_defaults(fn=lambda a: __import__("update").cmd_update(a))
    p = sub.add_parser("backup", help="make the backups now (records ZIP if changed; your backup folder from Settings)"); p.set_defaults(fn=cmd_backup)
    p = sub.add_parser("export", help="write the inventory to out/inventory_<date>.xlsx"); p.set_defaults(fn=lambda a: print("wrote", export_inventory()))
    p = sub.add_parser("check-approved", help="read-only: compare approved Form 1s on eForms with your records"); p.add_argument("--import-unknown", action="store_true", help="also add approved suppressors missing from your inventory, and record suppressors in process on eForms (filed by hand) as filed"); p.set_defaults(fn=cmd_check_approved)
    p = sub.add_parser("fill-form1"); p.add_argument("row", type=int, nargs="?"); p.add_argument("--force", action="store_true", help="allow re-filing a row already marked filed"); p.add_argument("--all", action="store_true", help="loop through every unfiled row in one browser session"); p.add_argument("--count", type=int, help="loop through at most N unfiled rows"); p.add_argument("--dry", action="store_true", help="print the steps with their values; no browser"); p.add_argument("--capture", action="store_true"); p.add_argument("--submit", action="store_true", help="opt-in: tick certification and Sign & Submit, then wait for you to enter the emailed code and PIN"); p.add_argument("--auto-affirm", action="store_true", help="click the 'I affirm' popup for you (your affirmation)"); p.add_argument("--skip-ssn", action="store_true", help="don't pause for you to type your SSN (optional field; may delay processing)"); p.add_argument("--cleo", action="store_true", help="after the batch, save each filed Form 1's CLEO copy to out/cleo/ (read-only)"); p.set_defaults(fn=cmd_fill_form1)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
