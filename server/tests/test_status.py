"""The server's own status (GET /api/status), one backup file with everything (GET /api/backup, `restore`), and
`update`: against made-up data, a local stand-in for GitHub's repository, never GitHub itself."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import zipfile
from types import SimpleNamespace

import pytest

from atf_server import __version__, db, status, update
from atf_server.__main__ import main

NEXT = ".".join(map(str, (*update.parse(__version__)[:2], update.parse(__version__)[2] + 1)))


@pytest.fixture(autouse=True)
def no_github(monkeypatch):
    def offline(version="?"):
        raise update.UpdateError("no network in tests")
    monkeypatch.setattr(update, "fetch_latest", offline)


def release(version=NEXT):
    return {"version": version, "tag": f"v{version}", "url": f"https://example.com/v{version}"}


# ---------- status ----------
def test_status_needs_a_paired_device_and_says_how_the_server_is(client, auth, filled, data_dir):
    assert client.get("/api/status").status_code == 401 and client.get("/api/backup").status_code == 401
    s = client.get("/api/status", headers=auth).json()
    assert s["version"] == __version__ and s["api"] == 3 and s["update"] is None
    assert s["counts"] == {"suppressors": 38, "other": 2, "pdfs": 27, "devices": 1}
    assert s["missing_pdfs"] == [] and s["storage"]["pdf_bytes"] > 0 and s["storage"]["free_bytes"] > 0
    assert s["certificate"] is None                                    # the test server has no tls/ in its data folder
    assert s["problems"] == ["No backup has been made yet (the server makes one a day)."]
    db.backup(filled, data_dir / "backups")
    s = client.get("/api/status", headers=auth).json()
    assert s["problems"] == [] and s["backups"]["count"] == 1 and s["backups"]["age_days"] == 0
    assert "built" not in json.dumps(s) and "last_seen" not in json.dumps(s)   # nothing about when anything was done


def test_status_names_what_needs_attention(client, auth, filled, data_dir, monkeypatch):
    old = db.backup(filled, data_dir / "backups")
    os.utime(old, (old.stat().st_mtime - 3 * 86400,) * 2)
    gone = db.pdfs(filled)[0]
    (data_dir / "approved" / f"{gone['sha256']}.pdf").unlink()
    monkeypatch.setattr(status, "_certificate", lambda folder: {"expires": "2026-12-01", "days_left": 12})
    monkeypatch.setattr(status.shutil, "disk_usage", lambda p: SimpleNamespace(free=50 * 1024 * 1024))
    (data_dir / "update_check.json").write_text(json.dumps({"checked": "2026-11-09T10:00:00", "latest": release()}))
    s = client.get("/api/status", headers=auth).json()
    assert s["update"] == {"version": NEXT, "url": f"https://example.com/v{NEXT}"} and s["missing_pdfs"] == [gone["serial"]]
    text = " | ".join(s["problems"])
    assert "newest backup is 3 days old" in text and "expires in 12 days" in text and "50 MB free" in text
    assert f"1 approval PDF is missing from the server's disk: {gone['serial']}" in text and len(s["problems"]) == 4


def test_certificate_expiry_is_read(tls_dir):
    c = status._certificate(tls_dir)
    assert 790 <= c["days_left"] <= 800 and len(c["expires"]) == 10
    assert status._certificate(tls_dir / "nowhere") is None


def test_the_release_check(data_dir, monkeypatch):
    data_dir.mkdir()
    assert update.check(data_dir, __version__) is None and update.available(data_dir, __version__) is None   # offline
    asked = []
    monkeypatch.setattr(update, "fetch_latest", lambda version="?": asked.append(1) or release())
    assert update.check(data_dir, __version__)["version"] == NEXT and update.check(data_dir, __version__) and asked == [1]
    assert update.available(data_dir, __version__)["version"] == NEXT and update.available(data_dir, NEXT) is None
    monkeypatch.setenv("ATF_UPDATE_CHECK", "0")
    assert update.available(data_dir, __version__) is None and not update.enabled()


# ---------- one file with everything ----------
def test_backup_file_and_restore(client, auth, filled, data_dir):
    r = client.get("/api/backup", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "atf-inventory-backup-" in r.headers["content-disposition"]
    assert not list((data_dir / "outgoing").glob("*"))                 # the file made for the download is gone again
    z = zipfile.ZipFile(io.BytesIO(r.content))
    pdfs = [n for n in z.namelist() if n.startswith("approved/")]
    assert "inventory.db" in z.namelist() and "atf-server restore" in z.read("README.txt").decode() and len(pdfs) == 27
    assert all(hashlib.sha256(z.read(n)).hexdigest() == n[9:73] for n in pdfs)
    assert not [n for n in z.namelist() if "tls" in n or "key" in n]   # the server's keys never leave it

    saved = data_dir / "kept.zip"
    saved.write_bytes(r.content)
    before = db.rows(filled), db.sbrs(filled)
    db.reset(filled, data_dir / "approved", data_dir / "backups")      # the server loses everything
    assert db.is_empty(filled) and not list((data_dir / "approved").glob("*.pdf"))
    assert main(["--data", str(data_dir), "restore", str(saved)]) == 0
    assert (db.rows(filled), db.sbrs(filled)) == before and len(list((data_dir / "approved").glob("*.pdf"))) == 27
    assert client.get("/pdf/A101", headers=auth).status_code == 200
    assert not (data_dir / "restore.db.tmp").exists()


def test_a_damaged_backup_file_restores_nothing(filled, data_dir):
    good = data_dir / "good.zip"
    status.write_backup(filled, data_dir / "approved", good, __version__)
    bad, src = data_dir / "bad.zip", zipfile.ZipFile(good)
    with zipfile.ZipFile(bad, "w") as z:
        for n in src.namelist():
            z.writestr(n, b"%PDF changed" if n.startswith("approved/") else src.read(n))
    shutil.rmtree(data_dir / "approved")
    filled.execute("UPDATE suppressors SET design = 'changed since' WHERE serial = 'A101'")
    with pytest.raises(db.Refused, match="damaged"):
        status.restore_backup(filled, bad, data_dir)
    assert db.row(filled, "A101")["design"] == "changed since" and not (data_dir / "approved").exists()
    notzip = data_dir / "x.zip"
    with zipfile.ZipFile(notzip, "w") as z:
        z.writestr("hello.txt", "x")
    with pytest.raises(db.Refused, match="isn't an inventory backup"):
        status.restore_backup(filled, notzip, data_dir)


# ---------- update ----------
def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=str(cwd),
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def checkout(tmp_path, monkeypatch, filled, data_dir):
    """A plain install: a clone at v<this version> of a local 'GitHub' that also has v<NEXT> (other packages) and a
    newer unreleased commit. update.ROOT = the clone."""
    if not shutil.which("git"):
        pytest.skip("git isn't installed")
    up = tmp_path / "upstream"
    (up / "server").mkdir(parents=True)
    git(up, "init", "-q", "-b", "main")
    for v, reqs in ((__version__, "fastapi\n"), (NEXT, "fastapi\nnewthing\n"), ("99.0.0", "fastapi\n")):
        (up / "server" / "version.txt").write_text(v)
        (up / "server" / "requirements.txt").write_text(reqs)
        git(up, "add", "-A")
        git(up, "commit", "-q", "-m", v)
        if v != "99.0.0":
            git(up, "tag", f"v{v}")
    app = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(up), str(app))
    git(app, "reset", "-q", "--hard", f"v{__version__}")
    monkeypatch.setattr(update, "ROOT", app)
    monkeypatch.setattr(update, "REPO_URL", str(up))
    monkeypatch.setattr(update, "fetch_latest", lambda version="?": release())
    return app


def test_update_backs_up_installs_packages_then_moves_to_the_tag(checkout, data_dir, monkeypatch, capsys):
    ran, restarted = [], []
    monkeypatch.setattr(update, "_run", lambda *a: ran.append(((checkout / "server/version.txt").read_text(), open(a[-1]).read())))
    monkeypatch.setattr(update, "_restart", lambda: restarted.append(1) or "The server was restarted.")
    assert update.kind() == "git"
    assert main(["--data", str(data_dir), "update", "--check"]) == 2 and (checkout / "server/version.txt").read_text() == __version__
    assert main(["--data", str(data_dir), "update", "--yes"]) == 0
    assert ran == [(__version__, "fastapi\nnewthing\n")]             # the NEW list, while the code was still the old one
    assert (checkout / "server/version.txt").read_text() == NEXT and restarted == [1]   # the tag, not main's newer commit
    assert len(list((data_dir / "backups").glob(f"inventory-before-update-{__version__}-*.db"))) == 1
    assert f"Updated to {NEXT}. The server was restarted." in capsys.readouterr().out


def test_update_leaves_everything_when_it_cannot_go_through(checkout, data_dir, monkeypatch, capsys):
    def fails(*a):
        raise update.UpdateError("-m pip install failed: no network")
    monkeypatch.setattr(update, "_run", fails)
    monkeypatch.setattr(update, "_restart", lambda: pytest.fail("restarted without an update"))
    assert main(["--data", str(data_dir), "update", "--yes"]) == 1
    assert f"left on version {__version__}" in capsys.readouterr().err and (checkout / "server/version.txt").read_text() == __version__
    (checkout / "server/version.txt").write_text("edited by hand")
    assert main(["--data", str(data_dir), "update", "--yes"]) == 1 and "files were changed" in capsys.readouterr().err
    git(checkout, "checkout", "-q", "--", "server/version.txt")
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert main(["--data", str(data_dir), "update"]) == 1 and "Not updated." in capsys.readouterr().out
    monkeypatch.setattr(update, "fetch_latest", lambda version="?": release(__version__))
    assert main(["--data", str(data_dir), "update"]) == 0 and "newest version" in capsys.readouterr().out


def test_update_in_docker_says_how(data_dir, filled, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(update, "ROOT", tmp_path)                       # no .git: an image
    monkeypatch.setattr(update, "fetch_latest", lambda version="?": release())
    assert update.kind() == "image"
    assert main(["--data", str(data_dir), "update", "--yes"]) == 1 and "docker compose up -d --build" in capsys.readouterr().err
