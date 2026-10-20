-- =============================================================================
-- SME Finance Copilot - canonical ledger schema (DuckDB)            owner: A1
-- =============================================================================
-- Contract for D1 (data generators), C1 (connectors) and M1 (engine).
-- Plain-words guide: backend/ledger/SCHEMA.md. Change requests go to A1.
--
-- Conventions
--   * Multi-company: every business table has company_id (fx_rates may be global).
--   * Ids are VARCHAR and must match ^[A-Za-z0-9_.-]+$ (no ':' or spaces) so an
--     evidence reference "table:id" is unambiguous. Use backend.core.ids.make_id().
--   * Money: DECIMAL(18,2) in the row's own currency, plus amount_base in the
--     company's base currency. fx_rate = base-currency units per 1 unit of the
--     row currency (1.0 when currency = base currency).
--   * Signs: journal_lines use debit/credit columns (both >= 0) and a signed
--     amount_base = (debit - credit) * fx_rate. Statement lines: amount > 0 is
--     money IN to the account, amount < 0 is money OUT.
--   * Dates are DATE; ingestion stamps are TIMESTAMP (UTC).
--   * LINEAGE - every data table ends with the same five columns:
--       source_system   e.g. 'quickbooks', 'bank_csv', 'bkash_csv', 'square_pos',
--                       'gsheets', 'synthetic', 'copilot' (rows the copilot drafted)
--       source_file     file name or API path the row came from, e.g.
--                       'brac_bank_2024.csv' or 'qbo-api:JournalEntry'
--       source_row_id   row number / external id inside that source
--       ingested_at     when it was loaded (UTC)
--       ingest_batch_id id of the load in ingest_batches
--     The loader (backend/ledger/loader.py) rejects rows with empty
--     source_system / source_file / source_row_id and stamps the other two.
--   * No foreign-key constraints (keeps bulk loads order-independent); the
--     "-> table" comments document the references the engine relies on.
-- =============================================================================

-- One row per load from any connector/generator. Not a data table itself.
CREATE TABLE IF NOT EXISTS ingest_batches (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR,                -- -> companies.id (NULL for global data)
    source_system   VARCHAR NOT NULL,
    source_file     VARCHAR,
    connector_id    VARCHAR,                -- -> connector registry id, if any
    target_table    VARCHAR NOT NULL,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    rows_received   INTEGER DEFAULT 0,
    rows_inserted   INTEGER DEFAULT 0,
    rows_rejected   INTEGER DEFAULT 0,
    status          VARCHAR NOT NULL DEFAULT 'running'   -- running | ok | partial | failed
);

