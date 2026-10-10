# ATF eForms Form 1 automation (suppressors, filed by a trust)

Fills and submits ATF Form 1s in the eForms portal (https://eforms.atf.gov) for suppressors made by the user's trust,
and stamps the 5320.23 Responsible Person Questionnaire required with each submission. Run `./atf` (menu,
`src/ui.py`); the engine and plain CLI is `src/atf.py`. Python 3.9+ (developed on 3.14), Playwright, PyMuPDF, PyYAML,
rich + questionary. Built interactively 2026-10-08/09. Personal status for a given checkout is in `CLAUDE.local.md`
(gitignored) if present.

## How a user runs it
- **Setup:** Mac/Linux `./install.sh`, Windows `.\install.cmd` (find Python 3.9+ or use `ATF_PYTHON`, make `.venv`,
  install requirements + Chromium); then `./atf` / `.\atf.cmd` (Windows: always say `atf.cmd`; PowerShell may try to
  open the extensionless `atf` shell script as a document).
  First run = setup wizard (disclaimer, trust, responsible person, CLEO, firearm defaults, files) -> `data/trust.yaml`.
- **Normal use:** `./atf` -> Add suppressors -> File N queued. The menu stamps the 5320.23s (dated today), asks once
  per batch whether to pause for the SSN, shows one confirmation (= "I affirm" + authorization to Sign & Submit),
  opens the browser, the user logs in, then per row: open a new Form 1 (home page `#FORM53201LinkId`), fill, read back,
  certify, sign, wait for the user's emailed code + PIN, record the application number. Pending rows are resolved
  from the menu. Settings and Advanced (preview steps, re-date a 5320.23, re-file, capture portal pages) are in the menu.
- **5320.23 template:** the user makes ONE signed 5320.23 with https://jakepro716.github.io/atf-5320-generator/
  (Form 1, their details, the CLEO, signature, placeholder serial `TEMPLATE` and caliber `00`); the wizard copies it to
  `forms/5320.23.pdf` and test-stamps it. The wizard asks for the template FIRST and `read_template()` pre-fills
  trust name, DOB, residence/birth state, CLEO (agency, official, title, street/city/state/ZIP) and model from it
  (never the SSN: not read, and SSN-shaped values are dropped). A newly chosen template's values win over saved
  settings; a kept one only fills gaps; the template is copied in only on save. Stamping and reading rely on that
  generator's layout: typed text in Nimbus fonts, positioned relative to readable printed labels (`_near()`; some
  labels are font-garbled), the item-5 CLEO "NAME, TITLE" line found by position right of the agency name. ATF's own fillable PDF was rejected:
  digital-signature field (refilling after signing breaks it), image field for the photo, hybrid XFA form.

## Commands
`./atf` with no arguments opens the menu. `./atf <command> ...` (= `.venv/bin/python src/atf.py <command> ...`) is the
plain CLI, kept for debugging/scripting:

| Command | What it does |
|---|---|
| `fill-5320 [row] [--date MM/DD/YYYY]` | Stamps caliber (4d), serial (4e), signature date and the CLEO official line onto `forms/5320.23.pdf` -> `out/5320.23_<serial>.pdf`. No row = all unfiled rows. |
| `fill-form1 [row] [--dry] [--auto-affirm] [--skip-ssn] [--submit] [--capture] [--force] [--all] [--count N]` | Runs the portal steps. No row = first unfiled row. `--all`/`--count N` loop over unfiled rows in one browser session. `--force` re-files a filed/pending row. `--dry` prints the steps, no browser. The menu's "File" = `--auto-affirm --submit` (+ `--skip-ssn` unless the user chose to type the SSN). |
| `inspect` | Dumps controls + visible text + dialog text of the page you're on, to map new/changed steps. |
| `fields <pdf>` | Lists AcroForm fields (only for a fresh fillable PDF; generator output is flattened). |

## Code structure
- `config/mapping.yaml` (tracked, shared, NO personal data): portal steps with `{placeholders}` filled from settings by
  `_row_context()`; placeholders work in selectors too (e.g. `:has-text('{rp_race}')`). Step options: `section`
  (progress marker), `repeat` over a list setting (docs), `optional`,
  `skip_if_disabled`, `tag: ssn`, `label`/`private` on `expect`. `new_form_steps` opens a Form 1 from the home page.
