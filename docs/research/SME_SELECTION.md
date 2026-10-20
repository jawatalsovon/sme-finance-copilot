# Demo SME selection (R1, 2026-10-17)

Decision: **three demo SMEs in three countries, three currencies, covering all four entry points.**

| ID | SME | Country | Currency | Primary entry points |
|---|---|---|---|---|
| `bd_grocer` | Neighbourhood grocery and household store, Mirpur, Dhaka | Bangladesh (the local one) | BDT | Wallet + bank statement CSVs; Google Sheets sales and credit notebook |
| `cn_xborder` | Cross-border e-commerce seller of phone and laptop accessories, Shenzhen | China (the "other country", chosen to appeal to WeBank judges) | CNY, USD, EUR, GBP | Excel files; marketplace settlement CSV; multi-currency account statement CSV |
| `uk_garden` | Independent garden centre and plant nursery, Yorkshire | United Kingdom (third country; domestic sales, cleanest accounting-software case) | GBP | POS export CSV (Square-style); accounting software (QuickBooks Online UK sandbox); bank CSV |

All numbers about each business below are **design assumptions for synthetic data**, not facts about real firms. Exchange rates used for the USD column are assumptions (BDT 122, CNY 7.1, GBP 0.76 per USD), not looked up. Public datasets named here are the anchors; their licences are in `DATASETS.md` and `../LICENSES_DATA.md`.

---

## 1. User constraints and how the picks meet them

| Constraint (from Jawat) | How it is met |
|---|---|
| Not all three Bangladeshi | One Bangladeshi, one Chinese, one British. |
| One local Bangladeshi SME | `bd_grocer`, Dhaka. |
| One from another country | `cn_xborder` (China) and `uk_garden` (UK) are both non-Bangladeshi. |
| One with international sales / multi-currency | `cn_xborder`: sells in USD, EUR and GBP, buys in CNY, pays freight in USD. |
| ≥ 5 years of history each | All three: **1 Jan 2020 – 30 Sep 2026 (6 years 9 months)**, daily granularity. Public anchors cover this window (see section 4). The business records themselves are synthetic and seeded. |
| Different data entry points | `bd_grocer` = wallet + bank statements + spreadsheet. `cn_xborder` = Excel + marketplace settlement + multi-currency account. `uk_garden` = POS + accounting software + bank. Spreadsheets, accounting software, POS and bank/wallet statements are all used at least once as a primary entry point. |
| Integration and demo easiest | CSV and Sheets need no accounts. QuickBooks Online and Square sandboxes are free (sources in section 3). `bd_grocer` and `cn_xborder` need only file upload. |

---

## 2. Shortlist and scoring

Seven candidates plus one extra. Scores are 1–5 (5 best). Weights: data availability 25%, integration ease 25%, books-vs-reality gap 20%, Topic E financing relevance 15%, WeBank judge appeal 15%.

| # | Candidate | Data ≥5 y (real anchor / synthetic share) | Integration ease | Books-vs-reality gap | Topic E financing | Judge appeal | Weighted |
|---|---|---|---|---|---|---|---|
| 1 | **Bangladesh neighbourhood grocer** (cash + bKash + notebook) | 4 — real WFP retail food prices, Pink Sheet commodity costs; sales synthetic | 4 — CSV, Sheets; no login | 5 — cash expenses, customer credit ("baki"), personal/business mixing | 4 — Eid stock-up, CMSME refinance | 4 | **4.20** |
| 2 | Bangladesh knitwear sub-contract exporter (USD LC, BDT costs) | 3 — little public SME-level data; FX, cotton prices | 3 — spreadsheets, LC paperwork | 4 | 5 — export finance, back-to-back LC | 4 | 3.65 |
| 3 | **Shenzhen cross-border e-commerce seller** (Amazon-style, CNY/USD/EUR/GBP) | 4 — real FX series, supply-chain pressure index, UCI basket data; ledger synthetic | 4 — settlement-style CSV, Wise-style statements, Excel | 5 — fee deductions, FX differences, returns, lost-stock reimbursements, duplicates | 5 — inventory finance, receivable finance, export credit | 5 — Shenzhen is the hub; WeBank has an SME product | **4.50** |
| 4 | China tea-and-snack shop (WeChat Pay/Alipay + POS) | 3 | 3 — WeChat/Alipay bill exports exist but no sandbox for us | 4 | 4 | 4 — but resembles the private the pilot company QSR case | 3.50 |
| 5 | Sri Lanka tea exporter (USD sales, LKR costs) | 5 — Pink Sheet tea 1960–, FX; 2022 crisis | 3 — Xero/QBO + bank CSV | 4 | 5 | 3 | 4.00 |
| 6 | **UK independent garden centre** (Square POS + QuickBooks UK) | 4 — NOAA daily weather, ONS retail index, BoE rate; sales synthetic | 5 — Square and QuickBooks UK sandboxes free | 4 — Square payouts net of fees vs gross sales, petty cash, wastage | 4 — seasonal stock, VAT quarter, revenue-based finance | 3 | **4.10** |
| 7 | UK/US Shopify DTC food brand (Stripe, Shopify dev store) | 3 | 5 | 3 | 4 | 3 | 3.65 |
| 8 | Kenya agro-input dealer (M-Pesa) | 3 | 3 — M-Pesa Daraja sandbox needs an account | 5 | 4 | 3 | 3.55 |

