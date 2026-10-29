# Changelog

What changed in each release. The version is shown on the home screen (and by `./atf --version`). To hear about new
releases, choose **Watch > Custom > Releases** on the GitHub repository page.

To update: with Git, `git pull` in the folder, then run `./install.sh` (Windows: `.\install.cmd`) again in case the
requirements changed. Without Git, download the new release's ZIP and copy your `data`, `forms` and `out` folders into
the new copy.

## 1.1.0 (2026-10-28)

Your inventory on your phone, a setup checker, and an **optional** inventory server for keeping your records on a
machine at home. Nothing changes in how you file unless you choose the server.

**Updating:** `git pull`, then run the installer again (`./install.sh`, Windows `.\install.cmd`): there's one new
package (`segno`, for the QR code). Your settings, inventory and files carry over unchanged.

### New: your inventory on your phone
**Inventory > Open on my phone** (or `./atf serve`) shows your inventory in your phone's browser while you walk
through your safes and bags. Nothing to set up: the helper shows a QR code, you scan it on the same Wi-Fi.

- Search by typing a serial's digits on the number pad (`83` finds A083), or switch to letters (ABC) to search by
  design, caliber or model.
- Filters: All, Built, Not built, Waiting, Queued, SBRs. A summary line: how many suppressors, built, not built, SBRs.
- Each suppressor's details: caliber, length, filed and approval dates, the last 4 digits of its application number
  and eForm ID, any approval conditions, and its **approval PDF**.
- **Built** switch with the design, for approved suppressors (the only thing the page can change). Turning it off
  asks on the page first (Keep it / Clear).
