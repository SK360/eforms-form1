"""The app's side (src/remote.py, standard library only) against this server over HTTPS with its own certificate:
probe, compare the fingerprint, pair, read. Keeps the two sides from drifting apart."""
import socket
import sys
import threading
import time

import pytest

from conftest import PAGE, SERVER
from atf_server import db, tls

sys.path.insert(0, str(SERVER.parent / "src"))
import remote  # noqa: E402


@pytest.fixture
def https(filled, data_dir):
    import uvicorn
    from atf_server.app import create_app
    cert, key = tls.ensure_certificate(data_dir / "tls")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(data_dir, page=PAGE, fingerprint=tls.fingerprint(cert)), host="127.0.0.1",
                                           port=port, log_level="warning", ssl_certfile=str(cert), ssl_keyfile=str(key)))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"https://127.0.0.1:{port}", tls.fingerprint(cert)
    server.should_exit = True
    thread.join(5)


def test_the_app_pairs_and_reads(https, filled):
    address, fingerprint = https
    found = remote.probe(address)
    assert found["fingerprint"] == fingerprint and found["api"] == remote.API and found["server_id"] == db.meta(filled, "server_id")
    code, _ = db.new_pair_code(filled)
    paired = remote.pair(found, code, "PC")
    server = remote.Server(found["server_id"], [address], found["fingerprint"], paired["key"])
    sup, sbrs = server.rows()
    assert len(sup) == 38 and sup[0]["serial_number"] == "A101" and sup[0]["built"] == "yes" and len(sbrs) == 2
    assert server.pdf("A101").startswith(b"%PDF") and server.pdf("A130") is None
    assert [d["name"] for d in db.devices(filled)] == ["PC"]
    server.post(f"/api/devices/{paired['device_id']}/revoke", {})
    remote.Server.working.clear()
    with pytest.raises(remote.ServerError, match="doesn't know this computer"):
        server.rows()
