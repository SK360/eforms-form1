"""Build tests/fixtures/fake_5320.pdf: a FAKE signed-template stand-in with no real person's data.

ATF's blank 5320.23 (public form) with made-up answers written the way the 5320.23 generator does: flattened,
in an embedded NimbusSans font, at the generator's positions. Includes a fake SSN so tests can prove it is never read.

    python tests/fixtures/make_fake_5320.py path/to/blank_f5320.23.pdf

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


if __name__ == "__main__":
    main(sys.argv[1])
