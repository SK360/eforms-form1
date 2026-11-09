"""New versions: tell the user one is out, and update the helper from the menu (or `./atf update`). Stdlib only.

Check: GitHub's "latest release" of SK360/eforms-form1 (drafts and pre-releases never count), at most once a day,
in a background thread at menu start; the answer is kept in out/update_check.json, so the home screen reads a file
and never waits for the network. Offline or GitHub not answering = nothing shown. Off with `update_check: false` in
data/trust.yaml (Settings > New versions). Nothing personal is sent: one anonymous GET with a generic User-Agent.

Update (always the user's yes; never while a row is pending or a filing result waits for the inventory server):
  - a git checkout (a .git folder): `git fetch <repo> tag vX.Y.Z` + `git merge --ff-only vX.Y.Z` (released code only,
    never main's newer commits). Refused with local changes to tracked files, on another branch, or with local
    commits (not a fast-forward). data/ forms/ out/ .venv/ are gitignored, so git never touches them.
  - a ZIP install: that tag's source ZIP from github.com into memory, checked (no absolute or .. paths, no links, and
    src/atf.py says the version asked for), then each program file that differs is replaced; the old one is copied to
    .update-backup/<old version>/ first, and any failure puts every file back. Personal folders (data/, forms/,
    out/, .browser-profile/, .venv/) are never written, even if a ZIP had them. Files a release deleted stay.
Packages first: when the release's requirements.txt differs, pip install -r (the new list) + playwright install
chromium run BEFORE any program file changes, so a failed install leaves this folder as it was (never new code on
old packages, which might not start). Then the menu restarts itself (Windows: the user starts it again). A different inventory-server API in the new remote.py = a reminder to update the server.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import atf

REPO = "SK360/eforms-form1"
REPO_URL = f"https://github.com/{REPO}.git"  # git fetches the tag from here (a fork's own "origin" doesn't matter)
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ZIP_URL = "https://github.com/" + REPO + "/archive/refs/tags/{tag}.zip"
ZIP_HOSTS = ("github.com", "codeload.github.com")  # where the ZIP may come from (github.com redirects to codeload)
APP = atf.ROOT  # the helper's folder (tests point it at a temp folder)
EVERY = timedelta(hours=20)  # check at most this often
MAX_ZIP = 60 * 1024 * 1024
# Never written by an update, whatever a ZIP contains: the user's records, their browser session, the Python env.
PERSONAL = ("data", "out", "forms", ".browser-profile", ".venv", ".git", ".update-backup", "CLAUDE.local.md")
SKIP_PARTS = (".venv", "__pycache__", ".pytest_cache", ".DS_Store")


class UpdateError(Exception):
    """Why an update can't be done (said to the user as is)."""


# ---------- versions ----------
def parse(v):
    """'v1.2.10' -> (1, 2, 10); None if it isn't a plain release number."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(latest, current=None):
    a, b = parse(latest), parse(current or atf.__version__)
    return bool(a and b and a > b)


# ---------- the check ----------
def _get(url, limit=1024 * 1024, timeout=5):
    """GET over HTTPS -> (bytes, final URL). The only network access here (tests replace it)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": f"eforms-form1-helper/{atf.__version__}",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(limit + 1)
            final = r.geturl()
    except Exception as e:  # offline, DNS, timeout, HTTP error
        raise UpdateError(f"GitHub can't be reached ({e})")
    if len(body) > limit:
        raise UpdateError("the answer from GitHub was larger than expected")
    return body, final


def fetch_latest():
    """{'version': '1.2.5', 'tag': 'v1.2.5', 'notes': ..., 'url': ...} for the newest release; UpdateError if unknown."""
    body, _ = _get(LATEST_API)
    try:
        r = json.loads(body.decode("utf-8"))
    except ValueError:
        raise UpdateError("GitHub's answer couldn't be read")
    tag = str(r.get("tag_name") or "")
    if r.get("draft") or r.get("prerelease") or not parse(tag):
        raise UpdateError("no released version found on GitHub")
    return {"version": ".".join(map(str, parse(tag))), "tag": tag, "notes": str(r.get("body") or "")[:20000],
            "url": str(r.get("html_url") or f"https://github.com/{REPO}/releases/tag/{tag}")}


