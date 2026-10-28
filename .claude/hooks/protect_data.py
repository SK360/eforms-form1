#!/usr/bin/env python3
"""Claude Code PreToolUse hook: development and testing never write the user's real records.

data/ (suppressors.csv = the only copy of the inventory, trust.yaml, the user's files), forms/ (the signed 5320.23
templates), out/submissions.csv (filing log) and out/approved/ (approval PDFs) are production data. On 2026-10-28 an
ad-hoc probe script imported atf, set only atf.DATA and called save_rows: atf.SUPP still pointed at the real
data/suppressors.csv, and the inventory was overwritten. So this hook refuses, for Claude's tool calls:
  - Edit/Write/NotebookEdit of a protected path;
  - shell commands that write, move or delete a protected path (redirection, cp/mv/rm/tee/sed -i, git clean -x ...);
  - Python run outside pytest that imports atf/phone/ui (inline, heredoc, or a script file): such code must be a
    pytest test (tests/conftest.py points every test at a temp folder), or a tools/docs script that sandboxes itself;
  - the app itself (./atf, src/atf.py) except read-only commands (--version, check-setup, fill-form1 --dry, fields).
Reading is never blocked. Reads the tool call as JSON on stdin; prints a deny decision, or nothing to allow.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2]).resolve()
PROTECTED = ("data", "forms", "out/submissions.csv", "out/approved")
# Scripts that import the app but point it at a temp folder first (reviewed): allowed to run.
SANDBOXED_SCRIPTS = ("docs/make_screenshots.py", "tools/scan_personal.py", "tests/")
READ_ONLY_APP = ("--version", "check-setup", "fields")
IMPORTS = re.compile(r"(^|[\s;,('\"])(import\s+(atf|phone|ui)\b|from\s+(atf|phone|ui)\s+import\b)|__import__\(\s*['\"](atf|phone|ui)['\"]",
                     re.M)
WRITERS_ANY_ARG = {"rm", "rmdir", "unlink", "shred", "truncate", "touch", "tee", "chmod", "chown", "chflags", "mv",
                   "xattr"}
WRITERS_LAST_ARG = {"cp", "rsync", "install", "ln", "ditto"}
WHY = ("The user's real records (data/, forms/, out/submissions.csv, out/approved/) are production data with no "
       "other copy; development and testing must never write them. ")


def protected(path):
    """True if the path (relative to the project or absolute) is inside a protected location."""
    p = path.strip().strip("'\"")
    if not p or p.startswith("-") or "://" in p:
        return False
    p = os.path.expanduser(p)
    full = Path(p) if os.path.isabs(p) else ROOT / p
    try:
        rel = Path(os.path.normpath(str(full))).relative_to(ROOT).as_posix()
    except ValueError:
        return False
    return any(rel == q or rel.startswith(q + "/") for q in PROTECTED)


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": WHY + reason}}))
    sys.exit(0)


def segments(command):
    """Simple commands of a shell line (split on ; && || | and newlines), each as a token list."""
    for part in re.split(r"&&|\|\||[;|\n]", command):
        try:
            toks = shlex.split(part, comments=True)
        except ValueError:
            toks = part.split()
        while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):  # VAR=value prefixes
            toks = toks[1:]
        if toks:
            yield toks


def check_bash(command):
    for m in re.finditer(r"(?<![<>&\d])\d?>>?\|?\s*([^\s;&|)]+)", command):  # > file, >> file, 2> file
        if protected(m.group(1)):
            deny(f"This command redirects output into {m.group(1)}.")
    if re.search(r"\bpython[\d.]*\b|\bpypy", command) and IMPORTS.search(command):
        deny("This command runs Python that imports the app (atf/phone/ui) outside pytest, where atf.SUPP and the "
             "other paths still point at the real folders. Write it as a pytest test using the `sandbox` or "
             "`configured` fixture instead.")
    for toks in segments(command):
        cmd = os.path.basename(toks[0])
        args = [t for t in toks[1:] if not t.startswith("-")]
        if cmd == "sudo" and len(toks) > 1:
            toks = toks[1:]
            cmd, args = os.path.basename(toks[0]), [t for t in toks[1:] if not t.startswith("-")]
        if cmd in WRITERS_ANY_ARG and any(protected(a) for a in args):
            deny(f"`{cmd}` would change or remove a protected file.")
        if cmd in WRITERS_LAST_ARG and args and protected(args[-1]):
            deny(f"`{cmd}` would write into a protected location.")
        if cmd in ("sed", "perl", "gsed") and any(t.startswith("-i") or t == "--in-place" for t in toks) \
                and any(protected(a) for a in args):
            deny(f"`{cmd} -i` would edit a protected file in place.")
        if cmd == "git" and len(toks) > 1:
            sub = toks[1]
            if sub == "clean" and any(t.startswith("-") and ("x" in t or "X" in t) for t in toks[2:]):
                deny("`git clean -x/-X` deletes ignored files, which include data/ and forms/.")
            if sub in ("checkout", "restore", "rm", "stash") and any(protected(a) for a in args[1:]):
                deny(f"`git {sub}` on a protected path.")
        if re.match(r"^python[\d.]*$|^pypy", cmd):
            rest = toks[1:]
            if "-m" in rest and rest[rest.index("-m") + 1:rest.index("-m") + 2] == ["pytest"]:
                continue
            script = next((t for t in rest if t.endswith(".py")), None)
            if script:
                rel = _rel(script)
                if rel == "src/atf.py":
                    check_app(rest[rest.index(script) + 1:])
                    continue
                if rel and rel.startswith(SANDBOXED_SCRIPTS):
                    continue
                try:
                    text = Path(os.path.expanduser(script) if os.path.isabs(os.path.expanduser(script))
                                else ROOT / script).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                if IMPORTS.search(text):
                    deny(f"{script} imports the app (atf/phone/ui) outside pytest, where atf.SUPP and the other paths "
                         "still point at the real folders. Write it as a pytest test using the `sandbox` or "
                         "`configured` fixture instead.")
        if cmd in ("atf", "atf.cmd") and (toks[0].startswith(("./", ".\\")) or _rel(toks[0]) in ("atf", "atf.cmd")):
            check_app(toks[1:])


def _rel(path):
    p = os.path.expanduser(path)
    full = Path(p) if os.path.isabs(p) else ROOT / p
    try:
        return Path(os.path.normpath(str(full))).relative_to(ROOT).as_posix()
    except ValueError:
        return None


def check_app(args):
    """The app on the real folders: only read-only commands."""
    if args and (args[0] in READ_ONLY_APP or (args[0] == "fill-form1" and "--dry" in args)):
        return
    deny("Running the app (./atf or src/atf.py) here works on the real records. Only read-only commands are allowed "
         "(--version, check-setup, fill-form1 --dry, fields). Ask the user to run anything else themselves, or "
         "test it in pytest.")


def main():
    try:
        call = json.load(sys.stdin)
    except ValueError:
        return
    tool, given = call.get("tool_name", ""), call.get("tool_input") or {}
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = given.get("file_path") or given.get("notebook_path") or ""
        if protected(path):
            deny(f"{tool} of {path} is not allowed. If the user asked for a change to their records, tell them "
                 "exactly what to change and let them do it (the app's menu, or their own editor).")
    elif tool == "Bash":
        check_bash(given.get("command") or "")


if __name__ == "__main__":
    main()
