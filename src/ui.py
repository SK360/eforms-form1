#!/usr/bin/env python3
"""Menu interface for the eForms Form 1 helper. Run ./atf (no arguments).
./atf <command> ... runs the plain CLI in atf.py instead (fill-form1, fill-5320, inspect, fields)."""
import re
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import questionary
import yaml
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table

import atf

con = Console(highlight=False)
STATUS = {"filed": "[green]✔ filed[/]", "pending": "[bold yellow]⚠ pending[/]", "": "[cyan]○ queued[/]"}
SHOW_FILED = 5  # most recent filed rows shown on the home screen


def short(num):
    """Last 4 digits of an ATF application number (the screen gets shared; full numbers stay in the CSVs)."""
    num = str(num or "")
    return f"…{num[-4:]}" if num.isdigit() and len(num) > 4 else num


# ---------- Live output for a filing run (replaces atf.emit / atf.ask) ----------
class Progress:
    """Turns the engine's events into a ticked list of pages, a read-back table and a code/PIN banner."""

    def __init__(self):
        self.verbose, self.section, self.status, self.reads = False, None, None, []

    def _stop(self):
        if self.status:
            self.status.stop()
            self.status = None

    def _start(self, text):
        self._stop()
        self.status = con.status(text, spinner="dots")
        self.status.start()

    def _close(self, mark="[green]✔[/]"):
        """End the current page: tick it off and show any read-back results collected on it."""
        self._stop()
        if self.section and mark:
            con.print(f"  {mark} {escape(self.section)}")
        self.section = None
        if self.reads:
            t = Table(box=box.SIMPLE_HEAD, padding=(0, 1))
            for col in ("Field", "Expected", "Portal shows", ""):
                t.add_column(col)
            for label, want, got, ok in self.reads:
                t.add_row(escape(label), escape(want), escape(got), "[green]✔[/]" if ok else "[bold red]✖ MISMATCH[/]")
            con.print(Padding(t, (0, 0, 0, 4)))
            self.reads = []

    def emit(self, kind, text, **d):
        msg = escape(text.strip("\n").strip("* ").strip())
        if kind == "section":
            self._close()
            self.section = d["name"]
            self._start(f"[cyan]{escape(d['name'])}[/]…")
        elif kind == "step":
            if self.verbose:
                con.print(f"      [dim]{msg}[/]")
        elif kind == "expect":
            if d.get("private"):  # e.g. the trust name: checked, but not printed
                self.reads.append((d["label"], "(hidden)", "matches" if d["ok"] else "differs (see browser)", d["ok"]))
            else:
                self.reads.append((d["label"], d["want"], d["got"], d["ok"]))
        elif kind == "row":
            self._close()
            con.print()
            con.rule(f"[bold]{escape(d['serial'])}[/]  ·  caliber {escape(d['caliber'])}")
        elif kind == "banner":
            self._close()
            con.bell()
            con.print(Panel("[bold]1.[/] Enter the 6-digit code from your email, click [bold]Verify[/]\n"
                            "[bold]2.[/] Enter your eForms PIN, click [bold]Submit[/]",
                            title="✋ Your turn in the browser", border_style="yellow", expand=False, padding=(1, 3)))
            self._start("[yellow]Waiting for the confirmation (up to 15 min)…[/]")
        elif kind == "done":
            self._stop()
            con.print(f"  [bold green]✔ Filed: application #{escape(short(d['number']))}[/]")
        elif kind == "error":
            self._close(mark="[red]✖[/]")
            con.print(f"  [bold red]✖ {msg}[/]")
        elif kind == "warn":
            con.print(f"  [yellow]⚠ {msg}[/]")
        else:
            con.print(f"    [dim]{msg}[/]")

    def ask(self, prompt):
        section = self.section
        self._stop()
        try:
            return con.input(f"  [bold yellow]›[/] {escape(prompt.strip())} ")
        finally:
            if section and self.section == section:
                self._start(f"[cyan]{escape(section)}[/]…")

    def finish(self):
        self._close()


P = Progress()