def _cache():
    return atf.OUT / "update_check.json"  # looked up at call time (tests sandbox atf.OUT)


def _read_cache():
    try:
        return json.loads(_cache().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(d):
    try:
        atf.OUT.mkdir(parents=True, exist_ok=True)
        tmp = _cache().with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, _cache())  # whole file or nothing: the home screen may read it meanwhile
    except Exception:
        pass


def enabled(trust=None):
    """The start-up check is on unless `update_check: false` (an absent or unreadable trust.yaml = on)."""
    try:
        t = trust if trust is not None else atf.load_trust()
    except Exception:
        t = {}
    return (t or {}).get("update_check", True) is not False


def _due(c):
    try:
        return not c.get("latest") or datetime.now() - datetime.fromisoformat(c.get("checked", "")) >= EVERY
    except ValueError:
        return True


def check(force=False):
    """Ask GitHub (unless asked within EVERY and not forced) and remember the answer. Returns the release dict, or
    None if GitHub couldn't tell. Never raises."""
    c = _read_cache()
    if not force and not _due(c):
        return c["latest"]
    try:
        latest = fetch_latest()
    except UpdateError:
        return c.get("latest")  # last answer known (if any); asked again next time
    _write_cache({"checked": datetime.now().isoformat(timespec="seconds"), "latest": latest})
    return latest


def check_in_background():
    """At menu start, when a check is due: ask GitHub in a thread (the menu waits for it 2 s at most). Returns the
    thread, or None (off, or asked recently)."""
    if not enabled() or not _due(_read_cache()):
        return None
    t = threading.Thread(target=check, daemon=True, name="update-check")
    t.start()
    return t


def available():
    """The release to offer (newer than this one), from the last check; None if none, unknown or the check is off."""
    if not enabled():
        return None
    latest = _read_cache().get("latest") or {}
    return latest if newer(latest.get("version")) else None


# ---------- updating ----------
def blockers(rows=None):
    """Reasons not to update now: a pending row (its result must be resolved by the code that filed it) or a filing
    result that hasn't reached the inventory server yet."""
    out = []
    try:
        rows = atf.load_rows() if rows is None else rows
        pending = [r["serial_number"] for r in rows if r["status"] == "pending"]
        if pending:
            out.append(f"pending row{'s' if len(pending) > 1 else ''} to resolve first: {', '.join(pending)}")
    except Exception:
        pass
    try:
        if atf.outbox():
            out.append("a filing result hasn't reached your inventory server yet")
    except Exception:
        pass
    return out


def kind():
    """'git' (a git checkout) or 'zip' (unzipped release)."""
    return "git" if (APP / ".git").exists() else "zip"


