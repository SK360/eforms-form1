#!/usr/bin/env python3
"""ATF eForms Form 1 helper (suppressors, filed by a trust). See CLAUDE.md for the full picture.

Normally run through the menu: ./atf (src/ui.py). These commands are the plain CLI underneath it:
  fill-5320 [row] [--date MM/DD/YYYY]
        Stamp caliber (4d), serial (4e), signature date and CLEO line onto the user's completed
        forms/5320.23.pdf -> out/5320.23_<serial>.pdf. No row: all unfiled rows.
  fill-form1 [row] [--dry] [--auto-affirm] [--skip-ssn] [--submit] [--capture] [--force] [--all] [--count N]
        Open eForms, wait for the user to log in, open a new Form 1, then run the steps in
        data/mapping.yaml. No row: the first row in data/suppressors.csv not marked filed.
        --submit        also verify + certify + Sign & Submit (only if every read-back matched);
                        the user still types the emailed code and PIN, then the row is marked filed.
        --auto-affirm   click the "I affirm" popup (the user's affirmation).
        --skip-ssn      skip the pause for typing your SSN (it's optional; leaving it blank may delay processing).
        --dry           print the steps with their values; no browser.
        --capture       after the steps, record each screen the user clicks through to out/capture_*.jsonl.
  inspect
        Open eForms and dump the controls/dialog text of whatever page the user is on (to map new steps).
  fields <pdf>
        List AcroForm field names in a PDF (only useful for a fresh, fillable ATF 5320.23).
"""
import argparse
import csv
import json
import re
from datetime import datetime
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader

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
    return yaml.safe_load((DATA / "trust.yaml").read_text())


MAPPING = ROOT / "config" / "mapping.yaml"  # shared portal steps (tracked); personal values live in data/trust.yaml


def load_mapping():
    return yaml.safe_load(MAPPING.read_text())


# ---------- Queue ----------
SUPP = DATA / "suppressors.csv"
COLS = ["caliber", "serial_number", "status", "application_number"]


def load_rows():
    rows = list(csv.DictReader(SUPP.open()))
    for r in rows:
        r["status"] = (r.get("status") or "").strip().lower()
        r["application_number"] = r.get("application_number") or ""
    return rows


def save_rows(rows):
    tmp = SUPP.with_suffix(".csv.tmp")  # write-then-rename so a crash can't leave a half-written queue
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(SUPP)


def log_submission(item, num):
    """Append to out/submissions.csv (the audit log of filed applications)."""
    OUT.mkdir(exist_ok=True)
    log = OUT / "submissions.csv"
    new = not log.exists()
    with log.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "serial_number", "caliber", "application_number"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), item["serial_number"], item["caliber"], num])
    return log


# status "pending" = Sign & Submit was clicked but the confirmation was never read: it may be filed.
# Such rows are skipped until you check the dashboard and resolve them (menu: Resolve a pending row).
SKIP = ("filed", "pending")


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


# ---------- 5320.23 PDF ----------
def _find_5320(serial, warn=False):
    """Newest generated 5320.23 for a serial (5320.23_<serial>.pdf, or the older 5320.23_<row>_<serial>.pdf)."""
    hits = sorted(OUT.glob(f"5320.23*_{serial}.pdf"), key=lambda p: p.stat().st_mtime)
    if not hits:
        sys.exit(f"No generated 5320.23 for serial {serial}; run fill-5320 first.")
    if warn and len(hits) > 1:
        emit("info", f"({len(hits)} 5320.23 files for {serial}; using the newest: {hits[-1].name})")
    return hits[-1]


def _check_5320(path, item, today):
    """Problems with a generated 5320.23: serial not on page 1, or signature date not today."""
    import pymupdf
    doc = pymupdf.open(path)
    probs = []
    if not _token_match(item["serial_number"], doc[0].get_text()):
        probs.append(f"serial {item['serial_number']} not found in {path.name}")
    if today not in doc[1].get_text():
        probs.append(f"{path.name} is not dated today ({today}); regenerate it if filing today")
    return probs


def cmd_fields(args):
    for name, f in (PdfReader(args.pdf).get_fields() or {}).items():
        print(f"{name}\t{f.get('/FT')}\t{f.get('/_States_', '')}")


