# Changelog

What changed in each release. The version is shown on the home screen (and by `./atf --version`). To hear about new
releases, choose **Watch > Custom > Releases** on the GitHub repository page.

To update: with Git, `git pull` in the folder, then run `./install.sh` (Windows: `.\install.cmd`) again in case the
requirements changed. Without Git, download the new release's ZIP and copy your `data`, `forms` and `out` folders into
the new copy.

## Unreleased

### New
- **Settings > Check my setup** (and `./atf check-setup`): checks, without opening eForms, that your settings are
  complete; that every file (fingerprints, photo, trust document, supporting documents, each responsible person's) is
  there, readable, the right kind and not an online-only OneDrive/iCloud copy; that each 5320.23 template stamps
  correctly; that the browser is installed; and that the inventory isn't open in Excel or waiting on a pending row.
  Individuals now get a small Settings menu too (All settings, Check my setup).
- **Your inventory on your phone** (Inventory > Open on my phone, or `./atf serve`): scan the QR code on the same
  Wi-Fi to see your inventory, search by a serial's digits or a design, open approval PDFs, and mark approved
  suppressors built (with their design) or clear that, while you walk through your safes. Served from your computer
  only while it's open; nothing goes to the internet. **Run the installer again** after updating (`./install.sh`,
  Windows `.\install.cmd`) for the QR code; without it the link is printed instead.
- **Optional inventory server** (`server/`; see [server/README.md](server/README.md)). Completely optional: nothing
  changes unless you set one up. For keeping your inventory in one place on an always-on machine at home (Proxmox,
  Docker, Linux): daily backups and restore, a history of every change (builds excepted: when something was built is
  never recorded), the phone page always available to every paired phone with live updates, and the same inventory on
  every paired device. Settings > **Where your inventory lives** connects the helper (pairing code, certificate
  check), moves your inventory there (checked field by field first; **Try it first** changes nothing) and back.
  Filing and Check approvals work with it, with extra safety: the server must confirm "pending" before Sign & Submit,
  and filing results are kept on your computer until the server has them. Home network only: no internet, no
  Tailscale or VPN needed (optional for away from home). Tested on a Proxmox container with an iPhone and a Mac,
  including a real move and a real filing; Check approvals through the server and Windows not yet tried for real.

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
