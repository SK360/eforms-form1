#!/usr/bin/env python3
"""Menu interface for the eForms Form 1 helper. Run ./atf (no arguments).
./atf <command> ... runs the plain CLI in atf.py instead (fill-form1, fill-5320, inspect, fields)."""
import os
import re
import subprocess
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")  # some terminals (e.g. IDE panes) can't answer cursor queries; skip the warning
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
STATUS = {"approved": "[green]✔ approved[/]", "filed": "[blue]● filed[/]", "pending": "[bold yellow]⚠ pending[/]",
          "": "[cyan]○ queued[/]", "disapproved": "[bold red]✖ disapproved[/]"}
IN_PROGRESS = ("filed", "pending")
SHOW_FILED = 5  # most recent filed rows shown on the home screen


def short(num):
    """Last 4 digits of an ATF application number (the screen gets shared; full numbers stay in the CSVs)."""
    num = str(num or "")
    return f"…{num[-4:]}" if num.isdigit() and len(num) > 4 else num


def _drop_typeahead():
    """Throw away keys typed before a prompt appeared, so they can't answer it. E.g. an Enter pressed after 'y' on
    "Open the browser?" used to answer "Log in ... then press Enter" at once, before the user had logged in."""
    try:
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        elif sys.stdin.isatty():
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:  # not a console (tests, pipes): nothing to drop
        pass


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
            self._close()  # tick off Certify before the Filed line
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
        _drop_typeahead()
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
    for col in ("Serial", "Caliber", "Length", "Status", "Application #"):
        t.add_column(col)
    idx = range(len(rows)) if only is None else only
    if only is None:
        done = [i for i in idx if rows[i]["status"] not in ("", "pending")]
        hidden = set(done[:-SHOW_FILED])
        if hidden:
            t.add_row(f"[dim]… {len(hidden)} earlier[/]", "", "", "[dim](Inventory)[/]", "")
        idx = [i for i in idx if i not in hidden]
    for i in idx:
        r = rows[i]
        t.add_row(escape(r["serial_number"]), escape(r["caliber"]), escape(r["overall_length"]), STATUS.get(r["status"], escape(r["status"])),
                  escape(short(r["application_number"])))
    return t


def summary(rows):
    n = lambda *st: sum(1 for r in rows if r["status"] in st)  # noqa: E731
    built = sum(1 for r in rows if atf.is_built(r))
    parts = [f"[bold]{len(rows)}[/] suppressors", f"[green]{n('approved')} approved[/]"] + ([f"[green]{built} built[/]"] if built else []) + [
             f"[blue]{n(*IN_PROGRESS)} in progress[/]", f"[cyan]{n('')} queued[/]"] + ([f"[red]{n('disapproved')} disapproved[/]"] if n("disapproved") else [])
    sbrs = len(atf.load_sbrs())
    return " · ".join(parts + ([f"[magenta]{sbrs} SBR{'s' if sbrs != 1 else ''}[/]"] if sbrs else []))


def home(rows):
    con.clear()
    con.print(queue_table(rows, title="[bold]eForms Form 1[/]"))  # no trust name: the screen gets shared
    con.print(f"  {summary(rows)}")


def _days(date_str):
    try:
        return (date.today() - datetime.strptime(date_str, "%m/%d/%Y").date()).days
    except ValueError:
        return None


def inventory_table(rows, title):
    t = Table(box=box.ROUNDED, header_style="bold", title=title, title_justify="left", border_style="blue")
    for col in ("Serial", "Caliber", "Length", "Status", "Filed", "Approved / waiting", "Built", "App # / eForm"):
        t.add_column(col)
    for r in sorted(rows, key=lambda r: atf.serial_key(r["serial_number"])):
        if r["status"] == "approved":
            when = r["approved_date"] + (" [yellow]⚠ conditions[/]" if r["conditions"] else "")
        elif r["status"] in IN_PROGRESS and _days(r["filed_date"]) is not None:
            when = f"[dim]waiting {_days(r['filed_date'])} day(s)[/]"
        else:
            when = ""
        ident = short(r["application_number"]) or (f"eForm {short(r['eform_id'])}" if r["eform_id"] else "")
        t.add_row(escape(r["serial_number"]), escape(r["caliber"]), escape(r["overall_length"]), STATUS.get(r["status"], escape(r["status"])),
                  escape(r["filed_date"]), when, "[green]✔[/]" if atf.is_built(r) else "", escape(ident))
    return t


def build_table(rows, title):
    """Approved suppressors, built or not, and what each one is (for the Built / Approved, not built views)."""
    t = Table(box=box.ROUNDED, header_style="bold", title=title, title_justify="left", border_style="blue")
    for col in ("Serial", "Caliber", "Length", "Approved", "Built", "Design"):
        t.add_column(col)
    for r in sorted(rows, key=lambda r: atf.serial_key(r["serial_number"])):
        t.add_row(escape(r["serial_number"]), escape(r["caliber"]), escape(r["overall_length"]), escape(r["approved_date"]),
                  "[green]✔ built[/]" if atf.is_built(r) else "[dim]not built[/]", escape(r["design"]))
    return t