def _git(*args, check=True):
    try:
        r = subprocess.run(["git", *args], cwd=str(APP), capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        raise UpdateError("this folder was set up with Git, but Git isn't installed (or not on PATH)")
    except subprocess.TimeoutExpired:
        raise UpdateError("git took too long")
    if check and r.returncode:
        last = ((r.stderr or r.stdout).strip().splitlines() or [""])[-1]
        raise UpdateError(f"git {args[0]} failed: {last}")
    return r


def _git_prepare(tag):
    """Check this checkout can take the tag and fetch it (nothing in the folder changes yet). Returns the tag's
    requirements.txt (LF; b"" if it has none)."""
    changed = [line[3:] for line in _git("status", "--porcelain", "--untracked-files=no").stdout.splitlines() if line.strip()]
    if changed:
        raise UpdateError("files of the helper were changed in this folder: " + ", ".join(changed[:5])
                          + (" …" if len(changed) > 5 else "") + ". Undo that (or commit it), then update.")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch not in ("main", "HEAD"):  # HEAD = a tag checked out (detached)
        raise UpdateError(f"this folder is on branch {branch}, not main: switch back (git switch main) to update")
    r = _git("fetch", "--quiet", "--force", REPO_URL, f"refs/tags/{tag}:refs/tags/{tag}", check=False)
    if r.returncode:
        raise UpdateError(f"couldn't download {tag} with git: {(r.stderr or r.stdout).strip()[:300]}")
    if _git("merge-base", "--is-ancestor", "HEAD", tag, check=False).returncode:
        raise UpdateError(_NOT_FF.format(tag=tag))
    r = _git("show", f"{tag}:requirements.txt", check=False)
    return b"" if r.returncode else r.stdout.encode("utf-8").replace(b"\r\n", b"\n")


_NOT_FF = ("{tag} can't simply replace this copy (it has commits of its own); update it by hand (git pull) or start "
           "from a fresh copy")


def _git_merge(tag):
    if _git("merge", "--ff-only", "--quiet", tag, check=False).returncode:
        raise UpdateError(_NOT_FF.format(tag=tag))


def _personal(rel):
    parts = Path(rel).parts
    return not parts or parts[0] in PERSONAL or any(p in SKIP_PARTS for p in parts)


def _read_zip(data, version):
    """{relative path: (bytes, executable)} of a GitHub source ZIP (one top folder), checked; UpdateError if odd."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("the download isn't a ZIP file")
    files = {}
    for info in z.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir():
            continue
        parts = name.split("/")
        if name.startswith("/") or ":" in parts[0] or ".." in parts or len(parts) < 2:
            raise UpdateError(f"the ZIP has an unexpected path ({name}): not used")
        mode = info.external_attr >> 16
        if (mode & 0o170000) == 0o120000:  # a symbolic link
            continue
        rel = "/".join(parts[1:])  # drop GitHub's top folder (eforms-form1-1.2.5/)
        if _personal(rel):
            continue
        files[rel] = (z.read(info), bool(mode & 0o111))
    atf_py = files.get("src/atf.py", (b"", False))[0].decode("utf-8", "replace")
    m = re.search(r'^__version__ = "([^"]+)"', atf_py, re.M)
    if not m or m.group(1) != version:
        raise UpdateError(f"the download doesn't contain version {version} (it says {m.group(1) if m else 'nothing'})")
    return files


def _zip_download(tag, version):
    """The tag's program files, downloaded and checked (nothing in the folder changes yet)."""
    data, final = _get(ZIP_URL.format(tag=tag), limit=MAX_ZIP, timeout=120)
    from urllib.parse import urlparse
    if urlparse(final).hostname not in ZIP_HOSTS or urlparse(final).scheme != "https":
        raise UpdateError(f"the download came from an unexpected place ({urlparse(final).hostname})")
    return _read_zip(data, version)


def _zip_write(files):
    backup = APP / ".update-backup" / atf.__version__
    saved, created = [], []
    try:
        for rel, (body, executable) in sorted(files.items()):
            dest = APP / rel
            if dest.exists() and dest.read_bytes() == body:
                continue  # unchanged (on Windows this also leaves the running atf.cmd alone)
            if dest.exists():
                keep = backup / rel
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, keep)
                saved.append(rel)
            else:
                created.append(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".update-tmp")
            tmp.write_bytes(body)
            if os.name != "nt":
                mode = dest.stat().st_mode & 0o777 if dest.exists() else 0o644
                os.chmod(tmp, mode | (0o111 if executable else 0))
            os.replace(tmp, dest)
    except Exception as e:
        for rel in saved:  # put every replaced file back, remove the new ones
            try:
                shutil.copy2(backup / rel, APP / rel)
            except Exception:
                pass
        for rel in created:
            try:
                (APP / rel).unlink()
            except Exception:
                pass
        for tmp in APP.rglob("*.update-tmp"):
            try:
                tmp.unlink()
            except Exception:
                pass
        raise UpdateError(f"the update couldn't be written ({e}); the files were put back as they were")
    return len(saved) + len(created)


def _api(text):
    m = re.search(r"^API = (\d+)", text or "", re.M)
    return int(m.group(1)) if m else None


