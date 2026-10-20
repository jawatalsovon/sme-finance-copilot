# Public anchors: sources, licences, processing (D1)

All raw files were downloaded on **2026-10-17** by D1 from the publisher's official URL (listed in
`docs/research/DATASETS.md` section 2) into `data/raw/<source>/` (git-ignored). The raw copies are
logged in `data/raw/MANIFEST.md` (also git-ignored); this file is the committed copy of that log.
`scripts/data/build_anchors.py` turns them into the small CSVs in this folder. Nothing here is a real
SME's data. The anchors drive the synthetic ledgers (prices, FX, weather, freight, seasonality).

| Raw file (data/raw/...) | Official URL | Licence | Bytes | Anchor file(s) produced |
|---|---|---|---|---|
| worldbank_pinksheet/CMO-Historical-Data-Monthly.xlsx | https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx | CC BY 4.0 | 586,099 | pinksheet_monthly.csv |
| wfp_bgd/wfp_food_prices_bgd.csv | https://data.humdata.org/dataset/c76eabb7-fdb5-43b7-a5c4-09091bb8acde/resource/966ab7ac-56d6-4dac-8eba-dfe815d59a52/download/wfp_food_prices_bgd.csv | CC BY-IGO | 4,516,947 | wfp_bgd_retail_monthly.csv |
| wfp_bgd/wfp_markets_bgd.csv | https://data.humdata.org/dataset/c76eabb7-fdb5-43b7-a5c4-09091bb8acde/resource/60852e15-3e4c-43eb-abf0-650e83c8eb91/download/wfp_markets_bgd.csv | CC BY-IGO | 8,709 | (not used beyond inspection) |
| worldbank_wdi/wdi_fx.json | https://api.worldbank.org/v2/country/BGD;CHN;GBR/indicator/PA.NUS.FCRF?format=json&per_page=500&date=2015:2026 | CC BY 4.0 | 8,065 | bdt_usd_monthly_modelled.csv (annual value, interpolated: MODELLED) |
| worldbank_wdi/wdi_cpi.json | same API, indicator FP.CPI.TOTL.ZG | CC BY 4.0 | 7,672 | wdi_cpi_inflation_annual.csv |
| fred_h10/fx_DEXCHUS.csv, fx_DEXUSEU.csv, fx_DEXUSUK.csv | https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES> (Fed H.10) | Public domain, citation requested | 31,795 each | fx_h10_daily.csv |
| nyfed_gscpi/gscpi_data.xlsx | https://www.newyorkfed.org/medialibrary/research/interactives/gscpi/downloads/gscpi_data.xlsx | NY Fed Terms of Use (attribution, no endorsement) | 105,984 | gscpi_monthly.csv |
| uci_online_retail2/online_retail_ii.zip | https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip | CC BY 4.0 | 45,622,418 | online_retail_ii_basket_shape.csv (shape statistics only) |
| noaa_ghcnd/ghcnd-stations.txt, ghcnd-inventory.txt | https://www.ncei.noaa.gov/pub/data/ghcn/daily/ | NOAA open data (no licence text; cite Menne et al. 2012, DOI 10.7289/V5D21VHZ) | 11,441,354 / 36,396,304 | (station pick) |
| noaa_ghcnd/UKE00105903.dly (Bradford, West Yorkshire) | https://www.ncei.noaa.gov/pub/data/ghcn/daily/all/UKE00105903.dly | as above | 1,114,830 | ghcnd_bradford_daily.csv (ends 2026-08-31) |
| noaa_ghcnd/BGM00041923.dly (Dhaka) | https://www.ncei.noaa.gov/pub/data/ghcn/daily/all/BGM00041923.dly | as above | 503,550 | ghcnd_dhaka_daily.csv (ends 2025) |
| ons/retail_sales_index_ons.xlsx | https://www.ons.gov.uk/file?uri=/businessindustryandtrade/retailindustry/datasets/retailsalesindexreferencetables/current/mainreferencetables.xlsx | Open Government Licence v3.0 | 11,246,749 | ons_retail_nonfood_volume_monthly.csv |
| ons/ons_cpi_d7bt.csv | https://www.ons.gov.uk/generator?format=csv&uri=/economy/inflationandpriceindices/timeseries/d7bt/mm23 | OGL v3.0 | 11,912 | ons_cpi_d7bt_monthly.csv (ends 2026-08) |
| boe/bank_rate.html | https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp | UK OGL (per Bank legal page) | 55,600 | boe_bank_rate_changes.csv |

Notes
- All 14 datasets are the ones listed in DATASETS.md (DS01-DS11). **No licence differs from `docs/LICENSES_DATA.md`.**
  GSCPI (DS06) was downloadable directly at the URL above (the file is old binary .xls format inside a .xlsx name).
- `holidays` (DS12) is **not** installed; calendars (Eid, Chinese New Year, Easter, UK bank holidays) are
  hard-coded in `scripts/data/calendars.py` from public calendars (assumption, flagged in the README).
- Bangladesh BDT/USD monthly is MODELLED from the WDI annual average (DATASETS.md section 6 says no free monthly series was found).
- Series ending before 2026-09-30 (Bradford weather 2026-08-31, Dhaka weather 2025, WFP 2026-08, ONS CPI 2026-08)
  are completed by the generator with seasonal climatology / last value; this is flagged in `data/synthetic/README.md`.
- Attribution texts to ship: see DATASETS.md section 3 and `data/synthetic/README.md` section 8.
