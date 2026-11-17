"""Backups: the daily records ZIP in out/backups and the backup folder (Settings > Backups). Fake data only."""
import os
import zipfile
from datetime import datetime, timedelta

import yaml

from conftest import atf, pick, queue, ui

QUIT = lambda kw: "quit"  # noqa: E731
DAY1, DAY2 = datetime(2026, 11, 17, 9), datetime(2026, 11, 18, 9)


def set_folder(path):
    t = atf.load_trust()
    t["backup_folder"] = str(path)
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t, sort_keys=False), encoding="utf-8")


def touch_later(path, text):
    """Change a file and make sure its time moves on (some file systems keep coarse times)."""
    st = path.stat()
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_records_zip_only_when_something_changed(configured):
    queue(("S1", "9"))
    (atf.OUT / "approved").mkdir()
    (atf.OUT / "approved" / "S1.pdf").write_bytes(b"%PDF big")
    (atf.OUT / "approved" / "index.csv").write_text("eform_id,file\n1,S1.pdf\n", encoding="utf-8")
    (atf.DATA / "server.yaml").write_text("key: SECRET\n", encoding="utf-8")
    res = atf.run_backups(now=DAY1)
    assert res["error"] is None and res["records"] == atf.OUT / "backups" / "records-2026-11-17.zip"
    with zipfile.ZipFile(res["records"]) as z:
        names = set(z.namelist())
    assert {"data/suppressors.csv", "data/trust.yaml", "out/approved/index.csv"} <= names
    assert "data/server.yaml" not in names  # the server pairing key never goes into a backup
    assert not any(n.endswith(".pdf") for n in names)  # records only: a few KB
    assert atf.run_backups(now=DAY1)["records"] is None  # nothing changed: nothing written
    assert atf.run_backups(now=DAY2)["records"] is None  # a new day alone isn't a change
    touch_later(atf.SUPP, atf.SUPP.read_text(encoding="utf-8") + "S2,9,,,,,,,,,,,\n")
    assert atf.run_backups(now=DAY2)["records"].name == "records-2026-11-18.zip"
    assert sorted(p.name for p in (atf.OUT / "backups").glob("records-*.zip")) == ["records-2026-11-17.zip", "records-2026-11-18.zip"]


def test_only_the_newest_records_zips_are_kept(configured, monkeypatch):
    monkeypatch.setattr(atf, "BACKUP_KEEP_DAYS", 2)
    queue(("S1", "9"))
    for n in range(4):
        touch_later(atf.SUPP, atf.SUPP.read_text(encoding="utf-8") + f"X{n},9,,,,,,,,,,,\n")
        atf.run_backups(now=DAY1 + timedelta(days=n))
    assert sorted(p.name for p in (atf.OUT / "backups").glob("records-*.zip")) == ["records-2026-11-19.zip", "records-2026-11-20.zip"]


