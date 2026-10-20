# Financing product catalogue (R1, 2026-10-17)

Machine-readable file: `financing_catalogue.json` (18 products: BD 5, CN 6, GB 7). Valid JSON, checked with
`python -I -c "import json;json.load(open('docs/research/financing_catalogue.json',encoding='utf-8'))"`.

**Every product is illustrative** (`illustrative: true`). These are summaries of public information, not offers or advice, and the agent never applies for the owner. Every row carries an `unconfirmed` list. Where a source was a search summary or a broker page rather than the provider's own page, the list says so. `accessed_date` is 2026-10-17 for all rows.

## 1. How to read the JSON

- Top level: `schema_version`, `notes`, `field_dictionary`, `products`.
- Per product: `id, country, name, provider, type, currency, amount_min, amount_max, tenor_months_min, tenor_months_max, indicative_rate_text, collateral, eligibility, documents_required, source_url, accessed_date, illustrative`, plus `unconfirmed`.
- `null` means no public figure was found. Do not treat it as zero or unlimited.
- `eligibility` is a list of `{field, op, value, confirmed}`. `op` is one of `==`, `>=`, `<=`, `in`. `confirmed: false` marks an assumption the UI should label. The fields are defined in `field_dictionary` and map to metrics the engine can compute from the SME profile: years trading, trailing revenue, export revenue, sector, tax credit grade, debt-service cover (`dscr`, from the forecast), months of card sales, B2B invoice share, and so on.
- The matcher still needs the **affordability check** from `PLAN.md`: the JSON gives rate text, not a number, so M1 must parse or assign a modelling rate per product and say that it is an assumption. A single `modelling_rate_pct` field was not added because I could not source one for every product; M1 should define it in the matcher and mark it "assumed".
- `documents_required` is typical practice, not taken from the source (stated in the JSON `notes`).

## 2. Products

| ID | Country | Product | Type | Amount | Rate (as reported) | Fit with demo SMEs | Evidence quality |
|---|---|---|---|---|---|---|---|
| `bd-bb-cmsme-wc-refinance-2026` | BD | Bangladesh Bank CMSME Working Capital Refinance Fund | central-bank refinance, working capital | not stated | borrower pays at most 9%; BB lends to the bank at 4%; grace 3–6 months; 3-year scheme | `bd_grocer` (best fit: pre-Eid stock) | Good: two news reports agree; official circular not read |
| `bd-bank-unsecured-cmsme-loan` | BD | Bank unsecured CMSME loan | term loan | BDT 100,000–2,500,000 (sources disagree) | promo 13.75% (2025, expired); market average lending rate 11.86% (June 2026) | `bd_grocer` | Weak: amounts old and conflicting |
| `bd-bank-secured-overdraft` | BD | Secured overdraft / cash credit | overdraft | not found | only context rates | `bd_grocer` | Weak: generic |
| `bd-mfi-microenterprise-loan` | BD | MFI micro-enterprise loan | microfinance | not found | cap 24% declining balance | low fit by size (included to show a "not a good match" explanation) | Medium: secondary sources |
| `bd-citybank-bkash-digital-nano-loan` | BD | Wallet-app digital nano loan | digital loan | BDT 500–50,000 | 9% + ~0.5% fee | tiny cash gaps only | Medium: news reports |
| `cn-webank-weiyedai-working-capital` | CN | WeBank Weiyedai | online unsecured working capital | up to RMB 3 million (co-lending version) | shown at signing | `cn_xborder` (judge-appeal product) | Medium: Standard Chartered page read; WeBank's own page not |
| `cn-bank-tax-credit-loan` | CN | Bank-tax interaction credit loan | unsecured, tax-based | not found | not found | `cn_xborder` (domestic tax grade) | Medium: older reports |
| `cn-inclusive-small-micro-loan` | CN | Inclusive small and micro enterprise loan | bank term loan | ≤ RMB 10 million (statistical definition) | average new rate 3.48% (June 2025) | `cn_xborder` | Medium: figure from a search summary |
| `cn-government-financing-guarantee-loan` | CN | Government-backed guarantee | guaranteed bank loan | ≤ RMB 5 million (policy quota) | guarantee fee ≤ 1% (average 0.72%) | `cn_xborder` | Medium: policy summaries |
| `cn-sinosure-sme-short-term-export-credit-insurance` | CN | Sinosure SME export credit insurance | insurance | n/a | not found | `cn_xborder` (only for direct B2B sales) | Medium: city policy PDFs |
| `cn-platform-data-inventory-finance` | CN | Marketplace-data inventory finance | fintech inventory finance | RMB 2 million per store (unconfirmed) | "as low as 3.99%" (marketing) | `cn_xborder` | Weak |
| `uk-ggs-term-loan` | GB | Growth Guarantee Scheme loan | government-guaranteed term loan | up to GBP 2 million | lender-set | `uk_garden` | Medium: broker and lender pages; official page unreachable |
| `uk-bank-overdraft` | GB | Business overdraft | overdraft | up to ~GBP 50,000 | 11–35% EAR (13–17% for small limits) | `uk_garden` (seasonal buffer) | Medium: broker guide |
| `uk-asset-finance` | GB | Asset finance | equipment finance | n/a | 4–10% | `uk_garden` (polytunnel) | Medium: broker guide |
| `uk-invoice-finance` | GB | Invoice finance | receivables finance | n/a | advance 70–90%; discount SONIA + 2.5–4.5%; service fee 0.8–1.8% | `uk_garden` (borderline: 15% trade sales) | Medium: broker index |
| `uk-revenue-based-finance-card-sales` | GB | Revenue-based finance on card sales | merchant cash advance | GBP 3,000–1,000,000 | fixed fee; repay as % of daily card sales | `uk_garden` (POS history) | Medium: provider page via search |
| `uk-unsecured-term-loan` | GB | Unsecured term loan | term loan | n/a | 10–18% APR prime, 18–30% standard | `uk_garden` | Medium: broker index |
| `uk-hmrc-time-to-pay` | GB | HMRC Time to Pay | tax payment plan (not a loan) | n/a | n/a | `uk_garden` (VAT quarter) | Weak: overview page only |