def sbr_table(rows, title):
    """Same look as the suppressor inventory; SBRs add Model and Barrel. They come only from approvals (always approved,
    no filed date), so there is no Filed or waiting column."""
    t = Table(box=box.ROUNDED, header_style="bold", title=title, title_justify="left", border_style="blue")
    for col in ("Serial", "Caliber", "Model", "Barrel", "Length", "Status", "Approved", "eForm"):
        t.add_column(col)
    for r in sorted(rows, key=lambda r: atf.serial_key(r["serial_number"])):
        t.add_row(escape(r["serial_number"]), escape(r["caliber"]), escape(r["model"]), escape(r["barrel_length"]),
                  escape(r["overall_length"]), STATUS["approved"],
                  escape(r["approved_date"]) + (" [yellow]⚠ conditions[/]" if r["conditions"] else ""),
                  f"eForm {escape(short(r['eform_id']))}" if r["eform_id"] else "")
    return t


def sbr_summary(sbrs):
    return f"[bold]{len(sbrs)}[/] SBR{'s' if len(sbrs) != 1 else ''} · [green]{len(sbrs)} approved[/]"


def inventory_flow():
    view = "all"
    filters = {"all": ("All", lambda r: True), "approved": ("Approved", lambda r: r["status"] == "approved"),
               "progress": ("In progress", lambda r: r["status"] in IN_PROGRESS), "queued": ("Queued", lambda r: r["status"] == ""),
               "built": ("Built", atf.is_built), "unbuilt": ("Approved, not built", lambda r: r["status"] == "approved" and not atf.is_built(r))}
    while True:
        rows = atf.load_rows()
        con.clear()
        if view == "sbrs":
            sbrs = atf.load_sbrs()
            con.print(sbr_table(sbrs, f"[bold]Inventory[/] · SBRs ({len(sbrs)})"))
            con.print(f"  {sbr_summary(sbrs)}")
            cond, where = [r["serial_number"] for r in sbrs if r["conditions"]], "data/sbrs.csv"
        else:
            name, keep = filters[view]
            shown = [r for r in rows if keep(r)]
            table = build_table if view in ("built", "unbuilt") else inventory_table
            con.print(table(shown, f"[bold]Inventory[/] · {name} ({len(shown)})"))
            con.print(f"  {summary(rows)}")
            cond = [r["serial_number"] for r in shown if r["status"] == "approved" and r["conditions"]]
            where = "data/suppressors.csv"
        if cond:
            con.print(f"  [yellow]⚠ approved with conditions: {escape(', '.join(cond))}[/] [dim](conditions are in {where})[/]")
        choices = [questionary.Choice(f"Show {label.lower()}", key) for key, (label, _) in filters.items() if key != view]
        if view != "sbrs" and atf.load_sbrs():
            choices.append(questionary.Choice("Show SBRs", "sbrs"))
        if any(r["status"] == "" for r in rows):
            choices.append(questionary.Choice("Edit or remove a queued suppressor", edit_queued_flow))
        if any(r["status"] == "approved" for r in rows):
            choices.append(questionary.Choice("Record builds", builds_flow))
            choices.append(questionary.Choice("Open an approved PDF", open_approved_flow))
        choices.append(questionary.Choice("Export to Excel", export_flow))
        what = questionary.select("Inventory:", choices=choices + [questionary.Choice("Back", "back")]).ask()
        if what in (None, "back"):
            return
        if callable(what):
            what()
        else:
            view = what


def open_file(path):
    """Open a file in the OS default app (PDF viewer, Excel)."""
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif os.name == "nt":
        os.startfile(str(path))  # noqa: S606 (Windows only)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def edit_queued_flow():
    """Change or remove a QUEUED row. Filed/approved rows are the record of what was submitted, so they stay locked."""
    rows = atf.load_rows()
    queued = sorted((i for i, r in enumerate(rows) if r["status"] == ""), key=lambda i: atf.serial_key(rows[i]["serial_number"]))
    if not queued:
        con.print("  Nothing is queued.")
        return pause()
    choices = [questionary.Choice(f"{rows[i]['serial_number']}  (caliber {rows[i]['caliber']}, {rows[i]['overall_length'] or '?'}\")", i)
               for i in queued]
    i = questionary.select("Which queued suppressor?", choices=choices + [questionary.Choice("Back", "back")]).ask()
    if i in (None, "back"):
        return
    r = rows[i]
    what = questionary.select(f"{r['serial_number']}:", choices=[
        questionary.Choice("Change the serial", "serial_number"), questionary.Choice("Change the caliber", "caliber"),
        questionary.Choice("Change the overall length", "overall_length"), questionary.Choice("Remove it from the queue", "remove"),
        questionary.Choice("Back", "back")]).ask()
    if what in (None, "back"):
        return
    if what == "remove":
        if questionary.confirm(f"Remove {r['serial_number']} from the queue?", default=False).ask():
            del rows[i]
            atf.save_rows(rows)
            con.print(f"  [green]✔[/] Removed {escape(r['serial_number'])}")
            pause()
        return
    others = {x["serial_number"].upper() for j, x in enumerate(rows) if j != i}
    check = {"serial_number": lambda v: (bool(v.strip()) or "Enter a serial number") if v.strip().upper() not in others
             else "That serial is already in the list",
             "caliber": lambda v: bool(v.strip()) or "Enter a caliber", "overall_length": _inches}[what]
    label = {"serial_number": "Serial number:", "caliber": "Caliber:", "overall_length": "Overall length (inches):"}[what]
    v = questionary.text(label, default=r[what], validate=check).ask()
    if v is None:
        return
    r[what] = v.strip()
    atf.save_rows(rows)
    con.print(f"  [green]✔[/] Updated {escape(r['serial_number'])}")
    pause()


