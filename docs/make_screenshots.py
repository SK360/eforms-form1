"""Screenshots of the ./atf menu for the docs and for posting, rendered from the REAL menu code with MADE-UP data.

Nothing personal can appear: the script runs in a temporary folder with a fake trust, fake serials (A101 ...),
fake application numbers and eForm IDs, and never opens eForms (the browser parts are replayed as the same events
the engine sends). Run:  .venv/bin/python docs/make_screenshots.py   ->  docs/screenshots/*.png
"""
import io
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import questionary
import yaml
from rich.console import Console
from rich.markup import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import atf  # noqa: E402
import ui  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import scan_personal  # noqa: E402

PERSONAL = scan_personal.personal_values()  # from the real data/ here; the screens themselves use made-up records

OUT = ROOT / "docs" / "screenshots"
WIDTH = 114
TODAY = date.today()


def d(days_ago):
    return (TODAY - timedelta(days=days_ago)).strftime("%m/%d/%Y")


# ---------- a fake trust in a temporary folder ----------
def sandbox():
    tmp = Path(tempfile.mkdtemp(prefix="atf-shots-"))
    for sub in ("data", "out", "forms", "files"):
        (tmp / sub).mkdir()
    atf.DATA, atf.OUT, atf.FORMS, atf.SUPP = tmp / "data", tmp / "out", tmp / "forms", tmp / "data" / "suppressors.csv"
    f = tmp / "files"
    for name in ("prints.eft", "me.jpg", "trust.pdf", "printer.jpg"):
        (f / name).write_bytes(b"x")
    settings = {
        "trust": {"name": "DEMO FAMILY TRUST"},
        "responsible_persons": [{"title": "Settlor", "dob": "01/02/1980", "sex": "Male", "race": "White",
                                 "place_of_birth": {"country": "United States of America", "state": "Ohio"},
                                 "home_address": {"state": "Ohio"}}],
        "cleo": {"department": "SPRINGFIELD POLICE DEPARTMENT", "official": "PAT EXAMPLE", "title": "CHIEF OF POLICE",
                 "address": {"street": "200 MAIN ST", "zip": "45502", "city": "Springfield", "county": "Clark", "state": "Ohio"}},
        "firearm": {"model": "N/A", "overall_length": "12", "description": ui.DEFAULT_DESCRIPTION},
        "files": {"trust_document": str(f / "trust.pdf"), "trust_document_desc": "TRUST", "eft": str(f / "prints.eft"),
                  "photo": str(f / "me.jpg")},
        "docs": [{"path": str(f / "printer.jpg"), "description": "3D PRINTER"}]}
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    shutil.copy(ROOT / "tests" / "fixtures" / "fake_5320.pdf", atf.FORMS / "5320.23.pdf")
    return tmp


def inventory():
    """A101-A106 approved (A101 with conditions), A107-A112 filed and waiting, A113-A115 queued. All made up."""
    rows = []
    for n in range(101, 116):
        r = {"serial_number": f"A{n}", "caliber": "22" if n < 107 else "9", "overall_length": "6.5" if n < 103 else "12"}
        if n <= 106:
            r.update(status="approved", filed_date=d(320 - n * 2), approved_date=d(220 - n * 2), eform_id=f"1000{n}",
                     application_number=f"100000{n}", conditions="Mark per 27 CFR 479.102" if n == 101 else "")
            if n <= 104:  # built (made-up design names)
                r.update(built="yes", design="Rimfire Short" if n < 103 else "Rimfire 12in")
        elif n <= 112:
            r.update(status="filed", filed_date=d(230 - n * 2), application_number=f"100000{n}")
        else:
            r.update(status="")
        rows.append(r)
    atf.save_rows(rows)
    atf.save_sbrs([
        {"serial_number": "R2001", "caliber": "5.56", "model": "AR-15", "barrel_length": "10.5", "overall_length": "26.5",
         "approved_date": d(200), "eform_id": "1000201", "control_number": "", "conditions": ""},
        {"serial_number": "R2002", "caliber": "9", "model": "PCC", "barrel_length": "8", "overall_length": "24",
         "approved_date": d(90), "eform_id": "1000202", "control_number": "", "conditions": ""}])


# ---------- recording console + questionary prompts echoed the way questionary leaves them on screen ----------
def recorder():
    ui.con = Console(record=True, width=WIDTH, file=io.StringIO(), force_terminal=True, color_system="truecolor")
    ui.con.clear = lambda *a, **k: None
    ui.con.input = lambda prompt="", **k: (ui.con.print(prompt), "")[1]
    ui.P = ui.Progress()
    ui.P._start = lambda text: None  # no spinners in a still picture
    atf.emit, atf.ask = ui.P.emit, ui.P.ask


