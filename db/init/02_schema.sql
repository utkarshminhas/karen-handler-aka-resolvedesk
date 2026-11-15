-- ResolveDesk schema (plan §5, v2.1). Runs once, on first container start.
-- Zones are Postgres schemas: store, app, vectors, graph.

CREATE EXTENSION IF NOT EXISTS vector;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO store_ops, resolvedesk_app;   -- for the vector type

CREATE SCHEMA store;     -- MCP-owned shop data
CREATE SCHEMA app;       -- chatbot records
CREATE SCHEMA vectors;   -- pgvector search index
CREATE SCHEMA graph;     -- LangGraph checkpointer tables (created by PostgresSaver.setup())

-- =====================================================================
-- Zone store
-- =====================================================================
CREATE TABLE store.customers_store (
    customer_unique_id  text PRIMARY KEY,
    city                text,
    state               text
);

CREATE TABLE store.orders (
    order_id            text PRIMARY KEY,
    customer_unique_id  text NOT NULL REFERENCES store.customers_store,
    status              text NOT NULL,
    purchase_ts         timestamp NOT NULL,
    delivered_ts        timestamp,
    estimated_ts        timestamp,
    total               numeric(12,2) NOT NULL
);
CREATE INDEX orders_customer_idx ON store.orders (customer_unique_id);

CREATE TABLE store.products (
    product_id          text PRIMARY KEY,
    category            text,
    name_en             text,
    description         text,
    price               numeric(12,2)          -- median item price; no stock in the PoC
);

CREATE TABLE store.order_items (
    order_id            text NOT NULL REFERENCES store.orders,
    item_seq            int  NOT NULL,
    product_id          text NOT NULL,          -- not FK: catalog is a curated subset
    price               numeric(12,2) NOT NULL,
    freight             numeric(12,2) NOT NULL,
    PRIMARY KEY (order_id, item_seq)
);

CREATE TABLE store.payments (
    order_id            text NOT NULL REFERENCES store.orders,
    payment_seq         int  NOT NULL,
    type                text,
    installments        int,
    value               numeric(12,2) NOT NULL,
    PRIMARY KEY (order_id, payment_seq)
);

-- The only mutation target. State machine enforced by the MCP server:
-- staged -> confirmed -> approved -> issued ; staged -> expired ;
-- confirmed -> pending_staff -> approved|rejected ; confirmed -> staged (revise)
CREATE TABLE store.refunds (
    refund_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proposal_id          uuid NOT NULL UNIQUE,  -- idempotency key for commit
    order_id             text NOT NULL REFERENCES store.orders,
    amount               numeric(12,2) NOT NULL CHECK (amount > 0),
    reason               text NOT NULL,
    status               text NOT NULL DEFAULT 'staged' CHECK (status IN
                           ('staged','confirmed','approved','pending_staff','issued','rejected','expired')),
    created_ts           timestamp NOT NULL DEFAULT now(),
    expires_ts           timestamp,
    confirmed_ts         timestamp,
    approved_by          text,                  -- 'verifier' | 'staff:<id>'
    approval_token_hash  text,
    issued_ts            timestamp
);
CREATE INDEX refunds_order_idx ON store.refunds (order_id);

-- =====================================================================
-- Zone app
-- =====================================================================
CREATE TABLE app.customers (
    customer_id         text PRIMARY KEY,
    customer_unique_id  text NOT NULL UNIQUE,  -- link to store zone (no cross-zone FK)
    name                text NOT NULL,
    email               text NOT NULL,
    persona_note        text
);

CREATE TABLE app.conversations (
    conversation_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         text NOT NULL REFERENCES app.customers,
    thread_id           text NOT NULL UNIQUE,
    status              text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active','resolved','escalated')),
    intent              text,
    started_ts          timestamptz NOT NULL DEFAULT now(),
    ended_ts            timestamptz,
    outcome             text,
    summary             text
);

CREATE TABLE app.messages (                   -- audit log; checkpointer is the agent's source of truth
    message_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id     uuid NOT NULL REFERENCES app.conversations,
    turn_no             int  NOT NULL,
    role                text NOT NULL CHECK (role IN ('user','assistant','system')),
    content             text NOT NULL,
    created_ts          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_conv_idx ON app.messages (conversation_id, turn_no);

CREATE TABLE app.actions (                    -- UI panel + eval scorer + cost report
    action_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id     uuid NOT NULL REFERENCES app.conversations,
    turn_no             int  NOT NULL,
    seq                 int  NOT NULL,
    node                text NOT NULL,
    tool                text,
    args_json           jsonb,
    result_json         jsonb,
    dry_run             boolean NOT NULL DEFAULT false,
    tokens_in           int,
    tokens_out          int,
    latency_ms          int,
    created_ts          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, turn_no, node, seq)   -- retries don't duplicate rows
);

CREATE TABLE app.escalations (                -- HITL queue
    escalation_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id      uuid NOT NULL REFERENCES app.conversations,
    customer_id          text NOT NULL REFERENCES app.customers,
    proposal_id          uuid,
    reason               text NOT NULL,
    summary              text,
    proposed_action_json jsonb,
    amount               numeric(12,2),
    status               text NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','approved','rejected')),
    created_ts           timestamptz NOT NULL DEFAULT now(),
    decided_ts           timestamptz
);
CREATE INDEX escalations_status_idx ON app.escalations (status);

CREATE TABLE app.customer_memory (            -- long-term memory, fixed-schema summaries only
    customer_id         text PRIMARY KEY REFERENCES app.customers,
    summary             text,
    last_intents        jsonb NOT NULL DEFAULT '[]',
    updated_ts          timestamptz NOT NULL DEFAULT now()
);

-- =====================================================================
-- Zone vectors (1024 dims = nvidia/nv-embedqa-e5-v5; per-model product tables
-- product_chunks_<model> are created by scripts/ingest_products.py)
-- =====================================================================
CREATE TABLE vectors.policy_chunks (
    chunk_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doc_name            text NOT NULL,
    section             text,
    content             text NOT NULL,
    embedding           vector(1024) NOT NULL
);
CREATE INDEX policy_chunks_hnsw ON vectors.policy_chunks
    USING hnsw (embedding vector_cosine_ops);

-- =====================================================================
-- Grants: zone ownership enforced by the database
-- =====================================================================
-- store_ops: read the whole store; write ONLY refunds; read policy vectors (search_policy)
GRANT USAGE ON SCHEMA store TO store_ops;
GRANT SELECT ON ALL TABLES IN SCHEMA store TO store_ops;
GRANT INSERT, UPDATE ON store.refunds TO store_ops;
GRANT USAGE ON SCHEMA vectors TO store_ops;
GRANT SELECT ON vectors.policy_chunks TO store_ops;
ALTER ROLE store_ops SET search_path = store, public;

-- resolvedesk_app: app zone read/write, vectors read, graph zone owned (checkpointer DDL).
-- No USAGE on schema store at all.
GRANT USAGE ON SCHEMA app TO resolvedesk_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO resolvedesk_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO resolvedesk_app;
GRANT USAGE ON SCHEMA vectors TO resolvedesk_app;
GRANT SELECT ON ALL TABLES IN SCHEMA vectors TO resolvedesk_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA vectors GRANT SELECT ON TABLES TO resolvedesk_app, store_ops;
ALTER SCHEMA graph OWNER TO resolvedesk_app;
ALTER ROLE resolvedesk_app SET search_path = app, graph, vectors, public;