# ---------- Screens ----------
def queue_table(rows, only=None, title=None):
    t = Table(box=box.ROUNDED, header_style="bold", title=title, title_justify="left", border_style="blue")
    for col in ("Serial", "Caliber", "Status", "Application #"):
        t.add_column(col)
    idx = range(len(rows)) if only is None else only
    if only is None:
        filed = [i for i in idx if rows[i]["status"] == "filed"]
        hidden = set(filed[:-SHOW_FILED])
        if hidden:
            t.add_row(f"[dim]… {len(hidden)} earlier[/]", "", "[dim]✔ filed[/]", "")
        idx = [i for i in idx if i not in hidden]
    for i in idx:
        r = rows[i]
        t.add_row(escape(r["serial_number"]), escape(r["caliber"]), STATUS.get(r["status"], escape(r["status"])),
                  escape(short(r["application_number"])))
    return t


def home(rows):
    con.clear()
    con.print(queue_table(rows, title="[bold]eForms Form 1[/]"))  # no trust name: the screen gets shared


def pause():
    con.input("\n[dim]Press Enter to go back to the menu…[/] ")


def _row_choice(rows, i, extra=""):
    r = rows[i]
    return questionary.Choice(f"{r['serial_number']}  (caliber {r['caliber']}){extra}", value=i)


def next_serial(serial):
    """S009 -> S010, keeping the prefix and zero padding."""
    m = re.match(r"^(.*?)(\d+)$", serial or "")
    return f"{m.group(1)}{int(m.group(2)) + 1:0{len(m.group(2))}d}" if m else ""


def add_flow():
    rows = atf.load_rows()
    while True:
        last = rows[-1] if rows else {"serial_number": "", "caliber": ""}
        taken = {r["serial_number"].upper() for r in rows}

        def valid(s):
            if not s.strip():
                return "Enter a serial number"
            return s.strip().upper() not in taken or "That serial is already in the list"
        serial = questionary.text("Serial number:", default=next_serial(last["serial_number"]), validate=valid).ask()
        if serial is None:
            return
        cal = questionary.text("Caliber:", default=last["caliber"], validate=lambda s: bool(s.strip()) or "Enter a caliber").ask()
        if cal is None:
            return
        rows.append({"caliber": cal.strip(), "serial_number": serial.strip(), "status": "", "application_number": ""})
        atf.save_rows(rows)
        con.print(f"  [green]✔[/] Added {escape(serial.strip())} (caliber {escape(cal.strip())})")
        if not questionary.confirm("Add another?", default=True).ask():
            return


def resolve_flow():
    rows = atf.load_rows()
    pend = [i for i, r in enumerate(rows) if r["status"] == "pending"]
    con.print(Panel("Pending means Sign & Submit was clicked but the confirmation was never seen.\n"
                    "Check the eForms dashboard for this serial before choosing.", border_style="yellow", expand=False))
    i = pend[0] if len(pend) == 1 else questionary.select("Which row?", choices=[_row_choice(rows, i) for i in pend]).ask()
    if i is None:
        return
    what = questionary.select(f"{rows[i]['serial_number']} on the dashboard:", choices=[
        questionary.Choice("It was filed: enter the application number", "filed"),
        questionary.Choice("It was NOT filed: put it back in the queue", "queued"),
        questionary.Choice("Cancel", "cancel")]).ask()
    if what == "filed":
        num = questionary.text("Application number:", validate=lambda s: s.strip().isdigit() or "Digits only").ask()
        if num is None:
            return
        rows[i]["status"], rows[i]["application_number"] = "filed", num.strip()
        atf.log_submission(rows[i], num.strip())
        atf.save_rows(rows)
        con.print(f"  [green]✔[/] {escape(rows[i]['serial_number'])} marked filed (#{escape(short(num.strip()))})")
    elif what == "queued":
        rows[i]["status"] = ""
        atf.save_rows(rows)
        con.print(f"  [green]✔[/] {escape(rows[i]['serial_number'])} is back in the queue")