def answered(msg, shown):
    ui.con.print(f"[#5f819d]?[/] [bold]{escape(msg)}[/] [bold #FF9D00]{escape(shown)}[/]")


def script(*answers):
    """Patch questionary so each prompt takes the next answer and prints itself like an answered questionary prompt."""
    todo = list(answers)

    def prompt(kind, msg, kw):
        class Q:
            def ask(self):
                a = todo.pop(0)
                a = a(kw) if callable(a) else a
                if kind == "confirm":
                    shown = "Yes" if a else "No"
                elif kind == "checkbox":
                    titles = {c.value: c.title for c in kw["choices"]}
                    shown = "[" + ", ".join(str(titles[v]) for v in a) + "]"
                elif kind == "select":
                    shown = next((str(c.title) for c in kw["choices"] if c.value == a), str(a))
                else:
                    shown = a
                answered(msg, shown)
                return a
        return Q()
    for kind in ("select", "checkbox", "confirm", "text", "path", "autocomplete"):
        setattr(questionary, kind, lambda msg, _k=kind, **kw: prompt(_k, msg, kw))


def open_select(msg, titles, at=0):
    """How a questionary list looks while it's waiting for you."""
    ui.con.print(f"[#5f819d]?[/] [bold]{escape(msg)}[/] [dim](Use arrow keys)[/]")
    for i, t in enumerate(titles):
        ui.con.print(f" [bold #FF9D00]» {escape(t)}[/]" if i == at else f"   {escape(t)}")


def menu_titles(rows):
    queued = [r["serial_number"] for r in rows if r["status"] not in atf.SKIP]
    unbuilt = any(r["status"] == "approved" and not atf.is_built(r) for r in rows)
    return ([f"File {len(queued)} queued suppressors ({', '.join(queued)})"] if queued else []) + [
        "Inventory", "Add suppressors"] + (["Building one? Find the serial to use"] if unbuilt else []) + [
        "Edit or remove a queued suppressor", "Check approvals on eForms", "Settings", "Advanced…", "Quit"]


# ---------- the screens ----------
def shot_home():
    rows = atf.load_rows()
    ui.home(rows)
    open_select("What next?", menu_titles(rows))


def shot_add():
    rows = atf.load_rows()
    ui.home(rows)
    answered("What next?", "Add suppressors")
    script("10", lambda kw: kw["default"], "9", lambda kw: kw["default"], True, False)
    ui.add_flow()
    atf.save_rows(rows)  # back to the shared starting point for the next screens


class Cut(Exception):
    pass


def shot_file():
    """File the 3 queued: the confirmation is one picture, the filing (replayed, no browser) the next."""
    rows = atf.load_rows()
    answered("What next?", menu_titles(rows)[0])
    pick_all = lambda kw: [c.value for c in kw["choices"] if c.checked]  # noqa: E731
    script(pick_all, False, True)  # all three; no SSN pause; confirm

    def replay(*a):
        cut("03-file")
        filing()
        cut("04-filing")
        raise Cut
    atf.file_rows = replay
    try:
        ui.file_flow()
    except Cut:
        pass


def filing():
    """The browser part, replayed as the events the engine sends for one form (no browser, no eForms)."""
    e = ui.P.emit
    ui.P.ask("Log in (MFA included) and wait for the eForms home page, then press Enter (the script opens the Form 1)... ")
    e("info", "\nStarting a new Form 1 for A113 (Ctrl-C stops and logs out)...")
    e("row", "", serial="A113", caliber="9")
    for name in ("Applicant type", "Intended use", "Application", "Applicant", "Responsible person"):
        e("section", "", name=name)
    e("info", "    DIALOG: Status: Valid EBTS Package | Ok")
    for name in ("CLEO", "Firearm: manufacturer", "Firearm: details", "Firearm: supporting documents", "Trust document",
                 "Read-back check"):
        e("section", "", name=name)
    for label, want in (("Serial number", "A113"), ("Model", "N/A"), ("Type", "SILENCER"), ("Caliber", "9"),
                        ("Overall length", "12")):
        e("expect", "", label=label, want=want, got=want, ok=True)
    e("expect", "", label="Trust name", want="", got="", ok=True, private=True)
    e("section", "", name="Certify")
    e("banner", "")
    e("done", "", serial="A113", number="100000113")
    e("info", "\nStarting a new Form 1 for A114 (Ctrl-C stops and logs out)...")
    e("row", "", serial="A114", caliber="9")
    e("section", "", name="Applicant type")
    ui.P._close()