- **`data/suppressors.csv` = the inventory** (single source of truth; `COLS` in atf.py): caliber, serial_number,
  overall_length (per suppressor; empty = settings default, recorded at filing), status ("" queued | pending | filed |
  approved | disapproved; `SKIP` = never (re)filed without --force), application_number (from the filing confirmation),
  filed_date (set at Sign & Submit; `backfill_filed_dates()` from out/submissions.csv), approved_date, eform_id,
  control_number, conditions (from approvals). `load_rows()` tolerates older files with fewer columns.
  `apply_approvals(records, import_unknown)` records approvals (fills an empty length from the PDF, never overwrites
  one), optionally adds approved suppressors missing from the inventory, and then re-sorts by `serial_key` (natural
  order); sorting happens ONLY there (never during filing: file_rows holds row indices). out/submissions.csv stays a
  write-only filing log. There is no approved.csv any more.
- `data/trust.yaml` (personal): see `examples/trust.example.yaml`. `REQUIRED` + `settings_problems()` in atf.py list
  what must be set; filing refuses to start until it's complete.
- `src/atf.py` engine: `stamp_5320(row, when, out_dir)`, `file_rows(rows, todo, opts)` (prechecks, browser, per-row
  `_start_form` + `_file_one` + `_submit`, logout in `finally`), `_run_steps` (mapping interpreter), `log_submission`.
  All user-facing output goes through `emit(kind, text, **data)` and prompts through `ask(prompt)`; the CLI prints,
  the menu (`src/ui.py` `Progress`) renders them (page ticks from `section`, read-back table from `expect` events, a
  code/PIN panel on `banner`).
- `src/ui.py` menu: home table + `summary()`, `inventory_flow` (serial order, filters), `file_flow`, `add_flow` (serial,
  caliber, length), `resolve_flow`, `approvals_flow`, `settings_flow` (wizard), `advanced_flow`.
- **Check approvals** (`fetch_approved` -> `read_approved` -> `approval_report`; CLI `check-approved`), READ-ONLY:
  home page search `#form-type-search` = "ATF Form 1 (5320.1)", `#form-status-search` = APPROVED, `#queryBtn`; results
  rows (`tr` with a "View Form" button; cells: eForm ID, Permit/Control #, ICN, Created By, Created, Submitted,
  Finalized), 10 per page, "Total Rows: N", page links by number. Per new row: View Form -> form page `#viewFormBtn`
  ("View PDF": the portal builds a Blob and opens it in a new tab; `_HOOK_JS` wraps URL.createObjectURL to keep the
  Blob, `_READ_BLOB_JS` reads it) -> `out/approved/<eForm ID>.pdf` -> `#goHomeBtn` -> search again. Saved PDFs are
  skipped on later runs. `read_approved_pdf()` reads item 4 (type, serial, caliber, model, barrel/overall length) and
  ATF's decision block (Approved checkbox = ZapfDingbats glyph left of the "Approved" label ON the "Disapproved" line:
  older approvals also carry a banner "APPROVED WITH CONDITIONS - SEE CONDITIONS ON THE LAST PAGE OF PDF" that a plain
  search finds first; conditions = last-page text above its "Approval Conditions" heading; approval date under the
  "Date" label below the decision line)
  by position relative to labels, ignoring the label font. Report = SUPPRESSORS (type contains SILENCER) matched to
  suppressors.csv by serial; other types (SBRs) only counted. eForm IDs (7-8 digits) and control numbers (11) do NOT
  equal the 9-digit application numbers recorded at filing, hence matching by serial. Report only; nothing imported.
  Results-table rows are found from each VISIBLE "View Form" button (the page has hidden duplicates whose innerText
  still reads "View Form"), climbing to the smallest ancestor with one such button and a 7+ digit ID; buttons are
  tagged data-atf-row so the click hits that row. The home page shows "Total Rows: 0" before any search: wait for rows.
  Overall length is compared with the row's recorded length (else the settings default) only for suppressors in your
  records (older builds differ). Older control numbers are 10 digits (newer 11); also read from the PDF's last page.
- questionary gotcha: `Choice(title, None)` returns the TITLE as the value; menu items use explicit string values.
- Keep code Python 3.9-compatible (stock macOS python3): no `X | Y` types, no backslashes inside f-string expressions.
- **Screenshot-safe menu (users share screenshots):** no trust name anywhere in `ui.py`; application numbers shown as
  last 4 digits (`short()`); `expect` steps with `private: true` (the trust name) show only matches/differs.
  Full values stay in the CSVs and the plain CLI output. Keep it that way when adding screens.