def _ask_design(default="", serial=""):
    """What it is (e.g. "PCC Short"), typed each time. Returns the text, or None if cancelled."""
    v = questionary.text(f"Design of {serial} (what it is, e.g. PCC Short):" if serial else "Design (what it is, e.g. PCC Short):",
                         default=default).ask()
    return None if v is None else v.strip()


def builds_flow():
    """Record which approved suppressors are built and what each one is. Only approved rows can be marked built."""
    rows = atf.load_rows()
    todo = [r for r in rows if r["status"] == "approved" and not atf.is_built(r)]
    built = [r for r in rows if atf.is_built(r)]
    choices = ([questionary.Choice(f"Record builds ({len(todo)} approved, not built yet)", "new")] if todo else []) + (
        [questionary.Choice("Change or clear a build record", "change")] if built else []) + [questionary.Choice("Back", "back")]
    what = questionary.select("Builds:", choices=choices).ask()
    if what == "new":
        _record_builds(todo)
    elif what == "change":
        _change_build(built)


def _record_builds(todo):
    todo = sorted(todo, key=lambda r: atf.serial_key(r["serial_number"]))
    spec = questionary.text("Which serials? A range and/or list (e.g. A101-A110, A115), or empty to pick from a list:",
                            validate=lambda v: not v.strip() or not atf.serials_in(v, todo)[1]
                            or "; ".join(atf.serials_in(v, todo)[1]) + " (approved, not built yet)").ask()
    if spec is None:
        return
    if spec.strip():
        serials = atf.serials_in(spec, todo)[0]
    else:
        serials = questionary.checkbox("Built (space to tick, Enter to continue):", choices=[
            questionary.Choice(f"{r['serial_number']}  (caliber {r['caliber']}, {r['overall_length'] or '?'}\", approved {r['approved_date']})",
                               r["serial_number"]) for r in todo]).ask()
    if not serials:
        return
    designs = {}
    for s in serials:  # each one can be a different design; nothing is saved until the confirmation below
        design = _ask_design(serial=s)
        if design is None:
            return
        designs[s] = design
    t = Table(box=box.ROUNDED, header_style="bold", border_style="blue")
    for col in ("Serial", "Design"):
        t.add_column(col)
    for s, design in designs.items():
        t.add_row(escape(s), escape(design) or "[dim](none)[/]")
    con.print(t)
    if not questionary.confirm(f"Record {'these ' + str(len(serials)) if len(serials) > 1 else serials[0]} as built?", default=True).ask():
        return
    atf.record_builds(designs)
    con.print(f"  [green]✔[/] Recorded {len(serials)} as built: {escape(', '.join(serials))}")
    pause()


def _change_build(built):
    built = sorted(built, key=lambda r: atf.serial_key(r["serial_number"]))
    r = questionary.select("Which one?", choices=[questionary.Choice(
        f"{r['serial_number']}  ({r['design'] or 'built'})", r) for r in built]
        + [questionary.Choice("Back", "back")]).ask()
    if r in (None, "back"):
        return
    what = questionary.select(f"{r['serial_number']}:", choices=[questionary.Choice("Change the design", "edit"),
                              questionary.Choice("Clear it (not built)", "clear"), questionary.Choice("Back", "back")]).ask()
    if what == "clear":
        if questionary.confirm(f"Clear the build record of {r['serial_number']}?", default=False).ask():
            atf.clear_build(r["serial_number"])
            con.print(f"  [green]✔[/] {escape(r['serial_number'])} is no longer marked built")
            pause()
    elif what == "edit":
        design = _ask_design(r["design"])
        if design is not None:
            atf.record_build([r["serial_number"]], design)
            con.print(f"  [green]✔[/] Updated {escape(r['serial_number'])}")
            pause()