def file_flow(indices=None):
    probs = atf.settings_problems()
    if probs:
        con.print(Panel("\n".join(f"• {escape(x)}" for x in probs), title="Fix these in Settings first", border_style="red", expand=False))
        return pause()
    rows = atf.load_rows()
    if indices is None:
        queued = [i for i, r in enumerate(rows) if r["status"] not in atf.SKIP]
        choices = [_row_choice(rows, i) for i in queued]
        for c in choices:
            c.checked = True
        indices = questionary.checkbox("File these (space to untick, Enter to continue):", choices=choices,
                                       validate=lambda a: bool(a) or "Pick at least one").ask()
        if not indices:
            return
    ssn = questionary.confirm("Type your SSN on each form? (optional; leaving it blank may delay processing)",
                              default=False).ask()
    if ssn is None:
        return
    today = date.today().strftime("%m/%d/%Y")
    for i in indices:
        dest, missing = atf.stamp_5320(rows[i], today)
        probs = missing + atf._check_5320(dest, rows[i], today)
        if probs:
            con.print(f"  [bold red]✖ 5320.23 for {escape(rows[i]['serial_number'])}: {escape(', '.join(probs))}[/]")
            return pause()
    con.print(f"  [green]✔[/] 5320.23s generated, dated {today}")
    lines = "\n".join(f"  • {escape(rows[i]['serial_number'])}  caliber {escape(rows[i]['caliber'])}" for i in indices)
    con.print(Panel(
        f"[bold]File {len(indices)} Form 1{'s' if len(indices) > 1 else ''}:[/]\n{lines}\n\n"
        f"SSN: {'you type it on each form' if ssn else 'left blank'}\n\n"
        "By confirming, you affirm that each is a legitimate Form 1 application (the portal's \"I affirm\"),\n"
        "and you authorize the script to tick the certification and click Sign & Submit for each one,\n"
        "only after every read-back check and the portal's validation pass. You still enter the code and PIN.",
        title="Confirm", border_style="cyan", expand=False))
    if not questionary.confirm("Confirm and open the browser?", default=False).ask():
        return
    opts = SimpleNamespace(dry=False, submit=True, auto_affirm=True, skip_ssn=not ssn, capture=False)
    try:
        atf.file_rows(rows, indices, opts)
    finally:
        P.finish()
        after = atf.load_rows()
        con.print()
        con.print(queue_table(after, only=indices, title="[bold]Result[/]"))
    pause()


def preview_flow():
    rows = atf.load_rows()
    i = questionary.select("Preview the steps for which row?", choices=[_row_choice(rows, i) for i in range(len(rows))][::-1]).ask()
    if i is None:
        return
    if not any(atf.OUT.glob(f"5320.23*_{rows[i]['serial_number']}.pdf")):
        atf.stamp_5320(rows[i], date.today().strftime("%m/%d/%Y"))
    P.verbose = True
    try:
        atf.file_rows(rows, [i], SimpleNamespace(dry=True, submit=False, auto_affirm=True, skip_ssn=False, capture=False))
    finally:
        P.finish()
        P.verbose = False
    pause()


def redate_flow():
    rows = atf.load_rows()
    i = questionary.select("Regenerate the 5320.23 for which row?", choices=[_row_choice(rows, i) for i in range(len(rows))][::-1]).ask()
    if i is None:
        return

    def valid(s):
        try:
            datetime.strptime(s.strip(), "%m/%d/%Y")
            return True
        except ValueError:
            return "Use MM/DD/YYYY"
    when = questionary.text("Signature date (MM/DD/YYYY):", default=date.today().strftime("%m/%d/%Y"), validate=valid).ask()
    if when is None:
        return
    dest, missing = atf.stamp_5320(rows[i], when.strip())
    con.print(f"  [red]✖ not found after stamping: {', '.join(missing)}[/]" if missing else f"  [green]✔[/] wrote {escape(str(dest))}")
    pause()


def refile_flow():
    rows = atf.load_rows()
    filed = [i for i, r in enumerate(rows) if r["status"] == "filed"][::-1]
    if not filed:
        con.print("  Nothing is marked filed.")
        return pause()
    i = questionary.select("Re-file which row?", choices=[_row_choice(rows, i, f"  #{short(rows[i]['application_number'])}") for i in filed]).ask()
    if i is None:
        return
    con.print(Panel(f"{escape(rows[i]['serial_number'])} is already filed (#{escape(short(rows[i]['application_number']))}).\n"
                    "Filing again creates a SECOND application for the same serial.", border_style="red", expand=False))
    if questionary.confirm("Re-file it anyway?", default=False).ask():
        file_flow([i])


