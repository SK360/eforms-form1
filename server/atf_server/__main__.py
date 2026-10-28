"""python -m atf_server <command>. The data folder is $ATF_DATA (Docker: /data), else ./inventory-data.

  serve            run the server (HTTPS with its own certificate on port 8765; --http behind a proxy only)
  pair-code        a 6-digit code to pair a device, valid 5 minutes (the first device, or when none is at hand)
  devices          list paired devices;  revoke <id>  removes one
  fingerprint      the server's CA fingerprint (what phones and the app trust; also on the page under Devices)
  import <folder>  load an app folder's inventory (data/*.csv, out/approved/) into an EMPTY server; reads only
  demo             fill an empty server with a made-up inventory (A101...) to try it
  backup           a backup now into <data>/backups (the server also makes one each day, keeping 30)
"""
import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

from . import db, demo, tls


def _data(args):
    return Path(args.data or os.environ.get("ATF_DATA") or "inventory-data").resolve()


def _open(args):
    folder = _data(args)
    folder.mkdir(parents=True, exist_ok=True)
    conn = db.connect(folder / "inventory.db")
    db.migrate(conn, folder / "backups")
    return folder, conn


def _daily_backups(folder):
    while True:
        try:
            conn = db.connect(folder / "inventory.db")
            try:
                if not list((folder / "backups").glob(f"inventory-{time.strftime('%Y-%m-%d')}*.db")):
                    db.backup(conn, folder / "backups")
            finally:
                conn.close()
        except Exception as e:  # a failed backup must not stop the server; it's logged and tried again
            logging.getLogger("atf_server").error("backup failed: %s", e)
        time.sleep(3600)


def serve(args):
    import uvicorn
    from .app import create_app
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    folder, conn = _open(args)
    try:
        if not any(d["revoked_at"] is None for d in db.devices(conn)):
            code, _ = db.new_pair_code(conn)
            print(f"No device is paired yet. Pairing code (valid {db.PAIR_MINUTES} minutes): {code}\n"
                  "Open this server's page in a browser and enter it. Another code: python -m atf_server pair-code",
                  flush=True)
    finally:
        conn.close()
    ssl = {}
    ca = None
    if not args.http:
        cert, key = tls.ensure_certificate(folder / "tls", names=(os.environ.get("ATF_NAMES") or "").split(","))
        ssl, ca = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}, tls.ca_path(folder / "tls")
        print(f"Server CA fingerprint: {tls.fingerprint(ca)}\nHTTPS for: {', '.join(tls.addresses())}", flush=True)
    threading.Thread(target=_daily_backups, args=(folder,), daemon=True).start()
    app = create_app(folder, name=os.environ.get("ATF_SERVER_NAME") or "ATF inventory", ca=ca)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, proxy_headers=False, **ssl)


def main(argv=None):
    p = argparse.ArgumentParser(prog="atf_server", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", help="data folder (default $ATF_DATA, else ./inventory-data)")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--host", default=argparse.SUPPRESS)
    s.add_argument("--port", type=int, default=argparse.SUPPRESS)
    s.add_argument("--http", action="store_true", help="plain HTTP (only behind a proxy on the same machine, or tests)")
    sub.add_parser("pair-code")
    sub.add_parser("devices")
    r = sub.add_parser("revoke")
    r.add_argument("device_id", type=int)
    sub.add_parser("fingerprint")
    i = sub.add_parser("import")
    i.add_argument("folder", help="the app's folder (with data/suppressors.csv)")
    sub.add_parser("demo")
    sub.add_parser("backup")
    p.set_defaults(host=os.environ.get("ATF_HOST", "0.0.0.0"), port=int(os.environ.get("ATF_PORT", "8765")), http=False)
    args = p.parse_args(argv)
    try:
        if args.cmd in (None, "serve"):
            return serve(args)
        folder, conn = _open(args)
        if args.cmd == "pair-code":
            code, expires = db.new_pair_code(conn)
            print(f"Pairing code: {code} (valid {db.PAIR_MINUTES} minutes, until {expires} UTC)")
        elif args.cmd == "devices":
            for d in db.devices(conn):
                print(f"{d['id']:>3}  {d['name']:<20} paired {d['created_at']}  last seen {d['last_seen'] or '-'}"
                      + (f"  REMOVED {d['revoked_at']}" if d["revoked_at"] else ""))
        elif args.cmd == "revoke":
            db.revoke(conn, args.device_id, "server console")
            print("Removed.")
        elif args.cmd == "fingerprint":
            tls.ensure_certificate(folder / "tls", names=(os.environ.get("ATF_NAMES") or "").split(","))
            print(tls.fingerprint(tls.ca_path(folder / "tls")))
        elif args.cmd in ("import", "demo"):
            sup, sbrs, pdfs = db.read_app_folder(args.folder) if args.cmd == "import" else demo.inventory()
            counts = db.import_inventory(conn, folder / "approved", sup, sbrs, pdfs, device=f"server console ({args.cmd})")
            print(f"Imported {counts['suppressors']} suppressors, {counts['sbrs']} SBRs, {counts['pdfs']} approval PDFs.")
        elif args.cmd == "backup":
            print(db.backup(conn, folder / "backups", label=time.strftime("%Y-%m-%d-%H%M%S")))
        conn.close()
    except db.Refused as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