def open_approved_flow():
    rows = sorted([r for r in atf.load_rows() if r["status"] == "approved"] + [{**r, "_sbr": True} for r in atf.load_sbrs()],
                  key=lambda r: atf.serial_key(r["serial_number"]))
    choices = [questionary.Choice(f"{r['serial_number']}  ({'SBR, ' if r.get('_sbr') else ''}approved {r['approved_date']})", n)
               for n, r in enumerate(rows)]
    n = questionary.select("Open which approved Form 1?", choices=choices + [questionary.Choice("Back", "back")]).ask()
    if n in (None, "back"):
        return
    path = atf.approved_pdf_for(rows[n])
    if path is None:
        con.print(f"  [yellow]No saved PDF for {escape(rows[n]['serial_number'])} yet: run Check approvals on eForms.[/]")
        return pause()
    open_file(path)


def export_flow():
    path = atf.export_inventory()
    con.print(f"  [green]✔[/] Wrote {escape(str(path))}")
    if questionary.confirm("Open it now?", default=True).ask():
        open_file(path)


def pause():
    con.input("\n[dim]Press Enter to go back to the menu…[/] ")


def _row_choice(rows, i, extra=""):
    r = rows[i]
    return questionary.Choice(f"{r['serial_number']}  (caliber {r['caliber']}){extra}", value=i)


def next_serial(serial):
    """Suggest the next serial: add 1 to the LAST group of digits, keeping everything around it and the zero padding
    (A009 -> A010, ABC-0099 -> ABC-0100, 2026-001A -> 2026-002A). No digits -> no suggestion."""
    m = re.match(r"^(.*?)(\d+)(\D*)$", serial or "")
    return f"{m.group(1)}{int(m.group(2)) + 1:0{len(m.group(2))}d}{m.group(3)}" if m else ""


def serial_run(first, count):
    """[first, next, ...] count serials, counting up the last group of digits (A055 -> A055..A064). [] if it can't count."""
    run = [first]
    while len(run) < count:
        nxt = next_serial(run[-1])
        if not nxt:
            return []
        run.append(nxt)
    return run


def add_flow():
    """Add one suppressor, or a run of N with consecutive serials sharing a caliber and length."""
    rows = atf.load_rows()
    while True:
        last = rows[-1] if rows else {"serial_number": "", "caliber": ""}
        taken = {r["serial_number"].upper() for r in rows}
        count = questionary.text("How many suppressors to add?", default="1",
                                 validate=lambda v: (v.strip().isdigit() and 1 <= int(v) <= 100) or "A number from 1 to 100").ask()
        if count is None:
            return
        n = int(count)

        def valid(v):
            if not v.strip():
                return "Enter a serial number"
            if v.strip().upper() in taken:
                return "That serial is already in the list"
            return n == 1 or bool(next_serial(v.strip())) or "To add several, the serial needs a number to count up from"
        serial = questionary.text("Serial number:" if n == 1 else f"First of the {n} serial numbers:",
                                  default=next_serial(last["serial_number"]), validate=valid).ask()
        if serial is None:
            return
        cal = questionary.text("Caliber:", default=last["caliber"], validate=lambda v: bool(v.strip()) or "Enter a caliber").ask()
        if cal is None:
            return
        default_len = last.get("overall_length") or str((_settings().get("firearm") or {}).get("overall_length") or "12")
        length = questionary.text("Overall length (inches):", default=default_len, validate=_inches).ask()
        if length is None:
            return
        serials = serial_run(serial.strip(), n)
        clash = [x for x in serials if x.upper() in taken]
        if clash:
            con.print(f"  [bold red]✖ Already in your list: {escape(', '.join(clash))}. Nothing added.[/]")
            continue
        what = f"{escape(serials[0])}" if n == 1 else f"{escape(serials[0])} – {escape(serials[-1])} ({n} suppressors)"
        if n > 1 and not questionary.confirm(f"Add {serials[0]} – {serials[-1]} ({n} suppressors), caliber {cal.strip()}, "
                                             f"{length.strip()}\"?", default=True).ask():
            continue
        rows += [{"caliber": cal.strip(), "serial_number": x, "overall_length": length.strip(), "status": ""} for x in serials]
        atf.save_rows(rows)
        con.print(f"  [green]✔[/] Added {what}, caliber {escape(cal.strip())}, {escape(length.strip())}\"")
        if not questionary.confirm("Add more?", default=False).ask():
            return


def _inches(s):
    return s.strip().replace(".", "", 1).isdigit() or "A number of inches, e.g. 12 or 6.5"


