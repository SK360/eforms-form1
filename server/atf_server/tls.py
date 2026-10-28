"""The server's own HTTPS for its home-network address, with no tailnet and no domain needed.

At first start the server makes a small certificate authority of its own (ca.pem; its key never leaves the server)
and signs its HTTPS certificate with it. A phone trusts that CA once (the page: "Trust this server"), and from then on
opens https://<the server's address>:8765 without warnings; the app trusts the same CA, saved when pairing. The CA can
only vouch for home-network names and addresses (name constraints), so even a stolen CA key couldn't impersonate a
real website on a phone that trusts it.

The server certificate names the container's current addresses and is re-issued at start when they change or it is
due (Apple accepts at most 825 days for a server certificate). The CA stays the same, so nothing has to be trusted again.
"""
import hashlib
import ipaddress
import socket
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

LEAF_DAYS, RENEW_DAYS = 800, 60
PRIVATE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
                "fc00::/7", "fe80::/10", "::1/128")
PRIVATE_SUFFIXES = ("local", "lan", "home.arpa", "internal", "localhost")


def fingerprint(cert_path):
    """SHA-256 of a certificate (DER), as AB:CD:... (the CA's is what the app and the Devices page show)."""
    return fingerprint_pem(Path(cert_path).read_text(encoding="ascii"))


def fingerprint_pem(pem):
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def addresses():
    """This machine's addresses on its networks (no packet is sent: a UDP connect only picks the route)."""
    found = {"127.0.0.1"}
    for target, family in (("10.255.255.255", socket.AF_INET), ("fd00::1", socket.AF_INET6)):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect((target, 1))
                found.add(s.getsockname()[0])
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(a.split("%")[0] for a in found)


def _names(extra):
    host = socket.gethostname().split(".")[0]
    names = {"localhost", f"{host}.local", f"{host}.lan", f"{host}.home.arpa"}
    ips = set(addresses())
    for item in extra:
        item = item.strip()
        if not item:
            continue
        try:
            ips.add(str(ipaddress.ip_address(item)))
        except ValueError:
            names.add(item.lower())
    return sorted(names), sorted(ips)


def _allowed(name, suffixes):
    return any(name == s or name.endswith("." + s) for s in suffixes)


def ensure_certificate(folder, names=()):
    """(cert path, key path): the CA (made once) and a server certificate it signed, re-issued when this machine's
    addresses changed, it's due, or it wasn't signed by this CA (an older server's self-signed one)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    ca_path, ca_key_path = folder / "ca.pem", folder / "ca-key.pem"
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    dns, ips = _names(names)
    now = datetime.now(timezone.utc)

    def write_key(path, key):
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        path.chmod(0o600)

    if not (ca_path.exists() and ca_key_path.exists()):
        ca_key = ec.generate_private_key(ec.SECP256R1())
        suffixes = sorted(set(PRIVATE_SUFFIXES) | {n.split(".", 1)[-1] if "." in n else n for n in dns
                                                   if not _allowed(n, PRIVATE_SUFFIXES)})
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"ATF inventory server CA ({socket.gethostname()})")])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
              .not_valid_after(now + timedelta(days=365 * 20))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                           key_encipherment=False, data_encipherment=False, key_agreement=False,
                                           encipher_only=False, decipher_only=False), critical=True)
              .add_extension(x509.NameConstraints(
                  permitted_subtrees=[x509.DNSName(s) for s in suffixes]
                  + [x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETS], excluded_subtrees=None), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
        write_key(ca_key_path, ca_key)
        ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        if cert_path.exists():
            cert_path.unlink()  # an older server's self-signed certificate: replaced below
    ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
    allowed_ips = [ipaddress.ip_network(n) for n in PRIVATE_NETS]
    ips = [i for i in ips if any(ipaddress.ip_address(i) in net for net in allowed_ips)]
    constraints = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    suffixes = [d.value for d in constraints.permitted_subtrees if isinstance(d, x509.DNSName)]
    dns = [n for n in dns if _allowed(n, suffixes)]

    if cert_path.exists() and key_path.exists():
        current = x509.load_pem_x509_certificate(cert_path.read_bytes())
        try:
            have = current.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have_ips = {str(i) for i in have.get_values_for_type(x509.IPAddress)}
            have_dns = set(have.get_values_for_type(x509.DNSName))
        except x509.ExtensionNotFound:
            have_ips, have_dns = set(), set()
        if (current.issuer == ca.subject and set(ips) <= have_ips and set(dns) <= have_dns
                and current.not_valid_after_utc - now > timedelta(days=RENEW_DAYS)):
            return cert_path, key_path
    key = ec.generate_private_key(ec.SECP256R1())
    alt = [x509.DNSName(n) for n in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname())]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=LEAF_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                         data_encipherment=False, key_agreement=True, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    write_key(key_path, key)
    # The chain (server certificate, then the CA): clients that trust the CA can build it from what is sent.
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def ca_path(folder):
    return Path(folder) / "ca.pem"
