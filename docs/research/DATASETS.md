# Datasets proposed for the demo SMEs (R1, 2026-10-17)

Principle: **public series are anchors and drivers; the SME ledgers are synthetic** (see `SME_SELECTION.md`). Nothing below is a real firm's data. Every licence is also logged in `../LICENSES_DATA.md`.

How it was checked: I read each publisher's page or terms in the in-app browser or by fetch. I did **not** download any dataset (the rules for this task require explicit permission before downloading); sizes shown are what the publisher page states. Items I could not confirm are marked **UNCONFIRMED**.

Time window for the demo data: **1 Jan 2020 – 30 Sep 2026** (all three SMEs).

## 1. Summary table

| ID | Dataset | Publisher | Licence (exact name) | Years | Granularity | Size | Login | Used for |
|---|---|---|---|---|---|---|---|---|
| DS01 | Commodity Price Data ("Pink Sheet"), monthly | World Bank | Creative Commons Attribution 4.0 International (CC BY 4.0) — <https://creativecommons.org/licenses/by/4.0>, default for World Bank datasets per <https://datacatalog.worldbank.org/public-licenses> | 1960 – Sep 2026 (start year from 2016 documentation; confirm in file) | monthly, nominal USD | Excel workbook; size not stated on page | No | Cost drivers: edible oils, sugar, wheat, rice, cotton, coffee, tea, crude oil |
| DS02 | Bangladesh – Food Prices | WFP via HDX | Creative Commons Attribution for Intergovernmental Organisations (CC BY-IGO) (shown on the HDX page) | HDX lists the period as "15 Aug 1900 – 15 Aug 2026" (a placeholder); real first observation **UNCONFIRMED** (not downloaded) | monthly by market and commodity | CSV 4.3 MB | No | `bd_grocer` price paths (rice, oil, sugar, lentils) |
| DS03 | Bangladesh – Markets | WFP via HDX | CC BY-IGO | n/a | market list with coordinates | CSV 8.5 KB | No | Market names for DS02 |
| DS04 | World Development Indicators (exchange rate, CPI inflation) | World Bank | CC BY 4.0 (same page as DS01) | 1960 – latest year | annual | API JSON, small | No | BDT/USD and inflation path for `bd_grocer`; CNY and GBP cross-check |
| DS05 | Foreign exchange rates, H.10 (daily CNY, EUR, GBP per USD; also LKR if the backup SME is used) | Board of Governors of the Federal Reserve System, via FRED | Public domain (Board website policy: "in the public domain"; citation requested). The FRED series page tags it "Public Domain: Citation Requested" | 1970s – Oct 2026 (start varies by currency; **UNCONFIRMED**) | daily (business days) | CSV, a few hundred KB each | No for the CSV link; an API key is needed only for the FRED API | `cn_xborder` FX (CNY, EUR, GBP); `uk_garden` GBP |
| DS06 | Global Supply Chain Pressure Index (GSCPI) | Federal Reserve Bank of New York | New York Fed Terms of Use (non-exclusive licence to access, copy, distribute and modify, attribution, no implied endorsement) — <https://www.newyorkfed.org/privacy/termsofuse> | start year **UNCONFIRMED** (page gave none) | monthly | small spreadsheet | No | Freight and lead-time driver for `cn_xborder` |
| DS07 | Online Retail II | UCI Machine Learning Repository (donated by the dataset authors) | Creative Commons Attribution 4.0 International (CC BY 4.0), stated on the UCI page | 1 Dec 2009 – 9 Dec 2011 | transaction lines, 1,067,371 rows | zip 43.5 MB | No | Basket-size, weekday/hour shape, customer-country mix (shape only; dates shifted) |
| DS08 | Global Historical Climatology Network – Daily (GHCN-Daily) | NOAA NCEI | **No licence text on the dataset readme or product page.** NOAA open data practice is no use restrictions with attribution requested (NCEI Open Data Policy); the sister dataset GHCN-Hourly is listed on AWS under CC0-1.0. **Treat as NOAA open data; cite the dataset and the DOI** (see §3) | 1700s – present, daily; by station | daily TMAX, TMIN, PRCP per station | one small file per station | No | Weather driver for `uk_garden` (Yorkshire station) and optional Dhaka rainfall |
| DS09 | Retail Sales Index (Great Britain, value and volume) | Office for National Statistics | Open Government Licence v3.0 (site footer: all content under OGL v3.0 unless otherwise stated) | long monthly history; start year **UNCONFIRMED** | monthly, by retail sector | xlsx 10.7 MB | No | National retail seasonality shape and trend for `uk_garden` (no garden-centre series; use non-food stores) |
| DS10 | CPI Index 00: All Items (2015=100), CDID D7BT | Office for National Statistics | Open Government Licence v3.0 | long monthly history; start year **UNCONFIRMED** | monthly | CSV via generator URL, small | No | Price inflation path for `uk_garden` costs |
| DS11 | Bank Rate (official rate) history | Bank of England | The Bank states database reproduction is subject to the UK Open Government Licence; some exchange-rate series have third-party licences — Bank Rate itself is the Bank's own series. Terms: <https://www.bankofengland.co.uk/legal> | 1694 – present (changes only) | event dates | tiny | No | Interest-rate context for `uk_garden` loans |
| DS12 | `holidays` Python package (calendars for BD, CN, GB, US) | vacanza/holidays contributors | MIT License | not data; code | yearly calendars | package | No | Eid, Ramadan, Chinese New Year, Golden Week, UK bank holidays and Easter |

