"""Server tests: every test gets its own temp data folder and made-up inventory (atf_server.demo, serials A101...).
Nothing here reads or writes the app's data/ folder or a real server."""
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from atf_server import db, demo, tls  # noqa: E402

PAGE = SERVER.parent / "src" / "phone.html"


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "server-data"


@pytest.fixture
def conn(data_dir):
    data_dir.mkdir()
    c = db.connect(data_dir / "inventory.db")
    db.migrate(c, data_dir / "backups")
    yield c
    c.close()


@pytest.fixture
def filled(conn, data_dir):
    sup, sbrs, pdfs = demo.inventory()
    db.import_inventory(conn, data_dir / "approved", sup, sbrs, pdfs, device="test")
    return conn


@pytest.fixture
def tls_dir(tmp_path):
    """The server's CA and certificate, as a server makes them at first start."""
    tls.ensure_certificate(tmp_path / "tls")
    return tmp_path / "tls"


@pytest.fixture
def ca_fp(tls_dir):
    return tls.fingerprint(tls.ca_path(tls_dir))


@pytest.fixture
def client(filled, data_dir, tls_dir):
    from fastapi.testclient import TestClient
    from atf_server.app import create_app
    return TestClient(create_app(data_dir, page=PAGE, ca=tls.ca_path(tls_dir)))


def pair_device(conn, name="PC"):
    code, _ = db.new_pair_code(conn)
    return db.pair(conn, code, name)


@pytest.fixture
def auth(filled):
    return {"Authorization": f"Bearer {pair_device(filled)[1]}"}