-- A business (tenant). Country and currency drive financing rules and FX.
CREATE TABLE IF NOT EXISTS companies (
    id                      VARCHAR PRIMARY KEY,
    name                    VARCHAR NOT NULL,
    country                 VARCHAR NOT NULL,     -- ISO 3166-1 alpha-2, e.g. 'BD', 'KE', 'VN'
    base_currency           VARCHAR NOT NULL,     -- ISO 4217, e.g. 'BDT', 'USD'
    industry                VARCHAR,              -- free text or code, e.g. 'bakery', 'garments_export'
    fiscal_year_start_month INTEGER DEFAULT 1,    -- 1..12
    founded_on              DATE,
    description             VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- Chart of accounts.
CREATE TABLE IF NOT EXISTS accounts (
    id            VARCHAR PRIMARY KEY,
    company_id    VARCHAR NOT NULL,               -- -> companies.id
    code          VARCHAR,                        -- e.g. '1000'
    name          VARCHAR NOT NULL,
    type          VARCHAR NOT NULL CHECK (type IN ('asset','liability','equity','income','expense')),
    -- subtype vocabulary (engine relies on these; extend via A1):
    --   asset:     cash, bank, wallet, ar, inventory, prepaid, fixed_asset, other_asset
    --   liability: ap, loan, tax_payable, payroll_payable, accrued, other_liability
    --   equity:    owner_equity, retained_earnings, drawings
    --   income:    revenue, other_income
    --   expense:   cogs, opex, payroll, rent, utilities, marketing, interest, tax, fx_loss, other_expense
    subtype       VARCHAR NOT NULL,
    currency      VARCHAR NOT NULL,               -- account currency (wallet/bank may differ from base)
    parent_id     VARCHAR,                        -- -> accounts.id
    is_active     BOOLEAN DEFAULT TRUE,
    external_ref  VARCHAR,                        -- id in the source system (e.g. QBO account id)
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- Customers, suppliers and employees.
CREATE TABLE IF NOT EXISTS counterparties (
    id            VARCHAR PRIMARY KEY,
    company_id    VARCHAR NOT NULL,               -- -> companies.id
    kind          VARCHAR NOT NULL CHECK (kind IN ('customer','supplier','employee','other')),
    name          VARCHAR NOT NULL,
    email         VARCHAR,                        -- PII: redacted before the LLM
    phone         VARCHAR,                        -- PII: redacted before the LLM
    country       VARCHAR,
    tax_id        VARCHAR,                        -- PII-ish: redacted before the LLM
    payment_terms_days INTEGER,
    external_ref  VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- Journal entry header (the "book" side of the truth).
CREATE TABLE IF NOT EXISTS journal_entries (
    id            VARCHAR PRIMARY KEY,
    company_id    VARCHAR NOT NULL,               -- -> companies.id
    entry_date    DATE NOT NULL,
    reference     VARCHAR,                        -- voucher / document number
    memo          VARCHAR,                        -- UNTRUSTED free text
    status        VARCHAR NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','draft','void')),
    origin        VARCHAR DEFAULT 'imported',     -- imported | manual | copilot_draft
    created_by    VARCHAR,
    approval_id   VARCHAR,                        -- -> approvals.id when origin = copilot_draft
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- Journal lines: double entry. Per entry, SUM(amount_base) should be 0.
CREATE TABLE IF NOT EXISTS journal_lines (
    id              VARCHAR PRIMARY KEY,
    entry_id        VARCHAR NOT NULL,             -- -> journal_entries.id
    company_id      VARCHAR NOT NULL,             -- -> companies.id
    line_no         INTEGER NOT NULL,
    account_id      VARCHAR NOT NULL,             -- -> accounts.id
    debit           DECIMAL(18,2) NOT NULL DEFAULT 0,   -- >= 0, row currency
    credit          DECIMAL(18,2) NOT NULL DEFAULT 0,   -- >= 0, row currency
    currency        VARCHAR NOT NULL,
    fx_rate         DECIMAL(18,8) NOT NULL DEFAULT 1,
    amount_base     DECIMAL(18,2) NOT NULL,       -- (debit - credit) * fx_rate, base currency, signed
    counterparty_id VARCHAR,                      -- -> counterparties.id
    memo            VARCHAR,                      -- UNTRUSTED free text
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- Sales invoices (accounts receivable).
CREATE TABLE IF NOT EXISTS invoices (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    counterparty_id VARCHAR NOT NULL,             -- -> counterparties.id (customer)
    invoice_no      VARCHAR,
    issue_date      DATE NOT NULL,
    due_date        DATE,
    currency        VARCHAR NOT NULL,
    fx_rate         DECIMAL(18,8) NOT NULL DEFAULT 1,
    total           DECIMAL(18,2) NOT NULL,       -- incl. tax, row currency
    tax             DECIMAL(18,2) DEFAULT 0,
    total_base      DECIMAL(18,2) NOT NULL,
    status          VARCHAR NOT NULL DEFAULT 'open'
                    CHECK (status IN ('draft','open','partially_paid','paid','void','written_off')),
    journal_entry_id VARCHAR,                     -- -> journal_entries.id (if posted to the books)
    memo            VARCHAR,                      -- UNTRUSTED free text
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS invoice_payments (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    invoice_id      VARCHAR NOT NULL,             -- -> invoices.id
    payment_date    DATE NOT NULL,
    amount          DECIMAL(18,2) NOT NULL,       -- > 0, row currency
    currency        VARCHAR NOT NULL,
    fx_rate         DECIMAL(18,8) NOT NULL DEFAULT 1,
    amount_base     DECIMAL(18,2) NOT NULL,
    method          VARCHAR,                      -- cash | bank | wallet | card | other
    deposit_account_id VARCHAR,                   -- -> accounts.id where the money landed
    statement_line_id  VARCHAR,                   -- -> external_statement_lines.id once matched
    journal_entry_id   VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- Supplier bills (accounts payable).
CREATE TABLE IF NOT EXISTS bills (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    counterparty_id VARCHAR NOT NULL,             -- -> counterparties.id (supplier)
    bill_no         VARCHAR,
    issue_date      DATE NOT NULL,
    due_date        DATE,
    currency        VARCHAR NOT NULL,
    fx_rate         DECIMAL(18,8) NOT NULL DEFAULT 1,
    total           DECIMAL(18,2) NOT NULL,
    tax             DECIMAL(18,2) DEFAULT 0,
    total_base      DECIMAL(18,2) NOT NULL,
    status          VARCHAR NOT NULL DEFAULT 'open'
                    CHECK (status IN ('draft','open','partially_paid','paid','void')),
    expense_account_id VARCHAR,                   -- -> accounts.id (main expense/COGS account)
    journal_entry_id   VARCHAR,
    memo            VARCHAR,                      -- UNTRUSTED free text
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS bill_payments (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    bill_id         VARCHAR NOT NULL,             -- -> bills.id
    payment_date    DATE NOT NULL,
    amount          DECIMAL(18,2) NOT NULL,       -- > 0, row currency
    currency        VARCHAR NOT NULL,
    fx_rate         DECIMAL(18,8) NOT NULL DEFAULT 1,
    amount_base     DECIMAL(18,2) NOT NULL,
    method          VARCHAR,
    paid_from_account_id VARCHAR,                 -- -> accounts.id
    statement_line_id    VARCHAR,                 -- -> external_statement_lines.id once matched
    journal_entry_id     VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- What the outside world says: bank and wallet statements, cash counts, POS payouts.
-- This is the "reality" side of Cash Truth.
CREATE TABLE IF NOT EXISTS external_statement_lines (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    channel         VARCHAR NOT NULL CHECK (channel IN ('bank','wallet','cash_count','pos_settlement')),
    account_id      VARCHAR,                      -- -> accounts.id this statement maps to (cash/bank/wallet)
    statement_ref   VARCHAR,                      -- provider account label, MASKED (e.g. '****1234')
    txn_date        DATE NOT NULL,
    value_date      DATE,
    amount          DECIMAL(18,2) NOT NULL,       -- signed: + in, - out. For cash_count: counted total, see SCHEMA.md
    currency        VARCHAR NOT NULL,
    fx_rate         DECIMAL(18,8) NOT NULL DEFAULT 1,
    amount_base     DECIMAL(18,2) NOT NULL,
    description     VARCHAR,                      -- UNTRUSTED free text (prompt-injection surface)
    counterparty_text VARCHAR,                    -- UNTRUSTED free text
    balance         DECIMAL(18,2),                -- running balance after this line, if the source gives it
    external_txn_id VARCHAR,                      -- provider transaction id
    fee             DECIMAL(18,2) DEFAULT 0,      -- wallet / POS fees, row currency, >= 0
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_items (
    id            VARCHAR PRIMARY KEY,
    company_id    VARCHAR NOT NULL,
    sku           VARCHAR,
    name          VARCHAR NOT NULL,
    unit          VARCHAR,                        -- kg, pcs, litre ...
    category      VARCHAR,                        -- raw_material | finished_good | packaging | merchandise
    cost_method   VARCHAR DEFAULT 'average',      -- average | fifo
    reorder_level DECIMAL(18,4),
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    item_id         VARCHAR NOT NULL,             -- -> inventory_items.id
    movement_date   DATE NOT NULL,
    kind            VARCHAR NOT NULL CHECK (kind IN ('purchase','sale','production_in','production_out','adjustment','waste','count','transfer')),
    quantity        DECIMAL(18,4) NOT NULL,       -- signed: + into stock, - out of stock
    unit_cost       DECIMAL(18,4),                -- row currency
    currency        VARCHAR,
    amount_base     DECIMAL(18,2),                -- quantity * unit_cost * fx, signed
    reference       VARCHAR,                      -- bill / invoice / count sheet number
    journal_entry_id VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- One row per employee per pay run.
CREATE TABLE IF NOT EXISTS payroll_runs (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    run_ref         VARCHAR,                      -- groups the rows of one run
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    pay_date        DATE,
    employee_id     VARCHAR,                      -- -> counterparties.id (kind = employee)
    gross           DECIMAL(18,2) NOT NULL,
    deductions      DECIMAL(18,2) DEFAULT 0,
    employer_costs  DECIMAL(18,2) DEFAULT 0,
    net             DECIMAL(18,2) NOT NULL,
    currency        VARCHAR NOT NULL,
    fx_rate         DECIMAL(18,8) NOT NULL DEFAULT 1,
    amount_base     DECIMAL(18,2) NOT NULL,       -- total cost to company in base currency
    paid_from_account_id VARCHAR,
    journal_entry_id VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- Daily FX rates. rate = units of to_currency per 1 unit of from_currency.
CREATE TABLE IF NOT EXISTS fx_rates (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR,                      -- NULL = global/public rate
    rate_date       DATE NOT NULL,
    from_currency   VARCHAR NOT NULL,
    to_currency     VARCHAR NOT NULL,
    rate            DECIMAL(18,8) NOT NULL,
    rate_source     VARCHAR,                      -- e.g. 'ecb', 'bangladesh_bank', 'synthetic'
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- ---------------------------------------------------------------------------
-- POS (point of sale). Order level, so reconciliation, refunds, wastage, basket
-- analysis and shift cash counts are possible. Vendor mapping rules (Odoo, Square,
-- Loyverse, Shopify POS, foodpanda / Klikit exports ...): docs/research/POS_CONNECTORS.md
-- section 3.1 and backend/ledger/SCHEMA.md.
-- Amount convention: amounts on an ORDER are positive magnitudes; `status` says
-- whether it is a sale or a refund. PAYMENTS are signed (+ collected, - paid back).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pos_orders (
    id                  VARCHAR PRIMARY KEY,
    company_id          VARCHAR NOT NULL,
    external_order_id   VARCHAR,                  -- vendor order / receipt id (duplicates: flag, don't drop)
    location            VARCHAR,                  -- outlet / store name or code
    opened_at           TIMESTAMP,                -- UTC
    closed_at           TIMESTAMP,                -- UTC
    business_date       DATE NOT NULL,            -- business day in the LOCATION's time zone
    timezone            VARCHAR,                  -- IANA tz of the location, e.g. 'Asia/Dhaka'
    currency            VARCHAR NOT NULL,
    channel             VARCHAR NOT NULL DEFAULT 'counter'
                        CHECK (channel IN ('counter','dine_in','takeaway','delivery','online','other')),
    aggregator          VARCHAR,                  -- delivery platform: foodpanda, pathao_food, klikit, uber_eats ... or NULL
    status              VARCHAR NOT NULL DEFAULT 'sale' CHECK (status IN ('sale','refund','void')),
    refund_for_order_id VARCHAR,                  -- -> pos_orders.id of the original sale, if known
    gross               DECIMAL(18,2) NOT NULL,   -- sum(qty * unit_price) BEFORE discounts, EXCLUDING tax
    discount            DECIMAL(18,2) NOT NULL DEFAULT 0,
    tax                 DECIMAL(18,2) NOT NULL DEFAULT 0,
    tip                 DECIMAL(18,2) NOT NULL DEFAULT 0,   -- not part of net sales
    service_charge      DECIMAL(18,2) NOT NULL DEFAULT 0,   -- not part of net sales
    fee                 DECIMAL(18,2),            -- aggregator commission if the source gives it; NULL = unknown, never guessed
    total               DECIMAL(18,2) NOT NULL,   -- what the customer paid: gross - discount + tax + tip + service_charge
    employee_ref        VARCHAR,                  -- cashier id/code (no names)
    customer_ref        VARCHAR,                  -- PSEUDONYMISED customer / loyalty id, never a name or phone
    shift_id            VARCHAR,                  -- -> pos_shifts.id
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS pos_order_lines (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    order_id        VARCHAR NOT NULL,             -- -> pos_orders.id
    line_no         INTEGER NOT NULL,
    item_id         VARCHAR,                      -- -> inventory_items.id when the SKU is known
    sku             VARCHAR,
    name            VARCHAR NOT NULL,             -- UNTRUSTED free text
    qty             DECIMAL(18,4) NOT NULL,       -- positive (refund orders carry positive qty)
    unit_price      DECIMAL(18,4) NOT NULL,       -- excluding tax
    discount        DECIMAL(18,2) NOT NULL DEFAULT 0,
    tax             DECIMAL(18,2) NOT NULL DEFAULT 0,
    line_total      DECIMAL(18,2) NOT NULL,       -- qty * unit_price - discount (excluding tax)
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS pos_payments (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    order_id        VARCHAR NOT NULL,             -- -> pos_orders.id
    -- buckets: cash | card | wallet (bKash, Nagad, Alipay, WeChat Pay ...) |
    --   aggregator (collected by foodpanda, Pathao Food, Klikit, Uber Eats ..., settled later) |
    --   voucher (gift card / store credit) | bank_transfer | credit (on account, e.g. a "baki" book) | other
    method          VARCHAR NOT NULL
                    CHECK (method IN ('cash','card','wallet','aggregator','voucher','bank_transfer','credit','other')),
    provider        VARCHAR,                      -- bkash, nagad, visa, foodpanda, klikit, stripe ...
    amount          DECIMAL(18,2) NOT NULL,       -- SIGNED: + collected, - refunded
    fee             DECIMAL(18,2),                -- processing fee if the source gives it; NULL = unknown
    currency        VARCHAR NOT NULL,
    paid_at         TIMESTAMP,                    -- UTC
    reference       VARCHAR,                      -- card auth / wallet txn id (masked)
    settlement_statement_line_id VARCHAR,         -- -> external_statement_lines.id once the payout is matched
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- Optional: till sessions with cash counts (Odoo pos.session, Loyverse shifts, Square shifts).
CREATE TABLE IF NOT EXISTS pos_shifts (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    location        VARCHAR,
    business_date   DATE NOT NULL,
    opened_at       TIMESTAMP,
    closed_at       TIMESTAMP,
    currency        VARCHAR NOT NULL,
    opening_cash    DECIMAL(18,2),
    expected_cash   DECIMAL(18,2),                -- per the POS: opening + cash sales - cash refunds - payouts
    counted_cash    DECIMAL(18,2),                -- what the cashier counted
    variance        DECIMAL(18,2),                -- counted - expected (negative = shortfall)
    employee_ref    VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

-- Daily totals for sources that only export a daily summary (no order detail).
-- pos_sales_daily prefers orders when both exist for the same company/location/day.
CREATE TABLE IF NOT EXISTS pos_daily_imports (
    id                  VARCHAR PRIMARY KEY,
    company_id          VARCHAR NOT NULL,
    sales_date          DATE NOT NULL,
    location            VARCHAR,
    currency            VARCHAR NOT NULL,
    gross_sales         DECIMAL(18,2) NOT NULL,   -- excluding tax, before discounts
    discounts           DECIMAL(18,2) DEFAULT 0,
    refunds             DECIMAL(18,2) DEFAULT 0,  -- positive, on the day of the refund
    net_sales           DECIMAL(18,2) NOT NULL,   -- gross_sales - discounts - refunds
    tax                 DECIMAL(18,2) DEFAULT 0,
    tips                DECIMAL(18,2) DEFAULT 0,
    service_charge      DECIMAL(18,2) DEFAULT 0,
    fees                DECIMAL(18,2),            -- NULL = unknown
    cash_amount         DECIMAL(18,2) DEFAULT 0,  -- payment mix
    card_amount         DECIMAL(18,2) DEFAULT 0,
    wallet_amount       DECIMAL(18,2) DEFAULT 0,
    aggregator_amount   DECIMAL(18,2) DEFAULT 0,
    other_amount        DECIMAL(18,2) DEFAULT 0,  -- voucher, bank transfer, credit, other
    transactions_count  INTEGER,                  -- sales receipts only (not refunds / voids)
    source_system VARCHAR NOT NULL, source_file VARCHAR NOT NULL, source_row_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL, ingest_batch_id VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pos_orders_company_date ON pos_orders (company_id, business_date);
CREATE INDEX IF NOT EXISTS idx_pos_payments_order ON pos_payments (order_id);
CREATE INDEX IF NOT EXISTS idx_pos_lines_order ON pos_order_lines (order_id);

-- DERIVED VIEW (read-only; the loader refuses it): one row per company / location /
-- business day / currency, from pos_orders + pos_payments. Days that exist only in
-- pos_daily_imports are added from there (basis = 'imported').
CREATE OR REPLACE VIEW pos_sales_daily AS
WITH o AS (
    SELECT company_id, COALESCE(location, '') AS loc, business_date AS sales_date, currency,
        SUM(CASE WHEN status = 'sale' THEN gross ELSE 0 END)                      AS gross_sales,
        SUM(CASE WHEN status = 'sale' THEN discount ELSE 0 END)                   AS discounts,
        SUM(CASE WHEN status = 'refund' THEN gross - discount ELSE 0 END)         AS refunds,
        SUM(CASE WHEN status = 'sale' THEN tax WHEN status = 'refund' THEN -tax ELSE 0 END) AS tax,
        SUM(CASE WHEN status = 'sale' THEN tip ELSE 0 END)                        AS tips,
        SUM(CASE WHEN status = 'sale' THEN service_charge ELSE 0 END)             AS service_charge,
        SUM(fee)                                                                  AS order_fees,
        COUNT(*) FILTER (WHERE status = 'sale')                                   AS transactions_count,
        COUNT(*)                                                                  AS order_count,
        MAX(ingested_at)                                                          AS ingested_at
    FROM pos_orders
    WHERE status <> 'void'
    GROUP BY company_id, COALESCE(location, ''), business_date, currency
),
p AS (
    SELECT o.company_id, COALESCE(o.location, '') AS loc, o.business_date AS sales_date, o.currency,
        SUM(CASE WHEN p.method = 'cash' THEN p.amount ELSE 0 END)                 AS cash_amount,
        SUM(CASE WHEN p.method = 'card' THEN p.amount ELSE 0 END)                 AS card_amount,
        SUM(CASE WHEN p.method = 'wallet' THEN p.amount ELSE 0 END)               AS wallet_amount,
        SUM(CASE WHEN p.method = 'aggregator' THEN p.amount ELSE 0 END)           AS aggregator_amount,
        SUM(CASE WHEN p.method NOT IN ('cash', 'card', 'wallet', 'aggregator') THEN p.amount ELSE 0 END) AS other_amount,
        SUM(p.fee)                                                                AS payment_fees
    FROM pos_payments p
    JOIN pos_orders o ON o.id = p.order_id AND o.company_id = p.company_id
    WHERE o.status <> 'void'
    GROUP BY o.company_id, COALESCE(o.location, ''), o.business_date, o.currency
),
from_orders AS (
    SELECT
        'posd-' || substr(md5(o.company_id || '|' || o.loc || '|' || CAST(o.sales_date AS VARCHAR) || '|' || o.currency), 1, 16) AS id,
        o.company_id, o.sales_date, NULLIF(o.loc, '') AS location, o.currency,
        CAST(o.gross_sales AS DECIMAL(18,2))                                      AS gross_sales,
        CAST(o.discounts AS DECIMAL(18,2))                                        AS discounts,
        CAST(o.refunds AS DECIMAL(18,2))                                          AS refunds,
        CAST(o.gross_sales - o.discounts - o.refunds AS DECIMAL(18,2))            AS net_sales,
        CAST(o.tax AS DECIMAL(18,2))                                              AS tax,
        CAST(o.tips AS DECIMAL(18,2))                                             AS tips,
        CAST(o.service_charge AS DECIMAL(18,2))                                   AS service_charge,
        CAST(CASE WHEN o.order_fees IS NULL AND p.payment_fees IS NULL THEN NULL
                  ELSE COALESCE(o.order_fees, 0) + COALESCE(p.payment_fees, 0) END AS DECIMAL(18,2)) AS fees,
        CAST(COALESCE(p.cash_amount, 0) AS DECIMAL(18,2))                         AS cash_amount,
        CAST(COALESCE(p.card_amount, 0) AS DECIMAL(18,2))                         AS card_amount,
        CAST(COALESCE(p.wallet_amount, 0) AS DECIMAL(18,2))                       AS wallet_amount,
        CAST(COALESCE(p.aggregator_amount, 0) AS DECIMAL(18,2))                   AS aggregator_amount,
        CAST(COALESCE(p.other_amount, 0) AS DECIMAL(18,2))                        AS other_amount,
        CAST(o.transactions_count AS INTEGER)                                     AS transactions_count,
        'orders'                                                                  AS basis,
        CAST(o.order_count AS INTEGER)                                            AS order_count,
        'derived'                                                                 AS source_system,
        'pos_orders'                                                              AS source_file,
        o.company_id || '|' || o.loc || '|' || CAST(o.sales_date AS VARCHAR)      AS source_row_id,
        o.ingested_at                                                             AS ingested_at,
        'derived'                                                                 AS ingest_batch_id
    FROM o LEFT JOIN p USING (company_id, loc, sales_date, currency)
)
SELECT * FROM from_orders
UNION ALL
SELECT i.id, i.company_id, i.sales_date, i.location, i.currency, i.gross_sales, i.discounts, i.refunds,
       i.net_sales, i.tax, i.tips, i.service_charge, i.fees, i.cash_amount, i.card_amount, i.wallet_amount,
       i.aggregator_amount, i.other_amount, i.transactions_count, 'imported' AS basis, NULL AS order_count,
       i.source_system, i.source_file, i.source_row_id, i.ingested_at, i.ingest_batch_id
FROM pos_daily_imports i
WHERE NOT EXISTS (
    SELECT 1 FROM from_orders f
    WHERE f.company_id = i.company_id AND COALESCE(f.location, '') = COALESCE(i.location, '')
      AND f.sales_date = i.sales_date
);

CREATE INDEX IF NOT EXISTS idx_jl_company_account ON journal_lines (company_id, account_id);
CREATE INDEX IF NOT EXISTS idx_jl_entry ON journal_lines (entry_id);
CREATE INDEX IF NOT EXISTS idx_je_company_date ON journal_entries (company_id, entry_date);
CREATE INDEX IF NOT EXISTS idx_esl_company_date ON external_statement_lines (company_id, txn_date);
