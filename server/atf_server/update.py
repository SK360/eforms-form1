"""New versions of the server: knowing one is out, and `atf-server update` (python -m atf_server update). Stdlib only.

Check: GitHub's "latest release" of the helper's repository (the server is released with it, under the same version),
asked at most once a day by the running server and kept in <data>/update_check.json, so the page's Server screen and
the app can say "version X is out". One anonymous GET with a generic User-Agent; nothing about this server is sent.
Off with ATF_UPDATE_CHECK=0. No answer (no internet from the server) = nothing shown.

Update (a console command, never from the page: the service can't write its own code, on purpose): a plain install
is a git checkout (LXC.md), so, like the app's updater: refused with local changes, on another branch or with local
commits; the release TAG is fetched (never main's newer commits); the database is backed up; if the release's
server/requirements.txt differs, pip install runs BEFORE the code changes (a failure leaves the old version
running); then a fast-forward to the tag and, where the systemd service is running, a restart. In Docker there is no
checkout inside the image: the command says how to rebuild it instead.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = "SK360/eforms-form1"
REPO_URL = f"https://github.com/{REPO}.git"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ROOT = Path(__file__).resolve().parents[2]  # the checkout on a plain install (tests point it elsewhere)
EVERY = timedelta(hours=20)
SERVICE = os.environ.get("ATF_SERVICE", "atf-inventory")


class UpdateError(Exception):
    """Why it can't be updated (said as is)."""


def parse(v):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(latest, current):
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def kind():
    """'git' (a checkout: `update` works) or 'image' (Docker: rebuilt from outside)."""
    return "git" if (ROOT / ".git").exists() else "image"


def enabled():
    return (os.environ.get("ATF_UPDATE_CHECK") or "1").strip().lower() not in ("0", "false", "no", "off")


