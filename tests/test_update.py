"""New versions (src/update.py): the daily check, the home-screen line and menu item, and updating a ZIP install or a
git checkout. Never GitHub (a fake _get) and never the real program folder (update.APP = the sandbox's app/)."""
import io
import json
import os
import shutil
import subprocess
import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import atf, pick, queue, ui, update

QUIT = lambda kw: "quit"  # noqa: E731
CUR = atf.__version__
NEXT = ".".join(map(str, (*update.parse(CUR)[:2], update.parse(CUR)[2] + 1)))


def release_json(version=NEXT, **over):
    return json.dumps({"tag_name": f"v{version}", "body": "## Fixed\n- a thing", "draft": False, "prerelease": False,
                       "html_url": f"https://github.com/SK360/eforms-form1/releases/tag/v{version}", **over}).encode()


def fake_zip(version=NEXT, files=None, top="eforms-form1-x"):
    """A GitHub-style source ZIP: one top folder; src/atf.py says `version`."""
    files = {"src/atf.py": f'__version__ = "{version}"\n', "src/ui.py": "print('new menu')\n",
             "requirements.txt": "rich\n", "atf": "#!/bin/sh\necho new\n", **(files or {})}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for rel, text in files.items():
            info = zipfile.ZipInfo(f"{top}/{rel}" if top else rel)
            info.external_attr = (0o100755 if rel == "atf" else 0o100644) << 16
            z.writestr(info, text)
    return buf.getvalue()


@pytest.fixture
def github(monkeypatch):
    """github(release=bytes, zip=bytes, final=url) answers update._get like GitHub would; records the URLs asked."""
    state = {"asked": []}

    def configure(release=None, zip=None, final=None):
        def get(url, limit=0, timeout=0):
            state["asked"].append(url)
            if url == update.LATEST_API and release is not None:
                return release, url
            if url.endswith(".zip") and zip is not None:
                return zip, final or "https://codeload.github.com/SK360/eforms-form1/zip/refs/tags/v" + NEXT
            raise update.UpdateError("GitHub can't be reached (offline)")
        monkeypatch.setattr(update, "_get", get)
        return state
    return configure


def zip_app():
    """The sandbox's program folder as an older ZIP install (+ the user's records, which must stay as they are)."""
    app = update.APP
    for rel, text in {"src/atf.py": f'__version__ = "{CUR}"\n', "src/ui.py": "print('old menu')\n",
                      "requirements.txt": "rich\n", "atf": "#!/bin/sh\necho old\n", "data/trust.yaml": "mine: 1\n",
                      "forms/5320.23.pdf": "my template", "out/submissions.csv": "my log"}.items():
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_bytes(text.encode())  # bytes: text mode would write CRLF on Windows (not what a ZIP has)
    return app


# ---------- versions and the check ----------
def test_versions():
    assert update.parse("v1.2.10") == (1, 2, 10) and update.parse("1.2") is None and update.parse("v1.2.3-rc1") is None
    assert update.newer("1.2.10", "1.2.9") and not update.newer("1.2.9", "1.2.10") and not update.newer("1.2.4", "1.2.4")
    assert not update.newer("garbage", "1.0.0")


def test_check_remembers_the_answer_for_a_day(github):
    gh = github(release=release_json())
    assert update.check()["version"] == NEXT and update.available()["version"] == NEXT
    assert update.check()["version"] == NEXT and len(gh["asked"]) == 1          # cached: GitHub asked once
    assert update.check_in_background() is None                                   # not due: no thread, no wait
    cache = json.loads((atf.OUT / "update_check.json").read_text())
    cache["checked"] = (datetime.now() - timedelta(days=2)).isoformat()
    (atf.OUT / "update_check.json").write_text(json.dumps(cache))
    update.check()
    assert len(gh["asked"]) == 2                                                  # stale: asked again


def test_offline_and_odd_answers_show_nothing(github):
    github()
    assert update.check() is None and update.available() is None
    github(release=release_json(prerelease=True))
    assert update.check(force=True) is None
    github(release=b"<html>rate limited</html>")
    assert update.check(force=True) is None
    github(release=release_json(version=CUR))
    assert update.check(force=True)["version"] == CUR and update.available() is None   # same version: nothing offered


def test_the_check_can_be_turned_off(configured, github):
    gh = github(release=release_json())
    update.check()
    t = atf.load_trust()
    t["update_check"] = False
    ui._save_settings(t)
    assert update.available() is None and update.check_in_background() is None
    assert len(gh["asked"]) == 1


