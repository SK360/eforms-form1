# Inventory server on Proxmox (LXC)

Optional. The helper works without it; this is for keeping one inventory on a home server that your PC and phone
both use, at its address on your home network. Nothing else is needed: no domain, no port forwarding, no Tailscale
(that's an optional extra for reaching it away from home, at the end). Try it with made-up data first (`demo`
below); your real inventory moves only when you choose to (section 5).

## 1. The container

In Proxmox, create a container:

- Template: **Debian 13** (Debian 12 works too). **Unprivileged**. Hostname `atf-inventory`.
- 1 core, 512 MB memory, 4 GB disk is plenty.
- Network: DHCP, then give it a **DHCP reservation** on your router so its address never changes.
- Leave **nesting** on (Options > Features; Proxmox's default, and needed for Docker inside it).

Only if you'll add Tailscale later: it needs the TUN device. On Proxmox 8.1 or newer: `pct set <id> --dev0 /dev/net/tun` on the host (or
Resources > Add > Device Passthrough), then restart the container. On older versions, add to
`/etc/pve/lxc/<id>.conf` instead:

```
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
```

## 2. The server: plain install (recommended in an LXC)

In the container's console:

```bash
apt update && apt install -y git python3-venv curl
useradd --system --home /var/lib/atf-inventory --create-home atf
git clone https://github.com/SK360/eforms-form1.git /opt/atf-inventory
cd /opt/atf-inventory && git checkout server
python3 -m venv server/.venv && server/.venv/bin/pip install -r server/requirements.txt
cp server/atf-inventory.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now atf-inventory
journalctl -u atf-inventory -n 20      # the first pairing code, the server's CA fingerprint, its addresses
```

Made-up inventory to try it with (only into an empty server):

```bash
/opt/atf-inventory/server/atf-server demo
```

Updating later: `cd /opt/atf-inventory && git pull && systemctl restart atf-inventory` (the server backs up the
database before any schema change).

### Or with Docker

With nesting on and Docker installed in the container: `cd /opt/atf-inventory/server && docker compose up -d`, then
`docker compose logs` for the pairing code and `docker compose exec inventory python -m atf_server demo` for made-up data.

## 3. Your phone, on your home Wi-Fi

The server makes its own small certificate authority (CA) at first start and signs its HTTPS certificate with it.
A phone trusts that CA once; after that, `https://<its-address>:8765` opens without warnings, encrypted and checked.
(The CA can only vouch for home-network addresses and names, so it can't be misused for real websites. Its key
never leaves the server.)

1. On the phone, open `https://<its-address>:8765` (for example `https://192.168.1.138:8765`). The first time,
   Safari warns that the connection isn't private: tap Show Details, then visit this website.
2. Open **Trust this server on this device** and follow it: download the certificate, then on an iPhone
   Settings › Profile Downloaded › Install, **and then turn it on**: Settings › General › About › Certificate Trust
   Settings › **ATF inventory server CA** (installing the profile alone isn't enough; Safari keeps saying "Not
   Secure"). Android: install it as a CA certificate under Settings › Security. Its fingerprint is in
   `journalctl -u atf-inventory` to compare. Close the tab and open the address again: no more warning.
3. Pair: enter the code from `journalctl -u atf-inventory` (made at first start, valid 5 minutes). A new one any time:
   `/opt/atf-inventory/server/atf-server pair-code`. After that, more devices are added from the page:
   Devices > Add a device.
4. Safari › Share › Add to Home Screen gives it an icon.

The app on your PC connects to the same address (Settings › Where your inventory lives) and trusts the same CA.
Keep the DHCP reservation: the server renews its certificate when its address changes, and the CA stays the same,
but the app and the phone's bookmark use the address.

## 4. Optional: Tailscale (away from home)

Only if you use Tailscale and want the phone page away from home. Everything above works without it.

```bash
curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list
apt update && apt install -y tailscale
tailscale up
tailscale serve --bg https+insecure://localhost:8765
```

(Debian 12: replace `trixie` with `bookworm`.) Tailscale asks you to approve the machine and, the first time, to
enable Serve for your tailnet. The server is then also at `https://atf-inventory.<your-tailnet>.ts.net` with
Tailscale's certificate (no CA to trust there); a phone pairs once per address. In the Tailscale admin console, an
access rule can limit it to your own devices.

## Firewall (optional)

The server only needs port 8765 from your home network. With nftables in the container, for example
(`192.168.1.0/24` = your home network):

```
nft add table inet atf
nft add chain inet atf input '{ type filter hook input priority 0; policy accept; }'
nft add rule inet atf input tcp dport 8765 ip saddr != { 127.0.0.1, 192.168.1.0/24 } drop
```

## 5. Moving your inventory to the server

1. If you tried it with made-up data, empty it first (a backup is made; devices and certificates stay):
   `/opt/atf-inventory/server/atf-server reset` and type RESET.
2. In the app: Settings › Where your inventory lives › connect (if you haven't), then **Move this computer's
   inventory to the server**. **Try it first** sends everything, checks every field of every row and every approval
   PDF, and takes it back: nothing changes. **Move it now** does the same and, only on an exact match, renames this
   computer's files (`data/suppressors.moved-to-server-<date>.csv`, `data/sbrs.moved-to-server-<date>.csv`; kept,
   never deleted) and uses the server from then on. Any difference: the server is emptied again and nothing on the
   computer changes. The approval PDFs stay in `out/approved/` on the computer as well.
3. Back to the computer any time: Settings › Where your inventory lives › Use this computer's inventory again ›
   **Copy the server's inventory to this computer first** (checked the same way; the server keeps its copy).

Filing and Check approvals then work on the server's inventory too. A row is marked pending ON THE SERVER before
Sign & Submit is clicked (if the server doesn't confirm it, nothing is submitted), and each filing result is written
to the computer's `data/outbox.jsonl` before it's sent, so a server that goes away mid-batch never loses one.

## Backups and the restore drill

The server copies the database into `<data>/backups/` once a day (the last 30 are kept), safely while it runs.
Proxmox snapshots or Proxmox Backup Server of the whole container cover the PDFs and certificate too.
`atf-server backup` makes one now; `atf-server restore <file>` puts one back (the current database is backed up
first). Do it once as a drill after moving: `backup`, then `restore` that file, then check the counts in the app.
The approval PDF files aren't in the database backups: Proxmox's cover them.

## Commands

`/opt/atf-inventory/server/atf-server --help` (as root: it runs them as the atf user with the data folder
`/var/lib/atf-inventory`): pair-code, devices, revoke <id>, fingerprint, import <app folder>, demo, backup,
restore <file>, reset.
With Docker: `docker compose exec inventory python -m atf_server <command>`.
