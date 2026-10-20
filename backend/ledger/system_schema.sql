-- =============================================================================
-- System tables (security + runtime state). owner: A1
-- These are not business data, so they carry no lineage columns.
-- Only backend/security and backend/agent write here, through their own classes.
-- =============================================================================

-- Append-only, SHA-256 hash-chained audit log. See backend/security/audit.py.
-- row_hash = sha256(canonical JSON of every column except row_hash, incl. prev_hash).
CREATE TABLE IF NOT EXISTS audit_log (
    seq           BIGINT PRIMARY KEY,         -- 1, 2, 3 ... no gaps
    ts            VARCHAR NOT NULL,           -- ISO-8601 UTC, kept as text so hashing is exact
    company_id    VARCHAR,
    actor         VARCHAR NOT NULL,           -- agent | owner | system | api:<name>
    action        VARCHAR NOT NULL,           -- tool_executed, tool_denied, approval_requested, ...
    tool          VARCHAR,
    tier          VARCHAR,
    payload_json  VARCHAR NOT NULL,           -- canonical JSON, never raw secrets or unredacted prompts
    payload_hash  VARCHAR NOT NULL,
    prev_hash     VARCHAR NOT NULL,           -- row_hash of seq-1 ('0'*64 for seq 1)
    row_hash      VARCHAR NOT NULL
);

-- Latest known head of the chain, written on every append. Detects tail deletion
-- unless the attacker also edits this row (known risk, see security README).
CREATE TABLE IF NOT EXISTS audit_head (
    id        INTEGER PRIMARY KEY,           -- always 1
    seq       BIGINT NOT NULL,
    row_hash  VARCHAR NOT NULL,
    updated_at VARCHAR NOT NULL
);

-- T2 actions waiting for the owner.
CREATE TABLE IF NOT EXISTS approvals (
    id            VARCHAR PRIMARY KEY,
    company_id    VARCHAR NOT NULL,
    tool          VARCHAR NOT NULL,
    tier          VARCHAR NOT NULL,
    title         VARCHAR,                    -- plain-language one-liner for the inbox
    payload_json  VARCHAR NOT NULL,           -- {"company_id","tool","args"} canonical JSON
    payload_hash  VARCHAR NOT NULL,           -- sha256(payload_json), re-checked before execution
    status        VARCHAR NOT NULL CHECK (status IN ('pending','approved','rejected','failed')),
    requested_by  VARCHAR NOT NULL,
    requested_at  VARCHAR NOT NULL,
    session_id    VARCHAR,
    decided_by    VARCHAR,
    decided_at    VARCHAR,
    decision_note VARCHAR,
    result_json   VARCHAR
);

-- Permission tier granted per company by its owner.
CREATE TABLE IF NOT EXISTS company_permissions (
    company_id    VARCHAR PRIMARY KEY,
    granted_tier  VARCHAR NOT NULL CHECK (granted_tier IN ('T0','T1','T2','T3')),
    updated_by    VARCHAR NOT NULL,
    updated_at    VARCHAR NOT NULL
);

-- Every LLM call's token usage and cost (drives the daily USD cap).
CREATE TABLE IF NOT EXISTS llm_usage (
    id            VARCHAR PRIMARY KEY,
    ts            VARCHAR NOT NULL,
    day           DATE NOT NULL,              -- UTC day the cap applies to
    provider      VARCHAR NOT NULL,
    model         VARCHAR NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd      DOUBLE NOT NULL,
    company_id    VARCHAR,
    session_id    VARCHAR
);

-- Reminder drafts (T3 "limited execute" marks them ready). The system never sends them.
CREATE TABLE IF NOT EXISTS reminders (
    id              VARCHAR PRIMARY KEY,
    company_id      VARCHAR NOT NULL,
    counterparty_id VARCHAR,
    invoice_id      VARCHAR,
    channel         VARCHAR NOT NULL CHECK (channel IN ('email','sms','whatsapp','letter')),
    body            VARCHAR NOT NULL,
    status          VARCHAR NOT NULL CHECK (status IN ('draft','ready','cancelled')),
    created_by      VARCHAR NOT NULL,
    created_at      VARCHAR NOT NULL
);