def _settings():
    try:
        return atf.load_trust() or {}
    except Exception:
        return {}


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
        rows[i]["filed_date"] = rows[i]["filed_date"] or date.today().strftime("%m/%d/%Y")
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
    settings = _settings()
    individual = atf.applicant_type(settings) == "individual"
    for i in ([] if individual else indices):  # individuals file without a 5320.23
        dest, missing = atf.stamp_5320(rows[i], today)
        probs = missing + atf._check_5320(dest, rows[i], today)
        if probs:
            con.print(f"  [bold red]✖ 5320.23 for {escape(rows[i]['serial_number'])}: {escape(', '.join(probs))}[/]")
            return pause()
    if not individual:
        con.print(f"  [green]✔[/] 5320.23s generated, dated {today}")
    deflen = str((settings.get("firearm") or {}).get("overall_length") or "12")
    lines = "\n".join(f"  • {escape(rows[i]['serial_number'])}  caliber {escape(rows[i]['caliber'])}, "
                      f"{escape(rows[i]['overall_length'] or deflen)}\" overall" for i in indices)
    con.print(Panel(
        f"[bold]File {len(indices)} Form 1{'s' if len(indices) > 1 else ''}:[/]\n{lines}\n\n"
        f"Filing as: {'an individual (you)' if individual else 'your trust'}\n"
        f"SSN: {'you type it on each form' if ssn else 'left blank'}\n"
        + (_answers_line(settings) if individual else "") + "\n"
        "By confirming, you affirm that each is a legitimate Form 1 application (the portal's \"I affirm\"),\n"
        + ("that your answers to questions 11.A-11.M above are true today,\n" if individual else "") +
        "and you authorize the script to tick the certification and click Sign & Submit for each one,\n"
        "only after every read-back check and the portal's validation pass. You still enter the code and PIN.",
        title="Confirm", border_style="cyan", expand=False))
    if not questionary.confirm("Confirm and open the browser?", default=False, auto_enter=False).ask():
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


def _answers_line(settings):
    ans = atf.answers_11(settings)
    if all(a == "No" for a in ans.values()):
        return "Questions 11.A-11.M.1: all No; 11.M.2: N/A (from Settings)\n"
    return "Questions 11.A-11.M.1: " + ", ".join(f"{q} {a or '?'}" for q, a in ans.items()) + "\n"


def preview_flow():
    rows = atf.load_rows()
    i = questionary.select("Preview the steps for which row?", choices=[_row_choice(rows, i) for i in range(len(rows))][::-1]).ask()
    if i is None:
        return
    if atf.applicant_type(_settings()) == "trust" and not any(atf.OUT.glob(f"5320.23*_{rows[i]['serial_number']}.pdf")):
        atf.stamp_5320(rows[i], date.today().strftime("%m/%d/%Y"))
    P.verbose = True
    try:
        atf.file_rows(rows, [i], SimpleNamespace(dry=True, submit=False, auto_affirm=True, skip_ssn=False, capture=False))
    finally:
        P.finish()
        P.verbose = False
    pause()


def practice_flow():
    """Fill one queued row in eForms and stop before signing: nothing is submitted (submit=False)."""
    probs = atf.settings_problems()
    if probs:
        con.print(Panel("\n".join(f"• {escape(x)}" for x in probs), title="Fix these in Settings first", border_style="red", expand=False))
        return pause()
    rows = atf.load_rows()
    queued = [i for i, r in enumerate(rows) if r["status"] not in atf.SKIP]
    if not queued:
        con.print("  Nothing queued: add a suppressor first (a test serial is fine; it is never submitted).")
        return pause()
    i = questionary.select("Practice with which row?", choices=[_row_choice(rows, i) for i in queued]).ask()
    if i is None:
        return
    con.print(Panel("Fills this Form 1 in eForms the same way as filing, including the read-back check, then STOPS:\n"
                    "it never ticks the certification or clicks Sign & Submit. The row stays queued; the draft stays in\n"
                    "your eForms dashboard (delete it there, or it's deleted after 90 days). It clicks \"I affirm\" for you.",
                    title="Practice run (nothing is submitted)", border_style="green", expand=False))
    if not questionary.confirm("Open the browser?", default=True, auto_enter=False).ask():
        return
    if atf.applicant_type(_settings()) == "trust":
        atf.stamp_5320(rows[i], date.today().strftime("%m/%d/%Y"))
    try:
        atf.file_rows(rows, [i], SimpleNamespace(dry=False, submit=False, auto_affirm=True, skip_ssn=True, capture=False))
    finally:
        P.finish()
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
    if questionary.confirm("Re-file it anyway?", default=False, auto_enter=False).ask():
        file_flow([i])


# ---------- Settings (first-run wizard, and Settings in the menu) ----------
GENERATOR_URL = "https://jakepro716.github.io/atf-5320-generator/"
DISCLAIMER = ("This is an unofficial helper, provided as-is with no warranty, and not affiliated with or endorsed by ATF. "
              "You are solely responsible for every application it files for you: each submission is YOUR certification "
              "under penalty of perjury. Review what it fills, check ATF's eForms terms of use, and stop if anything looks wrong.\n\n"
              "It never asks for, stores or types your password, SSN, emailed code or PIN.")  # no hard wraps: the panel wraps it
