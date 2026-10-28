"""tools/scan_personal.py on made-up records in a temporary folder (never the real data/)."""
import io
import subprocess
import sys

import yaml
from rich.console import Console

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import scan_personal as sp  # noqa: E402

# Made-up, but built here so this file doesn't trip the scan itself (the commit hook scans it too)
SSN, MAIL, MAIL2 = "321-54-" + "9876", "ada.z" + "@mailbox.org", "me" + "@gmail.com"


def records(root):
    (root / "data").mkdir(exist_ok=True)  # the autouse sandbox already made it
    (root / "data" / "trust.yaml").write_text(yaml.safe_dump({
        "trust": {"name": "ZEPHYR FAMILY TRUST"},
        "responsible_persons": [{"first": "Quincy", "last": "Zephyrson", "dob": "07/08/1977", "sex": "Male", "race": "White",
                                 "title": "Settlor", "home_address": {"state": "Ohio"}},
                                {"first_name": "Ada", "last_name": "Zephyrson", "email": MAIL,
                                 "eft": "/Users/quincy/Docs/Ada Zephyrson prints.eft", "photo": "C:\\Users\\q\\me.jpg"}],
        "files": {"photo": "/Users/quincy/Docs/photo.jpg"},
        "firearm": {"description": "I POSSESS NO PARTS OR PLANS", "model": "N/A"}}), encoding="utf-8")
    (root / "data" / "suppressors.csv").write_text("caliber,serial_number,status,application_number,design\n"
                                                   "22,QX101,approved,987654321,Hush Mk2\n", encoding="utf-8")
    return sp.personal_values(root)


def test_values_from_records(tmp_path):
    values = {v: label for v, label, _ in records(tmp_path)}
    for v in ("ZEPHYR FAMILY TRUST", "Quincy", "Zephyrson", "07/08/1977", MAIL, "QX101", "987654321",
              "Hush Mk2", "Ada Zephyrson prints.eft", "/Users/quincy/Docs/photo.jpg"):
        assert v in values, v
    for v in ("Male", "White", "Settlor", "Ohio", "I POSSESS NO PARTS OR PLANS", "photo.jpg", "me.jpg", "22"):
        assert v not in values, v                                     # generic, or a shared default
    assert values["QX101"] == "one of your serials" and values["07/08/1977"] == "a date of birth"


def test_scan_text(tmp_path):
    values = records(tmp_path)
    text = f"ok line\nfiled zephyr family trust\nQX1015 is not QX101\nssn 123-45-6789 / {SSN}\nmail jane@example.com, {MAIL2}"
    hits = sp.scan_text(text, values)
    assert [(n, label) for n, label, _ in hits] == [(2, "a name"), (3, "one of your serials"), (4, "an SSN-shaped number"),
                                                    (5, "an email address")]   # the fixture SSN and example.com pass


def test_screenshot_text_reads_across_colors():
    con = Console(record=True, width=60, file=io.StringIO(), force_terminal=True)
    con.print("Trust: [red]ZEPHYR[/] [green]FAMILY TRUST[/] · [bold]QX101[/]")
    text = sp.svg_text(con.export_svg())
    assert "Trust: ZEPHYR FAMILY TRUST · QX101" in text


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_staged_all_and_files(tmp_path, capsys):
    records(tmp_path)
    git(tmp_path, "init", "-q")
    (tmp_path / "notes.md").write_text("line one\nNew build: Hush Mk2 on QX101\n", encoding="utf-8")
    (tmp_path / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0")
    git(tmp_path, "add", "notes.md", "pic.png")
    assert sp.main([], root=tmp_path) == 1
    out = capsys.readouterr().out
    assert "notes.md:2  a design name" in out and "notes.md:2  one of your serials" in out
    assert "not read (binary): pic.png" in out
    assert "Hush" not in out and "QX101" not in out                    # where and what, never the value
    assert sp.main(["--all"], root=tmp_path) == 1
    msg = tmp_path / "MSG"
    msg.write_text("Fix the build finder\n", encoding="utf-8")
    assert sp.main([str(msg)], root=tmp_path) == 0
    assert "nothing personal found (" in capsys.readouterr().out


def test_no_records_checks_patterns_only(tmp_path, capsys):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text(f"call {SSN}\n", encoding="utf-8")
    git(tmp_path, "add", "a.txt")
    assert sp.main([], root=tmp_path) == 1
    assert "patterns only" in capsys.readouterr().out
