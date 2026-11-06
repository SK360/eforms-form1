"""CLEO notification copies, all on FAKE data: the 5320.23's CLEO copy (stamped without a serial), the Form 1's CLEO
copy (the "CLEO Copy" pages of a submitted form's PDF, fetched read-only from a fake dashboard) and the mail packets."""
import shutil

import pymupdf
import yaml

from conftest import CLEO_FIXTURE, FIXTURE, FIXTURE_SSN, TICKED, TODAY, add_other_rp, atf, pick, queue, ui
from test_approvals import approved_pdf, dashboard  # noqa: F401  (the fake eForms dashboard fixture)

QUIT = lambda kw: "quit"  # noqa: E731


def text(path):
    return "\n".join(pg.get_text() for pg in pymupdf.open(path))


def set_cleo(person=None):
    shutil.copy(CLEO_FIXTURE, atf.cleo_template(person))


def submitted_pdf(serial, caliber="22"):
    """A stand-in for a SUBMITTED form's "View PDF": the ATF copy (item 4 at eForms' positions, so the serial can be
    read), an instructions page, then the CLEO copy, which (like eForms') leaves the serial out."""
    doc = pymupdf.open(stream=approved_pdf(serial=serial, caliber=caliber, approved=False, date=""))
    doc[0].insert_text((280, 20), "ATF Copy", fontname="tiro", fontsize=9)
    doc.new_page(width=612, height=792).insert_text((40, 80), "ATF Form 5320.1 (Form 1) instructions", fontname="tiro", fontsize=9)
    cleo = doc.new_page(width=612, height=792)
    cleo.insert_text((40, 20), "Previous Editions Are Obsolete", fontname="tiro", fontsize=9)
    cleo.insert_text((280, 40), "CLEO Copy", fontname="tiro", fontsize=9)
    cleo.insert_text((364, 384), "c. Caliber or gauge", fontname="tiro", fontsize=8)
    cleo.insert_text((398, 413), f"CAL-{caliber}", fontname="helv", fontsize=7)
    return doc.tobytes()


def filed(*rows):
    """filed(("S1", "45ACP", "11/06/2026"), ...): rows filed on those dates."""
    atf.save_rows([{"serial_number": s, "caliber": c, "status": "filed", "application_number": "9", "filed_date": d} for s, c, d in rows])


def test_copy_kind():
    assert atf.copy_kind(FIXTURE) == "ATF" and atf.copy_kind(CLEO_FIXTURE) == "CLEO"


def test_cleo_copy_is_stamped_without_the_serial(configured):
    set_cleo()
    res = atf.stamp_cleo_all({"serial_number": "S77", "caliber": "45ACP"}, TODAY)
    assert [(n, p.name, probs) for n, p, probs in res] == [(1, "5320.23_S77.cleo.pdf", [])]
    path = res[0][1]
    assert path.parent == atf.OUT / "cleo"
    t = text(path)
    assert "45ACP" in t and TODAY in t and "PAT EXAMPLE, CHIEF OF POLICE" in t
    assert "S77" not in t and "TEMPLATE" not in t and FIXTURE_SSN not in t
    dest, missing = atf.stamp_5320({"serial_number": "S77", "caliber": "45ACP"}, TODAY)   # the ATF copy is unchanged
    assert not missing and "S77" in text(dest)


def test_no_cleo_template_means_no_cleo_copies(configured):
    assert atf.stamp_cleo_all({"serial_number": "S1", "caliber": "9"}, TODAY) == []
    assert atf.template_problems() == []


def test_the_wrong_copy_is_refused(configured):
    shutil.copy(FIXTURE, atf.cleo_template())                     # the ATF copy where the CLEO copy goes
    assert "ATF copy" in atf.template_problems(cleo=True)[0]
    [(n, dest, probs)] = atf.stamp_cleo_all({"serial_number": "S1", "caliber": "9"}, TODAY)   # reported, never raised
    assert n == 1 and dest is None and "isn't a CLEO copy" in probs[0]   # not stamped: it would show the CLEO an SSN
    shutil.copy(CLEO_FIXTURE, atf.FORMS / "5320.23.pdf")          # the CLEO copy as the ATF template
    assert "CLEO copy" in atf.template_problems()[0]