STATES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "District of Columbia",
          "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
          "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
          "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
          "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
          "Washington", "West Virginia", "Wisconsin", "Wyoming"]
DEFAULT_DESCRIPTION = "I POSSESS NO PARTS OR PLANS, AND WILL NOT PURCHASE OR MANUFACTURE ANY PARTS UNTIL A FORM 1 IS APPROVED"
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
    """Paths dragged into a terminal arrive quoted (both OSes) or with escaped spaces (macOS/Linux)."""
    s = s.strip().strip("'\"")
    if os.name != "nt":  # on Windows a backslash is a path separator, never an escape
        s = s.replace("\\ ", " ")
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


def _check_template():
    """Test-stamp forms/5320.23.pdf with a dummy serial; returns a list of problems (empty = OK)."""
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as tmp:
            today = date.today().strftime("%m/%d/%Y")
            dest, missing = atf.stamp_5320({"serial_number": "TEST123", "caliber": "22"}, today, out_dir=tmp)
            return [f"could not place: {', '.join(missing)}"] if missing else atf._check_5320(dest, {"serial_number": "TEST123"}, today)
    except SystemExit as e:
        return [str(e.code)]


def _questions_11(saved):
    """Ask questions 11.A-11.M.1 in ATF's words. Returns {id: 'No'|'Yes'}."""
    con.rule("Questions 11.A-11.M (asked on every Form 1 you file as an individual)")
    con.print("  ATF's wording. Your answers are saved, filled in on each form, checked on the verify page, and shown\n"
              "  for you to confirm before every batch. Definitions: ATF's Form 1 instructions (eForms shows them too).\n"
              "  [dim]11.M.2 (exceptions for nonimmigrant visas) is answered N/A.[/]")
    out = {}
    for q, text in atf.QUESTIONS_11:
        out[q] = _a(questionary.select(f"{q}. {text}", choices=["No", "Yes"],
                                       default=saved.get(q) if saved.get(q) in ("No", "Yes") else None))
    if any(a == "Yes" for a in out.values()):
        con.print(Panel("You answered Yes to at least one question. This helper only files when every answer is No:\n"
                        "file this application yourself in eForms. (Your answers are saved; filing stays blocked.)",
                        border_style="red", expand=False))
    return out