def fetch_latest(version="?"):
    """{'version', 'tag', 'url'} of the newest release; UpdateError if GitHub can't tell."""
    import urllib.request
    req = urllib.request.Request(LATEST_API, headers={"User-Agent": f"eforms-form1-server/{version}",
                                                      "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read(1024 * 1024).decode("utf-8"))
    except Exception as e:
        raise UpdateError(f"GitHub can't be reached ({e})")
    tag = str(data.get("tag_name") or "")
    if data.get("draft") or data.get("prerelease") or not parse(tag):
        raise UpdateError("no released version found on GitHub")
    return {"version": ".".join(map(str, parse(tag))), "tag": tag,
            "url": str(data.get("html_url") or f"https://github.com/{REPO}/releases/tag/{tag}")}


def _cache(data_dir):
    return Path(data_dir) / "update_check.json"


def _read_cache(data_dir):
    try:
        return json.loads(_cache(data_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}


def check(data_dir, version, force=False):
    """Ask GitHub when due (or forced) and remember the answer. Returns the release dict or None. Never raises."""
    c = _read_cache(data_dir)
    try:
        due = not c.get("latest") or datetime.now() - datetime.fromisoformat(c.get("checked", "")) >= EVERY
    except ValueError:
        due = True
    if not force and not due:
        return c["latest"]
    try:
        latest = fetch_latest(version)
    except UpdateError:
        return c.get("latest")
    try:
        tmp = _cache(data_dir).with_suffix(".tmp")
        tmp.write_text(json.dumps({"checked": datetime.now().isoformat(timespec="seconds"), "latest": latest}), encoding="utf-8")
        os.replace(tmp, _cache(data_dir))
    except OSError:
        pass
    return latest


def available(data_dir, version):
    """The release to mention (newer than this server), from the last check; None if none, unknown or switched off."""
    if not enabled():
        return None
    latest = _read_cache(data_dir).get("latest") or {}
    return latest if newer(latest.get("version"), version) else None


# ---------- the command ----------
def _git(*args, check=True):
    try:
        r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        raise UpdateError("Git isn't installed (or not on PATH)")
    except subprocess.TimeoutExpired:
        raise UpdateError("git took too long")
    if check and r.returncode:
        raise UpdateError(f"git {args[0]} failed: {((r.stderr or r.stdout).strip().splitlines() or [''])[-1]}")
    return r


def _run(*args):
    r = subprocess.run([sys.executable, *args], cwd=str(ROOT / "server"), capture_output=True, text=True)
    if r.returncode:
        raise UpdateError(f"{' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[-500:]}")


def _backup(data_dir, label):
    """A database backup before the code changes, made AS THE DATA FOLDER'S OWNER: `update` runs as root on a plain
    install, and a database opened by root can leave files the server's own user can't open."""
    owner = Path(data_dir).stat().st_uid if hasattr(os, "geteuid") else None
    if owner is not None and os.geteuid() == 0 and owner != 0 and shutil.which("runuser"):
        import pwd
        cmd = ["runuser", "-u", pwd.getpwuid(owner).pw_name, "--", sys.executable, "-m", "atf_server",
               "--data", str(data_dir), "backup", "--label", label]
        r = subprocess.run(cmd, cwd=str(ROOT / "server"), capture_output=True, text=True)
        if r.returncode:
            raise UpdateError(f"the backup failed, so nothing was changed: {(r.stderr or r.stdout).strip()[-300:]}")
        return (r.stdout.strip().splitlines() or [""])[-1]
    from . import db
    conn = db.connect(Path(data_dir) / "inventory.db")
    try:
        return str(db.backup(conn, Path(data_dir) / "backups", label=label))
    finally:
        conn.close()


def _restart():
    """Restart the systemd service if it's the one running this server. Returns what to tell the user."""
    if not shutil.which("systemctl") or subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode:
        return "Restart the server to run the new version."
    if subprocess.run(["systemctl", "restart", SERVICE]).returncode:
        return f"Couldn't restart it: run  systemctl restart {SERVICE}"
    for _ in range(20):
        time.sleep(0.5)
        if not subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode:
            return f"The server was restarted ({SERVICE})."
    return f"The server didn't come back up: see  journalctl -u {SERVICE} -n 30"


def apply(release, data_dir, version, say=print):
    """Move this checkout to `release`. UpdateError = nothing changed (or only packages were installed)."""
    tag = release["tag"]
    if kind() != "git":
        raise UpdateError("this server runs from a Docker image, which is rebuilt from outside: on the machine running "
                          "Docker, in the helper's folder:  git pull  then  cd server && docker compose up -d --build")
    if not os.access(ROOT / ".git", os.W_OK):
        raise UpdateError(f"run it as the user who owns {ROOT} (on a plain install: as root, or with sudo)")
    changed = [line[3:] for line in _git("status", "--porcelain", "--untracked-files=no").stdout.splitlines() if line.strip()]
    if changed:
        raise UpdateError("files were changed in this folder: " + ", ".join(changed[:5]) + (" …" if len(changed) > 5 else "")
                          + ". Undo that (git checkout -- <file>), then update.")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch not in ("main", "HEAD"):
        raise UpdateError(f"this folder is on branch {branch}, not main: git switch main, then update")
    say(f"Getting {tag}…")
    r = _git("fetch", "--quiet", "--force", REPO_URL, f"refs/tags/{tag}:refs/tags/{tag}", check=False)
    if r.returncode:
        raise UpdateError(f"couldn't download {tag}: {(r.stderr or r.stdout).strip()[:300]}")
    if _git("merge-base", "--is-ancestor", "HEAD", tag, check=False).returncode:
        raise UpdateError(f"{tag} can't simply replace this copy (it has commits of its own); update it by hand (git pull)")
    saved = _backup(data_dir, f"before-update-{version}-{datetime.now():%Y-%m-%d-%H%M%S}")
    say(f"Database backed up: {saved}")
    r = _git("show", f"{tag}:server/requirements.txt", check=False)
    new_reqs = "" if r.returncode else r.stdout.replace("\r\n", "\n")
    try:
        old_reqs = (ROOT / "server" / "requirements.txt").read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError:
        old_reqs = ""
    if new_reqs.strip() and new_reqs != old_reqs:  # packages first: if they can't be installed, the code stays as it is
        say("Installing the Python packages the new version needs…")
        fd, tmp = tempfile.mkstemp(prefix="atf-server-requirements-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(new_reqs)
            _run("-m", "pip", "install", "--quiet", "-r", tmp)
        except UpdateError as e:
            raise UpdateError(f"the packages the new version needs couldn't be installed, so the server was left on "
                              f"version {version} ({e})")
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if _git("merge", "--ff-only", "--quiet", tag, check=False).returncode:
        raise UpdateError(f"{tag} couldn't be put in place (git merge refused); the server is still on version {version}")
    try:
        _cache(data_dir).unlink()
    except OSError:
        pass
    return saved


def cmd(args, data_dir, version):
    """`update [--check] [--yes]`. Exit code: 0 = up to date or updated, 2 = a newer version exists (--check), 1 = not updated."""
    rel = check(data_dir, version, force=True)
    if not rel:
        print("Couldn't ask GitHub for the newest version (no internet from here?).", file=sys.stderr)
        return 1
    if not newer(rel["version"], version):
        print(f"This server has the newest version ({version}).")
        return 0
    print(f"Version {rel['version']} is available (this server has {version}): {rel['url']}")
    if args.check:
        return 2
    if not args.yes and input(f"Update this server to {rel['version']} now? [y/N] ").strip().lower() != "y":
        print("Not updated.")
        return 1
    try:
        apply(rel, data_dir, version)
    except UpdateError as e:
        print(f"Not updated: {e}", file=sys.stderr)
        return 1
    print(f"Updated to {rel['version']}. {_restart()}")
    return 0
