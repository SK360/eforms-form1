"""Build tests/fixtures/fake_5320.pdf: a FAKE signed-template stand-in with no real person's data.

ATF's blank 5320.23 (public form) with made-up answers written the way the 5320.23 generator does: flattened,
in an embedded NimbusSans font, at the generator's positions. Includes a fake SSN so tests can prove it is never read.

    python tests/fixtures/make_fake_5320.py path/to/blank_f5320.23.pdf
    python tests/fixtures/make_fake_5320.py --cleo     # fake_5320_cleo.pdf, from fake_5320.pdf (no blank form needed)

The blank form: https://www.atf.gov/firearms/docs/form/form-5320-23-national-firearms-act-nfa-responsible-person-questionnaire
(a copy also ships inside the generator's repo: static/f_5320.23_national_firearms_act_nfa_responsible_person_questionnaire.pdf).
"""
import sys
from pathlib import Path

import pymupdf

# (page, x, top-of-text y, text): positions measured from a real generator output (values replaced by fake ones)
FAKE = [
    (0, 31, 150, "DOE FAMILY TRUST"), (0, 31, 159, "100 OAK ST"), (0, 31, 168, "SPRINGFIELD OH 45501"),   # 2. applicant
    (0, 29, 204, "JOHN Q DOE"), (0, 29, 213, "100 OAK ST"), (0, 29, 222, "SPRINGFIELD OH 45501"),         # 3a. RP
    (0, 320, 204, "(555) 555-0100"), (0, 320, 239, "JOHN.DOE@EXAMPLE.COM"),                               # 3b, 3c
    (0, 29, 307, "123-45-6789"),                                                                          # 3f. FAKE SSN
    (0, 317, 305, "01/02/1980"),                                                                          # 3g. DOB
    (0, 29, 380, "SILENCER"), (0, 29, 413, "DOE FAMILY TRUST"), (0, 29, 422, "UNITED STATES"),           # 4a, 4b
    (0, 31, 459, "N/A"), (0, 138, 458, "00"), (0, 248, 458, "TEMPLATE"),                                  # 4c, 4d, 4e
    (0, 28, 565, "SPRINGFIELD POLICE DEPARTMENT"), (0, 307, 565, "PAT EXAMPLE, CHIEF OF POLICE"),         # 5. CLEO
    (0, 28, 598, "200 MAIN ST"), (0, 28, 607, "SPRINGFIELD OH 45502"),
    (1, 95, 409, "OHIO"), (1, 390, 407, "UNITED STATES OF AMERICA"),                                      # 9b, 9c
    (1, 368, 459, "01/01/2026"),                                                                          # signature date
]


def main(blank, dest=Path(__file__).with_name("fake_5320.pdf")):
    doc = pymupdf.open(blank)
    doc.select([0, 1])  # the ATF copy: questionnaire pages 1-2
    for page in doc:
        for w in list(page.widgets()):
            page.delete_widget(w)
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", "null")  # flattened, like the generator's output
    font = pymupdf.Font("helv").buffer  # mupdf's built-in NimbusSans, as the generator embeds
    for page in doc:
        page.insert_font(fontname="nimbus", fontbuffer=font)
    for pno, x, top, text in FAKE:
        doc[pno].insert_text((x, top + 8.4), text, fontname="nimbus", fontsize=9)
    doc.save(dest, garbage=4, deflate=True)
    print("wrote", dest)


# The generator's CLEO copy is ATF's CLEO-copy pages: the same labels and values at the same places, without the SSN/UPIN,
# the photo box, item 4e (serial: label and value) and page 2's instructions, and with a "CLEO Copy" footer (compared
# with a real ATF/CLEO pair 2026-11-06). Removing exactly those from the fake ATF copy gives a stand-in for it.
CLEO_REMOVED = [
    (0, (20, 292, 310, 318)),     # 3d/3f: SSN / UPIN label and value (the DOB right of it stays)
    (0, (440, 290, 592, 386)),    # 3e: photo box
    (0, (240, 440, 420, 472)),    # 4e: serial number label and value
    (1, (20, 486, 592, 746)),     # page 2: instructions
    (0, (280, 750, 340, 766)), (1, (280, 750, 340, 766)),   # "ATF Copy" footer
]


def cleo(src=Path(__file__).with_name("fake_5320.pdf"), dest=Path(__file__).with_name("fake_5320_cleo.pdf")):
    doc = pymupdf.open(src)
    for pno, rect in CLEO_REMOVED:
        doc[pno].add_redact_annot(pymupdf.Rect(rect), fill=(1, 1, 1))
    for page in doc:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        page.insert_text((286, 762), "CLEO Copy", fontname="tiro", fontsize=9)
    doc.save(dest, garbage=4, deflate=True)
    print("wrote", dest)


if __name__ == "__main__":
    cleo() if sys.argv[1:] == ["--cleo"] else main(sys.argv[1])
