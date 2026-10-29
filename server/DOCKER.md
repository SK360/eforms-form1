# Inventory server with Docker Compose

Optional, like the server itself ([README.md](README.md) explains what it's for and whether you'd want it). This page
is for running it with **Docker Compose** on any always-on machine at home: a Linux box, a NAS that runs containers
(Synology Container Manager, Unraid, TrueNAS, QNAP), a mini PC, a Raspberry Pi 4/5 with 64-bit Linux, or a Mac or
Windows PC with Docker Desktop that stays on. On Proxmox, [LXC.md](LXC.md) is the simpler route (no Docker needed).

You need: Docker with Compose (`docker compose version` should answer), Git, and the machine's **address on your
home network** (for example `192.168.1.50`). Give that machine a **DHCP reservation** in your router so the address
never changes: your phone and the helper use it.

Status: the image is built and started on every change in CI, and the server inside it is the same one that has run
for real in a Proxmox container. Running it with Docker on a NAS or Docker Desktop hasn't been reported yet: if you
try it, an issue saying how it went helps.

## 1. Get it and set your address

```bash
git clone https://github.com/SK360/eforms-form1.git atf-inventory
cd atf-inventory/server
```

Open `compose.yaml` and put **your machine's address** in `ATF_NAMES` (and, if you like, a name your router gives
it), **before the first start**:

```yaml
    environment:
      ATF_SERVER_NAME: ATF inventory
      ATF_NAMES: 192.168.1.50,nas.home.arpa
```

Why: inside Docker the server only sees the container's own internal address, not your machine's. Its certificate
has to name the address your phone opens, or Safari keeps warning even after you trust the server. (The helper on
your computer doesn't mind either way.) Names ending in `.local`, `.lan`, `.home.arpa` or `.internal`, and any
home-network address, can be added later: change `ATF_NAMES` and restart, and the server issues itself a new
certificate. Another kind of name (your own domain) only takes effect if it's there at the first start.

## 2. Start it

```bash
docker compose up -d --build
docker compose logs inventory
```

The log shows:
- `No device is paired yet. Pairing code (valid 5 minutes): 123456`: the first pairing code.
- `Server CA fingerprint: …`: the server's certificate authority, to compare when you trust it and when the helper
  connects.
- `HTTPS for: …`: the addresses and names its certificate covers. Your machine's address should be in it.

Try it with made-up data first (A101…, nothing real):

```bash
docker compose exec inventory python -m atf_server demo
```

## 3. Your phone

On your home Wi-Fi, open `https://192.168.1.50:8765` (your address). The first time, the browser warns that the
connection isn't private: on an iPhone, Show Details, then visit this website. Then:

1. **Trust this server on this device** (on the page): download the certificate; on an iPhone Settings › Profile
   Downloaded › Install, **then turn it on** in Settings › General › About › Certificate Trust Settings › ATF inventory
   server CA (installing alone isn't enough); on Android, install it as a CA certificate under Settings › Security.
   Close the tab and open the address again: no more warning.
2. **Pair**: enter the code from the log. A new one any time:
   `docker compose exec inventory python -m atf_server pair-code`. After that, add more devices from the page
   (Devices › Add a device).

## 4. The helper on your computer

In `./atf`: Settings › Where your inventory lives › Connect to my server, with the same address. When you're ready
for your real inventory: `docker compose exec inventory python -m atf_server reset` (type RESET) to clear the
made-up data, then Move this computer's inventory to the server (Try it first changes nothing). See
[README.md](README.md) for the details.

## Commands

All server commands run inside the container, as `docker compose exec inventory python -m atf_server <command>`:

| Command | What it does |
|---|---|
| `pair-code` | a 6-digit pairing code, valid 5 minutes |
| `devices` / `revoke <id>` | list paired devices / remove one |
| `fingerprint` | the server CA's fingerprint |
| `demo` | a made-up inventory, into an empty server |
| `backup` | a database backup now (one is made every day; 30 kept) |
| `restore /data/backups/<file>` | put a backup back (the current one is saved first) |
| `reset` | remove the whole inventory (after a backup); devices and certificates stay |

## Your data, backups and updates

Everything lives in the Docker volume `inventory-data` (mounted at `/data`): the database, approval PDFs, the
server's certificates, and the daily backups in `backups/`. It holds personal records (approval PDFs show your name
and address): keep it on the machine at home.

- **Back up the volume** too, not only the database (the daily backups don't include the PDF files). For example,
  into a file in the current folder:
  ```bash
  docker run --rm -v server_inventory-data:/data -v "$PWD":/out alpine tar czf /out/inventory-data.tgz -C /data .
  ```
  (`docker volume ls` shows the volume's exact name.) Your NAS's own backup of the volume works as well.
- **Update:** `git pull`, then `docker compose up -d --build`. The server backs up its database before any change to
  how it's stored.
- **Stop it:** `docker compose down` (the volume and your data stay). `docker compose down -v` deletes the volume:
  only after you've moved your inventory back to your computer.

## Optional extras

- **Only your home network:** the server is published on every network the machine is on. To limit it to one, put
  the address in the port: `- "192.168.1.50:8765:8765"`, or use the machine's firewall.
- **Away from home:** only if you want it, with your own VPN. With Tailscale on the Docker host:
  `tailscale serve --bg https+insecure://localhost:8765`, then pair the phone at that address too. Nothing needs it.