def test_other_responsible_persons_keep_their_own_cleo_line(configured):
    add_other_rp()
    t = yaml.safe_load((atf.DATA / "trust.yaml").read_text(encoding="utf-8"))
    t["cleo"]["official"] = "NEW CHIEF"
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump(t), encoding="utf-8")
    person = atf.other_rps(atf.load_trust())[0]
    set_cleo(), set_cleo(person)
    assert atf.cleo_template(person).name == "5320.23_jane-roe.cleo.pdf"
    res = atf.stamp_cleo_all({"serial_number": "S5", "caliber": "9"}, TODAY)
    assert [(n, p.name, probs) for n, p, probs in res] == [(1, "5320.23_S5.cleo.pdf", []), (2, "5320.23_S5.jane-roe.cleo.pdf", [])]
    assert "NEW CHIEF" in text(res[0][1]) and "PAT EXAMPLE" in text(res[1][1])   # item 5: the CLEO where THEY live


def test_form1_cleo_copy_is_the_cleo_pages_only(configured, tmp_path):
    pdf = tmp_path / "full.pdf"
    pdf.write_bytes(submitted_pdf("T301", "45ACP"))
    index = {}
    assert atf.save_form1_cleo(pdf, "2000002", index) == "T301" and index == {"2000002": "T301"}
    kept = atf._form1_cleo_path("T301")
    doc = pymupdf.open(kept)
    assert doc.page_count == 1 and "CLEO Copy" in doc[0].get_text() and "CAL-45ACP" in doc[0].get_text()
    assert "T301" not in text(kept) and "ATF Copy" not in text(kept)
    assert atf._form1_index() == {"2000002": "T301"}


SUBMITTED_FORMS = [  # (eForm ID, finalized, PDF, submitted)
    ("2000001", "", submitted_pdf("T300"), "11/1/2026"),           # filed earlier: not opened
    ("2000002", "", submitted_pdf("T301", "45ACP"), "11/6/2026"),
    ("2000003", "", submitted_pdf("T302", "22LR"), "11/6/2026"),
    ("2000004", "", submitted_pdf("OTHER"), "11/6/2026"),           # never needed: both are found first
]


def test_form1_cleo_copies_are_fetched_read_only(configured, dashboard):
    state = dashboard(SUBMITTED_FORMS, status=atf.SUBMITTED)
    got, problems = atf.fetch_cleo_form1s({"T301": "11/06/2026", "T302": "11/06/2026"})
    assert problems == [] and sorted(got) == ["T301", "T302"] and all(p.is_file() for p in got.values())
    assert state["pdf_hits"] == 2 and state["bad_clicks"] == [] and state["logged_out"]   # only what was needed; read-only
    assert not list((atf.OUT / "cleo").glob(".*.part.pdf"))                                # the whole forms aren't kept
    state["pdf_hits"] = 0
    got, problems = atf.fetch_cleo_form1s({"T301": "11/06/2026", "T309": "11/06/2026"})  # saved: not fetched again
    assert "T301" in got and state["pdf_hits"] == 1                                       # only OTHER was still unopened
    assert problems == ["no submitted form found for T309"] and state["bad_clicks"] == []


def test_packet_for_your_cleo_shows_no_serials(configured, tmp_path):
    set_cleo()
    filed(("S2", "22LR", "11/06/2026"), ("S1", "45ACP", "11/06/2026"), ("S3", "9MM", "11/06/2026"))
    index = {}
    for n, (serial, cal) in enumerate((("S1", "45ACP"), ("S2", "22LR")), 1):   # S3's Form 1 copy couldn't be saved
        (tmp_path / f"{serial}.pdf").write_bytes(submitted_pdf(serial, cal))
        atf.save_form1_cleo(tmp_path / f"{serial}.pdf", f"300000{n}", index)
    for r in atf.load_rows():
        atf.stamp_cleo_all(r, r["filed_date"])
    [(n, path, apps, missing)] = atf.cleo_packets(["S3", "S2", "S1"])
    assert n == 1 and apps == 3 and missing == ["S3: Form 1 copy"]
    doc = pymupdf.open(path)
    assert doc.page_count == 1 + 2 * (1 + 2) + 2                      # cover; S1, S2: Form 1 + 5320.23; S3: 5320.23
    cover = doc[0].get_text()
    for shown in ("PAT EXAMPLE, CHIEF OF POLICE", "SPRINGFIELD POLICE DEPARTMENT", "200 MAIN ST", "SPRINGFIELD, OH 45502",
                  "3 applications", "filed with ATF on 11/06/2026.",
                  "the CLEO copy of ATF Form 1 and the CLEO copy of the responsible person's Form 5320.23"):
        assert shown in " ".join(cover.split()), shown
    assert "?" not in cover                                            # every character drawn (the font has no curly quotes)
    assert "CAL-45ACP" in doc[1].get_text() and "45ACP" in doc[2].get_text() and "CAL-22LR" in doc[4].get_text()  # serial order
    assert not any(s in text(path) for s in ("S1", "S2", "S3")) and FIXTURE_SSN not in text(path)   # ATF keeps it from the CLEO


