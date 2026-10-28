"""A made-up inventory for trying the server (serials A101..., no real records): `python -m atf_server demo`."""


def _pdf(text):
    """A one-page PDF saying `text` (stands in for an approval PDF)."""
    content = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (n, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def inventory():
    """(suppressors, sbrs, pdfs) in the app's CSV shape: 25 approved (8 built), 10 filed, 3 queued; 2 SBRs."""
    designs = ["PCC Short", "Rimfire 12in", "Pistol 9", "Rimfire Short"]
    sup, pdfs = [], []
    for i, n in enumerate(range(101, 139)):
        serial = f"A{n}"
        caliber = ("22", "9", "5.56")[i % 3]
        r = {"caliber": caliber, "serial_number": serial, "overall_length": "12", "status": "", "application_number": "",
             "filed_date": "", "approved_date": "", "eform_id": "", "control_number": "", "conditions": "", "built": "",
             "design": ""}
        if n <= 135:
            r.update(status="filed", application_number=f"9000{n:05d}", filed_date="08/10/2026")
        if n <= 125:
            r.update(status="approved", approved_date="10/15/2026", eform_id=f"10{n:05d}", control_number=f"2026{n:07d}")
            pdfs.append({"eform_id": r["eform_id"], "serial": serial, "data": _pdf(f"Made-up approval for {serial}")})
        if n <= 108:
            r.update(built="yes", design=designs[i % len(designs)])
        if n == 103:
            r["conditions"] = "Mark per 27 CFR 479.102"
        sup.append(r)
    sbrs = [{"serial_number": f"R{n}", "caliber": "9", "model": "PCC", "barrel_length": "8", "overall_length": "24",
             "approved_date": "09/02/2026", "eform_id": f"20{n:05d}", "control_number": "", "conditions": ""}
            for n in (1, 2)]
    pdfs += [{"eform_id": s["eform_id"], "serial": s["serial_number"], "data": _pdf(f"Made-up SBR {s['serial_number']}")}
             for s in sbrs]
    return sup, sbrs, pdfs
