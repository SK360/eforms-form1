# Inventory server on Proxmox (LXC)

Optional. The helper works without it; this is for keeping one inventory on a home server that your PC and phone
both use. Phase 1 (this version) is **read-only with made-up data**: try it before any real records move.

## 1. The container

In Proxmox, create a container:

- Template: **Debian 12**. **Unprivileged**. Hostname `atf-inventory` (this becomes its tailnet name).
- 1 core, 512 MB memory, 4 GB disk is plenty.
- Network: DHCP, then give it a **DHCP reservation** on your router so its address never changes.
- Turn on **nesting** (Options > Features) only if you'll use Docker inside it.

Tailscale needs the TUN device. On the Proxmox host, add to `/etc/pve/lxc/<id>.conf`, then restart the container:

```
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
```

## 2. The server: plain install (recommended in an LXC)

In the container's console:

```bash
apt update && apt install -y git python3-venv
useradd --system --home /var/lib/atf-inventory --create-home atf
git clone https://github.com/SK360/eforms-form1.git /opt/atf-inventory
cd /opt/atf-inventory && git checkout server
python3 -m venv server/.venv && server/.venv/bin/pip install -r server/requirements.txt
cp server/atf-inventory.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now atf-inventory
journalctl -u atf-inventory -n 20      # shows the first pairing code and the certificate fingerprint
```

Made-up inventory to try it with (only into an empty server):

```bash
sudo -u atf env ATF_DATA=/var/lib/atf-inventory /opt/atf-inventory/server/.venv/bin/python -m atf_server demo
```

Updating later: `cd /opt/atf-inventory && git pull && systemctl restart atf-inventory` (the server backs up the
database before any schema change).

### Or with Docker

With nesting on and Docker installed in the container: `cd /opt/atf-inventory/server && docker compose up -d`, then
`docker compose logs` for the pairing code and `docker compose exec inventory python -m atf_server demo` for made-up data.

## 3. Tailscale (the phone, at home and away)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
tailscale serve --bg https+insecure://localhost:8765
```

The server is then at `https://atf-inventory.<your-tailnet>.ts.net` with a real certificate. In the Tailscale admin
console, an access rule can limit it to your own devices.

## 4. Pair your phone

1. On the phone (on the tailnet), open `https://atf-inventory.<your-tailnet>.ts.net`. It asks for a pairing code.
2. Enter the code from `journalctl -u atf-inventory` (made at first start, valid 5 minutes). A new one any time:
   `sudo -u atf env ATF_DATA=/var/lib/atf-inventory /opt/atf-inventory/server/.venv/bin/python -m atf_server pair-code`
3. After that, more devices are added from the page: Devices > Add a device.

The local address (`https://<its-ip>:8765`) is for the app on your PC (phase 2), which checks the server's certificate
by its fingerprint. A browser there warns about that certificate; use the tailnet name in browsers.

## Firewall (optional)

The server only needs port 8765 from your home network. With nftables in the container, for example
(`192.168.1.0/24` = your home network):

```
nft add table inet atf
nft add chain inet atf input '{ type filter hook input priority 0; policy accept; }'
nft add rule inet atf input tcp dport 8765 ip saddr != { 127.0.0.1, 192.168.1.0/24 } drop
```

## Backups

The server copies the database into `<data>/backups/` once a day (the last 30 are kept), safely while it runs.
Proxmox snapshots or Proxmox Backup Server of the whole container cover the PDFs and certificate too.
`python -m atf_server backup` makes one now.

## Commands

`python -m atf_server --help`: serve, pair-code, devices, revoke <id>, fingerprint, import <app folder>, demo, backup.
The data folder is `$ATF_DATA`.
