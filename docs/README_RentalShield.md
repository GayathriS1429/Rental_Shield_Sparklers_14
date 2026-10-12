# RentalShield

> **Found a rental? Check it before you pay.**

RentalShield is a renter-focused web application that brings together public property-address data, rental-market benchmarks, nearby police-reported incident records, and warning phrases from a rental listing or landlord chat. It helps renters review evidence and decide what to verify before sending money or signing a tenancy agreement.

RentalShield is a **screening and decision-support tool**, not a rental marketplace, legal service, police service, ownership registry, or validated scam-prediction model. It does not prove that a listing is fraudulent or safe.

## Contents

- [Project overview](#project-overview)
- [Current features](#current-features)
- [Four-part concern score](#four-part-concern-score)
- [Crime-context method](#crime-context-method)
- [Alternative-neighbourhood suggestions](#alternative-neighbourhood-suggestions)
- [Technology stack](#technology-stack)
- [Architecture and workflow](#architecture-and-workflow)
- [Data sources](#data-sources)
- [Project structure](#project-structure)
- [Installation and setup](#installation-and-setup)
- [Running RentalShield](#running-rentalshield)
- [Analytics page and EDA](#analytics-page-and-eda)
- [Database and saved reports](#database-and-saved-reports)
- [Configuration and secrets](#configuration-and-secrets)
- [Important limitations](#important-limitations)
- [Troubleshooting](#troubleshooting)
- [Responsible use](#responsible-use)
- [Future improvements](#future-improvements)
- [Primary data and guidance links](#primary-data-and-guidance-links)

---

## Project overview

### Problem

Renters often find properties through rental websites, social-media posts, or community groups. A listing may look convincing while important facts remain difficult to assess. A renter may need to check whether an address appears in public records, whether the asking rent differs from a market benchmark, whether reported incidents occur around the location, and whether the listing text contains pressure or unusual payment requests.

The underlying problem is **fragmented evidence**: the information is distributed across different datasets, websites, and conversations.

### Proposed solution

RentalShield provides one workflow for entering a listing and reviewing four kinds of evidence:

1. Property-address match.
2. Nearby police-reported incident context.
3. Asking-rent comparison with a Vancouver CMA benchmark.
4. Rule-based warning-phrase analysis of a listing description or copied landlord/agent chat.

The report combines these checks into an explainable, provisional **Overall Rental Concern Score**, explains the findings, suggests next actions, and may suggest alternative neighbourhoods when the overall score is Medium or High.

### Geographic scope

The rental-address verification pilot is focused on **Downtown Vancouver and the West End**. The crime comparison baseline uses the supplied citywide VPD incident export and City of Vancouver property-address points to compare sampled 500-metre areas across Vancouver.

---

## Current features

- **Account access:** registration, sign-in, sign-out, and report history associated with the signed-in user.
- **Listing input:** manual address, neighbourhood, monthly rent, bedroom count, and listing description.
- **Best-effort listing fetch:** the user can submit a public listing URL; Playwright attempts to read public page text and populate fields. Extraction can fail when websites block automated access or their layouts change.
- **Address verification:** normalizes and searches the available Downtown and West End address datasets.
- **Rent benchmark:** compares the entered rent with a Statistics Canada Vancouver CMA average asking-rent benchmark for 1-bedroom, 2-bedroom, or 3+ bedroom units.
- **Listing/chat language checks:** matches configured patterns such as deposit-before-viewing requests, urgent payment deadlines, inability to show a property, and unusual payment methods.
- **Nearby crime context:** summarizes common reported incident types and the relative incident-record level near the matched address for the selected period, by default 2023–2026. The 2026 data is partial-year.
- **Four-part score:** displays up to 25 points from each verification component, with a transparent breakdown.
- **Alternative areas:** for Medium/High scores, attempts to suggest up to three nearby neighbourhoods with lower median nearby incident counts in the available comparison data. These are area suggestions, not individual available flats.
- **Saved reports:** stores the results as JSON in the SQLite database and displays them on the report page.
- **Downloads:** supports PDF and JSON report downloads.
- **Analytics:** provides charts and tables for the available police-reported incident records, rent benchmarks, address-data coverage, and data-quality notes. Its summary data is prepared by the EDA pipeline.
- **Homepage maps:** uses Leaflet with CARTO basemap tiles and OpenStreetMap/CARTO attribution.

---

## Four-part concern score

The overall score ranges from **0 to 100 points**, with a maximum of 25 points for each of four checks. The weights are heuristic design rules for the current MVP; they are not calibrated against a labelled rental-scam dataset.

| Component | Current rule | Maximum |
|---|---|---:|
| Address verification | Exact match found in the searched reference data: 0 points. No match: 25 points. Invalid input, unsupported area, or another uncompleted check is not assessed and adds 0 points. | 25 |
| Nearby crime activity | Low relative activity: 0 points; Medium: 13 points; High: 25 points. | 25 |
| Rent benchmark difference | Based on the absolute percentage difference from the Vancouver CMA asking-rent benchmark: under 5% = 0; 5% to under 10% = 5; 10% to under 20% = 10; 20% to under 30% = 18; 30% or more = 25. | 25 |
| Listing/chat warning wording | Each detected high-severity warning adds 12 points, medium adds 7, and low adds 3; the component is capped at 25 points. | 25 |
| **Total** | Sum of the component points. | **100** |

### Overall levels

| Total | Display label |
|---:|---|
| 0–24 | Low overall rental concern |
| 25–49 | Medium overall rental concern |
| 50–100 | High overall rental concern |

### Score interpretation

- A higher score means more configured concern indicators were found; it is **not a percentage probability of fraud**.
- A missing address match does not prove that the address or listing is invalid. The source dataset may not include every valid address.
- High nearby crime activity describes reported incidents around an area; it does not establish that the landlord, building, or listing is fraudulent.
- Rent far above or below the benchmark is a pricing difference to investigate, not proof of fraud.
- If a component cannot be assessed, it adds zero points and the report marks the assessment as **partial**. An incomplete score may understate concern. Users should complete all available checks before relying on the combined score.
- A clean text scan means no configured warning phrases were detected in the submitted text. It does not establish that the listing is genuine.

---

## Crime-context method

### Source data

The crime checker reads the combined VPD GeoDASH incident export and the City of Vancouver property-address dataset. It uses projected coordinates and a spatial index to count reported incident records within approximately **500 metres** of the matched address. It also calculates counts for nearby radius views used for diagnostics.

For the user-facing summary, the default period combines **2023, 2024, 2025, and 2026 year-to-date**. It identifies common recorded incident types (for example, Other Theft, Theft from Vehicle, and Mischief) and compares the combined nearby count with the distribution of counts around sampled property-address points across Vancouver.

### Relative Low / Medium / High bands

RentalShield calculates a percentile position for the local combined-period count among sampled Vancouver 500-metre address areas, then assigns RentalShield-defined bands:

- **Low:** bottom third of the comparison distribution.
- **Medium:** middle third.
- **High:** top third.

This percentile is **not** the percentage of all Vancouver crime that happened near the address and is **not** a population-adjusted crime rate. For example, a result at the 90th percentile means the local combined incident-record count is high relative to most sampled comparison locations; it does not mean that 90% of all city crimes occurred there.

### Data quality cautions

- The data records police-reported incidents, not every crime that occurred.
- Coordinates may be generalized or offset for privacy.
- The export can include repeated-looking records and does not provide a stable unique incident identifier for deduplication in every record.
- 2026 is partial-year and should not be compared with a complete year as if both covered the same time interval.
- Address points represent records in the City dataset, not rental units, current availability, or population distribution.
- The resulting classification is a relative context indicator defined by RentalShield, not an official police safety rating.

---

## Alternative-neighbourhood suggestions

When the total score is **25 or higher** (Medium or High), the application attempts to produce up to three area names to explore. The current logic:

1. Uses the matched rental address coordinates as the starting point.
2. Summarizes median combined 500-metre incident-record counts around sampled City address points by neighbourhood.
3. Excludes the current neighbourhood and considers nearby neighbourhoods within the configured search distance that have a lower median incident count.
4. Returns up to three neighbourhood names, an approximate distance, the relative activity label, and the number of address points represented in the source dataset.
5. Reuses the monthly rent entered by the user as a **search target** in the report.

The supplied datasets do **not** verify current rental availability or neighbourhood-specific rent averages. The feature does not identify particular apartments or guarantee that an area will be safe or within the renter's budget. Users must check current listings and verify the landlord/agent independently.

If the score is Medium/High but no suggestions appear, the application may not have enough comparable data, a usable matched address coordinate, or a compatible rebuilt baseline. See [Troubleshooting](#troubleshooting).

---

## Technology stack

| Layer | Technology |
|---|---|
| Web backend | Python, Flask |
| Frontend | HTML, CSS, JavaScript, Jinja templates |
| Maps | Leaflet; CARTO basemap tiles; OpenStreetMap/CARTO attribution |
| Database | SQLite through Flask-SQLAlchemy |
| User sessions | Flask-Login |
| CSRF protection | Flask-WTF |
| Password storage | Werkzeug password hashing |
| Listing-page extraction | Playwright / Chromium (best-effort) |
| Data processing | pandas, NumPy |
| Spatial calculation | pyproj, SciPy `cKDTree` |
| Charts | Chart.js in the analytics template; Matplotlib for EDA outputs |
| PDF reports | ReportLab |
| Configuration | `python-dotenv` and project-root `.env` |

---

## Architecture and workflow

```text
Renter's browser
      |
      v
Flask application (app.py)
      |
      +--> Account / session management
      |
      +--> Listing URL fetch (optional; Playwright)
      |
      v
Rental input validation
      |
      v
Risk engine (risk_engine.py)
      |
      +--> Address checker ------> Downtown / West End address CSVs
      |
      +--> Rent checker ---------> Statistics Canada rent benchmarks
      |
      +--> Listing analyzer -----> Submitted listing/chat text
      |
      +--> Crime checker --------> VPD citywide records + comparison baseline
      |
      v
Four-part concern score + evidence + recommended actions
      |
      +--> Alternative-area suggestions (when score is Medium/High)
      |
      v
SQLite saved report --> HTML report / PDF download / JSON download

Analytics route --> analytics_dashboard.py --> analytics/eda_outputs/ --> charts and tables
```

The core verification logic is rule-based and explainable. The application does not currently train a machine-learning model that predicts a fraud probability.

---

## Data sources

| Source | Main use | Main limitation |
|---|---|---|
| City of Vancouver — Property Addresses | Address matching for Downtown and West End; citywide reference points for nearby-area comparisons | Not a complete list of all valid addresses; presence does not verify ownership or a rental unit's availability |
| Vancouver Police Department GeoDASH — crime data | Local incident-type summary and comparison against sampled citywide address areas | Police-reported incident records; not population-adjusted rates; locations may be generalized |
| Statistics Canada — Table 46-10-0092-01 | Vancouver CMA asking-rent and paid-rent benchmark values by bedroom category | CMA-wide average, not an individual unit valuation or a neighbourhood-specific average |
| User-provided listing description or landlord/agent chat | Rule-based warning phrase analysis | Only evaluates supplied text and configured patterns; context can change the meaning of a phrase |
| Public rental-scam guidance | Supports the warning patterns and recommended verification steps | General guidance is not a listing-level fraud label |
| Public forum discussions | Qualitative evidence of renter pain points during product research | User-generated content; not independently verified case data |

### Relevant current benchmark files

- `rent_benchmark_1br_q2_2026.csv`
- `rent_benchmark_2br_q2_2026.csv`
- `rent_benchmark_3br_q2_2026.csv`

The app interprets bedroom selection `3` as **3+ bedrooms**. Benchmark data should be refreshed when a newer suitable Statistics Canada export is adopted, and the report should identify the period used.

---

## Project structure

The exact contents can vary with local setup, but the main structure is expected to look like this:

```text
RENTAL_SHIELD_SPARKLERS_14/
├── app.py                              # Flask routes, auth, report save/download
├── models.py                           # SQLAlchemy User and VerificationReport models
├── risk_engine.py                      # Four-check score and tailored next steps
├── address_checker.py                  # Address normalization and matching
├── rent_checker.py                     # Asking-rent benchmark calculation
├── listing_analyzer.py                 # Rule-based warning phrase detection
├── crime_checker.py                    # Nearby incidents, percentile and alternatives
├── data_loader.py                      # CSV and benchmark loading helpers
├── build_citywide_crime_baseline.py    # Builds cached 500 m reference counts
├── analytics_dashboard.py              # Loads EDA outputs for /analytics
├── rentalshield_eda.py                 # EDA / analytics-output preparation
├── requirements.txt                    # Python dependencies
├── .env                                # Local configuration; do not commit
├── instance/
│   └── rentalshield.db                 # Local SQLite database, created by the app
├── templates/
│   ├── index.html                      # Homepage, maps and rental check form
│   ├── login.html                      # Sign-in form
│   ├── register.html                   # Registration form
│   ├── dashboard.html                  # Saved reports
│   ├── report_detail.html               # Score, evidence and recommendations
│   ├── analytics.html                   # Analytics charts and tables
│   └── about.html                       # About page, if retained in the local build
├── data/
│   ├── property_addresses_downtown.csv
│   ├── property_addresses_west_end.csv
│   ├── rent_benchmark_1br_q2_2026.csv
│   ├── rent_benchmark_2br_q2_2026.csv
│   ├── rent_benchmark_3br_q2_2026.csv
│   └── police_crime/
│       ├── extracted_csvs/              # Pilot-area extracts used by EDA
│       └── citywide_reference/
│           ├── crimedata_csv_AllNeighbourhoods_AllYears.csv
│           ├── vancouver_property_addresses.csv
│           └── processed/               # Generated 500 m reference baseline files
└── analytics/
    └── eda_outputs/                     # Generated EDA CSVs/charts for /analytics
```

Do not put the virtual environment, SQLite database, `.env`, or large private/local files into a public repository unless they are intentionally meant to be shared.

---

## Installation and setup

These instructions assume Windows PowerShell and a local project folder such as `E:\RENTAL_SHIELD_SPARKLERS_14`.

### 1. Open the project folder

```powershell
cd E:\RENTAL_SHIELD_SPARKLERS_14
```

### 2. Create and activate the virtual environment

If you already have the working `rental_venv`, activate it instead of creating a second environment.

```powershell
python -m venv rental_venv
.\rental_venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Because listing extraction uses Playwright with Chromium, install the browser once if it is not already installed:

```powershell
python -m playwright install chromium
```

### 4. Configure `.env`

Create a file named `.env` in the same folder as `app.py`. Add a strong Flask secret key and the CARTO basemap key used by the homepage map. Example:

```env
SECRET_KEY=replace_with_a_long_random_secret
CARTO_BASEMAP_API_KEY=your_carto_basemap_key
FLASK_DEBUG=1
COOKIE_SECURE=0
```

Generate a random secret key locally with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the generated value into `SECRET_KEY`. Never commit `.env` to source control or paste real secrets into public issues or chats.

**CARTO note:** the basemap key is rendered into a browser request, so it is not a server-only secret. Restrict its allowed website/referrer origins and usage in the CARTO dashboard. The Leaflet library itself does not require an API key; the CARTO tile service may require one under its current access rules.

### 5. Confirm that required input data exists

The core address/rent/crime checks expect these files:

```text
data/property_addresses_downtown.csv
data/property_addresses_west_end.csv
data/rent_benchmark_1br_q2_2026.csv
data/rent_benchmark_2br_q2_2026.csv
data/rent_benchmark_3br_q2_2026.csv
data/police_crime/citywide_reference/crimedata_csv_AllNeighbourhoods_AllYears.csv
data/police_crime/citywide_reference/vancouver_property_addresses.csv
```

The address-area files use semicolon separators. Keep the downloaded source crime/address CSVs unchanged; processing scripts write separate output files.

### 6. Build or rebuild the citywide crime comparison baseline

Run this after adding the two citywide source CSVs, or whenever the baseline format/data changes:

```powershell
python build_citywide_crime_baseline.py
```

The script creates the `processed` folder beneath `data/police_crime/citywide_reference/` and writes a 500-metre reference-count file for each available year plus baseline metadata. It can take several minutes because it processes many address points and incident coordinates. Wait for the final `Done. Metadata: ...` message before testing the crime checker.

### 7. Prepare analytics outputs if needed

The analytics page depends on files produced by the EDA pipeline. If `/analytics` reports missing output files, check that the source CSVs expected by `rentalshield_eda.py` are present, then run:

```powershell
python rentalshield_eda.py
```

Outputs are written to `analytics/eda_outputs/`.

---

## Running RentalShield

Start the Flask application from the project root with the virtual environment activated:

```powershell
python app.py
```

Open the local URL printed by Flask, usually `http://127.0.0.1:5000/`.

For local development, `FLASK_DEBUG=1` enables debug mode in the current app code. Do not use debug mode in production.

### Typical renter workflow

1. Register and sign in.
2. Paste a public rental listing URL and click **Fetch Listing Details**, or enter the available information manually.
3. Review and correct the street number, street name, area, monthly rent, bedroom count, and listing description.
4. Click **Run Rental Checks**.
5. Review the score and each of the four component explanations.
6. Read the short nearby-crime summary and the recommended actions.
7. If the overall score is Medium or High, review any suggested neighbourhoods.
8. Download the report as PDF or JSON, or revisit it from the dashboard.

The fetch operation is best-effort. If a listing website blocks automated access, enter the relevant details manually.

---

## Analytics page and EDA

The Analytics page is served by the `/analytics` route and uses `analytics_dashboard.py` to build its page context from the EDA outputs. Its current content includes:

- Overview counts for supplied police-reported records, repeated-looking rows flagged for review, privacy-protected locations, and data period.
- Filters for year, area, and a sensitivity view that excludes rows flagged as exact repeats from selected charts.
- Annual, monthly, and incident-category charts.
- A completed-year comparison between 2023 and 2025; 2026 is shown as partial-year data.
- Statistics Canada asking-rent benchmark values and an interactive rent-versus-benchmark comparison.
- Address-reference-data coverage and a data-quality section.
- A careful explanation of why broad fraud datasets do not support an address-level rental-fraud rate.

If the Analytics page is blank or reports that data is not ready, run the EDA script and inspect `analytics/eda_outputs/`. EDA findings are descriptive and should not be described as proof that an individual property is safe or fraudulent.

---

## Database and saved reports

The app uses SQLite through Flask-SQLAlchemy. The database file is normally located at:

```text
instance/rentalshield.db
```

The application calls `db.create_all()` at startup, creating tables if they do not exist.

### `User`

Stores the username, email, password hash, optional phone country code/number, and creation timestamp. Passwords are stored as hashes, not plain text.

### `VerificationReport`

Stores the owning user ID, optional listing URL, address, optional monthly rent and bedroom count, serialized report JSON, and creation timestamp. Reports are associated with a user so one user cannot access another user's report through the normal report routes.

Saved report data is a snapshot of the findings at the time of the check. Existing reports are not automatically recalculated when the scoring rules or source datasets change; submit a new rental check to obtain a report using updated rules.

---

## Configuration and secrets

- `SECRET_KEY`: required by Flask for session/CSRF-related signing. Keep it private and stable for the environment.
- `CARTO_BASEMAP_API_KEY`: basemap key inserted into the homepage's CARTO tile URL. Restrict it by origin and usage in CARTO's settings.
- `FLASK_DEBUG`: set to `1` for local debugging only; omit or set to `0` for normal execution.
- `COOKIE_SECURE`: set to `1` when the app is deployed behind HTTPS; local HTTP development typically uses `0`.

Recommended `.gitignore` entries:

```gitignore
.env
rental_venv/
.venv/
__pycache__/
*.py[cod]
instance/
analytics/eda_outputs/
```

Review repository needs before ignoring outputs that you intentionally want to version. Never commit real API keys, passwords, session secrets, user data, or the SQLite database.

---

## Important limitations

1. **Not a validated fraud model.** The score is a hand-designed, rule-based indicator. Its weights and thresholds have not been statistically calibrated against verified rental-scam outcomes.
2. **Partial assessments can understate concern.** If a check is not performed or cannot access its data, it contributes zero points and the report should mark the assessment as partial.
3. **Address matching is limited.** A match means the address appears in the supplied source data. A missing match can occur because the data is incomplete or the input format differs.
4. **No owner/agent verification.** The app does not independently verify property ownership, the authority of the advertiser, unit vacancy, or permission to rent.
5. **Rent is a CMA-level benchmark.** The comparison is not a valuation of a particular unit and is not a neighbourhood-specific average.
6. **Crime is area context.** The relative percentile is based on recorded incidents around sampled address points, not population-adjusted rates or a guarantee of safety.
7. **No current rental inventory.** Alternative-area suggestions are neighbourhoods to research; they are not recommendations for confirmed available apartments. The entered monthly rent is a target, not a verified average for the area.
8. **Text warnings are rule-based.** They match configured phrases and can miss cleverly worded scams or flag legitimate messages that need context.
9. **Listing extraction is best-effort.** Some platforms restrict scraping or render data in ways the extractor cannot access. Respect site terms and applicable law.
10. **Public forum data is qualitative.** Reddit and other public discussions illustrate renter concerns but are not independently verified evidence about any specific advertiser.
11. **The source data can change.** Refresh benchmark/address/crime data intentionally and rebuild derived files when appropriate. Record source dates when publishing results.

---

## Troubleshooting

### `Address verification could not be completed with the supplied address/area.`

This is different from a normal “not found” result. It means the address checker returned a status other than `found` or `not_found`. Check:

- The civic/street fields are not empty and do not contain the full address in only one field.
- The selected area is either Downtown or West End, or leave the area unselected when supported to search both pilot datasets.
- The property CSVs exist at `data/property_addresses_downtown.csv` and `data/property_addresses_west_end.csv` and have the expected columns.
- The street format can be normalized to the source's `STD_STREET` naming convention.

A missing match does not establish fraud. If the error appears for a known matching address, check the saved report's address status and the current `address_checker.py` result.

### Nearby crime context is unavailable

- Confirm the two citywide CSV files are in `data/police_crime/citywide_reference/`.
- Confirm the processed baseline exists for each required year under `processed/`.
- If the code says the baseline uses an old format or lacks `reference_point_id`/`Geo Local Area`, rerun `python build_citywide_crime_baseline.py` using the latest project version.
- Confirm the matched property record has a usable `geo_point_2d` coordinate and an area that maps to Downtown or West End.

### No alternative neighbourhoods are shown

Suggestions are only triggered at Medium/High overall concern, and still require usable address coordinates and crime comparison data. There may be no other neighbourhood that satisfies the configured lower-median and proximity criteria. The feature does not fall back to inventing area suggestions.

### Analytics page says data is not ready

Run `python rentalshield_eda.py`, then verify the expected files are present in `analytics/eda_outputs/`. Check the terminal warnings for missing or unreadable source files.

### Listing URL fetch fails

The site may block automated access, time out, or change page structure. Try entering the address, rent, bedrooms, and description manually. A fetch failure does not mean the listing is fraudulent.

### Map shows `API KEY REQUIRED`

Check `CARTO_BASEMAP_API_KEY` in the project-root `.env`, confirm the app exposes it to the homepage template, and verify the key's allowed referrers and usage in the CARTO settings. Restart Flask after changing `.env` and hard-refresh the browser. The map key is visible to browsers and must be restricted by domain/usage.

### Validate Python syntax after file changes

```powershell
python -m py_compile app.py models.py risk_engine.py address_checker.py rent_checker.py listing_analyzer.py crime_checker.py data_loader.py analytics_dashboard.py build_citywide_crime_baseline.py rentalshield_eda.py
```

### Check application health

Open `/health` on the running Flask server. The route should return a small JSON response with status `ok` if the app starts and is reachable.

---

## Responsible use

RentalShield provides evidence for human review. A user should independently confirm the property, advertiser's authority, viewing arrangements, written tenancy terms, payment recipient, and applicable deposit rules before transferring money or sensitive information.

The application should not publicly accuse a named landlord or property manager. A legitimate listing can have warning signals, and a scam may avoid obvious warning phrases. The report should communicate what data was checked, what was found, what could not be checked, and what the user should verify next.

---

## Future improvements

Potential next steps, if the project scope and data access allow:

- Add unit tests for address normalization, scoring bands, partial-check behaviour, and crime summaries.
- Keep a version/date for scoring rules and source datasets in each saved report.
- Add reproducible evaluation using manually reviewed synthetic and real-world examples while protecting personal data.
- Use an authoritative, legally suitable method to check landlord/manager authorization rather than infer it from address presence.
- Add current, permitted rental inventory or area-specific rent datasets if recommendations must target actual listings and precise budgets.
- Evaluate alternative score thresholds with domain expertise and an audited dataset before describing them as predictive.
- Improve accessibility, mobile layout, and structured logging for failures.

These are future enhancements, not claims of existing functionality.

---

## Primary data and guidance links

- [B.C. RCMP — Rental Scams](https://rcmp.ca/en/bc/safety-tips/frauds-and-scams/rental-scams)
- [Richmond RCMP — Rental Deposit Scam Warning](https://rcmp.ca/en/bc/richmond/news/2025/08/richmond-rcmp-warns-public-about-rental-deposit-scams)
- [Statistics Canada — Table 46-10-0092-01](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=4610009201)
- [City of Vancouver — Property Addresses](https://opendata.vancouver.ca/explore/dataset/property-addresses/)
- [City of Vancouver — Rental Standards: Current Issues](https://opendata.vancouver.ca/explore/dataset/rental-standards-current-issues/)
- [Vancouver Police Department GeoDASH Open Data](https://geodash.vpd.ca/opendata/)
- [Competition Bureau Canada — Fraud and Scams](https://competition-bureau.canada.ca/en/fraud-and-scams/tips-and-advice/how-report-fraud-and-scams-canada)

---

## Project summary

**RentalShield = Flask web application + SQLite report history + public-data evidence checks + an explainable four-part concern score + practical next steps.**

Its value is not that it can guarantee a rental is safe. Its value is that it brings dispersed evidence into one place and helps renters know what to review before they pay.