- One screen at a time on a phone (the phone's back button and swipe return to the list), list and details side by
  side on a wider screen. Safari's Share > Add to Home Screen gives it an icon.
- It works only while you leave it open in the helper, with a new key in the link each time, and nothing goes to the
  internet. If the phone can't connect, allow Python through your computer's firewall.

### New: Check my setup
**Settings > Check my setup** (and `./atf check-setup`, which also shows the file paths) checks, without opening
eForms:
- that your settings are complete;
- every file (fingerprints, photo, trust document, supporting documents, each responsible person's): there,
  readable, the right kind (an EFT that looks like one, a JPG/PNG photo), and not an online-only OneDrive/iCloud
  placeholder;
- that each 5320.23 template (yours and each responsible person's) stamps correctly;
- that the browser is installed, and that the helper's folder isn't inside a syncing cloud folder;
- that the inventory isn't open in Excel and has no pending row waiting to be resolved.

Individuals now get a Settings menu too (All settings, Check my setup).

### New, optional: an inventory server
**You don't need it, and most people won't want it.** For people who'd rather keep their inventory in one place on
an always-on machine at home, with backups and a phone page that's always there. Full details:
[server/README.md](server/README.md). Without a server, the only thing you'll see is a new line in Settings, **Where
your inventory lives (this computer)**.

- **Runs on your own hardware, on your home network:** Docker Compose on a NAS or any Linux machine
  ([server/DOCKER.md](server/DOCKER.md)), or a Proxmox container ([server/LXC.md](server/LXC.md)). No cloud, no port
  forwarding, no internet. A VPN such as Tailscale is optional (for the phone page away from home); nothing needs it.
- **One inventory for every device:** the helper and every paired phone read and write the same records, with no
  syncing. Open pages update by themselves when something changes.
- **Your records, protected:** a database that enforces the rules itself (one row per serial, one application number
  each, only approved suppressors can be built, filed and approved rows can't be edited as queued); two devices can't
  overwrite each other (a stale change is refused and you're shown the current row); daily backups (30 kept) and a
  one-line restore.
- **A history of every change**, with the device that made it. **Builds excepted:** when something was built or
  un-built is never recorded anywhere (no history entry, no timestamp, no log line); only whether it's built and its
  design are kept.
- **The phone page, always on**, for every paired phone, with history, live updates, Devices (add one with a 6-digit
  code, remove a lost one) and a CSV export. Each phone trusts the server's own certificate once (the page walks you
  through it), then opens it with no warnings. That certificate authority can only vouch for home-network addresses.
- **Connecting the helper:** Settings > Where your inventory lives > Connect to my server (its address, a fingerprint
  to compare, a pairing code). Test the connection, or switch back to this computer at any time.
- **Moving your inventory there:** **Try it first** sends everything, checks every field of every row and every
  approval PDF, and takes it back: nothing changes. **Move it now** switches over only on an exact match, and keeps
  your computer's files (renamed `suppressors.moved-to-server-<date>.csv`). Switching back offers to copy the
  server's inventory to your computer first, checked the same way.
- **Filing and Check approvals with a server**, with extra safety: before the browser opens, the server must answer
  and every suppressor must still be queued there (if the phone changed one, nothing starts); just before Sign &
  Submit, the server must confirm "pending" or nothing is signed; each filing result is written to a file on your
  computer before it's sent, so a server that goes away mid-batch never loses one (it's sent again automatically,
  once, and the batch waits). Approvals, SBRs and new approval PDFs go to the server.
- **If the server can't be reached:** the helper shows the last inventory it saw, marked offline and read-only, and
  changes and filing wait.
- What never goes to the server: your settings, 5320.23 templates, fingerprints, photos, trust document, eForms login,
  and of course your password, SSN, codes and PIN (stored nowhere).

### Changed
- **README** reorganised: getting set up, using it (by task), your records, the optional server, troubleshooting,
  with a contents line. New: the phone page with screenshots (four new ones).
- **Settings** has a new item, **Where your inventory lives**.
- Inside the helper, adding, editing and removing queued suppressors, resolving a pending row, and the moment a
  filing is marked pending and then filed now go through one place each (so they work the same with or without a
  server). What they do without a server is unchanged; the automated tests cover it.

### Tested
- **For real:** the phone page on an iPhone (Safari); Check my setup on a Mac; the inventory server in a Proxmox
  container with an iPhone and a Mac: pairing, trusting its certificate, builds from the phone (also in a basement
  away from the computer), moving a real inventory onto it, a backup restored as a drill, a practice run and a real
  filing through it.
- **Automated** (on every change, on Windows with Python 3.9, 3.13 and 3.14, and on Linux): the whole helper, the
  phone page in Chrome's and Safari's engines, and the server (its rules, moving an inventory there and back, filing
  against a fake eForms with the server refusing at each step), including the server's Docker image starting up.
- **Not yet tried for real:** Check approvals through the server; the server with Docker on a NAS or Docker Desktop;
  the server with a Windows PC or an Android phone; a real filing without a server on this version (the changed
  parts are covered by the automated tests).

### For developers
- `tools/scan_personal.py` and git hooks (`git config core.hooksPath .githooks`) keep personal values from your own
  records out of commits and commit messages (the repository is public).
- Tests always run in a temporary folder and fail if your real `data/` folder changes.

## 1.0.0 (2026-10-27)

The first release: everything built so far.

### Filing
- **File Form 1s for suppressors** as a trust or as an individual. One confirmation per batch (your "I affirm" and
  your OK to sign), then each form is filled, read back against your records, certified and signed. You log in,
  optionally type your SSN, and enter the emailed code and your PIN for every submission; the helper never asks for,
  stores or types any of those.
- **Trusts:** each suppressor gets its own 5320.23, copied from one signed template you make once, with the caliber,
  serial and today's date stamped on it (and your current CLEO official).
- **Other responsible persons** in a trust (e.g. a co-trustee), each with their own template, photo and fingerprints.
- **Individuals:** ATF's questions 11.A-11.M are asked once in setup, filled on every form, read back on the verify
  page and shown in each batch's confirmation. It only files when every answer is No.
- **Safety checks:** nothing is signed unless every read-back matches, eForms says the form validated, and the
  fingerprint file shows "Valid EBTS Package". A form interrupted while signing is marked pending until you check the
  dashboard, so it's never filed twice. Ctrl-C and errors log you out of eForms.
- **Practice run** (Advanced): fills a whole form without submitting.

### Inventory
- **Add suppressors** one at a time or as a run of serials (A101 … A110) with the same caliber and length.
- **Inventory** of every suppressor (queued, filed, approved), with filed dates, days waiting and approval dates;
  views for approved, in progress, queued, built and not built; **export to Excel**.
- **Check approvals on eForms** (read-only): downloads your approved Form 1 PDFs, records approval dates, lengths,
  control numbers and conditions, and points out anything that differs from your records. Approved SBRs are kept in
  their own list.
- **Record builds:** mark approved suppressors as built, with what each one is.
- **Building one? Find the serial to use:** pick the caliber and the length you're building; it gives the first
  approved, not built serial of that caliber approved at that length or longer.

### Setup
- A setup wizard that fills in most of your settings from your 5320.23 template.
- Mac and Windows installers (Python 3.9-3.14); tests run on Windows and Linux for every change.

### Tested live vs. not yet
- **Filed live:** dozens of real trust filings (one responsible person) on Mac and Windows, by several people, and
  Check approvals across several pages of approvals.
- **Practice runs only, not filed for real yet:** filing as an individual; a trust with a second responsible person.
  Do a Practice run (Advanced) first and check every page.
