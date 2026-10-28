"""Check my setup (atf.check_setup, Settings > Check my setup, CLI check-setup) on fake files; no eForms."""
import pytest
import yaml

from conftest import FIXTURE, add_other_rp, atf, make_individual, pick, queue, ui

QUIT = lambda kw: "quit"  # noqa: E731
EFT, JPG, PDF = b"1.001:249\x1d1.002:0502", b"\xff\xd8\xff\xe0\x00\x10JFIF", b"%PDF-1.4\n"


@pytest.fixture
def healthy(configured, monkeypatch):
    """Fake settings whose files look like the real thing, a browser that's installed, a folder outside the cloud."""
    f = configured / "files"
    for name, head in (("prints.eft", EFT), ("me.jpg", JPG), ("trust.pdf", PDF), ("printer.jpg", JPG)):
        (f / name).write_bytes(head + b"rest")
    monkeypatch.setattr(atf, "_chromium_path", lambda: str(FIXTURE))
    monkeypatch.setattr(atf, "ROOT", configured)
    return configured


def by_label(res):
    return {label: (level, detail) for level, label, detail, _ in res}


def test_all_good(healthy):
    queue(("S1", "9"))
    res = by_label(atf.check_setup())
    assert list(res) == ["Python", "Browser (Chromium)", "Helper folder", "Settings", "Your fingerprint file", "Your photo",
                         "Trust document", "Supporting document 1", "Your 5320.23 template", "Inventory"]
    assert {level for level, _ in res.values()} == {"ok"}
    assert res["Settings"][1] == "complete (a trust)" and res["Your 5320.23 template"][1].startswith("stamps correctly")
    assert res["Your fingerprint file"][1] == "found, 1 KB" and res["Inventory"][1] == "1 suppressor"


def test_problems_are_found(healthy, monkeypatch):
    f = healthy / "files"
    t = yaml.safe_load((atf.DATA / "trust.yaml").read_text(encoding="utf-8"))
    del t["cleo"]["official"]
    t["docs"].append({"path": str(f / "cloud.jpg"), "description": "X"})
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t), encoding="utf-8")
    (f / "me.jpg").unlink()                                   # missing
    (f / "trust.pdf").write_bytes(b"")                        # empty
    (f / "prints.eft").write_bytes(b"not fingerprints")       # wrong kind
    (f / "cloud.jpg").write_bytes(JPG + b"x" * 4321)          # online-only (size marks it here)
    monkeypatch.setattr(atf, "_online_only", lambda st: st.st_size == len(JPG) + 4321)
    (atf.FORMS / "5320.23.pdf").write_bytes(PDF + b"not a real template")
    monkeypatch.setattr(atf, "_chromium_path", lambda: (_ for _ in ()).throw(RuntimeError("Executable doesn't exist")))
    monkeypatch.setattr(atf, "ROOT", healthy / "OneDrive - Home" / "eForms")
    queue(("S1", "9", "pending"))
    res = by_label(atf.check_setup())
    assert res["Settings"] == ("fail", "missing CLEO official's name (cleo.official)")
    assert res["Your photo"] == ("fail", "not found (choose it again in Settings)")
    assert res["Trust document"] == ("fail", "the file is empty")
    assert res["Your fingerprint file"] == ("warn", "doesn't look like an EBTS fingerprint file (they start with 1.001:)")
    assert res["Supporting document 2"] == ("warn", "online-only (OneDrive/iCloud): set it to always stay on this device")
    assert res["Your 5320.23 template"][0] == "fail"
    assert res["Browser (Chromium)"][0] == "fail" and "run the installer again" in res["Browser (Chromium)"][1]
    assert res["Helper folder"][0] == "warn" and "inside OneDrive" in res["Helper folder"][1]
    assert res["Inventory"] == ("warn", "1 pending: resolve it from the menu before filing more")


def test_other_responsible_persons_are_numbered(healthy):
    add_other_rp()                                            # their .eft and photo are just "x"
    res = by_label(atf.check_setup())
    assert res["Responsible person 2: fingerprint file"][0] == "warn" and res["Responsible person 2: photo"][0] == "warn"
    assert res["Responsible person 2: 5320.23 template"] == ("ok", "stamps correctly (test serial, today's date)")
    assert not any("Jane" in k or "Roe" in k for k in res)


def test_individual_has_no_template_or_trust_document(healthy):
    make_individual()
    res = by_label(atf.check_setup())
    assert res["Settings"] == ("ok", "complete (an individual)")
    assert "Trust document" not in res and "Your 5320.23 template" not in res


def test_menu_screen_shows_no_paths_or_names(healthy, answers, screen):
    add_other_rp()
    answers(pick("Settings"), pick("Check my setup"), QUIT)
    ui.menu()
    out = screen.getvalue()
    assert "Check my setup" in out and "Responsible person 2: fingerprint file" in out and "2 to look at" in out
    assert str(healthy) not in out and "Jane" not in out and "DOE FAMILY TRUST" not in out.upper()


def test_individual_settings_menu_offers_the_check(healthy, answers, screen):
    make_individual()
    answers(pick("Settings"), pick("Check my setup"), QUIT)
    ui.menu()
    assert "Everything a filing needs is in place" in screen.getvalue()


def test_cli_shows_paths_and_fails(healthy, capsys):
    (healthy / "files" / "me.jpg").unlink()
    with pytest.raises(SystemExit) as e:
        atf.cmd_check_setup(None)
    out = capsys.readouterr().out
    assert e.value.code == 1 and "FIX  Your photo: not found" in out and str(healthy / "files" / "me.jpg") in out
