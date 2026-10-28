"""The server's own HTTPS certificate for its local address, made once at first start.

A tailnet certificate doesn't cover a home-network name, so the server signs its own. The app pins its fingerprint
when pairing (the Devices page shows it to compare), so the PC gets an encrypted, verified connection with nothing to
install. Browsers would warn about it; the phone uses the tailnet name, which `tailscale serve` covers.
"""
import hashlib
import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path


def ensure_certificate(folder, names=()):
    """(cert path, key path), made if missing: an EC P-256 key and a self-signed certificate valid for 20 years."""
    folder = Path(folder)
    cert, key = folder / "cert.pem", folder / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    folder.mkdir(parents=True, exist_ok=True)
    private = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname()
    dns = sorted({n for n in (host, f"{host}.local", f"{host}.home.arpa", "localhost", *names) if n})
    alt = [x509.DNSName(n) for n in dns] + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ATF inventory server")])
    start = datetime.now(timezone.utc) - timedelta(days=1)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(private.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(start).not_valid_after(start + timedelta(days=365 * 20))
                   .add_extension(x509.SubjectAlternativeName(alt), critical=False)
                   .sign(private, hashes.SHA256()))
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    key.chmod(0o600)
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return cert, key


def fingerprint(cert_path):
    """SHA-256 of the certificate (DER), as AB:CD:... (what the app pins and the Devices page shows)."""
    import ssl
    der = ssl.PEM_cert_to_DER_cert(Path(cert_path).read_text(encoding="ascii"))
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
