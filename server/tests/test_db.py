"""The database: schema and migrations, import and export, rules that hold even against direct SQL, pairing, backups."""
import csv
import io
import sqlite3

import pytest

from conftest import pair_device
from atf_server import db, demo


def test_new_database(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    server_id = db.meta(conn, "server_id")
    assert len(server_id) == 32
    db.migrate(conn)                                                   # again: nothing changes
    assert db.meta(conn, "server_id") == server_id and db.is_empty(conn)


def test_newer_database_is_refused(conn):
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    with pytest.raises(db.Refused, match="newer server"):
        db.migrate(conn)


def _csv(rows, cols):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def test_import_then_export_is_the_same_csv(filled, data_dir):
    sup, sbrs, pdfs = demo.inventory()
    assert db.export_csv(filled) == _csv(sup, db.COLS)                  # demo rows are already in serial order
    assert [{k: v for k, v in r.items()} for r in db.sbrs(filled)] == sbrs
    stored = sorted((data_dir / "approved").glob("*.pdf"))
    assert len(stored) == len(pdfs) == filled.execute("SELECT count(*) FROM approval_pdfs").fetchone()[0]
    assert tuple(filled.execute("SELECT status, filed_date, built FROM suppressors WHERE serial = 'a101'").fetchone()) == \
        ("approved", "2026-08-10", 1)                                   # ISO dates inside, MM/DD/YYYY outside
    assert db.row(filled, "a136")["status"] == ""                      # queued is "" in the app's CSV
    assert [h["operation"] for h in db.history(filled, "A101")] == ["import"]


def test_import_only_into_an_empty_server(filled, data_dir):
    sup, sbrs, pdfs = demo.inventory()
    with pytest.raises(db.Refused, match="empty server"):
        db.import_inventory(filled, data_dir / "approved", sup[:1])


@pytest.mark.parametrize("change, why", [
    (lambda rows: rows.append(dict(rows[0], serial_number="a101")), "appears twice"),
    (lambda rows: rows[-1].update(built="yes"), "built but not approved"),
    (lambda rows: rows[0].update(filed_date="Aug 10"), "not a date"),
    (lambda rows: rows[0].update(status="lost"), "unknown status"),
    (lambda rows: rows[1].update(application_number=rows[0]["application_number"]), "application number appears twice"),
])
def test_import_is_all_or_nothing(conn, data_dir, change, why):
    sup, sbrs, pdfs = demo.inventory()
    change(sup)
    with pytest.raises(db.Refused, match=why):
        db.import_inventory(conn, data_dir / "approved", sup, sbrs, pdfs)
    assert db.is_empty(conn) and not conn.execute("SELECT 1 FROM history").fetchone()


def test_rules_hold_against_direct_sql(filled):
    with pytest.raises(sqlite3.IntegrityError):                        # built only while approved
        filled.execute("UPDATE suppressors SET built = 1 WHERE serial = 'A130'")
    with pytest.raises(sqlite3.IntegrityError):
        filled.execute("UPDATE suppressors SET status = 'lost' WHERE serial = 'A130'")
    with pytest.raises(sqlite3.IntegrityError):
        filled.execute("UPDATE suppressors SET filed_date = '08/10/2026' WHERE serial = 'A130'")
    with pytest.raises(sqlite3.IntegrityError):
        filled.execute("UPDATE suppressors SET serial = 'a101' WHERE serial = 'A102'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        filled.execute("UPDATE history SET device = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        filled.execute("DELETE FROM history")
    before = db.row(filled, "A130")["version"]
    filled.execute("UPDATE suppressors SET caliber = '45' WHERE serial = 'A130'")
    assert db.row(filled, "A130")["version"] == before + 1             # every write bumps the version


def test_read_app_folder(tmp_path):
    """The app's own layout: index.csv maps eForm ID -> file; an unlisted <serial>.pdf counts; other files don't."""
    root = tmp_path / "app"
    (root / "data").mkdir(parents=True)
    (root / "out" / "approved").mkdir(parents=True)
    sup, sbrs, _ = demo.inventory()
    (root / "data" / "suppressors.csv").write_text(_csv(sup, db.COLS), encoding="utf-8-sig")   # Excel's BOM
    (root / "data" / "sbrs.csv").write_text(_csv(sbrs, db.SBR_COLS), encoding="utf-8")
    folder = root / "out" / "approved"
    for name in ("A101.pdf", "A102_2.pdf", "A103.pdf", "notes.pdf", "R1.pdf"):
        (folder / name).write_bytes(b"%PDF-1.4 " + name.encode())
    (folder / "index.csv").write_text("eform_id,file\n10000101,A101.pdf\n10000102,A102_2.pdf\n2000001,R1.pdf\n9,gone.pdf\n")
    got_sup, got_sbrs, pdfs = db.read_app_folder(root)
    assert len(got_sup) == len(sup) and len(got_sbrs) == 2
    assert sorted((p["eform_id"], p["serial"]) for p in pdfs if p["eform_id"]) == \
        [("10000101", "A101"), ("10000102", "A102"), ("2000001", "R1")]
    assert [(p["eform_id"], p["serial"]) for p in pdfs if not p["eform_id"]] == [(None, "A103")]


def test_pairing(conn):
    device_id, key = pair_device(conn, "iPhone")
    assert db.device_for_key(conn, key)["name"] == "iPhone"
    assert key not in str(conn.execute("SELECT * FROM devices").fetchall())          # only its hash is kept
    with pytest.raises(db.Refused, match="No pairing code"):                        # a code works once
        db.pair(conn, "000000", "Other")
    code, _ = db.new_pair_code(conn)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(db.PAIR_TRIES - 1):
        with pytest.raises(db.Refused, match="wrong"):
            db.pair(conn, wrong, "Guess")
    with pytest.raises(db.Refused, match="no longer works"):
        db.pair(conn, wrong, "Guess")
    with pytest.raises(db.Refused, match="No pairing code"):                        # even the right code now
        db.pair(conn, code, "Late")
    db.revoke(conn, device_id, "PC")
    assert db.device_for_key(conn, key) is None
    assert [h["operation"] for h in conn.execute("SELECT operation FROM history ORDER BY id")] == \
        ["pair_device", "revoke_device"]


def test_expired_code(conn, monkeypatch):
    code, _ = db.new_pair_code(conn)
    monkeypatch.setattr(db, "now", lambda: "2999-01-01T00:00:00Z")
    with pytest.raises(db.Refused, match="No pairing code"):
        db.pair(conn, code, "Late")


def test_backups(filled, data_dir):
    for n in range(4):
        db.backup(filled, data_dir / "backups", label=str(n), keep=3)
    kept = sorted(p.name for p in (data_dir / "backups").glob("*.db"))
    assert kept == ["inventory-1.db", "inventory-2.db", "inventory-3.db"]
    copy = sqlite3.connect(str(data_dir / "backups" / "inventory-3.db"))
    assert copy.execute("SELECT count(*) FROM suppressors").fetchone()[0] == 38


def test_migration_3_removes_earlier_build_records(data_dir):
    """Phase 3 servers recorded builds in the history and request rows; schema 3 removes them (and only them)."""
    data_dir.mkdir()
    c = db.connect(data_dir / "inventory.db")
    for v in (1, 2):
        c.execute("BEGIN")
        for statement in db._statements(db.MIGRATIONS[v]):
            c.execute(statement)
        c.execute(f"PRAGMA user_version = {v}")
        c.execute("COMMIT")
    for opn in ("import", "set_built", "clear_built", "add_queued"):
        c.execute("INSERT INTO history (at, device, operation, serial) VALUES ('2026-10-28T19:00:00Z', 'iPhone', ?, 'A101')", (opn,))
        c.execute("INSERT INTO requests VALUES (?, ?, '{}', '2026-10-28T19:00:00Z')", (opn, opn))
    db.migrate(c, data_dir / "backups")
    assert [r[0] for r in c.execute("SELECT operation FROM history")] == ["import", "add_queued"]
    assert [r[0] for r in c.execute("SELECT operation FROM requests")] == ["import", "add_queued"]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("DELETE FROM history")
    c.close()
    assert b"set_built" not in (data_dir / "inventory.db").read_bytes()          # not left in free pages either