def test_packets_for_other_responsible_persons(configured, tmp_path):
    add_other_rp()
    person = atf.other_rps(atf.load_trust())[0]
    set_cleo(person)                                                   # theirs only; you have none
    filed(("S1", "9", "11/06/2026"))
    (tmp_path / "S1.pdf").write_bytes(submitted_pdf("S1", "9"))
    atf.save_form1_cleo(tmp_path / "S1.pdf", "3000001", {})
    atf.stamp_cleo_all(atf.load_rows()[0], "11/06/2026")
    packets = atf.cleo_packets(["S1"])
    assert [(n, apps, missing) for n, _, apps, missing in packets] == [(1, 1, []), (2, 1, [])]
    yours, theirs = (pymupdf.open(p) for _, p, _, _ in packets)
    assert yours.page_count == 1 + 1 and theirs.page_count == 1 + 2   # yours: the Form 1 copy; theirs: their 5320.23
    assert packets[1][1].name.endswith(".jane-roe.pdf")
    cover = " ".join(theirs[0].get_text().split())
    assert "on which I am a responsible person" in cover and "the CLEO copy of my Form 5320.23" in cover
    assert "SPRINGFIELD POLICE DEPARTMENT" in cover                    # their CLEO, read from their CLEO copy


def test_cover_lists_several_filing_dates(configured, tmp_path):
    filed(("S1", "9", "11/06/2026"), ("S2", "9", "10/30/2026"), ("S3", "9", "11/01/2026"))
    for n, s in enumerate(("S1", "S2", "S3"), 1):
        (tmp_path / f"{s}.pdf").write_bytes(submitted_pdf(s, "9"))
        atf.save_form1_cleo(tmp_path / f"{s}.pdf", f"300000{n}", {})
    [(_, path, apps, _)] = atf.cleo_packets(["S1", "S2", "S3"])
    assert "filed with ATF on 10/30/2026, 11/01/2026 and 11/06/2026." in " ".join(pymupdf.open(path)[0].get_text().split())


def test_individual_packet_is_the_form1_copies(configured, tmp_path):
    from conftest import make_individual
    make_individual()
    filed(("S1", "9", "11/06/2026"))
    (tmp_path / "S1.pdf").write_bytes(submitted_pdf("S1", "9"))
    atf.save_form1_cleo(tmp_path / "S1.pdf", "3000001", {})
    [(n, path, apps, missing)] = atf.cleo_packets(["S1"])
    assert apps == 1 and missing == [] and pymupdf.open(path).page_count == 2
    assert "Enclosed for each application: the CLEO copy of ATF Form 1." in " ".join(pymupdf.open(path)[0].get_text().split())


def test_filing_from_the_menu_makes_the_packet(configured, portal, answers, screen, monkeypatch):
    set_cleo()
    queue(("S1", "9"))
    state = portal([("S1", "9")])
    opened = []
    monkeypatch.setattr(ui, "open_file", opened.append)
    answers(pick("File 1 queued"), TICKED, False, True, True, QUIT)    # ..., confirm, open the packet
    ui.menu()
    out = " ".join(screen.getvalue().split())
    assert atf.load_rows()[0]["status"] == "filed"
    assert "5320.23 CLEO copies generated" in out and "CLEO packet: made after filing (Form 1 and 5320.23 CLEO copies)" in out
    assert state["cleo_fetches"] == [{"S1": atf.load_rows()[0]["filed_date"]}]   # asked for, in the same session
    assert "CLEO packet for your CLEO: 1 application" in out and "Not in the packet for your CLEO: S1: Form 1 copy" in out
    assert len(opened) == 1 and opened[0].name.startswith("CLEO packet ")