Not catalogued, with the reason:

- **Bangladesh Bank term-loan refinance scheme at 7%** (2022; reported as continued). The only source was a search summary of a TBS report; the Financial Express article I opened does not mention it. Add only after checking Bangladesh Bank's circulars.
- **UK Start Up Loans.** The official site returned HTTP 403, so nothing was verified. It targets young businesses and so would not fit `uk_garden`.
- **Chinese export pre-shipment finance, accounts-receivable factoring platforms and supply-chain finance.** I did not read a source for these.
- **Bank-specific Bangladesh and UK product pages.** None could be read.
- **UK Export Finance schemes.** Not relevant to the chosen SMEs.

## 3. What each demo SME should see (expected matcher behaviour, for M1 tests)

These are design expectations to check the matcher against, not computed results.

- `bd_grocer`: the CMSME refinance fund and the unsecured bank loan fit; the MFI loan and the nano loan fit poorly by size; the overdraft needs collateral the profile may not have. The explanation should say why: "meets the working-capital need and the CIB check; loan size not published, so ask the bank".
- `cn_xborder`: Weiyedai and the inclusive loan fit on size; the tax-credit loan depends on the tax grade; Sinosure is shown only if direct B2B export is above zero; platform-data finance fits the marketplace history. The affordability check must use CNY costs and USD revenue with the FX shock scenario.
- `uk_garden`: GGS, overdraft and revenue-based finance fit the seasonal pattern; asset finance fits only if an asset purchase is requested; invoice finance is borderline; Time to Pay appears when a VAT bill exceeds forecast cash.

## 4. Key unconfirmed facts to disclose in the deck and UI

1. All rates are indicative or reported ranges; none is a quote.
2. Bangladesh: per-borrower limit and tenor of the 2026 refinance fund; the exact issue date (8 June 2026 reported, one copy shows a different date); whether the owner's bank has joined the scheme.
3. China: WeBank's own limits and rates (only the co-lending page, RMB 3 million, was read); the 3.48%, 0.72% and 1% figures come from search summaries of official-statistics reports and have not been opened individually; the Sinosure threshold differs by city and year (USD 6 million in a Shantou 2022 notice, USD 8 million in a Shenzhen 2024 notice).
4. UK: the British Business Bank page could not be read; the 2026 changes to the scheme come from one broker's article and one news report; all market rates are broker figures.
5. No product has been checked for the Chinese-language "main" version of its terms beyond what is listed; if the deck quotes WeBank, have a Chinese reader check the live product page.

## 5. Main sources

- Bangladesh Bank refinance fund: <https://thefinancialexpress.com.bd/economy/bb-launches-tk-50b-revolving-refinance-fund-for-cmsmes>, <https://thefinancialexpress.com.bd/trade/nrbc-bank-signs-deal-with-bb-to-refinance-cmsme-working-capital> (read), <https://en.ittefaq.com.bd/16875/bb-launches-tk-5-000cr-revolving-refinance-fund>
- BRAC Bank collateral-free loans: <https://thefinancialexpress.com.bd/trade/brac-bank-offers-record-low-collateral-free-loans-for-cmsmes>
- Bangladesh lending rates: <https://thefinancialexpress.com.bd/bangladesh/june-spread-remains-unchanged-at-57pc>
- Microcredit cap: <https://www.tbsnews.net/economy/interest-rates-may-go-down-microcredit>
- City Bank and bKash nano loan: <https://en.prothomalo.com/corporate/local/city-bank-bkash-introduce-digital-nano-loan>
- WeBank co-lending page: <https://www.sc.com/cn/sme-banking/qianhaiweizhong-partnership-loan/>; WeBank scale: <https://news.pedaily.cn/20260112/121928.shtml>
- Bank-tax interaction: <https://cs.com.cn/xwzx/hg/201509/t20150922_4804248.html>
- Inclusive loan data, guarantee fees: <https://www.news.cn/20250724/fd4370a4e87f4e539078b71c5c1504f4/c.html>, <https://www.news.cn/fortune/20250414/6677c2dff23b46b6b3972fb4986bc147/c.html>, <https://www.chinanews.com.cn/cj/2025/05-08/10412223.shtml>
- Sinosure SME policy: <https://www.shantou.gov.cn/attachment/0/116/116583/2348676.pdf>
- Cross-border finance: <https://www.gtreview.com/news/asia/this-company-is-using-amazon-data-to-offer-trade-finance-to-asian-vendors/>, <https://eu.36kr.com/en/p/3047198999071620>
- UK GGS: <https://www.sparkfinance.co.uk/growth-guarantee-scheme/eligibility>, <https://www.novuna.co.uk/business-finance/finance-products/growth-guarantee-scheme/>, <https://www.simplybusiness.co.uk/knowledge/funding/growth-guarantee-scheme-updated-2026/>
- UK rates: <https://www.sparkfinance.co.uk/data/uk-business-loan-rate-index>, <https://www.sparkfinance.co.uk/data/uk-invoice-finance-rate-index>, <https://www.kandoo.co.uk/guides/uk-business-loan-rates-explained>
- UK revenue-based finance: <https://youlend.com/products/capital>
- HMRC: <https://www.gov.uk/difficulties-paying-hmrc>
