# ATF eForms Form 1 automation (suppressors, filed by a trust or an individual)

Fills and submits ATF Form 1s in the eForms portal (https://eforms.atf.gov) for suppressors made by the user's trust
(stamping the 5320.23 Responsible Person Questionnaire required with each submission) or by the user as an individual. Run `./atf` (menu,
`src/ui.py`); the engine and plain CLI is `src/atf.py`. Python 3.9+ (developed on 3.14), Playwright, PyMuPDF, PyYAML,
rich + questionary. Built interactively 2026-10-08/09. Personal status for a given checkout is in `CLAUDE.local.md`
(gitignored) if present.

## How a user runs it
- **Setup:** Mac/Linux `./install.sh`, Windows `.\install.cmd` (pick Python 3.9-3.14, preferring 3.13, or `ATF_PYTHON`; a
  brand-new Python like 3.15 is refused because PyYAML etc. had no Windows wheels yet and pip wanted a C++ compiler;
  a `.venv` from another Python is rebuilt; make `.venv`,
  install requirements + Chromium); then `./atf` / `.\atf.cmd` (Windows: always say `atf.cmd`; PowerShell may try to
  open the extensionless `atf` shell script as a document).
  First run = setup wizard (disclaimer, trust, responsible person, CLEO, firearm defaults, files, other responsible
  persons) -> `data/trust.yaml`.
- **Normal use:** `./atf` -> Add suppressors -> File N queued. The menu stamps the 5320.23s (dated today), asks once
  per batch whether to pause for the SSN, shows one confirmation (= "I affirm" + authorization to Sign & Submit),
  opens the browser, the user logs in, then per row: open a new Form 1 (home page `#FORM53201LinkId`), fill, read back,
  certify, sign, wait for the user's emailed code + PIN, record the application number. Pending rows are resolved
  from the menu. Settings (a trust: All settings | Other responsible persons) and Advanced (preview steps, re-date a
  5320.23, re-file, practice run, capture portal pages) are in the menu.
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
| `fill-5320 [row] [--date MM/DD/YYYY]` | Stamps caliber (4d), serial (4e), signature date and the CLEO official line onto `forms/5320.23.pdf` -> `out/5320.23_<serial>.pdf`, and each other responsible person's template -> `out/5320.23_<serial>.<first-last>.pdf` (no CLEO line). No row = all unfiled rows. |
| `fill-form1 [row] [--dry] [--auto-affirm] [--skip-ssn] [--submit] [--capture] [--force] [--all] [--count N]` | Runs the portal steps. No row = first unfiled row. `--all`/`--count N` loop over unfiled rows in one browser session. `--force` re-files a filed/pending row. `--dry` prints the steps, no browser. The menu's "File" = `--auto-affirm --submit` (+ `--skip-ssn` unless the user chose to type the SSN). |
| `check-setup` | Checks settings, files, templates, Chromium and the inventory without eForms (menu: Settings > Check my setup); exit 1 if something must be fixed. |
| `serve` | The phone page (src/phone.py) on the home network until Ctrl-C (menu: Inventory > Open on my phone). |
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
  control_number, conditions (from approvals), built ("yes" | ""; `is_built`), design (free text: what it is, e.g.
  "PCC Short"). Builds: `record_builds({serial: design})` / `record_build(serials, design)` (APPROVED rows only, else ValueError and nothing saved; a Form 1 must be
  approved before making), `clear_build`, `serials_in("A101-A110, A115")`; menu
  Inventory > Record builds (`builds_flow`), views Built / Approved, not built (`build_table`). Home "Building one?
  Find the serial to use" (`build_serial_flow`, shown while any approved row isn't built): caliber picked from the
  inventory's calibers (`caliber_key`: case, leading dot ignored) + build length -> `available_for_build` = approved,
  not built, same caliber, recorded length >= build length (a longer approval covers a shorter build; no length = left
  out and named), serial order; the first is "next"; can record it (or another fit) as built. Deliberately NOT
  tracked (the user's call): build date, print file, marked/engraved (markings are printed with the part). The
  design is typed every time, separately for each serial (no pick-from-previous list; one batch is not one design),
  all shown together, saved only on the confirmation. `load_rows()` tolerates older files with fewer columns.
  `apply_approvals(records, import_unknown)` records approvals (fills an empty length from the PDF, never overwrites
  one), optionally adds approved suppressors missing from the inventory, and then re-sorts by `serial_key` (natural
  order); sorting happens ONLY there (never during filing: file_rows holds row indices). out/submissions.csv stays a
  write-only filing log. There is no approved.csv any more.
- **`data/sbrs.csv` = SBR inventory** (`SBR_COLS`): approved short-barreled rifles (type contains RIFLE), filled by
  `record_sbrs()` from Check approvals (keyed by eForm ID; no duplicates); never filed or queued by this tool.
  Path via `_sbrs_path()` (resolved at call time so tests' sandbox redirects it). Inventory > Show SBRs, Excel sheet.
- **Development and tests must never write the real records** (`data/`, `forms/`, `out/submissions.csv`,
  `out/approved/`: production data, the user's only copy). The `sandbox` fixture is autouse (every test runs in a temp
  folder) and asserts the real data/ folder is unchanged after each test (a module-level path once leaked fake rows
  into data/sbrs.csv). New paths under DATA/OUT/FORMS must be looked up at call time or patched in conftest. No ad-hoc
  scripts that import atf/phone/ui: on 2026-10-28 one set only `atf.DATA` (`atf.SUPP` is module-level) and overwrote
  the real inventory. Write a pytest test instead. `.claude/hooks/protect_data.py` (PreToolUse, `.claude/settings.json`)
  enforces this for Claude: it denies Edit/Write of those paths, shell writes/moves/deletes of them, Python outside
  pytest that imports the app, and `./atf` except read-only commands (--version, check-setup, fill-form1 --dry, fields).
  A change the user wants in their records is theirs to make (the menu); Claude says exactly what to change.
- **Applicant type** (`applicant_type: trust | individual` in trust.yaml; missing = trust, so older settings are
  unchanged; `atf.applicant_type()`). Individual (mapped from a live capture 2026-10-11): applicant-type `#Individual`;
  no trust name/title/trust document/5320.23 (the RP popup's `#formFile` and `#citizenType` are locked for individuals:
  `only: trust` / `skip_if_disabled`); the APPLICANT page asks questions `[id='11.A']`..`[id='11.M.1']` (No/Yes) and
  `11.M.2` (only "N/A" once M.1 is No), `#13answer` alien number (locked), `#14answer` = item 13 UPIN (`if_set: upin`),
  `[id='17.Aanswer']` US/NON-US (US only); the verify page shows the answers again (read back by `expect`). Answers are
  saved in `individual.answers` (`QUESTIONS_11` holds ATF's wording; the wizard shows it); `settings_problems` refuses
  filing unless every answer is No; the menu's confirmation lists them and the user affirms they're true today.
  Mapping step options `only: trust|individual` (checked before placeholders are filled) and `if_set: <ctx key>`.
  The same RP popup carries the individual's photo, EFT, DOB, birthplace, sex, race (responsible_persons[0] settings).
- **Other responsible persons** (trust only; `responsible_persons[1:]`, `atf.other_rps()`): each has title, first/middle/
  last name, suffix (portal "Cadence"), email, dob, sex, race, place_of_birth.state, home_address {street, zip, city,
  state}, eft, photo, and `template` = their own signed 5320.23 copied to `forms/5320.23_<first-last>.pdf` (on save).
  `stamp_5320(row, when, person=p)` -> `out/5320.23_<serial>.<first-last>.pdf` (serial, caliber, date; NOT their CLEO
  line: item 5 is the CLEO where THEY live); `stamp_all()` = you + them (menu, practice, redate, fill-5320).
  Menu Settings (`settings_menu`): a trust picks "All settings" (the wizard, which also ends with this list) or
  "Other responsible persons" (`rps_flow`: just the list, saved via `_save_settings`). `read_person()` prefills them from their template (3a name/address, 3c email; never the SSN). `OTHER_RP_REQUIRED`;
  problems are numbered ("responsible person 2: ...") and screens show titles, never their names (Settings excepted).
  Mapping: `repeat: other_rps` after your popup: `#addResponsiblePerson`, the same popup fields (ids from the 10/11
  capture, where they were locked), ZIP -> `#state` / `select[name='city']`, then read back row `[id='{n-1}Edit']`
  (private expect on the row's text = last name) and `expect count` of `input[id$='Edit']` = 1 + others (a leftover RP
  blocks submitting). `_shown_value` reads a non-form element's text. Live: a practice run (2026-10-22, the user
  added as RP 2 with their own files) went all the way through; not yet filed for real with a different person.
- `data/trust.yaml` (personal): see `examples/trust.example.yaml`. `REQUIRED` + `settings_problems()` in atf.py list
  what must be set; filing refuses to start until it's complete.
- `src/atf.py` engine: `stamp_5320(row, when, out_dir)`, `file_rows(rows, todo, opts)` (prechecks, browser, per-row
  `_start_form` + `_file_one` + `_submit`, logout in `finally`), `_run_steps` (mapping interpreter), `log_submission`.
  All user-facing output goes through `emit(kind, text, **data)` and prompts through `ask(prompt)`; the CLI prints,
  the menu (`src/ui.py` `Progress`) renders them (page ticks from `section`, read-back table from `expect` events, a
  code/PIN panel on `banner`).
- `src/ui.py` menu: home table + `summary()`, `inventory_flow` (serial order, filters behind one "Show another view…"; `open_approved_flow` via
  `atf.approved_pdf_for`, `export_flow` -> `atf.export_inventory()` .xlsx (openpyxl), `edit_queued_flow`: queued rows
  only; filed/approved are locked), `file_flow`, `add_flow` (how many -> first serial, caliber, length; `serial_run`
  counts up the last digit group; any clash = nothing added), `resolve_flow`, `approvals_flow`, `settings_flow`
  (wizard), `advanced_flow`. `open_file()` uses open / os.startfile / xdg-open. `settings_menu`: All settings, Other
  responsible persons (trust), Check my setup (`check_setup_flow`).
- **Phone page** (`src/phone.py` + `src/phone.html`; menu `phone_flow` until Enter, CLI `serve` until Ctrl-C): stdlib
  `ThreadingHTTPServer` on 0.0.0.0:8765 (else any free port), `lan_address()` for the link, QR via `segno`
  (requirements; `qr_text` returns None without it). Every request needs `?k=<secrets.token_urlsafe(16)>` made per
  run (403 otherwise); `log_message` is silenced (it would print the key). GET `/` (the page, strict CSP, loads
  nothing external), `/api/inventory` (suppressors + SBRs; application/eForm as last 4), `/pdf/<serial>`
  (`approved_pdf_for`); POST `/api/build` {serial, built, design} -> `record_builds` / `clear_build`, approved rows
  only (400 otherwise; 409 if saving fails, e.g. Excel lock). The user chose: no locations, no "seen"/audit system;
  search defaults to the number pad (digits match a serial's digit group by value or ending: `83` -> A083), ABC
  switches to letters. One screen at a time under 760 px (hash = selected serial, so back works), side by side above.
  Turning Built off asks on the page (Keep it / Clear), not with `confirm()`: on the user's iPhone (2026-10-28) the
  switch couldn't be turned off with the pop-up.
- **Inventory server** (`server/`, branch `server`; design doc "Inventory server design" on claude.ai, 5 phases;
  phase 1 = read-only server with made-up data). Optional, never imported by the app; FastAPI + uvicorn + cryptography
  (`server/requirements.txt`; dev: `server/.venv`, `requirements-dev.txt`). `atf_server/db.py`: SQLite (WAL, schema =
  PRAGMA user_version, `MIGRATIONS`, backup before migrating, newer schema refused), tables suppressors (app CSV
  columns; status queued/pending/filed/approved/disapproved/skip, "" in CSV = queued; ISO dates inside, MM/DD/YYYY
  out; built only while approved; version bumped by trigger), sbrs, approval_pdfs (files `<data>/approved/<sha256>.pdf`),
  history (append-only by trigger), devices (key hashes), meta (server_id, pair_code). Writes are named operations
  in one transaction with a history row: `import_inventory` (empty server only, all or nothing) and, since phase 3,
  `operate(conn, name, body, device)` with `OPERATIONS` set_built {items: [{serial, design, version}]} (approved
  only, all or nothing), clear_built, add_queued {rows}, edit_queued {serial, changes, version} (queued only;
  serial_number/caliber/overall_length), remove_queued. `version` given and stale = `Conflict` (409 + the rows as they
  are now); `request_id` seen before = the stored answer (table `requests`, schema 2). **Builds leave no record of
  when** (the user's rule, 2026-10-28: "it could be self incriminating"): `PRIVATE_OPS` (set_built, clear_built)
  write no history row, don't touch updated_at/updated_by, keep no request row, and the API doesn't log them
  (`PRIVATE_PATHS`); schema 3 deleted earlier ones and VACUUMed; `secure_delete` is on. Never add a build date,
  build log or build audit anywhere. `last_change` = a time-free counter in meta (`changes`, bumped by every
  operation and import; what `/api/events` watches, 1 s, so CLI writes show too). And
  pairing (6-digit code, 5 min, one use, 5 wrong tries kill it). `app.py`: the page is `src/phone.html` (copied into
  the image), auth = `Authorization: Bearer` (the app) or the HttpOnly cookie a browser gets when pairing; POSTs
  must be JSON (CSRF); `/api/inventory` = the PC page's shape + `version` + `can_build: true` + `server`; `POST /api/ops/<name>`;
  `/api/events` (server-sent events: `change` when the history grows; the page reloads, but not over a design being
  typed or a Keep/Clear question); `/api/rows` = full CSV
  shape. The page, when `data.server` is set: pairing screen on 401, history per serial, Devices (`#:devices`: add a
  device = code, remove = tap twice), Export CSV. **No tailnet is ever required** (the user's rule, 2026-10-28):
  everything, the phone page included, works at the home-network address; Tailscale (`tailscale serve`) is an
  optional second address. `tls.py`: the server's own CA (`tls/ca.pem`, key 0600, 20 years, critical name
  constraints = private IP ranges + .local/.lan/.home.arpa/.internal/localhost, so a stolen key can't vouch for real
  sites) signs its server certificate (800 days, Apple's 825 limit; SAN = this machine's private addresses +
  host.local/.lan/.home.arpa + ATF_NAMES; re-issued at start when an address is new, it's due, or it wasn't signed
  by this CA, e.g. phase 1's self-signed one; the CA stays). `/ca.crt` (no auth) + the page's "Trust this server"
  (`trustHelp`, only on the server's own HTTPS, not *.ts.net): a phone installs the CA once, then no warnings.
  CLI `python -m atf_server serve|pair-code|devices|revoke|fingerprint|import <app folder>|demo|backup`. Deploy:
  `server/Dockerfile` (repo-root context), `compose.yaml` + `DOCKER.md` (Docker users must set `ATF_NAMES` = the host's
  home-network address: inside a container the server only sees its own; the log's "HTTPS for:" = `tls.covered`),
  `atf-inventory.service` + `LXC.md` (Proxmox), `server/README.md` (what/why, for users). Tests: `server/tests`
  (`server/.venv/bin/python -m pytest -q tests` from server/; root `pytest.ini` keeps the app's run to tests/); CI
  job `server` also builds and starts the image.
  **Phase 2 (the app reads from the server):** `src/remote.py` (stdlib only): `normalize` (bare address -> port
  8765, *.ts.net -> 443), `probe` (unverified connect: /api/hello + /ca.crt; fingerprint = the CA's; checks the
  served certificate really is signed by it; an older server without /ca.crt = its certificate's fingerprint), `pair`, `Server` (addresses in
  order, first that answers is kept in `Server.working` for the run; with `ca` only certificates that CA signed
  pass (hostname not checked: addresses change), else the pinned fingerprint; failing that, one that verifies
  normally (a tailnet name); a different server_id at an address = skipped; `Unreachable` falls through to the next
  address, other refusals don't). `data/server.yaml` (server id, addresses, fingerprint, ca, key, device_id; chmod 600)
  = server mode: `load_rows`/`load_sbrs` read `/api/rows` (reused 2 s), cached to `data/server_cache.json`, shown
  with `SERVER_STATE` online/offline (offline = last data seen, read-only; nothing seen = `remote.ServerError`,
  the menu offers Try again / Settings); `save_rows`/`save_sbrs` raise `ServerReadOnly` (so do record_builds etc.);
  `approved_pdf_for` fetches into `out/server-pdfs/`; backfill is skipped; Check my setup has an "Inventory server"
  line instead of the local inventory check. **Phase 3:** `record_builds`, `clear_build` and the new `add_queued`,
  `edit_queued`, `remove_queued` (local: load/save as before; the menu's add/edit flows use them) call `_server_op`
  (fresh request_id; version from `_seen_version` = the last read, or the row's own `version`). Menu in server mode:
  everything except File, Resolve a pending row, Check approvals, Advanced and Open on my phone (filing and
  approvals = phase 5; `save_rows` still raises `ServerReadOnly` for them). **Phase 4 (moving):**
  `atf.move_to_server(try_only)`: `local_for_move()` (the CSVs + out/approved PDFs, read directly whatever the mode)
  -> `POST /api/import` (empty server only; PDFs base64) -> read back `/api/rows` + `/api/pdfs`, `_differences`
  field by field (`_norm`: status/built lower, dates padded; SBRs keyed by serial + eForm ID) and PDF checksums ->
  any difference or try_only = `POST /api/import/undo` (server: allowed only while `meta import_mark` = the newest
  change; empties the server again) -> else the CSVs are renamed `.moved-to-server-<date>.csv` (`_move_aside`, never
  deleted). `copy_from_server()`: writes the CSVs (a non-empty one renamed `.replaced-by-server-<date>.csv` first),
  fetches PDFs it doesn't have by checksum (`GET /api/pdfs/<sha>`) into out/approved + index, compares. Menu:
  Settings > Where your inventory lives offers Move (when the server is empty), and on switching back Copy first /
  Keep. **Phase 5 (filing + approvals):** server ops mark_pending (queued only; `force` = deliberate re-file),
  mark_filed (pending/filed; "UNKNOWN" = filed without a number), unmark_pending, record_approvals ({updates:
  [{serial, fields}] approval fields only, a length only fills an empty one; added: [rows]}), record_sbrs (upsert
  by eForm ID, fills in); `POST /api/approval-pdfs` (uploaded first via /api/import/pdf). App: `_submit` calls
  `_set_pending` (server: `mark_pending` with the row's version; ANY failure = "NOT submitting", Sign & Submit not
  clicked); after the confirmation `log_submission` (out/submissions.csv) then `_set_filed`: server mode writes
  `data/outbox.jsonl` (fsync) FIRST, then sends; `outbox_replay()` runs before every server call/read and removes an
  entry only when the server confirms (request_id = harmless repeat); a waiting entry stops the batch before the next
  row and blocks new filing (`_server_precheck`, before the browser opens: server reachable, outbox empty, every row
  still queued there at the same version). `resolve_pending(serial, number)`. `apply_approvals`/`record_sbrs` send
  their changes as ops in server mode; `send_approval_pdfs(records)` uploads PDFs the server lacks. The menu offers
  everything in server mode except Open on my phone; the home screen shows waiting filing results. Tests:
  `tests/test_server_filing.py` (fake portal + REAL server: pending refused = not submitted, result kept and
  replayed, another device's change, unreachable, resolve, approvals); the `server` fixture lives in conftest. Server console: `reset` (type RESET; backup first; devices/certs stay) and `restore <file>`. Tests:
  `tests/test_move.py` runs the REAL server from server/.venv in a temp folder (skipped without it) for the round
  trip; `server/tests/test_move.py`. The test sandbox uses its own MonkeyPatch (a test's `monkeypatch.undo()` once
  pointed atf at the real data/); use `monkeypatch.context()` in tests.
  Settings > Where your inventory lives (`server_flow`): connect (address, compare fingerprint with the server
  page's Devices, optional tailnet name, name "PC", pairing code) / Test the connection / Use this computer's
  inventory again (revokes this device, `forget_server`). The local CSVs are never touched. Tests:
  `tests/test_server_mode.py` (fake HTTPS server, `tests/fixtures/test_server_*.pem` test-only cert) and
  `server/tests/test_app_client.py` (remote.py against the real server).
- **Check my setup** (`atf.check_setup()` -> [(ok|warn|fail, label, detail, path)]; CLI `check-setup`, exit 1 on a
  fail): Python version, Chromium (`_chromium_path`), helper folder in a cloud folder (`CLOUD_FOLDERS`), missing
  settings (settings_problems minus the file lines), each file via `_check_file` (exists, `_online_only`: Windows
  OFFLINE/RECALL attributes, macOS SF_DATALESS, not read then; not empty; readable; `FILE_KINDS` header: EFT starts
  `1.001:`, JPG/PNG, PDF = warn only), each 5320.23 template test-stamped (`template_problems`, also used by the
  wizard), inventory not locked by Excel (`r+b` open) and pending rows. Labels name no one ("Responsible person 2:
  photo") and the menu shows no paths; the CLI prints them.
- **Check approvals** (`fetch_approved` -> `read_approved` -> `approval_report`; CLI `check-approved`), READ-ONLY:
  home page search `#form-type-search` = "ATF Form 1 (5320.1)", `#form-status-search` = APPROVED, `#queryBtn`; results
  rows (`tr` with a "View Form" button; cells: eForm ID, Permit/Control #, ICN, Created By, Created, Submitted,
  Finalized), 10 per page, "Total Rows: N", page links by number. Per new row: View Form -> form page `#viewFormBtn`
  ("View PDF": the portal builds a Blob and opens it in a new tab; `_HOOK_JS` wraps URL.createObjectURL to keep the
  Blob, `_READ_BLOB_JS` reads it) -> saved as `out/approved/<serial>.pdf` (`_file_by_serial`; `<serial>_2.pdf` for a
  repeated serial, `eForm-<id>.pdf` if unreadable) with `out/approved/index.csv` mapping eForm ID -> file (the results
  list shows only eForm IDs, so that is how later runs skip forms already saved) -> `#goHomeBtn` -> search again.
  Older `<eForm ID>.pdf` files are renamed by `_migrate_approved_names`. `read_approved_pdf()` reads item 4 (type, serial, caliber, model, barrel/overall length) and
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
  Approvals from Oct 2026 on print the date as "October 15, 2026" ~4.5 pt below the "Date" label (older: 08/15/2026,
  ~10 pt); `_mmddyyyy` stores MM/DD/YYYY. Their PDFs have dangling refs: MuPDF warnings ("cannot find object in xref")
  are switched off in `read_approved_pdf`, harmless. Paginator (from a capture): icon-only first/prev anchors, page
  numbers, icon-only next/last, a "10" rows box; live 2026-10-19 with 7 pages the "6" link never showed (only some
  numbers shown). Each results row's eForm ID is ALSO a numeric link (`<span><a id="eformId-link"> 1234567 </a>`):
  page numbers = 1-3 digit links. `_PAGER_JS` tags the shown numbers and the empty anchor right after them (next); `_approved_results`
  clicks the target, else the highest shown number below it, else next (tests: block/sliding windows, current page
  marked or not); unreachable = error + `out/paginator_*.html`. Never click Search while results show (the old
  table reads as the new results, then the search resets to page 1): page n is reached from page n-1
  (`search=False`), Search only after Go Home; a failed page is retried once from a reloaded home page; if it still
  fails, paging stops with a problem line (✖) and the report covers every PDF already saved (index.csv).
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
sex, race), the trust's other responsible persons (Add Responsible Person: name, address, email, the same uploads/fields), CLEO page, firearm (manufacturer code FMI, silencer, model, caliber, serial, overall length, description),
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
- Before opening the browser: settings must be complete, and every queued row's 5320.23 (each responsible person's) must
  have the serial on page 1 and today's signature date (the menu regenerates them each time). The newest PDF for a serial is used.
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
- Yes/no questions that open the browser or re-file ("Confirm and open the browser?", "Open the browser?", "Re-file it
  anyway?") use `auto_enter=False`: y/n alone doesn't answer, Enter does. questionary normally answers on the key, and
  an Enter typed after it then answered "Log in ... then press Enter" before the user had logged in. `Progress.ask`
  also drops typed-ahead keys (`_drop_typeahead`: termios flush / msvcrt) before every prompt.
- First run shows a disclaimer the user must accept (unofficial, as-is, user responsible for every filing).

## Layout
- `LICENSE` (MIT, copyright SK360). `atf` launcher, `install.sh` installer, `src/ui.py` menu, `src/atf.py` engine + CLI, `config/mapping.yaml` portal steps,
  `examples/` sanitized samples, `requirements.txt`.
- Personal, gitignored, never shared: `data/` (trust.yaml, suppressors.csv queue, trust PDF, .eft, photos),
  `forms/5320.23.pdf` (signed template), `out/` (stamped 5320.23s, submissions.csv log, capture_*.jsonl),
  `.browser-profile/` (logged-in eForms session cookies), `CLAUDE.local.md`. Never zip/copy the folder to share it:
  share the git repo.

## Gotchas learned (read before debugging)
- **Login wait:** after "log in, then press Enter", `_wait_for_login()` only goes on once a tab shows a Log Out link
  (eForms logs in through app.atf.gov/adfs; pressing Enter there used to time out on `#form-type-search` with a
  traceback). It warns and asks again; q stops. The menu turns any unexpected error into one red line plus
  `out/last_error.txt` (traceback) and returns to the menu.
- **Dropdown picks** (`_select`): by label (else value), checked once right away. Inside a `check_picks` mapping
  block (the individual's applicant-page answers + citizenship) they're picked quickly and `_check_picks` reads them
  all back after 1 s, re-picking lost ones (2 rounds, then the step fails). First live individual run (2026-10-11):
  every 11.A-11.M answer and the item-16 citizenship (`17.Aanswer`) came out BLANK in the saved draft although the
  script had selected them; blank citizenship made the RP popup show NON U.S.CITIZEN (locked) and `#birthCountry` then timed out. The mapping now waits for the
  applicant page, picks, and reads every answer back on that page (`exact: true`) before Next; the verify page checks
  `#question17a` = exactly "UNITED STATES" (second live run: everything else read back ✔; the verify page spells
  it out). Practice runs: Advanced > "Practice run" (= `fill-form1 --auto-affirm`, submit=False). The menu's
  File path ALWAYS submits after the checks: never suggest it for testing. `expect` reads a dropdown's selected TEXT (`_shown_value`), not its value code.
- **Selectors:** IDs starting with a digit are invalid CSS: use `[id='0Edit']`, not `#0Edit`.
- **Step failures save the page:** `_save_failure()` writes `out/failed_<time>.jsonl` (like a capture, + `failed_step`)
  and `.html` (outermost open dialogs, else the page): read those first. `fill` goes through `_fill()`: an id that is a
  dropdown gets picked, a wrapper gets its inner field filled (first live trust RP-2 try, 2026-10-22: "#title ... is
  not an <input>").
- **Adding documents is asynchronous:** after Add Document the portal saves in the background, lists the file, THEN
  clears the form. A fixed 1 s wait once let the next file go in first (2026-10-22: only the 2nd doc listed, under the
  2nd description; form wiped; Add disabled -> timeout). Each add now waits for `td:has-text(<file name>)` (names
  JSON-quoted in `doc_file` / `trust_doc_file`). The failure record also has `last_steps`.
- **Hidden checkboxes:** the portal hides real inputs behind styled labels; `_tick()` falls back to the label/DOM click.
  The `check` action uses it. Playwright's plain `.check()` times out here.
- **Locked/prefilled fields** hang `select`/`fill` for 30s: use `skip_if_disabled: true` (done for the firearm popup
  country); the CLEO country is locked so its step was removed. Applicant address/phone/email are prefilled.
- **Race/Ethnicity** is a PrimeNG multiselect (`#raceEthnicity .ui-multiselect`, option panel is an overlay). Only the
  "White" option text has been verified live; other races in the wizard list are the standard category names.
- **Photo:** eForms requires a photo for the responsible person (`#photoFile`); the 5320.23 PDF itself doesn't need one.
- **Fingerprint popup:** attaching the EFT opens an info dialog ("Valid EBTS Package", Ok); the `dismiss_dialog` step
  WAITS (up to 60 s, `value`) for a dialog matching `require`, prints it and clicks Ok. It used to wait a fixed 1.5 s:
  on the friend's first live run (Windows, folder in OneDrive) the message hadn't appeared yet, the only dialog was the
  responsible-person form itself (Save & Close / Cancel) and the user was told to "click it yourself". Other dialogs
  are now ignored (only ones mentioning `about: EBTS` are shown) and the user is never asked to close the RP form.
  If no Valid message appears, submitting is refused (see `require`); look at it.
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

## Screenshots (docs/screenshots/*.png)
`.venv/bin/python docs/make_screenshots.py` regenerates them from the REAL menu code (rich's SVG export, made into PNG
with Playwright) in a temp folder with a fake trust and made-up serials A101... (no real serials, not even last 4:
the user posts these publicly). The browser part of filing and approvals is replayed as engine events; no eForms.
Re-run after changing any screen.

## Personal-data scan (the repo is PUBLIC)
`tools/scan_personal.py` compares text with the values in the real `data/trust.yaml` (identifying keys only: names,
DOB, email, addresses, CLEO, file paths and telling file names; not sex/race/state/title/description/model),
`data/suppressors.csv`, `data/sbrs.csv`, `out/submissions.csv` (serials, application/eForm/control numbers, designs),
plus SSN-shaped numbers (not the fixture's 123-45-6789) and emails (not example.com). It prints where and what kind,
never the value; exit 1 on a hit. No args = staged added lines (`.githooks/pre-commit`); a file arg = that file
(`.githooks/commit-msg` scans the message); `--all` = every tracked file. Hooks are on per checkout:
`git config core.hooksPath .githooks` (set in this one 2026-10-27). PNGs can't be read: `docs/make_screenshots.py`
scans each screen's text (`svg_text`, pieces placed by column) and refuses to save one with a hit. A false alarm:
check it, then `git commit --no-verify`. Generic file-name words (`GENERIC_FILE_WORDS`) keep "photo.jpg" etc. quiet.

## Releases
`__version__` in `src/atf.py` (shown in the home title, `./atf --version`) and `CHANGELOG.md` (user-facing: what's
new, fixed, and what is or isn't tested live; never real serials or names). To release: bump both, regenerate the
screenshots (the home title shows the version), commit, wait for CI to pass on main, then
`git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z` and `gh release create vX.Y.Z --title vX.Y.Z --notes-file
<that version's CHANGELOG section>`. Public (people Watch releases): run the personal-data scan first, and only with
the user's OK.

## When the portal changes
Run `./atf` > Advanced > Capture portal pages (= `inspect`), click through the Form 1, press Enter on each page (it
records each field's question text (`context`), dropdown options and selected answer, and the newest tab to `out/capture_*.jsonl` and saves any PDF the portal sends to `out/captures/`); Claude reads those; update selectors in `config/mapping.yaml`. Advanced > Preview (= `fill-form1 --dry`)
prints steps without a browser. `--capture` records screens after the automated part into `out/capture_*.jsonl`
(includes page text, may contain PII; do not share).

## Open items / known caveats
- 5320.23 template revision: the generator (and ATF's PDF it embeds) was the Jan 2023 revision (OMB expiry
  12/31/2025) when checked; ATF had a revision pending. If the generator updates, re-check stamping on a new template.
- Early test drafts may remain in the eForms dashboard (auto-delete after 90 days); not submissions.
- Other responsible persons (added 2026-10-21 on request): practice run passed 2026-10-22 with the user as RP 2 (own
  EFT); a real second person (their own EFT/name) not yet. Verify page RP read-back not mapped. U.S. citizens only.
- Individuals: U.S. citizens answering No to every question 11 only; not yet filed live (mapped from a capture).
- Suppressors only (SILENCER, caliber free text); other NFA types have different firearm fields.
- Windows: filing proven live by multiple testers (user confirmed 2026-10-27), and install/launcher/tests run on
  GitHub's Windows runners. Ctrl-C logout is tested by simulating the interrupt, not by a real console Ctrl-C on Windows.
- Ideas: Google Sheet as single source of truth (queue + write-back + status sync from the eForms dashboard);
  reusing eForms "Saved Line items".
- Legal: each submission is a certification under 18 U.S.C. 924 / 26 U.S.C. 5861 by the user. Check ATF's eForms
  terms of service on automation; the tool does not bypass CAPTCHAs or bot checks and the user stays in the loop.
