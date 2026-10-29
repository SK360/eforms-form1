"""The server's own CA and certificate (tls.py): what phones trust once, and what keeps working without a tailnet."""
import ipaddress
import ssl
from datetime import timedelta

import pytest
from cryptography import x509

from atf_server import tls


def _load(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_ca_and_certificate(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "addresses", lambda: ["127.0.0.1", "192.168.1.50", "8.8.8.8"])
    cert, key = tls.ensure_certificate(tmp_path, names=["inv.home.arpa", "10.1.2.3", "example.com", ""])
    ca = _load(tls.ca_path(tmp_path))
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical
    nets = {str(n.value) for n in nc.value.permitted_subtrees if isinstance(n, x509.IPAddress)}
    assert "192.168.0.0/16" in nets and "0.0.0.0/0" not in nets           # home-network addresses only
    leaf = _load(cert)
    alt = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ips = {str(i) for i in alt.get_values_for_type(x509.IPAddress)}
    assert ips == {"127.0.0.1", "192.168.1.50", "10.1.2.3"}              # a public address is never in it
    assert "inv.home.arpa" in alt.get_values_for_type(x509.DNSName)
    assert leaf.issuer == ca.subject
    assert leaf.not_valid_after_utc - leaf.not_valid_before_utc <= timedelta(days=825)   # Apple's limit
    assert (tmp_path / "ca-key.pem").stat().st_mode & 0o077 == 0 and key.stat().st_mode & 0o077 == 0
    # What a phone (or the app) does once it trusts the CA: the chain verifies, name constraints included.
    ctx = ssl.create_default_context(cadata=tls.ca_path(tmp_path).read_text())
    ctx.check_hostname = False
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(cert, key)
    _handshake(ctx, server, "192.168.1.50")


def _handshake(client_ctx, server_ctx, host):
    """A TLS handshake in memory (no network): raises if the client doesn't accept the server's chain."""
    c_in, c_out, s_in, s_out = ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_ctx.wrap_bio(c_in, c_out, server_hostname=host)
    srv = server_ctx.wrap_bio(s_in, s_out, server_side=True)
    for _ in range(10):
        done = 0
        for side, out, peer_in in ((client, c_out, s_in), (srv, s_out, c_in)):
            try:
                side.do_handshake()
                done += 1
            except ssl.SSLWantReadError:
                pass
            peer_in.write(out.read())
        if done == 2:
            return
    raise AssertionError("handshake didn't finish")


def test_new_address_reissues_with_the_same_ca(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "addresses", lambda: ["127.0.0.1", "192.168.1.50"])
    tls.ensure_certificate(tmp_path)
    ca_before, leaf_before = tls.ca_path(tmp_path).read_bytes(), (tmp_path / "cert.pem").read_bytes()
    tls.ensure_certificate(tmp_path)
    assert (tmp_path / "cert.pem").read_bytes() == leaf_before                  # nothing changed: kept
    monkeypatch.setattr(tls, "addresses", lambda: ["127.0.0.1", "192.168.1.77"])  # no DHCP reservation, new address
    tls.ensure_certificate(tmp_path)
    assert tls.ca_path(tmp_path).read_bytes() == ca_before                      # phones keep trusting it
    alt = _load(tmp_path / "cert.pem").extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("192.168.1.77") in alt.get_values_for_type(x509.IPAddress)


def test_an_older_self_signed_certificate_is_replaced(tmp_path):
    """Phase 1 servers had a self-signed certificate and no CA: the first start of this version makes both."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from datetime import datetime, timezone
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "old")])
    now = datetime.now(timezone.utc)
    old = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
           .serial_number(1).not_valid_before(now).not_valid_after(now + timedelta(days=7000)).sign(key, hashes.SHA256()))
    (tmp_path / "cert.pem").write_bytes(old.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
    tls.ensure_certificate(tmp_path)
    assert _load(tmp_path / "cert.pem").issuer == _load(tls.ca_path(tmp_path)).subject


def test_a_stolen_ca_cant_vouch_for_a_real_website(tmp_path):
    """Name constraints: a certificate for a public name, even signed with the CA's key, isn't accepted."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from datetime import datetime, timezone
    tls.ensure_certificate(tmp_path)
    ca = _load(tls.ca_path(tmp_path))
    ca_key = serialization.load_pem_private_key((tmp_path / "ca-key.pem").read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    fake = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "bank")]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(2).not_valid_before(now)
            .not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("www.example-bank.com")]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    (tmp_path / "fake.pem").write_bytes(fake.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "fake-key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                              serialization.NoEncryption()))
    ctx = ssl.create_default_context(cadata=tls.ca_path(tmp_path).read_text())
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(tmp_path / "fake.pem", tmp_path / "fake-key.pem")
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(ctx, server, "www.example-bank.com")


def test_names_given_for_docker(tmp_path, monkeypatch):
    """In Docker the server sees only the container's address: ATF_NAMES adds the host's (and a home name); the log
    line "HTTPS for:" lists what the certificate covers. A public address is never in it."""
    monkeypatch.setattr(tls, "addresses", lambda: ["127.0.0.1", "172.17.0.2"])
    cert, _ = tls.ensure_certificate(tmp_path, names=["192.168.1.50", "nas.home.arpa", "8.8.8.8", ""])
    shown = tls.covered(cert)
    assert {"192.168.1.50", "172.17.0.2", "nas.home.arpa"} <= set(shown) and "8.8.8.8" not in shown
    cert, _ = tls.ensure_certificate(tmp_path, names=["192.168.1.60"])          # changed later: a new certificate
    assert "192.168.1.60" in tls.covered(cert)