def stamp_5320(row, when, out_dir=None):
    """Stamp one row's caliber (4d), serial (4e), signature date and CLEO line onto your completed 5320.23.
    Old values are redacted (text removed), photo/signature untouched. Returns (path, keys not found after)."""
    import pymupdf
    out_dir = Path(out_dir or OUT)
    out_dir.mkdir(exist_ok=True)
    # (page, key) -- located by searching the template, so layout changes are caught
    targets = [(0, "4d"), (0, "4e"), (1, "date"), (0, "cleo")]
    cl = load_trust()["cleo"]
    new = {"4d": row["caliber"], "4e": row["serial_number"], "date": when, "cleo": f"{cl['official']}, {cl['title']}"}
    doc = pymupdf.open(FORMS / "5320.23.pdf")
    boxes = {}
    for pg, k in targets:
        boxes[k] = _find_value_box(doc[pg], k)
        doc[pg].add_redact_annot(pymupdf.Rect(boxes[k]) + (-1, -1, 30, 1), fill=(1, 1, 1))
    for pg in (0, 1):
        doc[pg].apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    for pg, k in targets:
        x0, _, _, y1 = boxes[k]
        doc[pg].insert_text((x0, y1 - 2.5), str(new[k]), fontname="helv", fontsize=8)
    dest = out_dir / f"5320.23_{row['serial_number']}.pdf"
    doc.save(dest)
    chk = pymupdf.open(dest)  # re-read: each new value must be in the text layer where it was stamped
    missing = [k for pg, k in targets if not chk[pg].search_for(str(new[k]), clip=pymupdf.Rect(boxes[k]) + (-2, -6, 250, 6))]
    return dest, missing


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
        dest, missing = stamp_5320(row, when)
        print("wrote", dest, f"  !! NOT FOUND after stamping: {', '.join(missing)}" if missing else "")
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
    word = {"4d": "Caliber", "4e": "Serial Number", "date": "Date", "cleo": "Title of Official"}[key]
    hits = page.search_for(word)
    if not hits:
        sys.exit(f"label for {key} not found")
    label = hits[-1] if key in ("date", "cleo") else hits[0]
    best = None
    for blk in page.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            for sp in ln["spans"]:
                r = sp["bbox"]
                if "Nimbus" not in sp["font"] or not sp["text"].strip() or abs(r[0] - label.x0) > (40 if key != "cleo" else 80):
                    continue
                dy = (label.y0 - r[3]) if key in ("date", "cleo") else (r[1] - label.y1)
                if 0 <= dy < 30 and (best is None or dy < best[0]):
                    best = (dy, r)
    if not best:
        sys.exit(f"could not locate existing value for {key}")
    return best[1]


US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
             "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
             "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
             "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
             "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
             "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
             "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
             "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
             "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}
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
            return {url: location.href, title: document.title, dialogs: [...new Set(dialogs)], pageText: document.body.innerText.replace(/\\n{2,}/g, " | ").slice(0, 2500), widgets, labels,
              controls: [...document.querySelectorAll('input,select,textarea,button,a[href],[role=button],[role=tab]')]
                .filter(vis).map(e => {
                  const o = {tag: e.tagName.toLowerCase(), id: e.id, name: e.name || undefined, type: e.type || undefined,
                             label: lab(e), text: ['BUTTON','A'].includes(e.tagName) || e.getAttribute('role') ? e.innerText.trim().slice(0,60) : undefined};
                  if (e.tagName === 'A') o.href = e.getAttribute('href');
                  if (e.disabled) o.disabled = true;
                  if (['radio','checkbox'].includes(e.type)) o.checked = e.checked;
                  if (['radio','checkbox'].includes(e.type)) o.value = e.value;
                  if (e.tagName === 'SELECT') o.options = [...e.options].map(x => x.text).slice(0,40);
                  return Object.fromEntries(Object.entries(o).filter(([k,v]) => v !== undefined && v !== ''));
                })};
        }""")


def _capture_loop(page, save_to=None):
    """Press Enter to dump the current page's controls (printed, and appended to save_to); 'q' quits."""
    while ask("> ").strip().lower() != "q":
        d = _dump_page(page)
        print(json.dumps(d, indent=1))
        if save_to:
            with open(save_to, "a") as f:
                f.write(json.dumps({"ts": datetime.now().isoformat(), **d}) + "\n")