def _read(rel):
    """A program file's bytes, line endings made LF (a CRLF copy of the same text isn't a change); b"" if missing."""
    try:
        return (APP / rel).read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return b""


def _run(*args):
    """Run a command with this Python (pip, playwright); output kept for the error message."""
    r = subprocess.run([sys.executable, *args], cwd=str(APP), capture_output=True, text=True)
    if r.returncode:
        raise UpdateError(f"{' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[-500:]}")


def _install_packages(reqs, say):
    """pip install the new version's requirements (its text, from a temp file: this folder's own requirements.txt
    is still the old one) and Chromium. UpdateError if either fails; the caller hasn't changed any program file."""
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="atf-requirements-", suffix=".txt")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(reqs)
        say("Installing the Python packages the new version needs…")
        _run("-m", "pip", "install", "--quiet", "-r", tmp)
        say("Updating the browser (Chromium)…")
        _run("-m", "playwright", "install", "chromium")
    except UpdateError as e:
        raise UpdateError(f"the packages the new version needs couldn't be installed, so this folder was left on "
                          f"version {atf.__version__} ({e}). Check your internet connection and try again.")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def apply(release, say=lambda text: None):
    """Update this folder to `release` (from check()). Returns {'version', 'how', 'packages', 'server_api'}:
    packages = requirements were installed again; server_api = the new version speaks another inventory-server API
    (the server needs updating too). UpdateError (nothing changed, or put back) if it can't."""
    version, tag = release["version"], release.get("tag") or f"v{release['version']}"
    if not newer(version):
        raise UpdateError(f"version {version} isn't newer than this one ({atf.__version__})")
    problems = blockers()
    if problems:
        raise UpdateError("not now: " + "; ".join(problems))
    reqs, api = _read("requirements.txt"), _api(_read("src/remote.py").decode("utf-8", "replace"))
    how = kind()
    if how == "git":
        say(f"Getting {tag} with git…")
        new_reqs = _git_prepare(tag)
    else:
        say(f"Downloading {tag} from GitHub…")
        files = _zip_download(tag, version)
        new_reqs = files.get("requirements.txt", (reqs, False))[0].replace(b"\r\n", b"\n")
    # Packages BEFORE the program files: if they can't be installed, nothing here has changed yet.
    packages = bool(new_reqs.strip()) and new_reqs != reqs
    if packages:
        _install_packages(new_reqs, say)
    say(f"Putting version {version} in place…")
    if how == "git":
        _git_merge(tag)
    else:
        _zip_write(files)
    new_api = _api(_read("src/remote.py").decode("utf-8", "replace"))
    _write_cache({})  # asked again next start, by the new version
    return {"version": version, "how": how, "packages": packages,
            "server_api": bool(atf.server_settings()) and new_api is not None and new_api != api}


def restart():
    """Start the updated menu in place of this one (POSIX). Windows: returns False; the user starts it again."""
    if os.name == "nt":
        return False
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, str(APP / "src" / "ui.py")])
    return True


# ---------- CLI: ./atf update [--check] [--yes] ----------
def cmd_update(args):
    rel = check(force=True)
    if not rel:
        sys.exit("Couldn't ask GitHub for the newest version (offline?).")
    if not newer(rel["version"]):
        print(f"You have the newest version ({atf.__version__}).")
        return
    print(f"Version {rel['version']} is available (you have {atf.__version__}): {rel['url']}")
    if args.check:
        sys.exit(2)  # 2 = a newer version exists (scripts can test it)
    print("\n" + rel["notes"].strip() + "\n")
    if not args.yes and input(f"Update this folder ({kind()} install) to {rel['version']}? [y/N] ").strip().lower() != "y":
        print("Not updated.")
        return
    try:
        res = apply(rel, say=print)
    except UpdateError as e:
        sys.exit(f"Not updated: {e}")
    print(f"Updated to {res['version']}." + (" Packages and Chromium were updated too." if res["packages"] else ""))
    if res["server_api"]:
        print("Your inventory server needs this update too (server/README.md): until then this helper can't use it.")