def settings_flow(first=False):
    path = atf.DATA / "trust.yaml"
    t = yaml.safe_load(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if first:
        con.print(Panel("[bold]Welcome.[/] This sets up your details once; you can change them later in Settings.\n\n"
                        + escape(DISCLAIMER), title="eForms Form 1 helper", border_style="cyan", expand=False))
        if not questionary.confirm("I understand and accept", default=False).ask():
            sys.exit("Setup not completed.")
    try:
        kind = _a(questionary.select("Who files the Form 1s?", default=atf.applicant_type(t), choices=[
            questionary.Choice("A trust (you are a responsible person; a 5320.23 goes with each form)", "trust"),
            questionary.Choice("Me, as an individual (no 5320.23; you answer questions 11.A-11.M once here)", "individual")]))
        trust_filing = kind == "trust"
        tpl, new_tpl, pre = atf.FORMS / "5320.23.pdf", None, {}
        if trust_filing:
            con.rule("Your 5320.23 template")
        if trust_filing and (not tpl.exists() or not _a(questionary.confirm("Keep your current 5320.23 template?", default=True))):
            con.print(f"  Make it once at [cyan]{GENERATOR_URL}[/]: Form 1, your details, your CLEO, sign it,\n"
                      "  and use serial [bold]TEMPLATE[/] and caliber [bold]00[/] (they get replaced for each suppressor).\n"
                      "  Download the ATF copy. Step by step: README.md, \"Making your 5320.23 template\".")
            new_tpl = Path(_clean_path(_a(questionary.path("5320.23 PDF (drag it here):", validate=_file(suffixes=(".pdf",))))))
        if trust_filing:
            try:
                pre = atf.read_template(new_tpl or tpl)
            except Exception:
                pre = {}
            if pre:
                con.print(f"  [green]✔[/] Read {len(pre)} answers from it (never the SSN). Press Enter to accept each, or type a correction.")
            else:
                con.print("  [yellow]⚠ Couldn't read answers from it (was it made with the generator?); you'll type them in.[/]")
        # A newly chosen template wins over saved settings (e.g. a new chief); a kept one only fills gaps.
        if new_tpl:
            get = lambda p, d="": pre.get(p) or atf._get(t, p) or d  # noqa: E731
        else:
            get = lambda p, d="": atf._get(t, p) or pre.get(p) or d  # noqa: E731
        trust = dict(t.get("trust") or {})
        rp = dict((t.get("responsible_persons") or [{}])[0])
        if trust_filing:
            con.rule("Trust")
            trust["name"] = _a(questionary.text("Trust name (exactly as on the trust document):", default=get("trust.name"), validate=_need))
            con.rule("Responsible person (you)")
            rp["title"] = _a(questionary.text("Your title in the trust:", default=get("responsible_persons.0.title", "Settlor"), validate=_need))
        else:
            con.rule("You (the applicant)")
            con.print("  [dim]Your name, address, phone and email come from your eForms account.[/]")
        rp["dob"] = _a(questionary.text("Date of birth (MM/DD/YYYY):", default=get("responsible_persons.0.dob"), validate=_date))
        rp["sex"] = _a(questionary.select("Sex (as the eForms dropdown lists it):", choices=["Male", "Female"],
                                          default=get("responsible_persons.0.sex", "Male").title()))
        race = get("responsible_persons.0.race")
        rp["race"] = _a(questionary.select("Race (as eForms lists it):", choices=RACES, default=race if race in RACES else None))
        rp["place_of_birth"] = {**(rp.get("place_of_birth") or {}), "country": "United States of America",
                                "state": _state("State of birth:", get("responsible_persons.0.place_of_birth.state"))}
        rp["home_address"] = {**(rp.get("home_address") or {}),
                              "state": _state("State you live in:", get("responsible_persons.0.home_address.state"))}
        ind = dict(t.get("individual") or {})
        if not trust_filing:
            ind["answers"], ind["upin"] = _questions_11(atf.answers_11(t)), _a(questionary.text(
                "Unique Personal Identification Number (UPIN), if ATF issued you one (else leave empty):",
                default=str(ind.get("upin") or ""))).strip()
        con.rule("CLEO (chief law enforcement officer you notify)")
        cl = dict(t.get("cleo") or {})
        cl["department"] = _a(questionary.text("Agency / department:", default=get("cleo.department"), validate=_need))
        cl["official"] = _a(questionary.text("Official's name:", default=get("cleo.official"), validate=_need))
        cl["title"] = _a(questionary.text("Official's title:", default=get("cleo.title", "CHIEF OF POLICE"), validate=_need))
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
        fa["model"] = _a(questionary.text("Model:", default=str(get("firearm.model", "N/A")), validate=_need))
        fa["overall_length"] = _a(questionary.text("Default overall length for new suppressors (inches):", default=str(get("firearm.overall_length", "12")),
                                                   validate=lambda s: s.strip().replace(".", "", 1).isdigit() or "A number"))
        fa.pop("manufacturer_code", None)  # no longer a setting: always FMI for a Form 1 (config/mapping.yaml)
        fa["description"] = _a(questionary.text("Firearm description (the description box on the firearm page; edit if you like):",
                                                default=get("firearm.description", DEFAULT_DESCRIPTION), validate=_need))
        con.rule("Files")
        files = dict(t.get("files") or {})
        files["eft"] = _clean_path(_a(questionary.path("Fingerprint file (.eft):", default=get("files.eft"), validate=_file(suffixes=(".eft",)))))
        files["photo"] = _clean_path(_a(questionary.path("Photo of you (eForms requires it):",
                                                         default=get("files.photo"), validate=_file(suffixes=(".jpg", ".jpeg", ".png")))))
        if trust_filing:
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
    if kind == "individual" or "applicant_type" in t:  # older trust settings stay as they were
        t["applicant_type"] = kind
    t.update({"trust": trust, "responsible_persons": [rp] + list((t.get("responsible_persons") or [])[1:]),
              "cleo": cl, "firearm": fa, "files": files, "docs": docs})
    if not trust_filing:
        t["individual"] = ind
    if not t.get("trust"):  # an individual who never had a trust
        t.pop("trust", None)
    if first:
        t["accepted_disclaimer"] = date.today().isoformat()
    atf.DATA.mkdir(exist_ok=True)
    if new_tpl:  # only now, so cancelling never replaces the old template
        atf.FORMS.mkdir(exist_ok=True)
        tpl.write_bytes(new_tpl.read_bytes())
    path.write_text("# Your settings (personal; data/ is never shared). Edit via ./atf > Settings.\n"
                    + yaml.safe_dump(t, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")
    if not atf.SUPP.exists():
        atf.save_rows([])
    probs = atf.settings_problems(t) + (_check_template() if trust_filing else [])
    if probs:
        con.print(Panel("\n".join(f"• {escape(x)}" for x in probs), title="Saved, but fix these before filing",
                        border_style="red", expand=False))
    elif trust_filing:
        con.print("  [green]✔ Settings saved, and your 5320.23 template stamps correctly.[/]")
    else:
        con.print("  [green]✔ Settings saved.[/]")
    pause()


def _approved_table(items, title, style):
    t = Table(box=box.ROUNDED, header_style="bold", title=title, title_justify="left", border_style=style)
    for col in ("Serial", "Caliber", "Length", "Approved", "Notes"):
        t.add_column(col)
    for r, notes in items:
        t.add_row(escape(r["serial"] or "?"), escape(r["caliber"]), escape(r["overall_length"]), escape(r["approval_date"]),
                  escape("; ".join(notes)) if notes else "[green]✔[/]")
    return t


def approvals_flow():
    con.print(Panel("Reads your [bold]approved[/] Form 1s on eForms. Read-only: it only clicks Search, View Form, View PDF\n"
                    "and Go Home. Each PDF is saved to out/approved/, and suppressors are checked against your records by\n"
                    "serial (caliber, overall length). SBRs and other types are only counted. The first run fetches every\n"
                    "approved form (roughly 10-20 seconds each); later runs only fetch new approvals.",
                    title="Check approvals", border_style="cyan", expand=False))
    if not questionary.confirm("Open the browser?", default=True, auto_enter=False).ask():
        return
    try:
        found, problems = atf.fetch_approved()
    finally:
        P.finish()
    records, more = atf.read_approved(found)
    rep = atf.approval_report(records, atf.load_rows(), atf.load_trust())
    sup = sum(1 for r in records if "SILENCER" in r["type"].upper())
    sbr = sum(1 for r in records if atf.is_sbr(r))
    other = ", ".join(f"{n} {escape(k.lower())}" for k, n in rep["skipped"].items() if "RIFLE" not in k.upper())
    con.print(f"\n  [bold]{len(records)}[/] approved Form 1s: [bold]{sup}[/] suppressor(s), [bold]{sbr}[/] SBR(s)"
              + (f"; other types skipped: {other}" if other else "") + "\n")
    if rep["matched"]:
        con.print(_approved_table(rep["matched"], f"[bold]Approved, matches your records ({len(rep['matched'])})[/]", "green"))
    if rep["differs"]:
        con.print(_approved_table(rep["differs"], f"[bold]Approved, check these ({len(rep['differs'])})[/]", "yellow"))
    if rep["unknown"]:
        con.print(_approved_table(rep["unknown"], f"[bold]Approved, NOT in your records ({len(rep['unknown'])})[/]", "cyan"))
    waiting = [r["serial_number"] for r in rep["waiting"]]
    con.print(f"  Filed, not approved yet: [bold]{len(waiting)}[/]" + (f"  ({escape(', '.join(waiting[:12]))}{', …' if len(waiting) > 12 else ''})" if waiting else ""))
    alerts = [f"serial {s} is on more than one approved Form 1" for s in rep["duplicates"]] + problems + more
    if alerts:
        con.print(Panel("\n".join(f"• {escape(a)}" for a in alerts), title="Look at these", border_style="red", expand=False))
    updated, added = atf.apply_approvals(records)
    if rep["unknown"]:
        serials = [r["serial"] for r, _ in rep["unknown"]]
        names = ", ".join(serials[:6]) + (", …" if len(serials) > 6 else "")
        if questionary.confirm(f"Add the {len(serials)} approved suppressor(s) that aren't in your records ({names}) to your inventory?",
                               default=True).ask():
            more_updated, added = atf.apply_approvals(records, import_unknown=True)
            updated += more_updated
    new_sbrs = atf.record_sbrs(records)
    if updated or added:
        con.print(f"  [green]✔[/] Inventory updated: {len(updated)} approval(s) recorded, {len(added)} suppressor(s) added.")
    if new_sbrs:
        con.print(f"  [green]✔[/] SBR inventory: {new_sbrs} added (Inventory > Show SBRs).")
    con.print("  [dim]PDFs: out/approved/[/]")
    pause()


def advanced_flow():
    trust_filing = atf.applicant_type(_settings()) == "trust"
    what = questionary.select("Advanced:", choices=[
        questionary.Choice("Preview the steps for a row (no browser)", preview_flow),
        questionary.Choice("Practice run: fill a form without submitting", practice_flow)] + ([
        questionary.Choice("Regenerate a 5320.23 with a different date", redate_flow)] if trust_filing else []) + [
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
    atf.backfill_filed_dates()
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
        choices += [questionary.Choice("Inventory", inventory_flow), questionary.Choice("Add suppressors", add_flow)]
        if queued:
            choices.append(questionary.Choice("Edit or remove a queued suppressor", edit_queued_flow))
        choices += [
                    questionary.Choice("Check approvals on eForms", approvals_flow),
                    questionary.Choice("Settings", settings_flow),
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
        except Exception as e:  # anything unexpected: a short message, the details in a file, and back to the menu
            P.finish()
            log = atf.OUT / "last_error.txt"
            try:
                atf.OUT.mkdir(parents=True, exist_ok=True)
                log.write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{traceback.format_exc()}", encoding="utf-8")
                where = f" Details: {log} (it can contain your serials; share it only with someone helping you)."
            except Exception:
                where = ""
            con.print(f"  [bold red]✖ Something went wrong: {escape(str(e).splitlines()[0] if str(e) else type(e).__name__)}[/]")
            con.print(f"  [dim]Any browser it opened has been closed; the lines above say whether it logged out.{escape(where)}[/]")
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