- **Tests:** `tests/` (pytest), all on FAKE data: sandboxed folders, `tests/fixtures/fake_5320.pdf` (ATF's blank form
  + made-up answers in the generator's font/positions, incl. a fake SSN that must never be read; rebuild with
  `tests/fixtures/make_fake_5320.py`), a fake portal page in headless Chromium, scripted questionary answers.
  Run `.venv/bin/python -m pytest -q` (pip install pytest). CI: `.github/workflows/tests.yml` installs via
  install.cmd/install.sh and runs them on Windows (Python 3.9 + 3.13) and Linux on every push to main.
- Cross-platform: all text files are read/written as UTF-8 (reads tolerate Excel's BOM); `_clean_path` unescapes
  `\ ` only off Windows; `.gitattributes` keeps `.cmd` CRLF and shell scripts LF.

## What is automated vs. manual
Automated: applicant pages, responsible-person popup (uploads of 5320.23/photo/EFT, DOB, birth/residence,
sex, race), CLEO page, firearm (manufacturer code FMI, silencer, model, caliber, serial, overall length, description),
supporting documents, trust document, read-back verification, verify-and-Next, certification, Sign & Submit, logging.

**Always manual (by design, do not automate):** logging in/MFA; typing the **SSN** (optional; the user chooses per
batch whether to pause for it); the **6-digit emailed verification code**; the **eForms PIN**. Credentials, SSN, codes
and PINs must never be read, stored, or typed by Claude or the script. (A user once asked for the script to type the
SSN from a file; declined, same as passwords/codes/PINs.)

## Safety design (keep these)
- Submitting is opt-in (CLI `--submit`, or the menu's confirmation) and gated: it verifies the on-page summary against
  the intended values (`expect` steps), ticks verify, goes to Certify, requires "validated successfully" on the page,
  only then ticks the certification and clicks Sign & Submit. Any mismatch or missing validation = does not submit.
- Auto-affirm is opt-in (CLI `--auto-affirm`, or the menu's confirmation, which lists each serial and says so): the
  "I affirm" popup is the user's per-application legal affirmation.
- A row is marked `pending` (saved to the CSV) just before Sign & Submit is clicked, and `filed` only after the
  portal's confirmation popup is read. Pending rows are skipped by every command until resolved (menu: Resolve a
  pending row; `--force` overrides): **check the dashboard** to avoid duplicate filings.
- Before opening the browser: settings must be complete, and every queued row's 5320.23 must have the serial on page 1
  and today's signature date (the menu regenerates them each time). The newest PDF for a serial is used.
- The fingerprint `dismiss_dialog` step has `require: "Valid EBTS Package"`: any other dialog text counts as a
  mismatch and blocks submitting.
- Step failures leave the browser open and log out cleanly (eForms allows ONE session; a crash without logout locks
  the account until the old session times out, ~15-30 min). The session is wrapped in `try/finally`, and the browser
  is launched with `handle_sigint=False` so **Ctrl-C also logs out** (by default Playwright kills Chromium on Ctrl-C).
- **Dashboard rows have a "Submit Void" button and form pages have "Save"/"Cancel"/"Next".** Read-only browsing goes
  through `_safe_click(loc, expected)`: clicks only if the visible text is exactly the expected one and matches none of
  `UNSAFE` (void/withdraw/delete/save/submit/sign/cancel/amend/copy/edit/abandon). Never click by position alone.
- Claude never clicks Sign & Submit / enters credentials itself; the user runs the commands. Claude watches the terminal
  and fixes the script.
- First run shows a disclaimer the user must accept (unofficial, as-is, user responsible for every filing).

## Layout
- `atf` launcher, `install.sh` installer, `src/ui.py` menu, `src/atf.py` engine + CLI, `config/mapping.yaml` portal steps,
  `examples/` sanitized samples, `requirements.txt`.
- Personal, gitignored, never shared: `data/` (trust.yaml, suppressors.csv queue, trust PDF, .eft, photos),
  `forms/5320.23.pdf` (signed template), `out/` (stamped 5320.23s, submissions.csv log, capture_*.jsonl),
  `.browser-profile/` (logged-in eForms session cookies), `CLAUDE.local.md`. Never zip/copy the folder to share it:
  share the git repo.

## Gotchas learned (read before debugging)
- **Selectors:** IDs starting with a digit are invalid CSS: use `[id='0Edit']`, not `#0Edit`.
- **Hidden checkboxes:** the portal hides real inputs behind styled labels; `_tick()` falls back to the label/DOM click.
  The `check` action uses it. Playwright's plain `.check()` times out here.
- **Locked/prefilled fields** hang `select`/`fill` for 30s: use `skip_if_disabled: true` (done for the firearm popup
  country); the CLEO country is locked so its step was removed. Applicant address/phone/email are prefilled.
- **Race/Ethnicity** is a PrimeNG multiselect (`#raceEthnicity .ui-multiselect`, option panel is an overlay). Only the
  "White" option text has been verified live; other races in the wizard list are the standard category names.
- **Photo:** eForms requires a photo for the responsible person (`#photoFile`); the 5320.23 PDF itself doesn't need one.
- **Fingerprint popup:** attaching the EFT opens an info dialog ("Valid EBTS Package", Ok); the `dismiss_dialog` step
  prints its text and clicks Ok. If it doesn't say Valid, submitting is refused (see `require`); look at it.
- **birthState is disabled until birthCountry is chosen** (800 ms wait step). Caliber is free text for silencers;
  barrel length is hidden for silencers. Birth country and citizenship are fixed to US in the mapping.
- **Manufacturer code is always `FMI`** for a Form 1 suppressor (fixed in config/mapping.yaml, not a setting).
  The wizard's default firearm description is "I POSSESS NO PARTS OR PLANS, AND WILL NOT PURCHASE OR MANUFACTURE
  ANY PARTS UNTIL A FORM 1 IS APPROVED" (`DEFAULT_DESCRIPTION` in ui.py); users may edit it.
- **CLEO city/county/state are dropdowns** populated from the ZIP (1500 ms wait); `_row_context` uppercases them.
- **"validated successfully"** appears only on the Certify page, after ticking verify (`#binary`) and Next.
- The portal shows the trust name uppercased on the summary; `expect` compares case-insensitively as a whole token
  (`22` matches `22`/`CAL 22` but not `223`; `S101` not `S1015`). Note `12` would still match `12.5`.
- **5320.23 stamping:** the template is flattened (no form fields). `stamp_5320` redacts the old typed text and inserts
  new text at the same spot (located by label/known text). Photo and signature image are untouched. The signature
  date is just stamped text (a pre-existing signature image stays); each signer decides whether that's acceptable.
- **Signature-date spot:** the blank form has a second "Date" label lower on page 2; the stamper tries each "Date"
  label and uses the one with a typed date just above it (generator output may only have one).
- `fill-form1` finds the PDF by **serial** (`out/5320.23_<serial>.pdf`, or the older `5320.23_<row>_<serial>.pdf`,
  newest if several). `stamp_5320` re-reads each PDF it writes and flags any value not found.
- **CLEO details** live only in `data/trust.yaml`. The stamper replaces the template's item-5 official line with
  `official, title` from settings on every copy (found by position), so a new official needs only Settings; a new
  agency/address needs a new template (agency and address lines are not restamped).
- Responsible person fields (name, title, street, zip) are prefilled from the user's eForms account.

## When the portal changes
Run `./atf` > Advanced > Capture portal pages (= `inspect`), click through the Form 1, press Enter on each page (it
records the newest tab to `out/capture_*.jsonl` and saves any PDF the portal sends to `out/captures/`); Claude reads those; update selectors in `config/mapping.yaml`. Advanced > Preview (= `fill-form1 --dry`)
prints steps without a browser. `--capture` records screens after the automated part into `out/capture_*.jsonl`
(includes page text, may contain PII; do not share).

## Open items / known caveats
- 5320.23 template revision: the generator (and ATF's PDF it embeds) was the Jan 2023 revision (OMB expiry
  12/31/2025) when checked; ATF had a revision pending. If the generator updates, re-check stamping on a new template.
- Early test drafts may remain in the eForms dashboard (auto-delete after 90 days); not submissions.
- One responsible person only (first entry in trust.yaml); additional RPs need new steps captured live (`[id='1Edit']`?).
- Suppressors only (SILENCER, caliber free text); other NFA types have different firearm fields.
- Windows: install/launcher/tests verified on GitHub's Windows runners; not yet used on a real Windows PC against the
  live portal. Ctrl-C logout is tested by simulating the interrupt, not by a real console Ctrl-C on Windows.
- Ideas: Google Sheet as single source of truth (queue + write-back + status sync from the eForms dashboard);
  reusing eForms "Saved Line items".
- Legal: each submission is a certification under 18 U.S.C. 924 / 26 U.S.C. 5861 by the user. Check ATF's eForms
  terms of service on automation; the tool does not bypass CAPTCHAs or bot checks and the user stays in the loop.