# ---------- the menu ----------
def test_home_shows_the_new_version_and_updates(configured, github, answers, screen, monkeypatch):
    zip_app()
    github(release=release_json(), zip=fake_zip())  # no check yet: the menu's own check must show on its first screen
    restarted = []
    monkeypatch.setattr(update, "restart", lambda: restarted.append(1))
    answers(pick("⬆ Update to version"), True)
    ui.menu()                                                                     # update -> restart -> menu ends
    out = screen.getvalue()
    assert f"Version {NEXT} is out (you have {CUR})" in out and "What's new in" in out and "a thing" in out
    assert f"Updated to {NEXT}" in out and restarted == ([] if os.name == "nt" else [1])  # Windows: started by hand
    assert 'print(\'new menu\')' in (update.APP / "src" / "ui.py").read_text()


def test_menu_refuses_while_a_row_is_pending(configured, github, answers, screen):
    zip_app()
    queue(("S1", "9", "pending"))
    github(release=release_json(), zip=fake_zip())
    update.check()
    answers(pick("⬆ Update to version"), QUIT)
    ui.menu()
    assert "Not now: pending row to resolve first: S1" in screen.getvalue()
    assert "old menu" in (update.APP / "src" / "ui.py").read_text()


def test_settings_and_advanced(configured, github, answers, screen):
    github(release=release_json(version=CUR))
    answers(pick("Settings"), pick("New versions (tell me"), "off", pick("Advanced"), pick("Check for a new version now"), QUIT)
    ui.menu()
    assert atf.load_trust()["update_check"] is False
    assert f"You have the newest version ({CUR})" in screen.getvalue()


# ---------- ZIP installs ----------
def test_zip_update_replaces_program_files_only(configured, github):
    app = zip_app()
    github(release=release_json(), zip=fake_zip(files={"data/trust.yaml": "THEIRS", "forms/x.pdf": "THEIRS",
                                                        "out/submissions.csv": "THEIRS", ".venv/bin/python": "THEIRS",
                                                        "config/mapping.yaml": "portal: {}\n"}))
    res = update.apply(update.check())
    assert res == {"version": NEXT, "how": "zip", "packages": False, "server_api": False}
    assert f'"{NEXT}"' in (app / "src/atf.py").read_text() and (app / "config/mapping.yaml").exists()
    assert (app / "data/trust.yaml").read_text() == "mine: 1\n" and (app / "out/submissions.csv").read_text() == "my log"
    assert (app / "forms/5320.23.pdf").read_text() == "my template" and not (app / "forms/x.pdf").exists()
    assert not (app / ".venv").exists()
    assert (app / ".update-backup" / CUR / "src/ui.py").read_text() == "print('old menu')\n"   # the old file kept
    assert not (app / ".update-backup" / CUR / "requirements.txt").exists()                     # unchanged: untouched
    if os.name != "nt":
        assert os.stat(app / "atf").st_mode & 0o111                                              # still runnable
    assert not (atf.OUT / "update_check.json").exists() or update.available() is None


def test_zip_update_puts_everything_back_on_a_failure(configured, github, monkeypatch):
    app = zip_app()
    github(release=release_json(), zip=fake_zip(files={"src/new.py": "x = 1\n"}))
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(b)
        if len(calls) == 3:
            raise OSError("disk full")
        real(a, b)
    monkeypatch.setattr(update.os, "replace", flaky)
    with pytest.raises(update.UpdateError, match="put back"):
        update.apply(update.check())
    assert (app / "src/atf.py").read_text() == f'__version__ = "{CUR}"\n'
    assert (app / "atf").read_text() == "#!/bin/sh\necho old\n" and not (app / "src/new.py").exists()
    assert not list(app.rglob("*.update-tmp"))


@pytest.mark.parametrize("bad, why", [
    (lambda: fake_zip(version="9.9.9"), "doesn't contain version"),
    (lambda: fake_zip(files={"../escape.txt": "x"}), "unexpected path"),
    (lambda: b"not a zip", "isn't a ZIP"),
])
def test_odd_downloads_change_nothing(configured, github, bad, why):
    app = zip_app()
    before = {p: p.read_bytes() for p in app.rglob("*") if p.is_file()}
    github(release=release_json(), zip=bad())
    with pytest.raises(update.UpdateError, match=why):
        update.apply(update.check())
    assert {p: p.read_bytes() for p in app.rglob("*") if p.is_file()} == before


def test_download_from_elsewhere_is_refused(configured, github):
    zip_app()
    github(release=release_json(), zip=fake_zip(), final="https://example.com/x.zip")
    with pytest.raises(update.UpdateError, match="unexpected place"):
        update.apply(update.check())


