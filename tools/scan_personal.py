#!/usr/bin/env python3
"""Personal-data scan for this PUBLIC repository: run it before every commit (the git hooks in .githooks/ do).

Compares text with the values in YOUR records: data/trust.yaml (trust name and address, names, dates of birth,
emails, addresses, the CLEO, file names), data/suppressors.csv and data/sbrs.csv (serials, application numbers,
eForm IDs, control numbers, designs; also their .moved-to-server-<date> copies and, with an inventory server, the
app's data/server_cache.json) and out/submissions.csv. Also flags SSN-shaped numbers and email addresses.
It never prints a value it finds: only where it is and what kind of value it is.

  python tools/scan_personal.py              what `git commit` would add (staged changes)   <- pre-commit hook
  python tools/scan_personal.py --all        every tracked file as it is now
  python tools/scan_personal.py FILE ...     these files (a commit message, release notes)  <- commit-msg hook

Exits 1 if anything is found. Images and PDFs can't be read here: docs/make_screenshots.py scans each screenshot's
text before saving it. Turn the hooks on once per checkout:  git config core.hooksPath .githooks
"""
import csv
import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# trust.yaml keys whose values say nothing about you on their own (or are shared defaults in the repo)
GENERIC_KEYS = {"sex", "race", "title", "country", "state", "description", "trust_document_desc", "model",
                "overall_length", "applicant_type", "answers", "suffix"}
LABELS = {"name": "a name", "first": "a name", "middle": "a name", "last": "a name", "first_name": "a name",
          "middle_name": "a name", "last_name": "a name", "official": "the CLEO official's name",
          "department": "the CLEO agency", "dob": "a date of birth", "email": "an email address",
          "street": "a street address", "zip": "a ZIP code", "city": "a city", "county": "a county",
          "phone": "a phone number", "upin": "a UPIN", "serial_number": "one of your serials",
          "serial": "one of your serials", "application_number": "an application number", "eform_id": "an eForm ID",
          "control_number": "a control number", "design": "a design name", "file": "a file name"}
CSV_COLS = {"data/suppressors.csv": ("serial_number", "application_number", "eform_id", "control_number", "design"),
            "data/sbrs.csv": ("serial_number", "eform_id", "control_number"),
            "out/submissions.csv": ("serial_number", "application_number")}
FAKE_SSNS = {"123-45-6789"}  # the made-up one in tests/fixtures
FILE_KEYS = {"eft", "photo", "template", "trust_document", "path"}
# A file name made only of these words (and digits) is too common to flag ("photo.jpg", "trust.pdf"); a name in it is
# caught as a name anyway.
GENERIC_FILE_WORDS = {"photo", "photos", "pic", "picture", "headshot", "image", "img", "me", "my", "mine", "print",
                      "prints", "fingerprint", "fingerprints", "finger", "eft", "ebts", "trust", "document", "doc",
                      "docs", "pdf", "jpg", "jpeg", "png", "printer", "3d", "scan", "scanned", "signed", "form",
                      "template", "copy", "final", "new", "old", "id", "the", "of", "and", "file", "files", "supporting",
                      "tool", "tools", "material", "materials", "filament", "spool", "resin", "suppressor", "rp", "atf", "questionnaire", "full", "page", "pages"}
SAFE_EMAIL = re.compile(r"@(example\.(com|org|net)|users\.noreply\.github\.com|anthropic\.com)$", re.I)


def personal_values(root=ROOT):
    """[(value, what it is, where it comes from)] from the records in `root` (never printed)."""
    import yaml
    found = {}

    def add(value, key, source):
        v = str(value).strip()
        if len(v) >= 3 and not re.fullmatch(r"\d{1,2}(\.\d+)?", v) and v.lower() not in ("n/a", "none", "yes", "no"):
            found.setdefault(v.lower(), (v, LABELS.get(key, "a value from your settings"), source))

    def walk(v, path):
        key = path.rsplit(".", 1)[-1].split("[")[0]
        if key in GENERIC_KEYS:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{path}.{k}" if path else str(k))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]")
        elif v is not None and not isinstance(v, bool):
            s = str(v)
            if key in FILE_KEYS:  # a file: the full path (folders can carry your user name), and a telling file name
                where = f"data/trust.yaml {path} (file)"
                add(s, "file", where)
                name = re.split(r"[\\/]", s)[-1]
                if set(re.findall(r"[a-z]+", Path(name).stem.lower())) - GENERIC_FILE_WORDS:
                    add(name, "file", where)
                    add(Path(name).stem, "file", where)
            else:
                add(s, key, f"data/trust.yaml {path}")
    settings = root / "data" / "trust.yaml"
    if settings.exists():
        walk(yaml.safe_load(settings.read_text(encoding="utf-8-sig")) or {}, "")
    for rel, cols in CSV_COLS.items():
        # also the copies a move to the inventory server leaves (suppressors.moved-to-server-<date>.csv) or replaces
        p = root / rel
        for q in [p] + sorted(p.parent.glob(f"{p.stem}.*{p.suffix}")):
            if q.exists():
                with q.open(newline="", encoding="utf-8-sig") as f:
                    for r in csv.DictReader(f):
                        for c in cols:
                            if (r.get(c) or "").strip():
                                add(r[c], c, f"{q.relative_to(root).as_posix()} {c}")
    # with an inventory server, the app's copy of what the server last sent (data/server_cache.json)
    cache = root / "data" / "server_cache.json"
    if cache.exists():
        import json
        saved = json.loads(cache.read_text(encoding="utf-8"))
        for table, rel in (("suppressors", "data/suppressors.csv"), ("sbrs", "data/sbrs.csv")):
            for r in saved.get(table) or []:
                for c in CSV_COLS[rel]:
                    if str(r.get(c) or "").strip():
                        add(str(r[c]), c, f"data/server_cache.json {c}")
    return list(found.values())


