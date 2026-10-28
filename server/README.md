# The inventory server (optional)

**You don't need this.** The helper does everything on its own: it files, checks approvals, keeps your inventory in
`data/suppressors.csv` on your computer, and can show it on your phone while it's open. Nothing here changes that,
and if you never set up a server you'll only ever see one extra line in Settings ("Where your inventory lives: this
computer").

The inventory server is for people who'd rather keep their inventory in one central place, on an always-on machine at
home, with automatic backups and a phone page that's always there. It's a small program you run on your own hardware
(a Proxmox container, Docker, or any Linux machine). It never touches the internet unless you add your own VPN, and
you can move your inventory back to your computer at any time.

## Do you want it?

**Probably not, if** you use one computer, you're happy with a CSV file you can open in Excel, and you already back
that computer up. The helper on its own is simpler, and that's how most people use it.

**Worth a look, if** any of these sound like you:

- You'd like your inventory **somewhere safer than one laptop**, with backups made every day without you thinking
  about it, and the option of restoring yesterday's copy.
- You want the **phone page available any time**: checking a serial at the safe, marking a build, opening an
  approval PDF, without starting the helper on your computer first.
- You use **more than one device** (a phone, maybe a second computer) and want them all to see the same inventory,
  updated live, without copying files around.
- You already run a **home server** (Proxmox, a NAS with Docker, a Raspberry Pi with Linux) and like having your
  records there.

You'll need: a machine at home that's always on, and to be comfortable following a page of setup commands once
([LXC.md](LXC.md)). It costs nothing beyond that machine.

## What it gets you

- **One inventory, in one place.** Your computer and your phone read and write the same records on the server. There's
  no syncing and no second copy to drift out of date: once connected, the server *is* your inventory.
- **Your records, protected.** The inventory lives in a database (SQLite) that enforces the rules itself: a serial
  appears once, an application number once, only approved suppressors can be marked built, filed and approved rows
  can't be edited as if they were queued. Two devices can't overwrite each other: if the phone changes a row while
  the computer has it open, the computer's change is refused and you're shown the row as it is now.
- **Backups without thinking about it.** A copy of the database every day (the last 30 kept), made safely while it
  runs, and a one-line restore. A Proxmox backup of the whole container covers everything, approval PDFs included.
- **A history of every change**, with the device that made it: queued, edited, filed, approved, and so on (see
  "Builds" below for the one exception).
- **The phone page, always on.** The same page as the helper's "Open on my phone", served by the server: search by
  serial digits or design, filters, details, approval PDFs, mark builds. Several phones can use it at once and every
  open page updates by itself when something changes. Pair each phone once.
- **Approval PDFs in one place**, viewable from the phone or the computer.
- **Filing that's at least as safe as before.** Filing still runs on your computer (it needs your eForms login), but
  with the server it's stricter:
  - Before the browser opens, the server must answer and every suppressor you're filing must still be queued there.
    If the phone changed one meanwhile, nothing starts.
  - Just before Sign & Submit, the server must confirm the row is "pending". If it doesn't, Sign & Submit isn't
    clicked.
  - Each filing result is written to a small retry file on your computer *before* it's sent to the server. If the
    server goes away mid-batch, nothing is lost: it's sent again automatically, the batch stops until it's delivered,
    and it's never applied twice.
- **Check approvals** records new approvals, SBRs and approval PDFs straight onto the server.

## What stays on your computer

The server only ever holds the inventory: suppressors, SBRs, builds, approval PDFs and the change history. Everything
else stays on your computer, as before:

- your settings, 5320.23 templates, fingerprint files, photos and trust document;
- your eForms login (the browser session), and the filing itself;
- `out/submissions.csv`, the log of every filing (kept on the computer as a second record).

Your password, SSN, emailed codes and PIN are never stored anywhere, server included.

## Privacy and security

- **Home network only.** The server listens on your home network and nowhere else. There's no port forwarding and no
  cloud service. Reaching it away from home is up to you and entirely optional (for example Tailscale, or your own
  VPN): nothing in the helper or the server needs it.
- **Every device pairs once.** A browser or computer gets in only after you enter a 6-digit pairing code made on an
  already-paired device (or on the server). You can see and remove devices at any time (Devices on the phone page).