# ---------- Settings (first-run wizard, and Settings in the menu) ----------
GENERATOR_URL = "https://jakepro716.github.io/atf-5320-generator/"
DISCLAIMER = ("This is an unofficial helper, provided as-is with no warranty, and not affiliated with or endorsed by ATF.\n"
              "You are solely responsible for every application it files for you: each submission is YOUR certification\n"
              "under penalty of perjury. Review what it fills, check ATF's eForms terms of use, and stop if anything looks wrong.\n"
              "It never asks for, stores or types your password, SSN, emailed code or PIN.")
STATES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "District of Columbia",
          "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
          "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
          "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
          "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
          "Washington", "West Virginia", "Wisconsin", "Wyoming"]
RACES = ["American Indian or Alaska Native", "Asian", "Black or African American", "Native Hawaiian or Other Pacific Islander", "White"]


class Cancelled(Exception):
    pass


def _a(q):
    """Ask a questionary prompt; Ctrl-C/Esc cancels the whole wizard (nothing is saved)."""
    v = q.ask()
    if v is None:
        raise Cancelled
    return v


def _need(s):
    return bool(str(s).strip()) or "Required"


def _date(s):
    try:
        datetime.strptime(s.strip(), "%m/%d/%Y")
        return True
    except ValueError:
        return "Use MM/DD/YYYY"


def _clean_path(s):
    """Paths dragged into Terminal arrive quoted or with escaped spaces."""
    s = s.strip().strip("'\"").replace("\\ ", " ")
    return str(Path(s).expanduser()) if s else ""


def _file(must_exist=True, suffixes=()):
    def check(s):
        p = Path(_clean_path(s))
        if not s.strip():
            return not must_exist or "Required (drag the file into this window)"
        if not p.is_file():
            return "File not found"
        return not suffixes or p.suffix.lower() in suffixes or f"Expected a {' or '.join(suffixes)} file"
    return check


def _state(msg, cur):
    upper = {x.upper(): x for x in STATES}
    v = _a(questionary.autocomplete(msg, choices=STATES, default=upper.get(str(cur or "").upper(), str(cur or "")),
                                    validate=lambda s: s.strip().upper() in upper or "Pick a state from the list"))
    return cur if str(cur or "").upper() == v.strip().upper() else upper[v.strip().upper()]  # unchanged: keep as saved


def _check_template(cleo):
    """Test-stamp forms/5320.23.pdf with a dummy serial; returns a list of problems (empty = OK)."""
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as tmp:
            today = date.today().strftime("%m/%d/%Y")
            dest, missing = atf.stamp_5320({"serial_number": "TEST123", "caliber": "22"}, today, out_dir=tmp)
            return [f"could not place: {', '.join(missing)}"] if missing else atf._check_5320(dest, {"serial_number": "TEST123"}, today)
    except SystemExit as e:
        return [str(e.code)]