def _logout(page):
    """Best-effort click on the portal's Log Out link so the single-session lock is released."""
    try:
        page.get_by_text("Log Out", exact=False).first.click(timeout=3000)
        page.wait_for_timeout(1500)
        emit("info", "Logged out of eForms.")
    except Exception:
        emit("warn", "Could not find a Log Out link; log out manually in the browser before closing it.")


def _tick(page, selector):
    """Check a checkbox/radio even when the real input is hidden behind a styled label."""
    loc = page.locator(selector)
    try:
        loc.check(timeout=4000)
    except Exception:
        el_id = loc.get_attribute("id")
        lab = page.locator(f"label[for='{el_id}']") if el_id else None
        if lab is not None and lab.count() and lab.first.is_visible():
            lab.first.click()
        else:
            loc.evaluate("e => e.click()")
    if not loc.is_checked():
        raise RuntimeError(f"could not tick {selector}")


def _submit(page, item, bad, rows, idx):
    """Opt-in: tick the certification and click Sign & Submit. You still enter the emailed code and your PIN."""
    if bad:
        emit("error", "*** NOT submitting: read-back mismatches above. ***"); return
    # Read-back above was clean: tick the verify box and move to the Certify page, where validation is reported.
    emit("section", "--- Certify ---", name="Certify")
    _tick(page, "#binary")
    page.locator("#nextBtn").click()
    try:
        page.locator("#signed").wait_for(timeout=20000)
        page.wait_for_timeout(1500)
    except Exception:
        emit("error", "*** NOT submitting: never reached the Certify page. ***"); return
    if not page.get_by_text("The application has validated successfully").count():
        emit("error", "*** NOT submitting: Certify page does not say 'validated successfully'. ***"); return
    _tick(page, "#signed")
    # From here on the application may get filed: mark the row pending first, so a crash, timeout or Ctrl-C
    # can't leave it looking unfiled (and get it filed twice by the next run).
    rows[idx]["status"] = "pending"
    save_rows(rows)
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
    rows[idx]["status"], rows[idx]["application_number"] = "filed", num
    save_rows(rows)
    emit("done", f"SUBMITTED. Application number {num} logged to {log}; row {idx + 1} marked filed.",
         serial=item["serial_number"], number=num)
    try:
        page.locator("#OkBtn").click(timeout=3000)
    except Exception:
        pass


def cmd_inspect(_):
    pw, ctx, page = _browser()
    out = True
    try:
        print("Log in, navigate to the page you want mapped, then press Enter here (or 'q' to quit).")
        _capture_loop(page)
        out = ask("Log out of eForms before closing? [Y/n] ").strip().lower() != "n"
    finally:
        _shutdown(page, ctx, pw, logout=out)


# Settings the shared mapping needs (data/trust.yaml). (path, description) -- used by the setup wizard and prechecks.
REQUIRED = [
    ("trust.name", "trust name"), ("responsible_persons.0.title", "your title in the trust (e.g. Settlor)"),
    ("responsible_persons.0.dob", "date of birth"), ("responsible_persons.0.sex", "sex"),
    ("responsible_persons.0.race", "race"), ("responsible_persons.0.place_of_birth.state", "state of birth"),
    ("responsible_persons.0.home_address.state", "state of residence"),
    ("cleo.department", "CLEO agency"), ("cleo.official", "CLEO official's name"), ("cleo.title", "CLEO official's title"),
    ("cleo.address.street", "CLEO street"), ("cleo.address.zip", "CLEO ZIP"), ("cleo.address.city", "CLEO city"),
    ("cleo.address.county", "CLEO county"), ("cleo.address.state", "CLEO state"),
    ("firearm.manufacturer_code", "eForms manufacturer code"), ("firearm.description", "firearm description"),
    ("files.trust_document", "trust document PDF"), ("files.eft", "fingerprint file (.eft)"),
    ("files.photo", "photo of you (eForms requires it for the responsible person)"),
]


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
    probs = [f"missing {desc} ({path})" for path, desc in REQUIRED if not str(_get(trust, path) or "").strip()]
    paths = [("files.trust_document", "trust document"), ("files.eft", "fingerprint file"), ("files.photo", "photo")]
    paths += [(f"docs.{i}.path", f"supporting document {i + 1}") for i in range(len(trust.get("docs") or []))]
    for path, desc in paths:
        v = _get(trust, path)
        if v and not Path(v).is_file():
            probs.append(f"{desc} not found: {v}")
    if not (FORMS / "5320.23.pdf").is_file():
        probs.append("no 5320.23 template at forms/5320.23.pdf")
    return probs