- **Encrypted at home too.** The server makes its own small certificate authority and uses HTTPS. Your computer checks
  it automatically. A phone trusts it once (the page walks you through it: download, install, turn on full trust) and
  then opens the server with no warnings. That authority can only vouch for home-network addresses, so it can't be
  misused for real websites, and its key never leaves the server.
- **Builds: no record of when.** The server keeps only whether a suppressor is built and its design. Marking or
  clearing a build adds nothing to the history, changes no "last changed" time, and isn't written to the server's log.
  (The daily backups do hold each day's state, like any backup.)
- **Treat the server's disk as personal records.** Approval PDFs show your name and address. Keep the machine at home
  and back it up.

## How it fits together

```
 Your computer (the helper)                         The server (at home, always on)
 ─────────────────────────                          ───────────────────────────────
 settings, templates, files     ── inventory ──▶    inventory (SQLite) + history
 eForms login, filing, approvals ◀── changes ──     approval PDFs, daily backups
 out/submissions.csv, retry file                    the phone page
                                                          ▲
 Your phone (Safari, on your home Wi-Fi) ─────────────────┘
```

## Setting it up

1. **Install it** on your always-on machine: [LXC.md](LXC.md) has the steps for a Proxmox container (a plain install
   with a system service, recommended), and for Docker. Any Linux machine with Python 3.11 or newer works the same way.
2. **Try it with made-up data first** (`atf-server demo`): pair your phone, look around, mark a few builds.
3. **Connect the helper**: Settings > Where your inventory lives > Connect to my server. It shows the server's
   fingerprint to compare with the one on your phone's Devices page, and asks for a pairing code.
4. **Move your inventory** (after `atf-server reset` clears the made-up data): Settings > Where your inventory lives >
   Move this computer's inventory to the server. **Try it first** does everything and then takes it back, so nothing
   changes. **Move it now** does the same, and only if every field of every row and every approval PDF matches does it
   switch over. Your computer's files are kept, renamed `suppressors.moved-to-server-<date>.csv`.

## Day to day

Use the helper exactly as before: the menu, filing, approvals, builds, Excel export all work, now on the server's
inventory. The home screen says "On your server". The phone page is at `https://<the server's address>:8765`.

**If the server can't be reached**, the helper shows the last inventory it saw, marked offline and read-only. Changes
and filing wait until the server answers. A filing result that couldn't be delivered is shown on the home screen
until it has been.

## Backups and restoring

- Daily: the server copies its database into its `backups/` folder (30 kept). `atf-server backup` makes one now.
- `atf-server restore <file>` puts one back (the current database is saved first). Try it once as a drill.
- Proxmox (or your NAS's) backups of the whole machine cover the approval PDFs and certificates too. Do that as well.

## Going back

Settings > Where your inventory lives > Use this computer's inventory again > **Copy the server's inventory to this
computer first**. It writes your inventory back into `data/`, fetches any approval PDFs your computer doesn't have,
checks the copy field by field, and disconnects. The server keeps its copy until you remove it.

## Questions

- **Do I need Tailscale or a VPN?** No. Everything works on your home network. A VPN only adds the phone page away
  from home, if you want that.
- **Does it need the internet?** No (only to install it).
- **Can two people use it?** It's one inventory. Each device pairs on its own, and every change in the history shows
  which device made it, but there are no user accounts.
- **Can I file from the phone?** No. Filing needs your eForms login and MFA, so it stays on your computer.
- **What if the server breaks?** Restore a backup onto a new one, or copy your inventory back to the computer from a
  backup. Your computer also keeps the CSV files from when you moved, and `out/submissions.csv`.

## Status

New, and run so far on one setup: a Proxmox container (Debian 13) at home, with an iPhone and a Mac: a real
inventory moved onto it, a backup restored as a drill, and a real filing made through it. Check approvals through
the server is tested but hasn't run against real approvals yet; Windows hasn't been tried with a server yet. Tests
cover the server, the helper with a server, and moving an inventory there and back. Technical details for
developers: `CLAUDE.md` in the repository root, and the code in this folder.
