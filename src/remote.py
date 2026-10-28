"""Talking to the optional inventory server (server/, design doc "Inventory server design"). Standard library only.

The server answers at its home-network address with its own certificate, which this side pins by fingerprint (saved
when pairing; the server's page shows it under Devices to compare), and may also have a tailnet name with a normally
trusted certificate. Addresses are tried in order; the first that answers is used for the rest of the run. A server
whose id isn't the one paired with is never used (a different machine at that address).
"""
import hashlib
import http.client
import json
import socket
import ssl
from urllib.parse import quote, urlparse

PORT = 8765
API = 1  # the server API this helper speaks (server: atf_server.app.API_VERSION)
TIMEOUT = 5


class ServerError(Exception):
    """The server can't be reached, isn't the paired one, or refused a request. The text is for the user."""


class Unreachable(ServerError):
    """Nothing (or not the paired server) answers at an address: the next address is tried."""


def normalize(address):
    """"192.168.1.138" -> https://192.168.1.138:8765; a tailnet name (*.ts.net) -> port 443; a full URL as given."""
    address = address.strip().rstrip("/")
    if not address:
        raise ServerError("Enter the server's address.")
    u = urlparse(address if "://" in address else "https://" + address)
    if u.scheme != "https" or not u.hostname:
        raise ServerError(f"Not a server address: {address}")
    port = u.port or (443 if u.hostname.endswith(".ts.net") or "://" in address else PORT)
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    return f"https://{host}:{port}"


def _fingerprint(der):
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _connect(address, pinned, timeout):
    """An HTTPS connection whose certificate is the pinned one, or (for a tailnet name) one trusted the normal way."""
    u = urlparse(address)
    loose = ssl.create_default_context()
    loose.check_hostname, loose.verify_mode = False, ssl.CERT_NONE
    conn = http.client.HTTPSConnection(u.hostname, u.port, timeout=timeout, context=loose)
    try:
        conn.connect()
    except (OSError, ssl.SSLError) as e:
        raise Unreachable(f"No answer at {address} ({_why(e)}).")
    seen = _fingerprint(conn.sock.getpeercert(binary_form=True))
    if pinned and seen == pinned:
        return conn
    conn.close()
    strict = http.client.HTTPSConnection(u.hostname, u.port, timeout=timeout, context=ssl.create_default_context())
    try:
        strict.connect()
        return strict
    except (OSError, ssl.SSLError):
        raise Unreachable(f"The certificate at {address} isn't the one this computer paired with "
                          f"(it shows {seen[:23]}…). If the server was rebuilt, connect again in Settings.")


def _why(e):
    if isinstance(e, socket.timeout):
        return "timed out"
    if isinstance(e, ConnectionRefusedError):
        return "connection refused: is the server running?"
    return str(e) or type(e).__name__


def _call(conn, method, path, body=None, key=None):
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
    try:
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        return r.status, r.getheader("Content-Type") or "", r.read()
    except (OSError, ssl.SSLError, http.client.HTTPException) as e:
        raise Unreachable(f"The server stopped answering ({_why(e)}).")
    finally:
        conn.close()


def _json(status, kind, data):
    try:
        out = json.loads(data.decode("utf-8")) if "json" in kind else {}
    except ValueError:
        out = {}
    if status >= 400:
        if status == 401:
            raise ServerError("The server doesn't know this computer any more (removed under Devices?). "
                              "Connect again in Settings > Where your inventory lives.")
        raise ServerError(out.get("error") or f"The server answered {status}.")
    return out


def probe(address, timeout=TIMEOUT):
    """Before pairing: who answers at `address`, and its certificate fingerprint (to compare with the server's page).
    Returns {"address", "fingerprint", "server_id", "name", "version", "api"}."""
    address = normalize(address)
    u = urlparse(address)
    loose = ssl.create_default_context()
    loose.check_hostname, loose.verify_mode = False, ssl.CERT_NONE
    conn = http.client.HTTPSConnection(u.hostname, u.port, timeout=timeout, context=loose)
    try:
        conn.connect()
    except (OSError, ssl.SSLError) as e:
        raise ServerError(f"No inventory server answers at {address} ({_why(e)}).")
    fingerprint = _fingerprint(conn.sock.getpeercert(binary_form=True))
    hello = _json(*_call(conn, "GET", "/api/hello"))
    if "server_id" not in hello:
        raise ServerError(f"Something answers at {address}, but it isn't an inventory server.")
    return {"address": address, "fingerprint": fingerprint, **{k: hello.get(k) for k in ("server_id", "name", "version", "api")}}


def pair(found, code, name, timeout=TIMEOUT):
    """Trade a pairing code (from the server's page: Devices > Add a device) for this computer's key, on the server
    `probe` found. Returns {"key", "device_id", "server_id"}."""
    out = _json(*_call(_connect(found["address"], found["fingerprint"], timeout), "POST", "/api/pair",
                       {"code": str(code).strip(), "name": name}))
    if out.get("server_id") != found["server_id"]:
        raise ServerError("A different server answered while pairing; nothing was saved.")
    return out


class Server:
    """A paired server: its id, addresses (in order), pinned fingerprint and this computer's key."""
    working = {}  # server_id -> the address that answered this run

    def __init__(self, server_id, addresses, fingerprint, key, timeout=TIMEOUT):
        self.server_id, self.addresses, self.fingerprint, self.key = server_id, list(addresses), fingerprint, key
        self.timeout = timeout

    def _request(self, method, path, body=None):
        first = Server.working.get(self.server_id)
        order = ([first] if first in self.addresses else []) + [a for a in self.addresses if a != first]
        problems = []
        for address in order:
            try:
                if address != first:
                    try:
                        hello = _json(*_call(_connect(address, self.fingerprint, self.timeout), "GET", "/api/hello"))
                    except Unreachable:
                        raise
                    except ServerError:
                        hello = {}
                    if hello.get("server_id") != self.server_id:
                        raise Unreachable(f"A different server answers at {address}; it isn't used.")
                result = _call(_connect(address, self.fingerprint, self.timeout), method, path, body, self.key)
            except Unreachable as e:
                Server.working.pop(self.server_id, None)
                problems.append(str(e))
                continue
            Server.working[self.server_id] = address
            return result
        raise Unreachable(" ".join(problems) or "No server address is set.")

    @property
    def address(self):
        """The address that answered last (None before any request, or after all failed)."""
        return Server.working.get(self.server_id)

    def get(self, path):
        return _json(*self._request("GET", path))

    def post(self, path, body):
        return _json(*self._request("POST", path, body))

    def rows(self):
        """(suppressors, sbrs) in the app's CSV shape."""
        out = self.get("/api/rows")
        return out["suppressors"], out["sbrs"]

    def pdf(self, serial):
        """The approval PDF's bytes for a serial, or None if the server has none."""
        status, kind, data = self._request("GET", f"/pdf/{quote(serial, safe='')}")
        if status == 404:
            return None
        _json(status, kind, data)
        return data
