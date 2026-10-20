# Data and dependency licences

Owner: R1 (research) creates it; other agents add their own rows through the orchestrator. Updated 2026-10-17.

Status key: **verified** = the publisher's page was read; **partly** = some part could not be confirmed; **unverified** = from memory, to be confirmed before submission (run `pip-licenses` and `npm ls` at code freeze).

## A. Datasets

| ID | Dataset | Publisher | Licence | Licence link | Used for | Attribution / conditions | Status |
|---|---|---|---|---|---|---|---|
| DS01 | Commodity Price Data (Pink Sheet) | World Bank | CC BY 4.0 (default for World Bank datasets; the datasets page adds dispute-resolution terms) | <https://creativecommons.org/licenses/by/4.0>, <https://datacatalog.worldbank.org/public-licenses> | Cost drivers for synthetic ledgers | Credit World Bank; note changes | verified (licence page read; the monthly file itself carries no separate notice that I saw) |
| DS02, DS03 | Bangladesh – Food Prices and Markets | WFP via HDX | CC BY-IGO | <https://data.humdata.org/dataset/wfp-food-prices-for-bangladesh> | `bd_grocer` price anchor | Credit WFP and sources listed on HDX | verified (licence field on HDX page); date coverage unconfirmed |
| DS04 | World Development Indicators | World Bank | CC BY 4.0 | as DS01 | Annual FX and inflation path | Credit World Bank | verified for the licence; indicator codes unconfirmed |
| DS05 | H.10 foreign exchange rates | Federal Reserve Board via FRED | Public domain; citation requested | <https://www.federalreserve.gov/disclaimer.htm>, <https://fred.stlouisfed.org/series/DEXCHUS> | FX for `cn_xborder`, `uk_garden` | Cite the Board and FRED. FRED API (if used) needs a key and the "not endorsed" notice; FRED third-party series are excluded | verified (disclaimer and series page read) |
| DS06 | Global Supply Chain Pressure Index | Federal Reserve Bank of New York | NY Fed Terms of Use (non-exclusive licence; attribution; no implied endorsement; label changes) | <https://www.newyorkfed.org/privacy/termsofuse> | Freight driver | Keep notices; do not imply endorsement | partly (terms read; dataset start year and download link not seen) |
| DS07 | Online Retail II | UCI Machine Learning Repository | CC BY 4.0 | <https://archive.ics.uci.edu/dataset/502/online+retail+ii> | Basket and timing shape only | Cite the dataset as on the UCI page | verified |
| DS08 | GHCN-Daily | NOAA NCEI | No licence text on the dataset readme; NOAA open data (attribution requested; do not imply NOAA endorsement; do not present modified data as original) | <https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt>, <https://www.ncei.noaa.gov/sites/default/files/2023-12/NCEI%20PD-10-2-02%20-%20Open%20Data%20Policy%20Signed.pdf> | Weather driver for `uk_garden` | Cite Menne et al. 2012 and the DOI 10.7289/V5D21VHZ, version and access date | partly (no explicit licence on the dataset itself; low risk) |
| DS09 | Retail Sales Index | ONS | Open Government Licence v3.0 | <https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/> | UK seasonality shape | "Contains public sector information licensed under the OGL v3.0" | verified (footer on the dataset page) |
| DS10 | CPI Index 00 (D7BT) | ONS | Open Government Licence v3.0 | as DS09 | UK price path | as DS09 | verified |
| DS11 | Bank Rate history | Bank of England | UK Open Government Licence (per the Bank's legal page; some FX series excluded) | <https://www.bankofengland.co.uk/legal> | Rate context | as DS09 | partly (legal page read; Bank Rate page not read) |
| DS12 | Iowa Liquor Sales (yearly files 2020–2024; retailer purchases from the state) | State of Iowa, Department of Revenue, Alcohol Operations Bureau, via the Iowa Data Hub | CC BY 4.0 (stated on each catalog.data.gov record) | <https://creativecommons.org/licenses/by/4.0/>, <https://catalog.data.gov/dataset/iowa-liquor-sales-2020> | Real store-level purchase history for forecast validation (see `research/REAL_DATA_VALIDATION.md`) | Credit State of Iowa; note changes. **Not downloaded yet**: files are several hundred MB each | verified (licence); size and columns unconfirmed |
| DS13 | London Borough of Bexley, "£500 Spend Report" CSVs (Aug–Oct 2025 used) | London Borough of Bexley | Open Government Licence (statement on the council page) | <https://www.bexley.gov.uk/bexley-business-employment/business-services/contracts-tenders-and-procurement/expenditure-records> | Real payments ledger for duplicate/credit-note detection tests | OGL attribution statement; suppliers may include sole traders, so pseudonymise supplier names in any public output | verified (council page) |
| DS14 | Car Parts Dataset (with missing values), Monash Time Series Forecasting Archive | Godahewa, Bergmeir, Webb, Hyndman, Montero-Manso; data from the R `expsmooth` package (Hyndman) | CC BY 4.0 (Zenodo record) | <https://zenodo.org/records/4656022> | Real intermittent monthly sales of 2,674 parts, 51 months, for forecast-method tests | Cite the Zenodo DOI 10.5281/zenodo.4656022. Upstream licence of the `expsmooth` data not checked | partly |
| DS15 | Dominick dataset (Monash archive version; weekly SKU profit) | Monash archive; original Kilts Center, University of Chicago Booth | CC BY 4.0 on the Zenodo record; **the original Kilts Center terms were not read** | <https://zenodo.org/records/4654802> | Not used for judge-facing results until the upstream terms are checked | Keep out of public outputs | unverified upstream; downloaded as zip only |
| — | Synthetic SME ledgers (`data/synthetic/`) | This team | Generated by our code; no third-party records | n/a | Demo | Labelled synthetic in every README | n/a |
| — | the pilot company data | Jawat's company | **Private; never committed** | n/a | Internal testing only | `data/private/` and `reports/private/` (git-ignored); no figures in any public file | n/a |

Rejected: see `research/DATASETS.md` §5 (Kaggle competition data, proprietary freight indices, and others).

## B. Code and dependencies

| Item | Licence | Use | Status |
|---|---|---|---|
| `holidays` (vacanza/holidays), 0.106 | MIT (PyPI metadata and GitHub page) | Public-holiday calendars | verified |
| FastAPI | MIT | Backend API | unverified |
| Uvicorn | BSD-3-Clause | ASGI server | unverified |
| DuckDB | MIT | Canonical ledger | unverified |
| pandas | BSD-3-Clause | Data handling | unverified |
| pytest | MIT | Tests | unverified |
| Next.js / React | MIT | Frontend | unverified |
| OpenAI / Anthropic Python SDKs | MIT / MIT | Optional LLM providers | unverified |

## C. External services (sandbox use only, no production data)

| Service | Terms noted | Status |
|---|---|---|
| QuickBooks Online sandbox (Intuit Developer) | Sandbox is for app development only; content and sample data provided for non-commercial use and testing; up to 10 sandboxes, valid two years. <https://developer.intuit.com/app/developer/qbo/docs/develop/sandboxes/manage-your-sandboxes> | verified (page read). The free developer account is created by Jawat |
| Square sandbox | Free isolated test environment; test cards only. <https://developer.squareup.com/docs/devtools/sandbox/overview> | partly (search summary; page not read in full) |
| Xero demo company (alternative) | Resets after 28 days; no automatic bank feeds. <https://central.xero.com/s/article/Use-the-demo-company> | unverified (secondary summaries only) |
| Amazon SP-API settlement report format (used only as a layout reference for synthetic files) | <https://developer-docs.amazon.com/sp-api/docs/report-type-values-settlement> — we copy no Amazon data or branding | partly |

## D. Rules from the competition

The competition rules require third-party data, code, open-source components and APIs to be properly licensed and to follow open-source licences and data-use terms; the team bears the liability (see `research/OFFICIAL_RULES.md` §6). The organiser may reuse entries for non-commercial promotion, so no private data may be in any submitted file.