def test_filing_without_cleo_copies_still_works(configured, portal, answers, screen):
    queue(("S1", "9"))
    portal([("S1", "9")])
    answers(pick("File 1 queued"), TICKED, False, True, QUIT)          # no "Open the CLEO packet?" question
    ui.menu()
    out = " ".join(screen.getvalue().split())
    assert atf.load_rows()[0]["status"] == "filed"
    assert "the Form 1 CLEO copies only (no 5320.23 CLEO copy is set" in out
    assert "Not in the packet for your CLEO: S1: Form 1 copy" in out


def test_settings_cleo_copies(configured, answers, screen, tmp_path):
    atf_copy, cleo_copy = tmp_path / "atf.pdf", tmp_path / "cleo.pdf"
    shutil.copy(FIXTURE, atf_copy), shutil.copy(CLEO_FIXTURE, cleo_copy)
    answers(pick("Settings"), pick("CLEO copies"), pick("CLEO copy of your 5320.23: not set"), str(atf_copy),   # refused
            pick("CLEO copy of your 5320.23: not set"), str(cleo_copy), "done", QUIT)
    ui.menu()
    out = screen.getvalue()
    assert "That's the ATF copy" in out and "Saved; it stamps correctly" in out
    assert atf.cleo_template().read_bytes() == cleo_copy.read_bytes()
    assert "DOE FAMILY TRUST" not in out.upper()                                   # screenshot-safe


def test_packet_later_is_a_setting(configured, answers, screen):
    assert atf.cleo_packet_after_batch()                                          # the default: right after each batch
    answers(pick("Settings"), pick("CLEO packet after each batch (on)"), "off", QUIT)
    ui.menu()
    assert atf.load_trust()["cleo_packet_after_batch"] is False and not atf.cleo_packet_after_batch()


def test_packet_later_still_saves_the_copies(configured, portal, answers, screen):
    set_cleo()
    t = yaml.safe_load((atf.DATA / "trust.yaml").read_text(encoding="utf-8"))
    (atf.DATA / "trust.yaml").write_text(yaml.safe_dump({**t, "cleo_packet_after_batch": False}), encoding="utf-8")
    queue(("S1", "9"))
    state = portal([("S1", "9")])
    answers(pick("File 1 queued"), TICKED, False, True, QUIT)                     # no "Open the CLEO packet?" question
    ui.menu()
    out = " ".join(screen.getvalue().split())
    assert "CLEO packet: later, for several batches at once" in out
    assert state["cleo_fetches"] == [{"S1": atf.load_rows()[0]["filed_date"]}]   # the Form 1 copy is still fetched now
    assert (atf.OUT / "cleo" / "5320.23_S1.cleo.pdf").is_file()                    # and the 5320.23 copy made
    assert "1 filed form is in no CLEO packet yet" in out and not list((atf.OUT / "cleo").glob("CLEO packet*"))


def test_make_packets_offers_every_form_in_no_packet_yet(configured, answers, screen, tmp_path, monkeypatch):
    filed(("S1", "9", "10/30/2026"), ("S2", "9", "11/01/2026"), ("S3", "9", "11/06/2026"))
    for n, s in enumerate(("S1", "S2", "S3"), 1):
        (tmp_path / f"{s}.pdf").write_bytes(submitted_pdf(s, "9"))
        atf.save_form1_cleo(tmp_path / f"{s}.pdf", f"300000{n}", {})
    atf.cleo_packets(["S1"])                                                      # S1 mailed already
    assert [r["serial_number"] for r in atf.cleo_unpacked()] == ["S2", "S3"]
    monkeypatch.setattr(ui, "open_file", lambda p: None)
    seen = {}
    answers(pick("Advanced"), pick("Make CLEO packets"), lambda kw: seen.setdefault("ticked", TICKED(kw)), False, QUIT)
    ui.menu()
    rows = atf.load_rows()
    assert [rows[i]["serial_number"] for i in seen["ticked"]] == ["S2", "S3"]     # two batches, one packet
    assert atf.cleo_unpacked() == [] and "CLEO packet for your CLEO: 2 applications" in screen.getvalue()