Selection rule: take the top score in each role, not just the top three scores. `cn_xborder` (4.50) is the only candidate covering the international/multi-currency role well. `bd_grocer` (4.20) is the best local case. For the third slot, `uk_garden` (4.10) edges Sri Lanka tea (4.00) and is the only candidate that exercises the POS and accounting-software paths together. **The gap is small (0.10).** Sri Lanka tea is the backup: its Pink Sheet tea price series is richer, and swapping it in costs about half a day of generator work. It would duplicate the multi-currency role and South Asia, which is why it lost.

Scores are my judgement, calibrated against the facts in section 3; they are not measured.

Why not the others: #2 doubles Bangladesh and is the hardest to generate credibly (LC documents). #4 looks like the private QSR data and has no free sandbox. #7 duplicates `cn_xborder`'s online-seller pattern with a weaker financing story. #8 would need an M-Pesa developer account.

---

## 3. Evidence for integration and relevance

- **QuickBooks Online sandbox:** free, created automatically with a developer profile; up to 10 sandbox companies, valid two years; Plus sandboxes are region-specific and the region cannot be changed later; base URL `https://sandbox-quickbooks.api.intuit.com/v3`; provided for non-commercial testing; "Clear data and reset" is available. Source: <https://developer.intuit.com/app/developer/qbo/docs/develop/sandboxes/manage-your-sandboxes>. A secondary source says Plus sandboxes exist for US, UK, AU, IN, CA and FR (Intuit blog, found via search; **the UK option should be confirmed in the portal**, which the docs page above does not list by country). **Unconfirmed:** whether multi-currency can be enabled in a sandbox company; the plan only needs GBP for `uk_garden`, so this does not block the pick.
- **Square sandbox:** free, isolated; default test account on sign-up; orders and payments appear in the Sandbox Seller Dashboard; no real card or bank details needed. Source: <https://developer.squareup.com/docs/devtools/sandbox/overview>. The sandbox has a subset of production features.
- **Marketplace settlement files:** Amazon's V2 settlement report is a tab-delimited flat file with amount-type, amount-description and amount columns plus a currency field, generated about every 14 days, kept 90 days. Source: <https://developer-docs.amazon.com/sp-api/docs/report-type-values-settlement>. We imitate this layout for `cn_xborder`; the synthetic files carry "Amazon-style" labels, not Amazon data or branding.
- **Multi-currency statements:** Wise offers CSV, PDF, CAMT.053 and MT940 statements per currency balance; the API documents CSV and XLSX. Source: <https://docs.wise.com/api-reference/balance-statement>. We use a Wise-style CSV for the multi-currency account.
- **Xero** (alternative accounting connector): the demo company is free with a developer account but resets after 28 days and has no automatic bank feeds; there is no separate sandbox host. Source: <https://central.xero.com/s/article/Use-the-demo-company> (via secondary summaries; not read directly). QuickBooks fits better because its sandbox lasts two years.
- **bKash:** I found no public document for the bKash merchant statement format. We invent a bKash-style wallet CSV (date, TrxID, counterparty, type, amount, fee, balance). **Label it "bKash-style", not bKash.**
- **Why financing is relevant:**
  - Bangladesh Bank CMSME refinance funds exist (a 2026 fund is reported at refinance rate 4%, lending cap 9%; see `FINANCING_CATALOGUE.md`).
  - Shenzhen: official figures cited by the Shenzhen government say about 150,000 cross-border e-commerce exporters, and one third of Chinese Amazon sellers are from Shenzhen (<https://www.sz.gov.cn/en_szgov/news/latest/content/post_11235963.html>); small sellers still struggle to get bank funding because of small scale and little collateral (<https://eu.36kr.com/en/p/3047198999071620>); platform-data lending exists (<https://www.gtreview.com/news/asia/this-company-is-using-amazon-data-to-offer-trade-finance-to-asian-vendors/>).
  - WeBank has an online unsecured working-capital product for small and micro enterprises, "Weiyedai" (微业贷), launched 2017, with more than 1.7 million enterprises granted credit by mid-2025 per a trade-press report (<https://news.pedaily.cn/20260112/121928.shtml>). This is the strongest pitch hook: the matcher can show it as an illustrative product.
  - UK garden centres are strongly weather- and Easter-dependent: the Garden Centre Association barometer reported March 2024 sales up 22% year on year mainly because Easter fell in March (<https://aiph.org/floraculture/news/despite-another-wet-and-rainy-month-in-march-the-uks-garden-centre-sales-reports-22-per-cent-increase-in-sales/>), and a January storm-driven fall of 12% in gardening categories (<https://thedirt.news/january-garden-centre-sales-down-2/>).

---

## 4. The three profiles

### 4.1 `bd_grocer` — Mirpur Family Store (Dhaka, Bangladesh)

**Profile.** A 700 sq ft neighbourhood grocery and household store in a residential block, owner-run, open 8:00–23:00, 8 staff (owner, manager, 4 counter/stock staff, 2 delivery boys). About 600 SKUs: rice, flour, edible oil, sugar, lentils, spices, eggs, dairy, snacks, cleaning goods. Sales BDT ~36 million a year (about USD 295,000), gross margin about 11–13%, net margin 2–4% before the owner's draw. Buys from wholesale markets (Karwan Bazar, Chawkbazar) and from distributors on short credit; sells for cash, bKash, a few bank-card sales, and **on credit to about 120 regular households (a hand-written "baki" book)**.

**Systems and entry points.** Daily sales and credit notebook typed into **Google Sheets** by the manager (one tab per month; inconsistent date formats). **bKash-style wallet statement CSV** (customer payments, supplier payments, cash-out, fees). **Bank current account statement CSV** (supplier cheques, cash deposits, rent, utilities). No accounting software. The Cash Truth demo here is "books = Sheets, reality = bank + wallet + cash count".

**Currencies.** BDT only. Supplier prices follow imported commodities in USD (edible oil, sugar, lentils), so the synthetic cost curves use the Pink Sheet and a BDT/USD path as a driver.

**Seasonality drivers.** Ramadan and both Eids (spices, oil, sugar, vermicelli, dates; demand roughly doubles in the weeks before Eid ul-Fitr, with a sag afterwards because many customers travel home), Pohela Boishakh in April, the monsoon (June–September: delivery disruption, vegetable prices), Aman and Boro harvest price cycles, school terms. Events inside the window that the generator should include: COVID lockdown (March–May 2020), the 2022 price inflation and edible-oil and sugar price-control episodes, and the July–August 2024 curfews and internet shutdown. Eid dates move 10–11 days a year; use the `holidays` library or fixed official dates per year (see `DATASETS.md`).

**Realistic data-entry problems to inject (answer key must record each).**
1. Cash expenses never entered in the sheet: porter, rickshaw, tea, tips, "chanda" (local contributions), small repairs, about BDT 800–2,500 a day.
2. Customer credit book never written off: about 8–12 households with dues older than 180 days (phantom receivables); some paid later by bKash with no reference, so the payment cannot be matched.
3. bKash receipts typed into the sheet as cash sales; bKash fees and cash-out charges never recorded.
4. The same wholesale purchase entered twice (sheet block pasted again), and some purchases entered in DD/MM and MM/DD formats.
5. Supplier advances (cash "dadon" to a wholesaler) recorded as expense when paid, then again when goods arrive.
6. Owner's personal bKash and household spending mixed with the shop wallet.
7. Spoilage and shrinkage never recorded: eggs, bread, short-dated dairy, loose-sold goods (weight loss).
8. Bank deposits made in round sums on the 1st and 15th that do not match daily sales (timing, plus cash retained to pay suppliers directly).

**Typical financing needs.** Pre-Eid inventory build (working capital for 4–8 weeks), a chiller or freezer (equipment), a second counter or shop (term loan), smoothing supplier credit. Candidate products: Bangladesh Bank CMSME refinance via a bank, bank SME term loan or overdraft against a fixed deposit, microfinance, supplier credit (see catalogue).

**Public datasets that anchor it.**
- WFP Bangladesh retail food prices (CC BY-IGO), for rice, oil, sugar, lentil price paths by month.
- World Bank Pink Sheet commodity prices (soybean oil, palm oil, sugar, wheat, rice), CC BY 4.0.
- World Bank WDI annual official exchange rate and inflation (CC BY 4.0) for the BDT path.
- `holidays` library calendar for Bangladesh (verify Eid dates).
- NOAA GHCN-Daily for Dhaka rainfall if wanted (see `DATASETS.md`).

### 4.2 `cn_xborder` — Lanxin Digital Accessories Co. (Shenzhen, China)

**Profile.** A 10-person limited company in Shenzhen's Longhua district selling phone and laptop accessories (cables, GaN chargers, cases, stands) through an Amazon-style marketplace (about 70% US, 20% UK and EU) and its own storefront (about 10%). Sales about USD 1.8 million a year (about CNY 12.8 million), gross margin about 28–35% before marketplace fees, net margin 3–8%. Staff: owner, 3 operations, 2 product and design, 2 warehouse and shipping, 1 finance, 1 customer service. Buys from 6–8 factories around Dongguan and Shenzhen, pays in CNY, ships inventory to marketplace fulfilment centres by sea (30–45 days) and air (about 7 days) with freight paid in USD.

**Systems and entry points.** **Excel**: purchase orders to factories, inventory and landed-cost sheet, supplier payment schedule. **Marketplace settlement files** (tab-delimited, roughly every 14 days, per currency). **Multi-currency account statement CSV** (Wise-style) receiving the marketplace payouts in USD, EUR and GBP and converting to CNY. **CNY bank statement CSV** (current account). Accounting is a spreadsheet plus a local bookkeeper's monthly summary; optional export to QuickBooks.

**Currencies.** Sales USD, EUR, GBP; costs CNY (goods, wages, rent), USD (freight, marketplace advertising); payouts converted at spot with a spread.

**Seasonality drivers.** Q4 (Black Friday, Cyber Monday, Christmas) is 35–40% of annual sales; mid-year Prime Day in July; Chinese New Year (factories close for about 3–5 weeks; the pre-CNY stock build is the main cash crunch, so cash needs peak in October–December); back-to-school in August. Events in the window that the generator should model: the 2020–21 e-commerce boom, the 2021–22 container-freight spike, CNY depreciation in 2022–23, marketplace fee changes, and the 2025 changes in US tariffs and import rules for Chinese goods. **I did not verify the 2025 tariff details; confirm them before they appear in the deck.**

**Realistic data-entry problems to inject.**
1. Marketplace fees, advertising and storage charges deducted from payouts but recorded in the sheet as full sales (net vs gross).
2. FX differences: the bookkeeper converts at a monthly average, the bank converts at spot less a spread, leaving an unexplained gap each month.
3. Customer refunds and returns recorded as expenses in one place and sales deductions in another; some refunds never recorded.
4. Lost or damaged units at the fulfilment centre that were reimbursed late (a receivable the sheet never shows) or never claimed.
5. Duplicate supplier payment rows (the sheet and the bank file both list it, and a factory deposit is booked twice).
6. Cash and personal-WeChat payments to small suppliers, tooling fees and sample costs that never reach the books.
7. Inventory in transit counted as available stock; freight invoiced in USD but booked in CNY at a stale rate.
8. A payout that lands in the wrong currency balance, and a payout held back by a reserve that shows as "missing".

**Typical financing needs.** Pre-CNY and pre-Q4 inventory purchases (3–4 months ahead of sales cash), receivable timing (payout delay and reserves), FX risk on USD revenue vs CNY costs, tariff shocks. Candidate products: WeBank-style online working-capital loans, bank inventory or supply-chain finance, platform-data lending, export credit insurance (see catalogue; some figures there are unconfirmed).

**Public datasets that anchor it.**
- FRED / Federal Reserve H.10 daily exchange rates for CNY, EUR, GBP (public domain, citation requested).
- NY Fed Global Supply Chain Pressure Index, as a freight-cost driver (terms to confirm; see `DATASETS.md`).
- UCI Online Retail II (CC BY 4.0): real order and basket-size distribution of a UK online giftware business, used only for order-size and weekday/hour shapes; its 2009–2011 dates do not overlap the window and must be shifted.
- `holidays` library for China, US and UK calendars (Chinese New Year, Golden Week, Thanksgiving).

### 4.3 `uk_garden` — Hollinbank Garden Centre (Yorkshire, UK)

**Profile.** An independent garden centre and small plant nursery on 3 acres: plants, pots, compost, tools, outdoor furniture and a small Christmas department. 12 staff (7 full-time equivalents, up to 5 seasonal). Sales about GBP 1.6 million a year (about USD 2.1 million), gross margin about 38–42%, net margin 3–6%. VAT-registered; most goods standard-rated, edible plants and seeds zero-rated. Customers: walk-in retail (about 85%), a small landscaper trade account base (about 15%, on 30-day credit).

**Systems and entry points.** **Square-style POS export CSV** (card and cash sales, refunds, tips, discounts, gift cards; payouts next business day net of fees). **QuickBooks Online (UK) books**: supplier bills, trade invoices, VAT return, payroll journal. **Bank statement CSV** (current account). This case exercises the POS connector and the QuickBooks sandbox write-draft path.

**Currencies.** GBP only.

**Seasonality drivers.** Strong spring peak (March–June, Easter timing matters, Mother's Day), Christmas trees and decorations (November–December), weather: a warm dry weekend lifts plant sales sharply; a cold, wet spring cuts them; bank holidays. Events in the window: garden centres closed for part of spring 2020 followed by a demand surge, the 2022 energy-cost spike, 2023–24 cost-of-living downturn, wet springs, and the VAT quarterly payment cycle (a predictable cash outflow the forecast must anticipate). UK VAT-registered businesses keep digital VAT records and file through compatible software, which fits the accounting-software path (not verified against HMRC pages).

**Realistic data-entry problems to inject.**
1. POS payouts booked as sales: payouts net of fees are posted to the books and the gross-to-net fee difference never shows as an expense.
2. Petty cash expenses (compost delivery driver, cleaning, float shortages) never entered.
3. Trade customers' overdue invoices (landscapers) left open past 90 days, one never collected (phantom receivable).
4. The same supplier bill entered twice (emailed PDF and a manual entry), with a different reference.
5. Plant wastage and shrinkage (dead stock after a cold snap) never written down, so stock value is too high.
6. Gift card sales recorded as revenue instead of a liability; gift cards redeemed later double-count.
7. VAT mis-coded on mixed baskets (zero-rated seeds sold with standard-rated pots), so the VAT liability is understated.
8. A supplier credit note recorded as income, and bank charges and loan interest left unrecorded.

**Typical financing needs.** Seasonal stock build January–March (plants and compost), polytunnel or greenhouse equipment, a quarterly VAT bill, a cash cushion for wet springs. Candidate products: bank overdraft or term loan under the government-backed Growth Guarantee Scheme, invoice finance on the trade account, revenue-based finance repaid from card sales, asset finance (see catalogue).

**Public datasets that anchor it.**
- NOAA GHCN-Daily station data for a Yorkshire or Leeds station (temperature, rainfall) as the weather driver; usage terms to confirm in `DATASETS.md`.
- ONS Retail Sales Index (Open Government Licence v3.0) for the national trend and monthly shape.
- Bank of England Bank Rate and `holidays` library UK calendar including Easter (see `DATASETS.md`).

---

## 5. Common design notes for D1 (data agent)

- One `answer_key.json` per SME listing every injected issue: type, rows, amount, date range, expected detection method.
- Use the same 1 Jan 2020 – 30 Sep 2026 window for all three, so charts line up in the demo.
- Do not use any the pilot company figures or product names; none of the three profiles resembles it (a quick-service restaurant).
- All people, firm names, bank and wallet references are fictional and labelled so. Wallet and marketplace formats are "style of", not copies.
- Public series are anchors and drivers. The ledgers are synthetic, and every file must say so in its README (the judges' "licence and authenticity" check).

## 6. Risks and open points

- **Time:** three SMEs with 6.75 years of daily data is the largest D1 workload. If D1 runs late, build `bd_grocer` and `cn_xborder` fully and reduce `uk_garden` to its QuickBooks and POS path.
- QuickBooks UK sandbox region and multi-currency availability: confirm in the portal after the user creates the free developer account (agents cannot create it).
- The `uk_garden` revenue, margin and staff figures and all three profiles are assumptions with no external source.
- 2025 tariff events, bKash statement format and WeChat/Alipay exports were not verified.