def settings_flow(first=False):
    path = atf.DATA / "trust.yaml"
    t = yaml.safe_load(path.read_text()) if path.exists() else {}
    if first:
        con.print(Panel("[bold]Welcome.[/] This sets up your trust's details once; you can change them later in Settings.\n\n"
                        + escape(DISCLAIMER), title="eForms Form 1 helper", border_style="cyan", expand=False))
        if not questionary.confirm("I understand and accept", default=False).ask():
            sys.exit("Setup not completed.")
    get = lambda p, d="": atf._get(t, p) or d  # noqa: E731
    try:
        con.rule("Trust")
        trust = dict(t.get("trust") or {})
        trust["name"] = _a(questionary.text("Trust name (exactly as on the trust document):", default=get("trust.name"), validate=_need))
        con.rule("Responsible person (you)")
        rp = dict((t.get("responsible_persons") or [{}])[0])
        rp["title"] = _a(questionary.text("Your title in the trust:", default=get("responsible_persons.0.title", "Trustee"), validate=_need))
        rp["dob"] = _a(questionary.text("Date of birth (MM/DD/YYYY):", default=get("responsible_persons.0.dob"), validate=_date))
        rp["sex"] = _a(questionary.select("Sex (as the eForms dropdown lists it):", choices=["Male", "Female"],
                                          default=get("responsible_persons.0.sex", "Male").title()))
        race = get("responsible_persons.0.race")
        rp["race"] = _a(questionary.select("Race (as eForms lists it):", choices=RACES, default=race if race in RACES else None))
        rp["place_of_birth"] = {**(rp.get("place_of_birth") or {}), "country": "United States of America",
                                "state": _state("State of birth:", get("responsible_persons.0.place_of_birth.state"))}
        rp["home_address"] = {**(rp.get("home_address") or {}),
                              "state": _state("State you live in:", get("responsible_persons.0.home_address.state"))}
        con.rule("CLEO (chief law enforcement officer you notify)")
        cl = dict(t.get("cleo") or {})
        cl["department"] = _a(questionary.text("Agency / department:", default=get("cleo.department"), validate=_need))
        cl["official"] = _a(questionary.text("Official's name:", default=get("cleo.official"), validate=_need))
        cl["title"] = _a(questionary.text("Official's title (exactly as on your 5320.23):", default=get("cleo.title", "CHIEF OF POLICE"), validate=_need))
        addr = dict(cl.get("address") or {})
        addr["street"] = _a(questionary.text("Agency street address:", default=get("cleo.address.street"), validate=_need))
        z = _a(questionary.text("Agency ZIP:", default=str(get("cleo.address.zip")), validate=_need))
        addr["zip"] = addr.get("zip") if str(addr.get("zip")) == z else z
        addr["city"] = _a(questionary.text("Agency city:", default=get("cleo.address.city"), validate=_need))
        addr["county"] = _a(questionary.text("Agency county (without 'County'):", default=get("cleo.address.county"), validate=_need))
        addr["state"] = _state("Agency state:", get("cleo.address.state"))
        cl["address"] = addr
        con.rule("Firearm (same for every suppressor)")
        fa = dict(t.get("firearm") or {})
        fa["manufacturer_code"] = _a(questionary.text("Your eForms manufacturer code (Add Firearm > By Code):",
                                                      default=get("firearm.manufacturer_code"), validate=_need))
        fa["model"] = _a(questionary.text("Model:", default=str(get("firearm.model", "N/A")), validate=_need))
        fa["overall_length"] = _a(questionary.text("Overall length (inches):", default=str(get("firearm.overall_length", "12")),
                                                   validate=lambda s: s.strip().replace(".", "", 1).isdigit() or "A number"))
        if not get("firearm.description"):
            con.print("  [dim]The description box on the firearm page, e.g. a statement about parts you do or don't possess.[/]")
        fa["description"] = _a(questionary.text("Firearm description:", default=get("firearm.description"), validate=_need))
        con.rule("Files")
        tpl = atf.FORMS / "5320.23.pdf"
        if not tpl.exists() or not _a(questionary.confirm("Keep your current 5320.23 template?", default=True)):
            con.print(f"  Make it once at [cyan]{GENERATOR_URL}[/]: Form 1, your details, the CLEO above, sign it,\n"
                      "  and use serial [bold]TEMPLATE[/] and caliber [bold]00[/] (they get replaced for each suppressor).\n"
                      "  Download the ATF copy. Step by step: README.md, \"Making your 5320.23 template\".")
            src = _clean_path(_a(questionary.path("5320.23 PDF (drag it here):", validate=_file(suffixes=(".pdf",)))))
            atf.FORMS.mkdir(exist_ok=True)
            tpl.write_bytes(Path(src).read_bytes())
        files = dict(t.get("files") or {})
        files["eft"] = _clean_path(_a(questionary.path("Fingerprint file (.eft):", default=get("files.eft"), validate=_file(suffixes=(".eft",)))))
        files["photo"] = _clean_path(_a(questionary.path("Photo of you (eForms requires it; not needed on the 5320.23):",
                                                         default=get("files.photo"), validate=_file(suffixes=(".jpg", ".jpeg", ".png")))))
        files["trust_document"] = _clean_path(_a(questionary.path("Trust document (PDF):", default=get("files.trust_document"),
                                                                  validate=_file(suffixes=(".pdf",)))))
        files["trust_document_desc"] = get("files.trust_document_desc", "TRUST")
        docs = list(t.get("docs") or [])
        if docs:
            con.print("  Supporting documents: " + escape(", ".join(f"{Path(d['path']).name} ({d['description']})" for d in docs)))
            if not _a(questionary.confirm("Keep these supporting documents?", default=True)):
                docs = []
        if not docs:
            con.print("  [dim]Supporting documents are uploaded with each firearm (e.g. photos of the tools/materials you'll use).[/]")
        while _a(questionary.confirm("Add a supporting document?", default=not docs)):
            dp = _clean_path(_a(questionary.path("  File (drag it here):", validate=_file())))
            docs.append({"path": dp, "description": _a(questionary.text("  Short description (e.g. 3D PRINTER):", validate=_need)).upper()})
    except Cancelled:
        con.print("  [yellow]Cancelled; nothing saved.[/]")
        return pause() if not first else sys.exit("Setup not completed.")
    t.update({"trust": trust, "responsible_persons": [rp] + list((t.get("responsible_persons") or [])[1:]),
              "cleo": cl, "firearm": fa, "files": files, "docs": docs})
    if first:
        t["accepted_disclaimer"] = date.today().isoformat()
    atf.DATA.mkdir(exist_ok=True)
    path.write_text("# Your settings (personal; data/ is never shared). Edit via ./atf > Settings.\n"
                    + yaml.safe_dump(t, sort_keys=False, allow_unicode=True, width=200))
    if not atf.SUPP.exists():
        atf.save_rows([])
    probs = atf.settings_problems(t) + _check_template(cl)
    if probs:
        con.print(Panel("\n".join(f"• {escape(x)}" for x in probs), title="Saved, but fix these before filing",
                        border_style="red", expand=False))
    else:
        con.print("  [green]✔ Settings saved, and your 5320.23 template stamps correctly.[/]")
    pause()