def shot_inventory():
    rows = atf.load_rows()
    answered("What next?", "Inventory")
    ui.con.print(ui.inventory_table(rows, f"[bold]Inventory[/] · All ({len(rows)})"))
    ui.con.print(f"  {ui.summary(rows)}")
    ui.con.print("  [yellow]⚠ approved with conditions: A101[/] [dim](conditions are in data/suppressors.csv)[/]")
    open_select("Inventory:", ["Show another view…", "Edit or remove a queued suppressor", "Record builds",
                               "Open an approved PDF", "Export to Excel", "Back"])


def shot_sbrs():
    sbrs = atf.load_sbrs()
    answered("Inventory:", "Show another view…")
    answered("Show:", "SBRs")
    ui.con.print(ui.sbr_table(sbrs, f"[bold]Inventory[/] · SBRs ({len(sbrs)})"))
    ui.con.print(f"  {ui.sbr_summary(sbrs)}")


def shot_check_setup():
    """Settings > Check my setup: the made-up files get real-looking headers; the supporting document is shown as an
    online-only OneDrive copy to show a warning."""
    f = atf.DATA.parent / "files"
    for name, head in (("prints.eft", b"1.001:249"), ("me.jpg", b"\xff\xd8\xff\xe0"), ("trust.pdf", b"%PDF-1.4"),
                       ("printer.jpg", b"\xff\xd8\xff\xe0")):
        (f / name).write_bytes(head + b"x" * 180000)
    (f / "trust.pdf").write_bytes(b"%PDF-1.4" + b"x" * 2400000)
    online = (f / "printer.jpg").stat().st_ino
    atf._online_only = lambda st: st.st_ino == online
    atf._chromium_path = lambda: sys.executable
    answered("What next?", "Settings")
    answered("Settings:", "Check my setup (files, templates, browser; no eForms)")
    ui.check_setup_flow()


def shot_phone():
    """The phone page (./atf serve) at iPhone size, served from the made-up records: the list with a number-pad search,
    and one suppressor's details. Its text is scanned like the terminal screens."""
    import phone
    from playwright.sync_api import sync_playwright
    server, link = phone.start(port=0, host="127.0.0.1")
    try:
        with sync_playwright() as pw:
            page = pw.chromium.launch().new_page(viewport={"width": 390, "height": 760}, device_scale_factor=3)
            page.goto(link)
            page.wait_for_selector(".row")
            for name, step in (("13-phone-list", lambda: page.fill("#q", "")), ("14-phone-detail", lambda: page.click(".row[data-s='A101']"))):
                step()
                page.wait_for_timeout(300)
                hits = scan_personal.scan_text(page.inner_text("body"), PERSONAL)
                if hits:
                    sys.exit(f"{name}: {len(hits)} possible personal value(s) on the page; not saved")
                page.screenshot(path=str(OUT / f"{name}.png"))
                print(f"docs/screenshots/{name}.png")
    finally:
        server.shutdown()
        server.server_close()


def shot_build_serial():
    """Which serial for a 5" 22 build: A105 and A106 are approved at 12" and not built yet."""
    answered("What next?", "Building one? Find the serial to use")
    script("22", "5", "back")
    ui.build_serial_flow()


def shot_builds():
    answered("Inventory:", "Record builds")
    script("new", "A105-A106", "Rimfire 12in", "Rimfire Short", True)
    ui.builds_flow()
    rows = [r for r in atf.load_rows() if r["status"] == "approved"]
    ui.con.print(ui.build_table(rows, f"[bold]Inventory[/] · Approved ({len(rows)})"))