def scan_text(text, values):
    """[(line number, what, from where)] for each personal value or SSN/email found in `text`."""
    hits = []
    pats = [(re.compile(r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])", re.I), label, source)
            for v, label, source in values]
    for n, line in enumerate(text.splitlines(), 1):
        for rx, label, source in pats:
            if rx.search(line):
                hits.append((n, label, source))
        for ssn in re.findall(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)", line):
            if ssn not in FAKE_SSNS:
                hits.append((n, "an SSN-shaped number", "pattern"))
        for mail in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", line):
            if not SAFE_EMAIL.search(mail):
                hits.append((n, "an email address", "pattern"))
    return hits


def svg_text(svg):
    """The visible text of a rich SVG export (for docs/make_screenshots.py), row by row, each piece at its column (rich
    leaves out plain spaces between colored pieces), so a value printed in several colors still reads as one."""
    pieces = []
    for attrs, body in re.findall(r"<text([^>]*)>(.*?)</text>", svg, re.S):
        num = lambda name: float((re.search(name + r'="([\d.]+)"', attrs) or [0, 0])[1])  # noqa: E731
        text = html.unescape(re.sub(r"<[^>]+>", "", body)).replace("\xa0", " ")
        if text:
            pieces.append((num("y"), num(r"\bx"), num("textLength") / len(text), text))
    width = min((w for *_, w, _ in pieces if w), default=1)  # one character (monospace)
    rows = {}
    for y, x, _, text in pieces:
        line = rows.setdefault(y, [])
        col = int(round(x / width))
        line.extend(" " * (col - len(line)))
        line[col:col + len(text)] = text
    return "\n".join("".join(chars) for _, chars in sorted(rows.items()))


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def _staged(root):
    """{path: [(line number in the new file, text)]} of the lines a commit would add, and binary files skipped."""
    out, skipped, path, line = {}, [], None, 0
    for raw in _git(root, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR").splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("Binary files "):
            skipped.append(raw.split(" and b/", 1)[-1].rsplit(" differ", 1)[0])
        elif raw.startswith("@@"):
            line = int(re.search(r"\+(\d+)", raw).group(1))
        elif raw.startswith("+") and path:
            out.setdefault(path, []).append((line, raw[1:]))
            line += 1
    return out, skipped


def _read(p):
    data = p.read_bytes()
    return None if b"\0" in data[:8000] else data.decode("utf-8", "replace")


def main(argv=None, root=ROOT):
    argv = sys.argv[1:] if argv is None else argv
    values = personal_values(root)
    found, skipped = [], []
    if argv and argv[0] == "--all":
        files = [f for f in _git(root, "ls-files", "-z").split("\0") if f]
        for f in files:
            text = _read(root / f) if (root / f).is_file() else None
            if text is None:
                skipped.append(f)
                continue
            found += [(f, n, label, src) for n, label, src in scan_text(text, values)]
    elif argv:
        for f in argv:
            text = _read(Path(f)) or ""
            found += [(f, n, label, src) for n, label, src in scan_text(text, values)]
    else:
        lines, skipped = _staged(root)
        for f, numbered in lines.items():
            for n, text in numbered:
                found += [(f, n, label, src) for _, label, src in scan_text(text, values)]
    what = f"{len(values)} values from your records" if values else "no records here (data/ is empty): patterns only"
    if skipped:
        print(f"scan_personal: not read (binary): {', '.join(skipped[:8])}{' …' if len(skipped) > 8 else ''}")
    if not found:
        print(f"scan_personal: ✔ nothing personal found ({what})")
        return 0
    print(f"scan_personal: ✖ {len(found)} possible personal value(s) ({what}). The values aren't shown:")
    for f, n, label, src in found:
        print(f"  {f}:{n}  {label}  (matches {src})")
    print("Remove them and try again. If one is a false alarm, check it yourself, then commit with --no-verify.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