def test_backup_folder_gets_everything_then_only_what_changed(configured, tmp_path):
    usb = tmp_path / "USB"
    usb.mkdir()
    set_folder(usb)
    queue(("S1", "9"))
    (atf.OUT / "approved").mkdir()
    (atf.OUT / "approved" / "S1.pdf").write_bytes(b"%PDF approval")
    (atf.OUT / "cleo").mkdir()
    (atf.OUT / "cleo" / "Form1_S1.cleo.pdf").write_bytes(b"%PDF cleo")
    (atf.OUT / "cleo" / "CLEO packet 2026-11-17 0900.pdf").write_bytes(b"%PDF packet")  # made from the rest: skipped
    (atf.OUT / "submissions.csv").write_text("serial\nS1\n", encoding="utf-8")
    (atf.OUT / "5320.23_S1.pdf").write_bytes(b"%PDF stamped")  # regenerated each filing: not backed up
    res = atf.run_backups(now=DAY1)
    root = usb / atf.BACKUP_DIR
    assert res["folder"] == "ok" and res["error"] is None and res["copied"][0] > 0
    files = {p.relative_to(root / "files").as_posix() for p in (root / "files").rglob("*") if p.is_file()}
    assert {"data/suppressors.csv", "data/trust.yaml", "forms/5320.23.pdf", "out/approved/S1.pdf",
            "out/cleo/Form1_S1.cleo.pdf", "out/submissions.csv"} <= files
    assert not any("packet" in f or f == "out/5320.23_S1.pdf" for f in files)
    # the settings' files outside the helper folder (here: <tmp>/files), under labels that name no one
    assert {"elsewhere/your-fingerprint-file-prints.eft", "elsewhere/trust-document-trust.pdf"} <= files
    assert (root / "records" / "records-2026-11-17.zip").exists() and (root / "README.txt").exists()
    assert atf.run_backups(now=DAY1)["copied"] == (0, 0)  # nothing changed: nothing copied
    assert atf.run_backups(force=True, now=DAY1)["copied"] == (0, 0)  # checked file by file: all there
    touch_later(atf.SUPP, atf.SUPP.read_text(encoding="utf-8") + "S2,9,,,,,,,,,,,\n")
    (atf.OUT / "approved" / "S1.pdf").unlink()  # gone here: stays in the backup folder
    res = atf.run_backups(now=DAY1)
    assert res["copied"][0] == 1 and (root / "files" / "out" / "approved" / "S1.pdf").exists()
    assert "S2" in (root / "files" / "data" / "suppressors.csv").read_text(encoding="utf-8")


def test_a_missing_backup_folder_is_reported_not_raised(configured, tmp_path):
    queue(("S1", "9"))
    set_folder(tmp_path / "unplugged")
    atf.note_backup_folder_set(now=DAY1)
    res = atf.run_backups(now=DAY1)
    assert res["folder"] == "missing" and res["error"] is None and res["records"]  # the local copy is still made
    assert atf.backup_stale_days(now=DAY1 + timedelta(days=6)) is None
    assert atf.backup_stale_days(now=DAY1 + timedelta(days=8)) == 8
    (tmp_path / "unplugged").mkdir()
    atf.run_backups(now=DAY1 + timedelta(days=8))
    assert atf.backup_stale_days(now=DAY1 + timedelta(days=9)) is None


def test_backups_never_raise(configured, monkeypatch):
    queue(("S1", "9"))
    monkeypatch.setattr(atf, "_records_zip", lambda *a: (_ for _ in ()).throw(PermissionError("locked by Excel")))
    assert atf.run_backups(now=DAY1)["error"] == "locked by Excel"


def test_settings_backups_choose_a_folder(configured, answers, screen, tmp_path):
    usb = tmp_path / "BACKUPDRIVE"
    usb.mkdir()
    queue(("S1", "9"))
    answers(pick("Settings"), pick("Backups (only on this computer)"), "choose", str(usb), QUIT)
    ui.menu()
    assert atf.backup_folder() == str(usb)
    assert (usb / atf.BACKUP_DIR / "files" / "data" / "suppressors.csv").exists()
    out = screen.getvalue()
    assert "Backed up to BACKUPDRIVE" in out and str(tmp_path) not in out  # the folder's name, never its path
    answers(pick("Settings"), pick("Backups (to BACKUPDRIVE)"), "stop", QUIT)
    ui.menu()
    assert atf.backup_folder() is None


def test_no_backup_folder_is_fine_in_check_my_setup(configured):
    """Backup folders are optional: without one, Check my setup says what's made, and it's not a warning."""
    level, label, detail, _ = atf._backup_check(atf.load_trust())
    assert (level, label) == ("ok", "Backups") and "optional" in detail


def test_backup_folder_inside_the_helper_folder_is_refused(configured):
    assert ui._backup_folder_ok(str(atf.ROOT / "out")) != True  # noqa: E712
    assert ui._backup_folder_ok(str(atf.ROOT)) != True  # noqa: E712


def test_home_warns_when_the_backup_folder_has_been_away_a_week(configured, answers, screen, tmp_path, monkeypatch):
    queue(("S1", "9"))
    set_folder(tmp_path / "unplugged")
    monkeypatch.setattr(atf, "backup_stale_days", lambda now=None: 9)
    answers(QUIT)
    ui.menu()
    assert "Your backup folder hasn't been reachable for 9 days" in screen.getvalue()