def shot_approvals():
    """Check approvals, with the eForms part replaced by made-up approval records."""
    rec = lambda serial, cal, length, typ="SILENCER", eid="": {  # noqa: E731
        "type": typ, "serial": serial, "caliber": cal, "model": "N/A", "barrel_length": "", "overall_length": length,
        "approved": True, "approval_date": d(1), "conditions": "", "eform_id": eid or f"1000{serial[1:]}",
        "control_number": "", "pdf": ""}
    # A107 and A108 were just approved; A108's PDF shows a different length, which the check points out
    old = {r["serial_number"]: r for r in atf.load_rows()}
    records = [dict(rec(f"A{n}", "22", "6.5" if n < 103 else "12"), approval_date=old[f"A{n}"]["approved_date"])
               for n in range(101, 107)]
    records[0]["conditions"] = "Mark per 27 CFR 479.102"
    records += [rec("A107", "9", "12"), rec("A108", "9", "12.5")]
    records += [dict(rec("R2001", "5.56", "26.5", "SHORT BARRELED RIFLE"), barrel_length="10.5"),
                dict(rec("R2002", "9", "24", "SHORT BARRELED RIFLE"), barrel_length="8")]
    atf.fetch_approved = lambda: ([], [])
    atf.read_approved = lambda found=None: (records, [])
    answered("What next?", "Check approvals on eForms")
    script(True)
    ui.pause = lambda: None
    ui.approvals_flow()


def shot_individual():
    """Filing as an individual: the confirmation (08) and the verify-page read-back (09). Same made-up data."""
    path = atf.DATA / "trust.yaml"
    t = yaml.safe_load(path.read_text(encoding="utf-8"))
    t["applicant_type"] = "individual"
    t["individual"] = {"answers": {q: "No" for q, _ in atf.QUESTIONS_11}, "upin": ""}
    t.pop("trust", None)
    t["files"].pop("trust_document", None)
    path.write_text(yaml.safe_dump(t, sort_keys=False), encoding="utf-8")
    rows = atf.load_rows()
    answered("What next?", menu_titles(rows)[0])
    pick_all = lambda kw: [c.value for c in kw["choices"] if c.checked]  # noqa: E731
    script(pick_all, False, True)

    def replay(*a):
        cut("08-individual-confirm")
        e = ui.P.emit
        e("row", "", serial="A113", caliber="9")
        for name in ("CLEO", "Firearm: manufacturer", "Firearm: details", "Firearm: supporting documents", "Attachments"):
            e("section", "", name=name)
        ui.con.export_svg(clear=True)  # the picture starts at the verify page
        e("section", "", name="Read-back check")
        for label, want in (("Serial number", "A113"), ("Model", "N/A"), ("Type", "SILENCER"), ("Caliber", "9"),
                            ("Overall length", "12")):
            e("expect", "", label=label, want=want, got=want, ok=True)
        for q, _ in atf.QUESTIONS_11:
            e("expect", "", label=f"Answer {q}", want="No", got="NO", ok=True)
        e("expect", "", label="Answer 11.M.2", want="N/A", got="N/A", ok=True)
        e("expect", "", label="Citizenship", want="UNITED STATES", got="UNITED STATES", ok=True)
        e("section", "", name="Certify")
        ui.P._close()
        cut("09-individual-readback")
        raise Cut
    atf.file_rows = replay
    try:
        ui.file_flow()
    except Cut:
        pass


SHOTS = [("01-home", shot_home), ("02-add", shot_add), (None, shot_file),  # shot_file makes 03-file and 04-filing
         ("05-inventory", shot_inventory), ("06-sbrs", shot_sbrs),
         ("11-build-serial", shot_build_serial), ("10-builds", shot_builds),  # before 10: that one builds A105-A106
         ("07-approvals", shot_approvals), ("12-check-setup", shot_check_setup), (None, shot_phone),
         (None, shot_individual)]  # 08-individual-confirm, 09-individual-readback (last: it switches the settings)


def to_png(svg, dest):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        page = pw.chromium.launch().new_page(device_scale_factor=2)
        page.set_content(f"<html><body style='margin:0;background:transparent'>{svg}</body></html>")
        page.wait_for_timeout(800)  # let the web font load
        page.locator("svg").first.screenshot(path=str(dest), omit_background=True)


def cut(name):
    """Save what is on the recorded screen so far as docs/screenshots/<name>.png and start a fresh screen. Its text is
    scanned for values from YOUR records first (made-up data only; a PNG can't be scanned after it's made)."""
    svg = ui.con.export_svg(title="./atf", clear=True)
    hits = scan_personal.scan_text(scan_personal.svg_text(svg), PERSONAL)
    if hits:
        sys.exit(f"{name}: {len(hits)} possible personal value(s) on the screen ({', '.join(sorted({h[1] for h in hits}))}); not saved")
    to_png(svg, OUT / f"{name}.png")
    print(f"docs/screenshots/{name}.png")


def main():
    tmp = sandbox()
    try:
        inventory()
        OUT.mkdir(parents=True, exist_ok=True)
        ui.pause = lambda: None
        for name, make in SHOTS:
            recorder()
            make()
            if name:
                cut(name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
