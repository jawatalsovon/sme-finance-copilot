# POS connectors: research and plan (R1, 2026-10-17)

Request: make the product "connection-ready" for every relevant POS (PLAN.md section 2). Evidence labels: **Checked** = I read the vendor's own page or docs this session (URL given). **Search** = a search summary or third-party page, not the vendor. **Knowledge** = my general knowledge, not re-verified. Where I could not identify a name, I say so and do not guess.

## 1. Names Jawat mentioned

| Name as said | Identified as | Confidence | Notes |
|---|---|---|---|
| Odoo | Odoo Point of Sale (open-source ERP, LGPL Community edition) | High | Official Docker image exists (<https://hub.docker.com/_/odoo>, search-confirmed: LGPL, needs a separate PostgreSQL). |
| "Clickit" / "Clicky" | **Klikit** is the likely match: a restaurant order aggregator that puts GrabFood, Gojek, Foodpanda, Uber Eats and TikTok Shop orders on one tablet and into the POS; from USD 25 per site per month. Its own pages list the Philippines, Indonesia, Singapore, Malaysia, Japan, Thailand and Taiwan as markets. **Bangladesh is not listed**, and I found nothing showing Klikit serves Bangladeshi restaurants (<https://klikit.io/en/learn/restaurant-order-aggregation-platform>, vendor marketing). | Medium | Ask Jawat which one he means and whether a Bangladeshi outlet uses it. Public API documentation for Klikit was not found. |
| GiantSoft | **Not identifiable from public sources.** Web search found nothing under that name. The team's own private project already has a working GiantSoft integration (visible only as titles and snippets of Jawat's other sessions, which I read for identification only). I have not copied any private detail here. | Public: none. Private: exists | **Action for the orchestrator:** ask the private project's owner for a redacted or synthetic sample export and the field list. Nothing from that project goes into the public repo unredacted (CLAUDE.md). |
| "PyHR" | **Not identified.** Only match in searches was PiHR, a Bangladeshi HR and payroll SaaS (attendance, leave, payroll). It is HR, not POS, and I cannot say it is what was meant. | Low | Ask Jawat. HR/payroll feeds `payroll_runs`, not `pos_sales_daily`. |
| "Kemai" | **Not identified.** The closest documented Chinese restaurant system I found is Keruyun (客如云), which has an open platform; I cannot say that is what was meant. | Low | Ask Jawat. |
| Other names in the brief | Loyverse, Square, Shopify POS, Toast, Lightspeed, Clover, Zoho POS/Inventory, EPOS Now, Pospal (银豹), Meituan, foodpanda, Pathao: all real products, researched below. "Yinbao" is the same company as Pospal (银豹, Pinyin Yinbao); Pospal is its international name (Knowledge). |
| "Hishabee", "Wonderpos" | Not found in search. Do not assume they exist as named. Related Bangladeshi names that did appear are in section 2, row 16. | | |

## 2. System matrix

| # | System | Region | API | Free sandbox or developer route | Auth | Key data objects | Export formats | Effort | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **Odoo POS** | Global | XML-RPC and JSON-RPC (Odoo up to 18); **JSON-2** (`POST /json/2/<model>/<method>`, Odoo 19) with a per-database reference at `<ODOO_URL>/doc` | **Self-hosted Community edition in Docker: no account, free, runs locally.** Odoo Online restricts the external API to Custom plans (docs) | User login or API key (keys since 14.0); JSON-2 uses a bearer API key | `pos.order`, `pos.order.line`, `pos.payment`, `pos.session`, `pos.config` (model names from Knowledge; confirm with `fields_get`/`/doc` on the instance) | List-view export to CSV/XLSX (Knowledge) | M | XML-RPC and API keys: <https://www.odoo.com/documentation/18.0/developer/reference/external_api.html> (Checked, partly: the page excerpt did not cover JSON-2 or Community limits); JSON-2 pattern: <https://www.cybrosys.com/blog/overview-of-api-integration-in-odoo-19> (Search) |
| 2 | **Loyverse** | Global | REST v1.0, base `https://api.loyverse.com/v1.0` | Free developer dashboard to create OAuth apps; no sandbox mentioned. A free Loyverse account works as a test account, since receipts can be created with `RECEIPTS_WRITE` | **Personal access token** (back-office page) or **OAuth 2.0** (authorization code, refresh token; scopes include `RECEIPTS_READ`, `SHIFTS_READ`, `INVENTORY_READ`, `ITEMS_READ`, `PAYMENT_TYPES_READ`, `STORES_READ`, `EMPLOYEES_READ`, `TAXES_READ`) | Receipts (`receipt_type` SALE or REFUND, `refund_for`, `receipt_date`, `total_money`, `total_tax`, line items and payments), items, variants, inventory, shifts, payment types, stores, employees, taxes, customers, webhooks | Back-office CSV export (Knowledge) | S–M | **Checked:** <https://developer.loyverse.com/docs/>. Rate limit 300 requests per 300 seconds per account. All times in UTC. |
| 3 | **Square** | US, CA, UK, IE, AU, JP, FR, ES (Knowledge) | REST v2: Orders (`POST /v2/orders/search`, create, pay), Payments, Refunds, Catalog, Inventory, Labor (shifts) | **Free isolated sandbox** with a default test account, test cards, a sandbox seller dashboard (some features missing) | OAuth 2.0 or personal access token | Order (line items, tenders, refunds, returns), Payment, Refund, Shift (Labor), Catalog item, Inventory count | Dashboard CSV: Transactions, Transaction Details, Transaction Items; deposits CSV under Balance (Search: community thread) | M | **Checked:** <https://developer.squareup.com/docs/devtools/sandbox/overview> and <https://developer.squareup.com/reference/square/orders-api> (endpoints only; refund and shift details Knowledge) |
| 4 | **Shopify POS** | Global | Admin REST/GraphQL; POS sales are orders with `source_name` = `pos` (filter `source_name:pos`); receipt number in `source_identifier`; staff in an employee field | **Free development stores** with unlimited test orders (Bogus Gateway or test mode); needs a free Partner account | OAuth or custom-app token | Order, line items, transactions, refunds, location, staff; **no confirmed POS cash-shift endpoint** | Admin orders CSV (up to 50 directly, more by email) | M | <https://shopify.dev/docs/api/development-stores/index>, <https://www.highviewapps.com/kb/how-can-i-export-only-shopify-pos-orders/> (Search) |
| 5 | **Toast** | US, CA, UK, IE, AU (Knowledge) | REST: orders, menus, labor, stock, restaurant configuration | **No free sandbox found.** Standard API access is self-serve only for an active Toast location (Restaurant Management Suite Essentials or higher, Manage Integrations permission); partner integration needs an application and certification | OAuth 2.0 client credentials | Orders, checks, payments, labor, menus | Toast web report CSV (Knowledge) | L (API) / S (export profile) | <https://doc.toasttab.com/doc/devguide/devApiAccessRequirements.html> (Search) |
| 6 | **Lightspeed** (X-Series retail; R-Series retail; K-Series restaurant) | Global | REST; X-Series per-account subdomain | X-Series: OAuth app needs approval only for public production apps (30 stores limit for unapproved); a trial account works for testing (secondary source). K-Series: gated to approved partners | OAuth 2.0 authorization code; personal tokens for one store | Sales, line items, payments, products, outlets, registers (names from Knowledge) | CSV reports (Knowledge) | M (X-Series) / L (K-Series) | <https://x-series-api.lightspeedhq.com/docs/authorization> (Search) |
| 7 | **Clover** | US, CA, EU, LatAm | REST v3 | Sandbox developer account with a test merchant; test API tokens from the sandbox dashboard. Whether it is free was not stated | OAuth 2.0; test tokens for sandbox | Orders, line items, payments, inventory, merchants | Dashboard CSV (Knowledge) | M | <https://docs.clover.com/docs/clover-environments> (Search) |
| 8 | **Zoho POS and Zoho Inventory** | Global; India, UAE, Africa strong (Knowledge) | REST per data centre (`.com`, `.eu`, `.in`, `.com.cn`, `.com.au`); Zoho POS docs list daily limits by plan: Free/Trial 1,500 and Standard 2,500 requests a day | No POS sandbox found; a free trial account | OAuth 2.0 (token in header only) | POS: invoices/sales, items, customers; Inventory: sales orders, items, stock | CSV/XLSX report export (Knowledge) | M | <https://www.zoho.com/pos/resources/api/> (Search) |
| 9 | **EPOS Now** | UK, IE, US, AU | REST ("EposNow HQ" API) with webhooks (beta) | Third-party directory lists a sandbox; access via an API device created in Back Office and the AppStore | HTTP Basic by API device | Transactions, products, categories, customers, stock, tax rates, devices | Back Office reports (Knowledge) | M | <https://www.eposnow.com/us/appstore/api> (Search); docs at developer.eposnowhq.com not read |
| 10 | **Pospal (银豹, Yinbao)** | China | REST OpenAPI V3 (e.g. `POST /openapi/v3/ticket/increment-page`, cursor paging; refunds flagged `refund`, reversed bills flagged `reversed=1`) | The platform gives test tokens on first login (10,000, per its docs); needs a Pospal store account and appId/appKey | appId + appKey (signature rules not read) | Tickets (sales), items, members | Excel reports (Knowledge) | M–L (Chinese docs, token billing) / S (export profile) | <https://platform.pospal.cn/openapi-doc/ticket.html> (Search) |
| 11 | **Meituan restaurant system (美团收银 / 餐饮系统)** | China | Open platform for ISVs: order, finance reconciliation, membership, supply chain | Needs ISV approval; no free sandbox found | appKey/secret (from third-party SDK; not verified) | Orders, finance reconciliation lines | Excel (Knowledge) | L (API) / S (export profile) | <https://shouyin.meituan.com> (product); developer.meituan.com not read |
| 12 | **Keruyun (客如云)** (not confirmed as "Kemai") | China | Open platform; V2 signature; order status callbacks; some paid endpoints | Service-provider certification path | Signed requests | Orders, status notifications, stores, tables, dishes | Excel (Knowledge) | L / S | <https://keruyun.apifox.cn/> (Search) |
| 13 | **Klikit** | SE Asia | API not found | None found | n/a | Aggregated delivery orders (per vendor marketing) | Unknown | S (profile) once a sample exists | <https://klikit.io/en/learn/restaurant-order-aggregation-platform> |
| 14 | **foodpanda (Delivery Hero)** | Bangladesh and others | Restaurant Integration API for POS vendors (access by request form, pos-support@deliveryhero.com); order history only for the last 60 days; a daily report for reconciliation (format not stated) | None for individuals; onboarding for a POS provider takes 1–2 weeks | Not public | Orders, status, items, delivery | Vendor-portal downloads (not confirmed) | L (API) / S–M (export profile) | <https://integration.foodpanda.com/en/>, <https://developer.foodpanda.com/> (Search) |
| 15 | **Pathao Food** | Bangladesh | Nothing found | Nothing found | n/a | n/a | n/a | S (profile) once a sample exists | No source found |
| 16 | **Bangladeshi retail and restaurant POS** (examples that appear in local lists: HisabPlus, Managerium by AKIJ iBOS, KarBariPlus, Hishab, Pi POS, Daffodil POS, PridePOS, EZ Hishab) | Bangladesh | Vendors say "API/plugins" in general; **no public API documentation confirmed for any** | None found | n/a | Sales, items, stock, due ledgers, bKash tracking (HisabPlus per its blog) | Excel/CSV likely (Knowledge, per product) | S per export profile, with a sample | <https://hisabplus.com/Blog/top-10-pos-software-in-bangladesh>, <https://ibos.io/top-5-pos-software-used-in-bangladesh/> (vendor-affiliated lists) |
| 17 | **GiantSoft** | Bangladesh (per the team's private project) | A JSON feed exists in the private project | n/a | n/a | n/a | n/a | S if a redacted sample and field list arrive | Private only; see section 1 |
| 18 | **Generic CSV/Excel POS export** | Any | n/a | n/a | n/a | Whatever the vendor exports | CSV, XLSX | S | The existing L1 and L6 connectors |

## 3. Proposal

### 3.1 One canonical POS mapping

The schema today has `pos_sales_daily` (daily totals only: gross, discounts, refunds, net, tax, tips, fees, cash/card/wallet amounts, transactions count; `backend/ledger/schema.sql`). Reconciliation, wastage and basket analysis need order-line level too, and Cash Truth needs shift cash counts. **I suggest A1 add three tables and one optional table** (A1 owns the schema; this is a request, not a change by me):

```
pos_orders     (id, company_id, location, opened_at, closed_at, business_date, currency,
                channel [dine_in|takeaway|delivery|online|other], aggregator [foodpanda|...|null],
                status [sale|refund|void], refund_for_order_id, gross, discount, tax, tip,
                service_charge, fee, total, employee_ref, customer_ref [pseudonymised],
                source_system, source_file, source_row_id, ingested_at, ingest_batch_id)
pos_order_lines(id, order_id, line_no, sku, name, qty, unit_price, discount, tax, line_total,
                source_*)
pos_payments   (id, order_id, tender [cash|card|wallet|aggregator|other], amount, fee, reference,
                source_*)
pos_shifts     (optional: id, company_id, location, opened_at, closed_at, opening_cash,
                expected_cash, counted_cash, variance, source_*)
```

`pos_sales_daily` is then a **derived view** of these tables for exports that only give daily totals. Mapping rules, to be written once in the adapter:

| Canonical field | Rule |
|---|---|
| `business_date` | The business day in the **location's time zone**, not UTC. Loyverse returns UTC; Odoo stores naive UTC datetimes; Square and Shopify carry offsets (Knowledge for Odoo, Checked for Loyverse). |
| `gross_sales` | Sum of quantity × unit price before discounts, **excluding tax**. If a vendor reports tax-inclusive prices, subtract tax first and record the rule in the profile. |
| `discounts`, `tax`, `tips`, `service_charge` | As reported by the vendor; tips and service charge stay out of `net_sales`. |
| `refunds` | Positive numbers on the day of the refund, not the day of the original sale; link by `refund_for` where the vendor provides it (Loyverse does; Pospal flags reversed bills). |
| `net_sales` | `gross_sales − discounts − refunds`. |
| `fees` | Card processing and aggregator commissions, only when the source gives them; otherwise null, never guessed. This is a main source of the "payout net of fees" reconciliation gap. |
| payment mix | `cash_amount`, `card_amount`, `wallet_amount`; **aggregator (delivery-platform) orders need their own bucket**. The table has no column for it, so A1 should add `other_amount` or `aggregator_amount`. |
| `transactions_count` | Sales receipts only, not refunds or voids. |
| Duplicates | Key on `source_system` + vendor order id; flag, do not drop, repeated ids. |
| PII | Customer names, phones and loyalty ids are pseudonymised before storage and never reach the LLM (CLAUDE.md). |

Field mapping examples (Knowledge unless noted):

| Canonical | Loyverse (Checked) | Square | Odoo POS | Shopify POS |
|---|---|---|---|---|
| order id | `receipt_number` | `order.id` | `pos.order.name` / id | `order.id`, receipt in `source_identifier` |
| status | `receipt_type` SALE / REFUND, `cancelled_at` | order state, refunds objects | state, refund orders with negative lines | `financial_status`, refunds |
| time | `receipt_date` (UTC) | `created_at` | `date_order` (UTC) | `created_at` |
| total | `total_money` | `total_money` | `amount_total` | `total_price` |
| tax | `total_tax` | `total_tax_money` | `amount_tax` | `total_tax` |
| payments | payments array | tenders | `pos.payment` | transactions |
| shift | Shifts endpoint | Labor shifts | `pos.session` (cash register balances) | none confirmed |

### 3.2 Which to make LIVE in two days

| Priority | Pick | Why | Effort | What Jawat must do |
|---|---|---|---|---|
| 1 | **Odoo POS, local Docker** (`odoo:18.0` or 19.0 plus `postgres`, one compose file) | Needs **no account and no payment**; runs offline; supports the "simulated environment" the judges want; gives order, line, payment and session objects; LGPL licence recorded. | M, about 1.5 days including seeding | Install Docker Desktop if absent; nothing else. |
| 2 | **Square sandbox** | A real free sandbox with an Orders API, matches the `uk_garden` Square-style profile, and provides real API logs for the evidence pack. | M, about 1 day | Create a free Square developer account; give the sandbox access token via `backend/.env`. |
| 3 (stretch) | **Loyverse** | Free POS, personal-token REST API, clean `receipts` schema with refunds, and popular with small shops; S effort. | S–M, about 0.5 day | Create a free Loyverse account and a personal access token (keep it in `.env`). |

Risks and fallbacks:
- **Odoo seeding.** Creating POS orders through the API is fiddly: a session must be open and `pos.order` needs lines and payments with consistent totals (Knowledge). If seeding takes more than about 2 hours, fall back to loading Odoo's own demo database and reading whatever POS orders it holds, or write the seed through Odoo's own `create_from_ui` flow after checking the model on the instance's `/doc` page.
- **Odoo version.** Pick one version and note it. The external API differs (XML-RPC vs JSON-2). Check that the chosen image tag exists before writing the compose file.
- **Square or Loyverse accounts** need Jawat. If neither is created in time, ship Odoo as the only API-live POS, with Square and Loyverse as export profiles.

### 3.3 The rest as export-format profiles

Each profile = a mapping file (vendor column names → canonical fields, units, time zone, tax-inclusive or not), a **sample file**, and one test that loads the sample and checks totals. The sample must come from a real export, or from a **synthetic file shaped exactly like that vendor's export**; I cannot confirm the exact column layouts for most vendors, so I will not invent them.

| Profile | Sample file source | Who gets it | Notes |
|---|---|---|---|
| Square CSV (Transactions, Transaction Details, Transaction Items; deposits) | Square sandbox seller dashboard export, or a real small account | Jawat, after creating the sandbox | Join Items and Details by transaction id; known sales-tax summing quirk when merged (community report). |
| Loyverse CSV | Back-office export from the free account | Jawat | |
| Shopify POS orders CSV | Development store export | Jawat (needs a free Partner account) | Filter source `pos`. |
| Toast, Lightspeed, Clover, Zoho, EPOS Now CSV | A real export, or a vendor's published sample | Jawat or a friend with a store | Do not guess layouts. Low priority. |
| Pospal (银豹) Excel, Meituan Excel, Keruyun Excel | A real export | A Chinese-reading teammate with a Chinese store; or the `cn_xborder` demo can skip these | Chinese column headers; use Simplified-Chinese header mapping; UTC+8. |
| Klikit export | A real file | Jawat, only if a Bangladeshi or SE-Asian outlet uses it | Unknown format. |
| foodpanda vendor export | A real order-history or daily report | Jawat, if he can export one | **Redact customer fields first**; orders carry customer data. |
| Pathao Food export | A real file | Jawat | Unknown. |
| Bangladeshi POS exports (HisabPlus, Managerium, KarBariPlus, others) | One real export per vendor | Jawat or the shop owners | Bangla headers and BDT; confirm VAT inclusion. |
| GiantSoft | Redacted sample and field list from the private project | Orchestrator, via that project's owner | **Redaction required before it enters the repo.** |

The registry should show: Odoo, Square, Loyverse as `live` or `sandbox-ready` only after a call succeeds; every profile as `export profile (sample tested)` only when its sample test passes; everything else `planned`. This keeps the registry honest (see `CONNECTORS_LANDSCAPE.md`).

## 4. Jawat's checklist

1. Install Docker Desktop (for Odoo).
2. Create a free Square developer account; put the sandbox token in `backend/.env`.
3. Create a free Loyverse account and a personal access token if the stretch goal is wanted.
4. Tell the orchestrator which POS names he meant for "Clickit/Clicky", "PyHR" and "Kemai", and whether any outlet uses Klikit or foodpanda exports.
5. Provide redacted or synthetic sample exports for the profiles in section 3.3 that matter to him (the Bangladeshi ones first).
6. Never put real customer data, tokens or the private project's files in the repo.

## 5. Not verified

Model and field names for Odoo POS (check `/doc` on the running instance); the Odoo Community edition's API availability; Loyverse's plan limits on API use; Square CSV column names; all export formats marked Knowledge; Zoho POS sandbox; Clover sandbox cost; EPOS Now's docs; Meituan and Pospal signing rules; whether foodpanda Bangladesh offers a vendor CSV.
