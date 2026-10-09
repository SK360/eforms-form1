# eForms Form 1 helper (suppressors, filed by a trust)

Fills and submits ATF Form 1s in eForms for suppressors your trust makes, and prepares the 5320.23 Responsible Person
Questionnaire for each one. You stay in the loop: you log in, optionally type your SSN, and enter the emailed code and
your PIN for every submission.

> **Disclaimer.** Unofficial, provided as-is with no warranty, not affiliated with or endorsed by ATF. You are solely
> responsible for every application it files for you: each submission is your certification under penalty of perjury.
> Review what it fills, check ATF's eForms terms of use, and stop if anything looks wrong. It never asks for, stores or
> types your password, SSN, emailed code or PIN.

Currently supports: one trust, one responsible person, suppressors (Form 1), on a Mac or Windows PC.

## Before you start
- An eForms account, your trust document (PDF), your fingerprint file (`.eft`), and a photo of you (JPG/PNG;
  eForms requires it for the responsible person, though the 5320.23 PDF itself doesn't need one).
- **One signed 5320.23 template** (see the next section).
- Optional: supporting documents for the firearm (e.g. photos of the tools/materials you'll use).

## Making your 5320.23 template (once)
You make one signed 5320.23 with placeholder caliber and serial; for each suppressor the helper copies it, replaces
the caliber and serial, and stamps the date you file.

At https://jakepro716.github.io/atf-5320-generator/ fill in a **single form** (not the CSV/batch mode):

1. **Form:** ATF Form 1.
2. **Applicant:** your trust's name and address. **Responsible person:** your own details.
3. **SSN:** optional. Whatever you enter is printed on every 5320.23 made from this template (and kept in `out/`);
   leaving it blank is the more private choice.
4. **Photo:** skip it; it isn't needed on the PDF (your photo is uploaded to eForms separately).
5. **Firearm:** type silencer; maker = your trust's name and address; model as you'll set it in the helper (e.g. N/A);
   **caliber `00`** and **serial `TEMPLATE`**. Fill these in; don't leave them blank, the helper looks for them.
6. **CLEO:** agency, official's name and title, and the agency's full mailing address.
7. **Questions, citizenship, place of birth:** answer them truthfully; they carry into every filing unchanged.
8. **Sign, date it, download** the **ATF copy** (the 2-page questionnaire, not the CLEO copy).

On the first run of `./atf`, the helper asks for this PDF first (drag it into the window) and reads your answers
from it: trust name, date of birth, states, CLEO details and model come pre-filled, so you mostly press Enter (it
never reads the SSN). It also test-stamps the template: "✔ your 5320.23 template stamps correctly" means you're
set. Before your first filing, look at one result: Advanced > Regenerate a 5320.23 writes it to `out/`.

- Each filing re-stamps the signature date next to the signature you made once. If you'd rather sign fresh, remake
  the template in the generator before a batch (a couple of minutes), then in `./atf` choose Settings and answer
  **No** to "Keep your current 5320.23 template?".
- Remake it whenever something on it changes: your address, the model, the CLEO agency or address. (A new official
  at the same agency only needs Settings: the helper stamps the current official's name on every copy.)

## Get it
- **With Git** (Mac: built in; Windows: install https://git-scm.com/download/win):
  `git clone https://github.com/SK360/eforms-form1.git` and later `git pull` (in that folder) to update.
- **Without Git:** on the repository page (you need to be added as a collaborator and logged in to GitHub):
  Code > Download ZIP, then unzip it. To update, download it again and copy your `data`, `forms` and `out`
  folders into the new copy.

## Install (once)
It needs Python 3.9 or newer.

**Mac** (Terminal, in the folder; macOS offers to install Python the first time if needed):
```bash
./install.sh
```
**Windows** (in the folder, right-click > Open in Terminal). First install Python from
https://www.python.org/downloads/ and tick **"Add python.exe to PATH"**. Then:
```
.\install.cmd
```

## Use
Mac: `./atf`  Windows: `.\atf.cmd` (always with the `.cmd`)

The first run asks for your trust, CLEO and file details (Settings; change them any time). Then:

- **Add suppressors**: suggests the next serial and your last caliber; Enter accepts.
- **File N queued suppressors**: pick the rows, say whether you want to type your SSN on each form, confirm (this is
  your "I affirm" and your OK to Sign & Submit each one once every check passes). A browser opens: log in, wait for
  the eForms home page, press Enter. For each suppressor it fills the form, shows each page ticked off and a read-back
  table, then a yellow **"Your turn in the browser"** box: enter the emailed code, Verify, enter your PIN, Submit.
- **⚠ Resolve a pending row**: only if a confirmation was missed. Check the eForms dashboard, then say whether it was filed.
- **Advanced**: preview the steps without a browser, regenerate a 5320.23 with another date, re-file, capture portal
  pages if ATF changes the site.

Ctrl-C is safe at any point: it logs you out of eForms first (eForms allows one session at a time).

## Your data stays local
Everything personal lives in `data/`, `forms/`, `out/` and `.browser-profile/` (your logged-in session), which are
never committed. **Never zip or copy this folder to share it**; share the git repository instead.

## Troubleshooting
- "Another user is logged in": a previous run didn't log out; wait ~15-30 min or log out in the other browser.
- A step fails: the browser stays open on that page; press Enter to log out. Check the eForms dashboard before filing
  that serial again. If ATF changed the site, the selectors in `config/mapping.yaml` need updating (see CLAUDE.md).
- The plain command line is still underneath: `./atf fill-form1 --help` (Windows: `.\atf.cmd fill-form1 --help`).
- Windows: if a step says a file is in use, close `suppressors.csv` in Excel and try again.