def _row_context(trust, item):
    """Placeholder values for config/mapping.yaml steps for one suppressor row."""
    rp = trust["responsible_persons"][0]
    f, cl, fa = trust.get("files", {}), trust["cleo"], trust.get("firearm", {})
    out_pdf = _find_5320(item["serial_number"])
    ctx = {**{f"trust_{k}": v for k, v in trust["trust"].items() if not isinstance(v, dict)},
           **{f"trust_{k}": v for k, v in trust["trust"].get("address", {}).items()}, **item,
           "rp_title": rp["title"], "rp_dob": rp["dob"], "rp_race": rp["race"],
           "rp_birth_state": rp["place_of_birth"]["state"].upper(),
           "rp_residence_state": rp["home_address"]["state"].upper(), "rp_sex": rp["sex"].upper(),
           "cleo_department": cl["department"], "cleo_official": cl["official"], "cleo_title": cl["title"],
           "cleo_street": cl["address"]["street"], "cleo_zip": str(cl["address"]["zip"]),
           # dropdowns: the portal lists these uppercased
           "cleo_city": cl["address"]["city"].upper(), "cleo_county": cl["address"]["county"].upper(),
           "cleo_state": cl["address"]["state"].upper(),
           "mfr_code": fa["manufacturer_code"], "model": str(fa.get("model") or "N/A"),
           "overall_length": str(fa.get("overall_length") or "12"), "description": fa["description"],
           "form_5320_path": str(out_pdf), "photo_path": f.get("photo"), "eft_path": f.get("eft"),
           "trust_doc_path": f.get("trust_document"), "trust_doc_desc": f.get("trust_document_desc") or "TRUST",
           "docs": [{"doc_path": d["path"], "doc_desc": d["description"]} for d in trust.get("docs") or []]}
    return ctx


_current_step = ""