def test_new_requirements_install_packages(configured, github, monkeypatch):
    zip_app()
    github(release=release_json(), zip=fake_zip(files={"requirements.txt": "rich\nnewthing\n"}))
    ran = []
    monkeypatch.setattr(update, "_run", lambda *a: ran.append(a))
    assert update.apply(update.check())["packages"] is True
    assert ran == [("-m", "pip", "install", "--quiet", "-r", "requirements.txt"), ("-m", "playwright", "install", "chromium")]


def test_line_endings_alone_are_not_new_requirements(configured, github, monkeypatch):
    app = zip_app()
    (app / "requirements.txt").write_bytes(b"rich\r\n")
    github(release=release_json(), zip=fake_zip())
    monkeypatch.setattr(update, "_run", lambda *a: pytest.fail("pip ran for a line-ending change"))
    assert update.apply(update.check())["packages"] is False


def test_new_server_api_is_mentioned_in_server_mode(configured, github, monkeypatch):
    app = zip_app()
    (app / "src/remote.py").write_text("API = 3\n")
    github(release=release_json(), zip=fake_zip(files={"src/remote.py": "API = 4\n"}))
    monkeypatch.setattr(atf, "server_settings", lambda: {"server_id": "x"})
    monkeypatch.setattr(atf, "load_rows", lambda: [])
    monkeypatch.setattr(atf, "outbox", lambda: [])
    assert update.apply(update.check())["server_api"] is True


def test_a_waiting_filing_result_blocks(configured, github, monkeypatch):
    zip_app()
    github(release=release_json(), zip=fake_zip())
    monkeypatch.setattr(atf, "outbox", lambda: [{"serial": "S1", "number": "1"}])
    with pytest.raises(update.UpdateError, match="hasn't reached your inventory server"):
        update.apply(update.check())


# ---------- git checkouts ----------
def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=str(cwd),
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    """A local 'GitHub' repo with v<CUR> and v<NEXT> tags (and a newer unreleased main commit); update.APP = a clone
    at v<CUR> on main. update.REPO_URL points at it."""
    if not shutil.which("git"):
        pytest.skip("git isn't installed")
    up = tmp_path / "upstream"
    (up / "src").mkdir(parents=True)
    git(up, "init", "-q", "-b", "main")
    (up / ".gitignore").write_text("data/\n")
    for v, text in ((CUR, "old"), (NEXT, "new"), ("99.0.0", "unreleased")):
        (up / "src/atf.py").write_text(f'__version__ = "{v}"\n# {text}\n')
        (up / "requirements.txt").write_text("rich\n")
        git(up, "add", "-A")
        git(up, "commit", "-q", "-m", v)
        if v != "99.0.0":
            git(up, "tag", f"v{v}")
    app = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(up), str(app))
    git(app, "reset", "-q", "--hard", f"v{CUR}")
    (app / "data").mkdir()
    (app / "data/trust.yaml").write_text("mine: 1\n")
    monkeypatch.setattr(update, "APP", app)
    monkeypatch.setattr(update, "REPO_URL", str(up))
    return app


def test_git_update_moves_to_the_release_tag(configured, github, upstream):
    github(release=release_json())
    assert update.kind() == "git"
    assert update.apply(update.check())["how"] == "git"
    text = (upstream / "src/atf.py").read_text()
    assert f'"{NEXT}"' in text and "unreleased" not in text                       # the tag, not main's newer commit
    assert (upstream / "data/trust.yaml").read_text() == "mine: 1\n"


def test_git_update_refuses_local_changes_and_other_branches(configured, github, upstream):
    github(release=release_json())
    rel = update.check()
    (upstream / "src/atf.py").write_text("changed by hand\n")
    with pytest.raises(update.UpdateError, match="files of the helper were changed"):
        update.apply(rel)
    git(upstream, "checkout", "-q", "--", "src/atf.py")
    git(upstream, "switch", "-q", "-c", "mine")
    with pytest.raises(update.UpdateError, match="branch mine"):
        update.apply(rel)
    git(upstream, "switch", "-q", "main")
    (upstream / "local.txt").write_text("x")
    git(upstream, "add", "local.txt")
    git(upstream, "commit", "-q", "-m", "local")
    with pytest.raises(update.UpdateError, match="commits of its own"):
        update.apply(rel)


# ---------- CLI ----------
def test_cli_check_and_update(configured, github, capsys, monkeypatch):
    zip_app()
    github(release=release_json(), zip=fake_zip())
    with pytest.raises(SystemExit) as e:
        update.cmd_update(SimpleNamespace(check=True, yes=False))
    assert e.value.code == 2 and f"Version {NEXT} is available" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    update.cmd_update(SimpleNamespace(check=False, yes=False))
    assert "Not updated." in capsys.readouterr().out
    update.cmd_update(SimpleNamespace(check=False, yes=True))
    assert f"Updated to {NEXT}." in capsys.readouterr().out
