# Filing SBRs (and other Form 1 types): research

Status: research (branch `sbr-research`). The firearm popups were mapped live on 2026-11-19 with a throwaway draft
(test serial, never submitted); see "Live mapping" below. The 5320.23 approach chosen: **stamp more fields** (option B).

## What changes compared to a suppressor

A suppressor Form 1 made by this tool is always "maker = the applicant" (manufacturer code `FMI`), type `SILENCER`,
model `N/A`, a fresh serial the user assigns, no barrel length. An SBR is usually made from a rifle or receiver that
already exists and already has a serial, so most of item 4 comes from that firearm instead of from settings.

## New information needed per SBR row

| Field | Form 1 item | Where it goes in eForms | Suppressor today | SBR |
|---|---|---|---|---|
| Type of firearm | 4b | `#productType` | fixed `SILENCER` | `SHORT BARRELED RIFLE` (also SBS / AOW use the same flow) |
| Original manufacturer | 4a | manufacturer popup (`#byCode` / `#manufacturerCode`, or search by name) | fixed `FMI` | the receiver's maker (e.g. Aero Precision, Brownells) chosen from ATF's licensee list; `FMI` only if the receiver is unserialized / home-built (80%) |
| Manufacturer country | 4a | `#country` | US | maker's country (imports: importer too) |
| Model | 4d | `#weaponModel` | `N/A` from settings | receiver's model as marked (e.g. `M4E1`, `BRN-10`) |
| Caliber or gauge | 4c | `#weaponCaliber` + `#unitOfMeasure` | free text, `CALIBER` | may be a dropdown once a firearm type is chosen; `GAUGE` for an SBS |
| Barrel length (in) | 4e | unknown selector (hidden for SILENCER, per mapping comment) | n/a | planned barrel length, e.g. `10.3` |
| Overall length (in) | 4f | `#overallLength` | settings default | measured with the stock **extended**, bare muzzle |
| Serial number | 4g | `#serialNumber` | user-assigned | the **existing** serial exactly as engraved on the receiver |
| Additional description | 4h | `#description` | "I possess no parts…" | e.g. "AR-15 type rifle to be fitted with a 10.3 in barrel"; the "no parts" statement doesn't fit since the receiver already exists |
| Supporting docs | | `docs:` repeat | none | usually none needed |

Per-row, not per-settings: manufacturer, model, caliber, barrel length, overall length, serial (and type). Today's
queue CSV (`data/suppressors.csv`) holds only serial, caliber, overall length, so SBRs need their own queue rows
with these columns. `SBR_COLS` in `src/atf.py` already has type, model, barrel_length, overall_length for approved
SBRs read from PDFs; a queue would add `manufacturer`, `manufacturer_code` (or name), `country`, `description`,
`status`, `application_number`, `filed_date`.

## 5320.23 (responsible person questionnaire)

The template from the jakepro716 generator has item 4 baked in. Checked a real trust template:
4a type = `SILENCER`, 4b maker = the trust's name and address, 4c model = `N/A`. Only 4d caliber and 4e serial (plus date and
CLEO line) are stamped per row.

For SBRs, either:
- **A. Separate SBR templates per responsible person** (type `SHORT BARRELED RIFLE`, model filled in), stamped the
  same way. Simple, but one template set per type/model.
- **B. Stamp 4a and 4c too** (cover the template's text and print the row's type and model, as 4d/4e are handled now).
  One template set covers everything; needs `_near()` positions for 4a/4c on the generator's layout.

4b on the 5320.23 stays the maker = the trust (the trust is the one making the NFA firearm). This still needs to be checked.

## Process differences (not code, but the tool should say so)

- After approval, 27 CFR 479.102: engrave the **maker (trust name) and city/state** on the receiver. The existing serial is kept,
  so no new serial is needed. The model and caliber markings only change if they differ from what's already marked.
- Don't install the short barrel or put the rifle in SBR configuration until it's approved.
- State law check (some states ban SBRs).
- The CLEO packet and the fingerprints / EFT are unchanged.

## Live mapping (2026-11-19, draft only)