def _run_steps(page, steps, ctx_vals, dry, bad, skip_tags=()):
    """Execute mapping steps. Raises on failure; read-back mismatches are appended to `bad`.
    Steps whose `tag` is in skip_tags are left out (e.g. tag: ssn with --skip-ssn). With dry, page may be None."""
    global _current_step
    for s in steps:  # {selector, action: fill|select|check|click|upload|..., value: "{key}" or literal}
        act = s["action"]
        val = str(s.get("value", "")).format(**ctx_vals) if s.get("value") else None
        sel = s["selector"].format(**ctx_vals) if s.get("selector") else None  # e.g. :has-text('{rp_race}')
        if act == "section":  # progress marker only
            emit("section", f"--- {val} ---", name=val)
            continue
        if act == "repeat":  # run the sub-steps once per item of a list setting (e.g. docs)
            for item in ctx_vals.get(s["over"]) or []:
                _run_steps(page, s["steps"], {**ctx_vals, **item}, dry, bad, skip_tags)
            continue
        _current_step = sel or act
        if s.get("tag") in skip_tags:
            emit("step", f"(skipping {s['tag']} step)")
            continue
        emit("step", f"{act} {sel or ''} {val or ''}")
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
        if act == "fill": loc.fill(val)
        elif act == "select": loc.select_option(val)
        elif act == "check": _tick(page, sel)
        elif act == "click": loc.click()
        elif act == "upload": loc.set_input_files(val)
        elif act == "pause": ask(f"  >> {s.get('note', 'Do this step manually in the browser')} then press Enter... ")
        elif act == "expect":  # compare a read-only field against the intended value; never changes anything
            got = (loc.input_value() or "").strip()
            ok = _token_match(val, got)
            emit("expect", f"    {'OK ' if ok else 'MISMATCH'} expected {val!r} got {got!r}",
                 label=s.get("label", sel), want=val, got=got, ok=ok, private=s.get("private", False))
            if not ok:
                bad.append((sel, val, got))
        elif act == "dismiss_dialog":  # print any open dialog's text (so warnings stay visible), then click its OK
            page.wait_for_timeout(int(val or 1500))
            dlgs = _dump_page(page).get("dialogs", [])
            if not dlgs:
                emit("info", "    (no dialog appeared)")
            for t in dlgs:
                emit("info", f"    DIALOG: {t[:400]}")
            need = s.get("require")  # e.g. "Valid EBTS Package": anything else blocks --submit
            if need and not any(_token_match(need, t) for t in dlgs):
                emit("error", f"    !! no dialog says {need!r}: this application will NOT be submitted")
                bad.append(("dialog", need, " | ".join(t[:120] for t in dlgs) or "(no dialog)"))
            btn = page.locator(".modal button:visible, [role=dialog] button:visible").filter(
                has_text=re.compile(r"^\s*(ok|okay)\s*$", re.I))
            if dlgs and btn.count():
                btn.first.click()
                emit("step", "    clicked OK")
            elif dlgs:
                emit("warn", "    !! dialog has no OK button I recognise; click it yourself")
                ask("    press Enter when closed... ")
        elif act == "press": page.keyboard.press(val)
        elif act == "wait": page.wait_for_timeout(int(val or 1000))
        elif act == "wait_for": loc.first.wait_for(state=s.get("state", "attached"), timeout=int(val or 30000))


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
        ask("Browser left open on the failing page. Look at it, then press Enter to log out and close... ")
        sys.exit(1)
    if bad:
        emit("error", f"\n*** {len(bad)} MISMATCH(ES) (above): DO NOT tick the verification box. ***")
    if args.submit:
        try:
            _submit(page, item, bad, rows, idx)
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
    for i in todo:
        _row_context(trust, rows[i])
        probs += _check_5320(_find_5320(rows[i]["serial_number"], warn=True), rows[i], today)
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
    pw, ctx, page = _browser()
    try:
        if portal.get("new_form_steps"):
            ask("Log in (MFA included) and wait for the eForms home page, then press Enter (the script opens the Form 1)... ")
        else:
            ask("Log in (MFA included), open a NEW Form 1 and stop on the applicant-type page (Trust/Individual choice). Without --auto-affirm, click the \"I affirm\" popup yourself. Then press Enter... ")
        for n, idx in enumerate(todo):
            if not _start_form(page, portal, args, rows[idx]["serial_number"], first=not n):
                break
            if not _file_one(page, args, trust, steps, rows, idx) and args.submit:
                emit("warn", f"Row {idx + 1} was not filed; stopping the batch.")
                break
        if args.capture:
            OUT.mkdir(exist_ok=True)
            dest = OUT / f"capture_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
            print(f"CAPTURE MODE: do the remaining steps by hand; press Enter at each new screen to record it ('q' to finish). Saving to {dest}")
            _capture_loop(page, dest)
        ask("Press Enter to log out and close the browser... ")
    finally:  # runs on errors, sys.exit and Ctrl-C too: eForms allows one session, so always log out
        _shutdown(page, ctx, pw)


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
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fields"); p.add_argument("pdf"); p.set_defaults(fn=cmd_fields)
    p = sub.add_parser("fill-5320"); p.add_argument("row", type=int, nargs="?"); p.add_argument("--date"); p.set_defaults(fn=cmd_fill_5320)
    p = sub.add_parser("inspect"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("fill-form1"); p.add_argument("row", type=int, nargs="?"); p.add_argument("--force", action="store_true", help="allow re-filing a row already marked filed"); p.add_argument("--all", action="store_true", help="loop through every unfiled row in one browser session"); p.add_argument("--count", type=int, help="loop through at most N unfiled rows"); p.add_argument("--dry", action="store_true", help="print the steps with their values; no browser"); p.add_argument("--capture", action="store_true"); p.add_argument("--submit", action="store_true", help="opt-in: tick certification and Sign & Submit, then wait for you to enter the emailed code and PIN"); p.add_argument("--auto-affirm", action="store_true", help="click the 'I affirm' popup for you (your affirmation)"); p.add_argument("--skip-ssn", action="store_true", help="don't pause for you to type your SSN (optional field; may delay processing)"); p.set_defaults(fn=cmd_fill_form1)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