## 2. Direct download URLs

None of these need a login. Do not store raw files in git without checking size; store them under `data/external/` (git-ignored if large) and keep this table as the manifest.

| ID | Direct URL | Target file name |
|---|---|---|
| DS01 | <https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx> (the hash changes each release; current is the 2 Oct 2026 file; next update 3 Nov 2026; if the link breaks, take it from <https://www.worldbank.org/en/research/commodity-markets>) | `CMO-Historical-Data-Monthly.xlsx` |
| DS02 | <https://data.humdata.org/dataset/c76eabb7-fdb5-43b7-a5c4-09091bb8acde/resource/966ab7ac-56d6-4dac-8eba-dfe815d59a52/download/wfp_food_prices_bgd.csv> | `wfp_food_prices_bgd.csv` |
| DS03 | <https://data.humdata.org/dataset/c76eabb7-fdb5-43b7-a5c4-09091bb8acde/resource/60852e15-3e4c-43eb-abf0-650e83c8eb91/download/wfp_markets_bgd.csv> | `wfp_markets_bgd.csv` |
| DS04 | `https://api.worldbank.org/v2/country/BGD;CHN;GBR/indicator/PA.NUS.FCRF?format=json&per_page=500` and `.../indicator/FP.CPI.TOTL.ZG?...` (indicator codes from my knowledge of the WDI API; **UNCONFIRMED** until fetched) | `wdi_fx.json`, `wdi_cpi.json` |
| DS05 | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXCHUS` (CNY per USD), `id=DEXUSEU` (USD per EUR), `id=DEXUSUK` (USD per GBP), `id=DEXSLUS` (LKR per USD, backup SME only). Series page confirmed for DEXCHUS: <https://fred.stlouisfed.org/series/DEXCHUS>. The `fredgraph.csv` URL pattern and the other three series IDs are from my knowledge, **UNCONFIRMED** | `fx_<series>.csv` |
| DS07 | <https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip> (or `ucimlrepo`, dataset id 502) | `online_retail_ii.zip` |
| DS08 | `https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt`, `ghcnd-inventory.txt`, then the chosen station's file under `.../ghcn/daily/all/<STATION>.dly` or CSV under `.../by_station/` (paths from the readme; read) | `ghcnd-stations.txt`, `ghcnd-inventory.txt`, `<STATION>.dly` |
| DS09 | `https://www.ons.gov.uk/file?uri=/businessindustryandtrade/retailindustry/datasets/retailsalesindexreferencetables/current/mainreferencetables.xlsx` | `retail_sales_index_ons.xlsx` |
| DS10 | `https://www.ons.gov.uk/generator?format=csv&uri=/economy/inflationandpriceindices/timeseries/d7bt/mm23` | `ons_cpi_d7bt.csv` |
| DS11 | <https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp> (page; I did not read it) | `boe_bank_rate.csv` (manual export) |
| DS12 | `pip install holidays` (latest 0.106 per PyPI JSON; needs Python ≥ 3.10; the repo uses 3.11) | n/a |

Station choices for DS08 are **UNCONFIRMED**: pick from `ghcnd-stations.txt` a station within about 30 km of the SME and check `ghcnd-inventory.txt` shows TMAX, TMIN and PRCP for 2020–2026. For Yorkshire, look for UK airport stations (IDs starting `UKM000`). For Dhaka, WMO 41923 is the likely source (ID `BG000041923`, **UNCONFIRMED**).

## 3. Attribution text to ship with the data

Put these in `data/synthetic/README.md` and the deck's data-sources slide:

- World Bank: "World Bank, Commodity Price Data (The Pink Sheet), and World Development Indicators, licensed CC BY 4.0. Changes: monthly series resampled and used as drivers for synthetic ledgers."
- WFP: "WFP Bangladesh food prices via the Humanitarian Data Exchange, CC BY-IGO. Used as an anchor for synthetic retail prices."
- Federal Reserve: "Source: Board of Governors of the Federal Reserve System, H.10 release, via FRED, Federal Reserve Bank of St. Louis." If the FRED API is used, show: "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis." The CSV links do not need the API.
- NY Fed: "Federal Reserve Bank of New York, Global Supply Chain Pressure Index; not endorsed by the New York Fed."
- UCI: "Chen, D. (2019). Online Retail II [dataset]. UCI Machine Learning Repository, CC BY 4.0." **Check the exact citation on the UCI page before publishing; the author name here is from my memory, not from the page I read.**
- NOAA: "Menne et al. 2012, J. Atmos. Oceanic Technol. 29, 897–910, doi:10.1175/JTECH-D-11-00103.1; GHCN-Daily dataset DOI 10.7289/V5D21VHZ; version and access date as used." (From the readme.)
- ONS and Bank of England: "Contains public sector information licensed under the Open Government Licence v3.0."
- Keep the "synthetic" label on every generated file.

## 4. For Jawat to download (manual steps)

Nothing needs a login or an account. These are the only items where I could not give a verified direct link:

1. **GSCPI (DS06).** Go to <https://www.newyorkfed.org/research/policy/gscpi>, find the "download data" link on the page (the text I could read gave no file URL), save the spreadsheet as `gscpi_data.xlsx` under `data/external/`. If the link is hard to find, skip it; the generator can use a published freight-cost shape instead, and the `cn_xborder` demo does not depend on it.
2. **Bank Rate history (DS11).** On <https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp>, choose the full history and save as `boe_bank_rate.csv`. The page was not read, so the export route is **UNCONFIRMED**. Optional: Bank Rate has only a handful of changes in 2020–2026 and can be typed from the Bank's own table.
3. **Fed H.10 fallback (DS05).** If `fredgraph.csv` does not work from the script, use the Federal Reserve Data Download Program: <https://www.federalreserve.gov/datadownload/Choose.aspx?rel=H10> and export CSV. Note the Board announced it will remove "Build Your Package" in the week of 9 Nov 2026; do it now.
4. **NOAA station pick (DS08).** Download `ghcnd-stations.txt` and `ghcnd-inventory.txt`, choose the station as described in §2, then download that station's file.

These are all quick, and none holds personal data.

## 5. Datasets considered and rejected (or not used)

| Dataset | Decision | Reason |
|---|---|---|
| Kaggle competition datasets for store sales (Rossmann Store Sales; other Kaggle forecasting competitions such as grocery or retail demand) | **Rejected** | Kaggle competition data is governed by each competition's rules and is generally limited to competition participation. I could not read the Rossmann rules, so reuse is unverified, and the FinTechathon rules require properly licensed data (OFFICIAL_RULES §6). Do not use. |
| UCI "Online Retail" (original, 2010–2011) | Not needed | Superseded by Online Retail II (DS07), which has a stated CC BY 4.0 licence and two years. |
| Drewry World Container Index, Freightos Baltic Index | Rejected | Proprietary indices; I found no open licence. DS06 is used instead (GSCPI). |
| Bangladesh Bank exchange-rate tables, BBS (Bangladesh Bureau of Statistics) CPI | Not used | I did not find a reuse licence, and a clearer substitute exists (DS04 for annual values). Not checked in detail. |
| Open-Meteo historical weather | Not default | The data are CC BY 4.0, but the free API is for non-commercial use with call limits (secondary source; the official terms page returned nothing readable). NOAA is the primary choice. Acceptable as an optional extra for Dhaka rainfall with attribution "Weather data by Open-Meteo.com". |
| Instacart, Dominick's and other academic retail datasets | Not used | Academic or non-commercial terms in my understanding; not read. |
| Any the pilot company data | **Forbidden** | Private; see CLAUDE.md. |
| FRED third-party series (e.g. OECD or national-statistics series hosted on FRED) | Avoid | FRED's API terms say third-party copyrighted series need the owner's permission for anything beyond personal use. Use only series whose notes say public domain (the H.10 series above). |

## 6. Gaps and cautions

- Bangladesh has no public monthly BDT/USD series that I could verify as freely licensed; the generator should model it from DS04 annual averages and say so ("modelled").
- No public SME-level sales dataset covering five years was found with a clean licence. This is why ledgers are synthetic and DS07 supplies only shapes.
- Eid dates differ by country announcement by a day or so; the `holidays` library for Bangladesh did not state its Islamic-date accuracy (GitHub page). Fix the Eid dates per year by hand from an official calendar if they matter to the demo.
- The ONS retail index does not include a garden-centre series (page did not mention one); the generator should model garden-centre seasonality from weather and Easter timing.
