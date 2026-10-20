# Connector landscape (R1, 2026-10-17)

40 systems the three demo SMEs (`bd_grocer`, `cn_xborder`, `uk_garden`) or businesses like them actually use. Use it to fill the connector registry page with honest statuses.

**Evidence level.** "Checked" = I read the publisher's page or documentation this session (URL given). Everything else is from general knowledge and was **not re-verified**; treat it as a starting point and confirm before building. Effort: **S** = about half a day or less (file parser, no account), **M** = 1–2 days (OAuth or API plus tests), **L** = more than 3 days or blocked by partner approval or a business account we do not have.

**Registry status proposal** (for the connector registry page; do not claim more than was tested): `live` = built and tested with data (the 6 below), `sandbox-ready` = documented free sandbox exists but we have not built it, `planned` = roadmap only.

## 1. The 6 live connectors (from `PLAN.md` section 2)

| # | Connector | Which SME uses it | Effort | Notes |
|---|---|---|---|---|
| L1 | CSV/Excel upload with LLM-proposed column mapping | `cn_xborder` (Excel sheets, marketplace settlement files), all | S | Handles tab-delimited files (the Amazon-style settlement format is tab-delimited) and comma-decimal locales (EU report amounts use a decimal comma per the Amazon docs). |
| L2 | Google Sheets (read) | `bd_grocer` (manager's sales notebook) | S–M | Sheets API: 300 read requests a minute per project, 60 per user, no daily cap, free; batch calls count as one (checked: <https://developers.google.com/sheets/api/limits>). OAuth consent needs a Google Cloud project by Jawat. |
| L3 | QuickBooks Online sandbox (read; write **draft** entries only) | `uk_garden` | M | Free sandbox, up to 10 companies, valid two years, region fixed at creation; sandbox base URL `https://sandbox-quickbooks.api.intuit.com/v3`; non-commercial use only (checked: <https://developer.intuit.com/app/developer/qbo/docs/develop/sandboxes/manage-your-sandboxes>). Needs a developer account created by Jawat. |
| L4 | Bank statement CSV (generic, multi-currency) | all three | S | Profile-driven mapping; also accept OFX/CAMT.053/MT940 later. Wise's help centre lists CSV, PDF, CAMT.053 and MT940 statement formats (checked: <https://docs.wise.com/api-reference/balance-statement>). |
| L5 | Mobile-money / wallet statement CSV | `bd_grocer` (bKash-style), `cn_xborder` (Wise-style multi-currency) | S | One parser, two profiles. No public bKash statement format was found, so the bKash profile is invented and labelled "bKash-style". |
| L6 | POS sales export CSV (Square-style) | `uk_garden` | S | Optional upgrade to the Square sandbox API (see section 3). |

## 2. Full landscape

| # | System | Category | Regions | Integration path | Auth | Effort | Status proposal | Evidence |
|---|---|---|---|---|---|---|---|---|
| 1 | QuickBooks Online | Accounting | US, CA, UK, AU, IN, FR (sandbox regions per an Intuit blog; docs page says "region-specific") | Public REST API; free sandbox | OAuth 2.0 | M | **live** | Checked |
| 2 | Xero | Accounting | UK, AU, NZ, global | REST API; Demo Company (resets after 28 days, no bank feeds); same API host for demo and live | OAuth 2.0 | M | planned (fallback if QBO OAuth fails) | Secondary summaries only: <https://central.xero.com/s/article/Use-the-demo-company> |
| 3 | Zoho Books | Accounting | India, Middle East, global | REST API | OAuth 2.0 | M | planned | Not checked |
| 4 | Sage Business Cloud Accounting | Accounting | UK, IE, EU, CA, ZA, US | REST API | OAuth 2.0 | M | planned | Not checked |
| 5 | FreshBooks | Accounting | US, CA, UK | REST API | OAuth 2.0 | M | planned | Not checked |
| 6 | Tally Prime | Accounting | India, Bangladesh, Gulf | Local XML over HTTP; Excel export | None (local) | M | planned | Not checked; popularity in Bangladesh from general knowledge |
| 7 | Kingdee (金蝶) / Yonyou (用友) | Accounting/ERP | China | Open platform APIs, partner onboarding | App key/OAuth | L | planned | Not checked |
| 8 | Square POS | POS | US, CA, UK, AU, JP, IE, FR, ES | REST API; free isolated sandbox; test cards; subset of production features | OAuth 2.0 / personal access token | M | sandbox-ready (CSV is live) | Checked: <https://developer.squareup.com/docs/devtools/sandbox/overview> |
| 9 | Shopify (store and POS) | E-commerce, POS | Global | REST and GraphQL Admin API; free development stores with unlimited test orders (Bogus Gateway or payment-provider test mode) | OAuth / custom app token | M | planned | Checked via search: <https://shopify.dev/docs/api/development-stores/index> |
| 10 | Lightspeed Retail | POS | Global | REST API | OAuth 2.0 | M | planned | Not checked |
| 11 | Youzan (有赞) | POS, e-commerce | China | Open platform | OAuth | L | planned | Not checked |
| 12 | Amazon Seller Central (SP-API) | Marketplace | Global | Reports API; settlement report V2 flat file (tab-delimited, about every 14 days, kept 90 days; role Finance and Accounting; needs a seller account) | Login with Amazon OAuth | L | planned (file layout used for synthetic data) | Checked: <https://developer-docs.amazon.com/sp-api/docs/report-type-values-settlement> |
| 13 | WooCommerce | E-commerce | Global | REST API (self-hosted) | API keys | S | planned | Not checked |
| 14 | eBay | Marketplace | Global | REST Sell APIs; sandbox | OAuth 2.0 | M | planned | Not checked |
| 15 | Alibaba / 1688 / Taobao | Marketplace, sourcing | China | Open platform with enterprise approval | App key | L | planned | Not checked |
| 16 | Daraz (Alibaba) | Marketplace | BD, PK, LK, NP | Seller open platform | App key | L | planned | Not checked |
| 17 | Generic bank CSV / OFX / MT940 / CAMT.053 | Banking | Global | File import | None | S | **live** (CSV) | Checked (Wise formats) |
| 18 | TrueLayer | Open banking | UK, EU | Account-information API; free sandbox (third-party review); mock bank for payments | OAuth 2.0 | M | planned | Search summary: <https://docs.truelayer.com/docs/test-payments-in-sandbox> |
| 19 | Plaid | Bank aggregation | US, CA, UK, EU | Link flow; sandbox | Link token / OAuth | M | planned | Search summary only |
| 20 | GoCardless Bank Account Data (formerly Nordigen) | Open banking | EU, UK | REST API | Secret ID/key | M | planned | Not checked; free-tier status unknown |
| 21 | Wise | Multi-currency account | Global | CSV, PDF, CAMT.053, MT940 downloads; API supports JSON, CSV, PDF, XLSX, CAMT.053, MT940, QIF; period limit 469 days; SCA for UK/EEA profiles | Personal token / OAuth | S (CSV) / M (API) | **live** (via wallet CSV profile) | Checked |
| 22 | Payoneer / WorldFirst / PingPong | Cross-border collections | Global, China | CSV export; API for partners only | Partner credentials | S (CSV) / L (API) | planned | Not checked |
| 23 | Bangladeshi bank statements | Banking | BD | Internet-banking CSV/Excel/PDF; no open banking | None (file) | S–M (PDF parsing M) | planned | General knowledge |
| 24 | Chinese bank e-statements | Banking | CN | Excel/PDF; no public API | None (file) | S–M | planned | General knowledge |
| 25 | bKash | Mobile money | BD | Merchant payment API (Tokenized Checkout) with sandbox credentials issued by bKash on merchant onboarding; statement export format not found | App key/secret + token | M (payments) / S (CSV) | **live** (bKash-style CSV) | Search summary; docs at developer.bka.sh not read |
| 26 | Nagad | Mobile money | BD | No official developer portal found; community packages only | Merchant keys | L | planned | Search summary only |
| 27 | M-Pesa (Daraja) | Mobile money | KE, TZ | REST API; sandbox | OAuth 2.0 | M | planned | Not checked |
| 28 | WeChat Pay / Alipay | Mobile payments | China | Merchant bill download (CSV); APIs need a merchant entity | Merchant keys | S (CSV) / L (API) | planned | General knowledge |
| 29 | PayPal | Payments | Global | REST API; sandbox | OAuth 2.0 | M | planned | Not checked |
| 30 | Stripe | Payments | Global | REST API; test mode | API key | M | planned | Not checked |
| 31 | CSV / Excel upload | Files | Global | File import | None | S | **live** | n/a |
| 32 | Google Sheets | Spreadsheets | Global | Sheets API | OAuth 2.0 / service account | S–M | **live** | Checked |
| 33 | Microsoft Excel / OneDrive | Spreadsheets | Global | Microsoft Graph | OAuth 2.0 | M | planned | Not checked |
| 34 | Airtable | Spreadsheets/database | Global | REST API | Personal access token | S | planned | Not checked |
| 35 | Gusto | Payroll | US | REST API | OAuth 2.0 | M | planned | Not checked |
| 36 | Payroll CSV (BrightPay / Xero Payroll exports) | Payroll | UK | File import | None | S | planned | General knowledge |
| 37 | HubSpot | CRM | Global | REST API; free developer test account | OAuth 2.0 / private app token | M | planned | Not checked |
| 38 | Zoho CRM | CRM | Global | REST API | OAuth 2.0 | M | planned | Not checked |
| 39 | HMRC Making Tax Digital (VAT) | Tax | UK | REST API; sandbox with test users (Create Test User API); production needs HMRC approval | OAuth 2.0 (user-restricted) | L (sandbox M) | planned | Third-party guides only: <https://github.com/hmrc/vat-api/wiki/Creating-Test-Agents-in-the-Sandbox> |
| 40 | Chinese e-invoice (fapiao) platform | Tax | China | No public API found; Excel/OFD export | n/a | L | planned | Not checked |

Mapping of the 6 live connectors to rows: L1 = row 31, L2 = row 32, L3 = row 1, L4 = row 17, L5 = rows 21 and 25 (one wallet parser with two profiles, so two registry rows), L6 = row 8 in CSV mode (the API mode of row 8 stays "sandbox-ready"). The registry therefore shows 7 rows as `live` for 6 connectors; say so on the page, or merge rows 21 and 25 into one "Wallet statement (bKash-style / Wise-style)" entry and keep 39 rows.

## 3. Swap recommendations

1. **No swap needed for the six.** They cover every entry point the three SMEs use (spreadsheets, accounting, POS, wallet and bank statements) and none depends on a business account except QuickBooks.
2. **Upgrade L6 only if time allows:** keep the CSV mode as the default and add a Square sandbox API mode (effort M). The sandbox gives real API calls and logs, which fits the "simulated-account or sandbox operation logs" evidence item (OFFICIAL_RULES §2), but requires Jawat to create a free developer account (agents cannot). Build it after the QuickBooks path works.
3. **QuickBooks risk and fallback:** (a) region: confirm a UK Plus sandbox can be created, or fall back to a US sandbox with GBP-style sample data (multi-currency enablement in a sandbox is unconfirmed); (b) if OAuth fails at the redirect step, the CSV export of the same books keeps the demo alive, and Xero's Demo Company is the alternative (expires after 28 days, so create it close to the demo). Mark the registry honestly if the sandbox path is not working by freeze.
4. **Treat L4 and L5 as profile-driven.** One mapping layer with named profiles ("bKash-style", "Wise-style", "generic bank") keeps the connector count honest and lets the registry list them as separate rows.
5. **Registry honesty.** Show every row except the 6 as "planned". Do not mark a connector "sandbox-ready" unless a developer account exists and a call has succeeded; the judges' security score will not reward fake integrations, and the plan already says so.
6. **Official wording check.** Topic E on the official site mentions reconciliation (OFFICIAL_RULES §5). The registry page and deck should say the connectors feed the reconciliation, not that they are a product on their own.

## 4. Things nobody should assume

- bKash, Nagad, WeChat Pay, Alipay and Chinese ERP integrations normally require a merchant or enterprise relationship; none is needed for the demo.
- Intuit says sandbox companies are for development only; the real QuickBooks of any owner must never be connected in the demo.
- Rate limits and pricing for every row marked "Not checked" must be read before building.