**Popup 1, manufacturer.** The popup's own instructions: silencer = Manufacturer Code `FMI`; "Short-Barrel
Rifle/Shotgun, AOW, or Destructive Device: Enter the name of the original manufacturer (stamped/engraved on serialized
part). Only use manufacturing code of FMI if serialized part is wholly manufactured by Registrant".
- `#byName` radio (default) | `#byCode`. By name: a text box with NO id (an autocomplete) offering ATF licensee names
  as you type ("AERO PRECISION" offered `AERO PRECISION INC` and `AERO PRECISION LLC`: the user must pick the exact
  legal name), then `#verifyManufacturer` -> "Manufacturer successfully verified" (Ok) and the code appears in
  `#manufacturerCodeFromName` (here `AP2`). Storing the CODE per row and using `#byCode` (as FMI does today) is the
  simplest automation; the name search is for finding it once.
- `#country` (select) must be set too. `#loadTemplate` = "Saved Line items" (not used).

**Popup 2, details.** `#productType` options: ANY OTHER WEAPON, DESTRUCTIVE DEVICE, MACHINEGUN, SHORT BARRELED RIFLE,
SHORT BARRELED SHOTGUN, SILENCER. With a manufacturer other than FMI, Model is an ng-select (`ng-select#weaponModel`, ATF's
list for that maker: for AP2 "17DM-15, A15, AC-9, AERO PRECISION M4E1, AP15, AR15, ...") and Caliber a select
(`select#weaponCaliber`, 58 values like `5.56`, `5.56 NATO`, `300 BLACKOUT`). Ticking `#descriptionNotListed` ("My
item description is not in the list, create new item") turns both into plain text inputs `input#weaponModel`
(maxlength 50) and `input#weaponCaliber`: the same ids the silencer steps fill, so **tick it and type** (no need to
know ATF's lists). Then `#unitOfMeasure` (CALIBER/GAUGE/...), **`#barrelLength`** (maxlength 6),
`#overallLength` (6), `#serialNumber` (30), `#description` (textarea, maxlength 120). "State Why You Intend To Make
Firearm" is fixed text "FOR ALL LAWFUL PURPOSES". On-screen rules: model must match the receiver's engraving; one
caliber (9, 45, 357, 5.56, 7.62, 300); serial must match the engraving. "Save As New Template" is a button to avoid.

**Popup 3, documents:** not enforced (Finish with none worked live), but the National Gun Trusts eForm 1 guide says
non-silencer NFA items should upload a photo of the engravings here. ATF's own eForm 1 guidance calls it optional
("photo of firearm with markings"); forum reports: examiners sometimes email for one on unusual models, and a
serial that doesn't match the engraving gets disapproved. Plan: one OPTIONAL markings photo per SBR (recommended). 10 files/line item, 3 MB each, 30 MB total.

**Line-item grid** columns: Status, ID, Type, Manufacturer, **Manufacturer Address**, Product Type, Caliber, Model,
Barrel Length, Overall Length, Serial Number. ATF supplies the maker's address from its licensee record: read it
here for the 5320.23 item 4b.

**Verify page** (`/verify`): Firearm block = Serial Number, Model, Weapon Type, Caliber, Barrel Length, Overall
Length, and a second "Business Name" (presumably the manufacturer). Ids not read (a browser extension blocked
scripts); the existing `#serialNumberMessage`, `#model`, `#weaponType`, `#caliber`, `#overallLength` presumably hold,
barrel length likely `#barrelLength`: confirm with Capture portal pages on the first SBR practice run.

**Application page:** "Tax Paid ... $0 for other types of firearms" = the same `#taxPaid` box.

ATF instruction K (Form 1): 4a, modifying an existing firearm = name and location of the ORIGINAL manufacturer;
4c one caliber or gauge; 4e/4f one barrel length and one overall length, folding/collapsible stock measured
EXTENDED; 4g never alter the existing serial; markings: maker's (the trust's) name, city and state per 27 CFR
478.92 / 479.102. SBR/SBS/DD/MG: a Form 5320.20 may be needed before interstate transport.

## Information to collect per SBR (final list)

| Setting | Example | Used for |
|---|---|---|
| type | SHORT BARRELED RIFLE | `#productType`, read-back, 5320.23 4a |
| manufacturer code | AP2 | `#byCode` + verify (found once with the name search) |
| manufacturer name + address | AERO PRECISION LLC, city/state | 5320.23 4b (name from verify, address from the grid) |
| manufacturer country | UNITED STATES | `#country` |
| model (as engraved) | M4E1 | `#weaponModel` (not-listed), 5320.23 4c |
| caliber/gauge + unit (ONE caliber; a receiver marked MULTI = the built caliber here, "MULTI" in description, per instruction K(3)) | 5.56 / CALIBER | `#weaponCaliber`, `#unitOfMeasure`, 5320.23 4d |
| barrel length | 10.3 | `#barrelLength` |
| overall length (stock extended) | 30 | `#overallLength` |
| serial (existing) | as engraved | `#serialNumber`, 5320.23 4e |
| description (optional) | free text, 120 max | `#description` |
| receiver markings photo | JPG/PDF, 3 MB max | popup 3 `#file-upload` + description, PER ROW (today's `docs:` is the same for every filing) |

5320.23 stamping (option B) then covers 4a type, 4b maker name/address, 4c model as well as 4d/4e; the template's
`SILENCER` / trust-as-maker / `N/A` text is covered like the caliber and serial are today. Decided 2026-11-19: 4b on the
5320.23 = the ORIGINAL manufacturer's name and address, matching Form 1 item 4a (ATF instruction K(1) for a modified
existing firearm). Suppressors keep the trust as maker.

## Manufacturer lookup (live 2026-11-19, clean browser)

Users don't know ATF codes, so the tool finds them: the name box is a PrimeNG autocomplete
(`p-autocomplete#manufacturerName input`) that needs real keystrokes and matches any part of a name ("palmetto" ->
6 incl. "RAWL, JAMES B DBA PALMETTO ARMAMENT"); suggestions are `.ui-autocomplete-panel li.ui-autocomplete-list-item`
(plain names; the code/address aren't in the page); no match = no panel. Verify -> code in
`#manufacturerCodeFromName`. 5320.23 item 4b uses the name + city/state MARKED on the receiver (27 CFR 478.92
requires them there; Form 1 instruction K(1) asks for "name and location"), so no address lookup is needed.

## Code touch points

1. `config/mapping.yaml`: `#productType` from `{firearm_type}`; manufacturer popup branches (FMI vs. lookup);
   barrel-length step (only for non-silencer); caliber select vs. fill; read-back `#weaponType` expects
   `{firearm_type}`, plus barrel length.
2. `src/atf.py`: queue rows with the new columns; `_row_context()` placeholders; filing-log match at ~line 253
   (`"SILENCER" in rec["type"]`) accepts the row's type; approved SBRs move from "queued" into `sbrs.csv`.
3. `src/ui.py`: "Add an SBR" flow (asks the fields above), queue/edit screens show type, and the "built" flow stays
   suppressor-only.
4. `src/remote.py` / `src/phone.py`: the server inventory API (types already there since API 2) needs queued SBRs.
5. 5320.23 stamping (option A or B above).

## Open questions to answer live (practice run up to the read-back page, never submitting)

- Manufacturer popup: is searching by name/code enough for common makers, and what does "not listed" look like?
- After `SHORT BARRELED RIFLE` is chosen: barrel-length selector, and whether caliber becomes a dropdown.
- Read-back page IDs for barrel length and the original manufacturer.
- Whether eForms requires a supporting document for an SBR (believed not).

Capture with `./atf inspect` on each popup during a practice run.

## Sources

- National Gun Trusts, "ATF eForm 1 walk-through guide": engraving photo for non-silencer NFA items; FMI only
  for items you make from scratch (https://www.nationalguntrusts.com/blogs/nfa-gun-trust-atf-information-database-blog/atf-eform-1-nfa-tax-stamp-walk-through-guide)

- John Pierce, "Why do some approved Form 1's say FORM 1 REGISTRATION in box 4a?": FMI for unserialized lowers;
  for a serialized receiver, choose the original licensed manufacturer
  (https://johnpierceesq.com/why-do-some-approved-form-1s-say-form-1-registration-in-box-4a/)
- Ammoland, "How to Fill Out an ATF eForm 1": OAL with stock extended; serial exactly as engraved
  (https://www.ammoland.com/2023/02/how-to-fill-out-an-atf-eform-1/)
- AccurateShooter, barrel and overall length rules (https://bulletin.accurateshooter.com/?p=58965)
- 27 CFR 479.102 (marking): https://www.ecfr.gov/current/title-27/chapter-II/subchapter-B/part-479/subpart-I/section-479.102