def advanced_flow():
    what = questionary.select("Advanced:", choices=[
        questionary.Choice("Preview the steps for a row (no browser)", preview_flow),
        questionary.Choice("Regenerate a 5320.23 with a different date", redate_flow),
        questionary.Choice("Re-file a row that's already filed", refile_flow),
        questionary.Choice("Capture portal pages (when ATF changes the site)", lambda: (atf.cmd_inspect(None), pause())),
        questionary.Choice("Back", "back")]).ask()
    if callable(what):
        what()


def menu():
    atf.emit, atf.ask = P.emit, P.ask
    if not (atf.DATA / "trust.yaml").exists():
        settings_flow(first=True)
    if not atf.SUPP.exists():
        atf.save_rows([])
    while True:
        rows = atf.load_rows()
        home(rows)
        probs = atf.settings_problems()
        if probs:
            con.print(f"  [yellow]⚠ Settings incomplete ({len(probs)}): {escape(probs[0])}{' …' if len(probs) > 1 else ''}[/]")
        queued = [r["serial_number"] for r in rows if r["status"] not in atf.SKIP]
        pending = [r["serial_number"] for r in rows if r["status"] == "pending"]
        choices = []
        if pending:
            choices.append(questionary.Choice(f"⚠ Resolve a pending row ({', '.join(pending)})", resolve_flow))
        if queued:
            names = ", ".join(queued[:4]) + (", …" if len(queued) > 4 else "")
            choices.append(questionary.Choice(f"File {len(queued)} queued suppressor{'s' if len(queued) > 1 else ''} ({names})", file_flow))
        choices += [questionary.Choice("Add suppressors", add_flow), questionary.Choice("Settings", settings_flow),
                    questionary.Choice("Advanced…", advanced_flow),
                    questionary.Choice("Quit", "quit")]
        what = questionary.select("What next?", choices=choices).ask()
        if what in (None, "quit"):
            return
        try:
            what()
        except SystemExit as e:  # the engine exits with a message (or 1 after a failure it already reported)
            P.finish()
            if isinstance(e.code, str):
                con.print(f"  [bold red]✖ {escape(e.code)}[/]")
            pause()
        except KeyboardInterrupt:
            P.finish()
            con.print("\n  [yellow]Stopped.[/]")
            pause()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.argv[0] = "atf"
        atf.main()
    else:
        try:
            menu()
        except KeyboardInterrupt:
            pass
