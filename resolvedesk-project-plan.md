# ResolveDesk — Project Plan (v2)

> A customer-facing, multi-agent support chat for e-commerce, built as a 5-day personal
> showcase project targeting GenAI Architect / GenAI SDE-3 interviews.
>
> **v2 change:** the product is a **user-facing chatbot** (Streamlit on FastAPI), not a
> ticket-first backend. One conversation = one issue, resolved over multiple turns. The
> "ticket" survives only as an internal case record used for metrics. Data layer moves
> from SQLite/Chroma to **PostgreSQL + pgvector** (one instance for store data, app data,
> checkpointer, and vectors).

---

## 1. Background & Goals

**Builder profile:** Python data engineer / GenAI builder. Strong in Snowflake, scraping
pipelines, stored procedures. **Has not built agentic systems professionally** — this
project exists to close that gap through execution.

**Constraints:** personal project, public data only, solo, 5 days, free tier / local PoC.

**Environment decisions:**

- LLM: **hosted NVIDIA NIM API** (`integrate.api.nvidia.com`) — free tier, ~40 RPM. Do NOT
  self-host NIM during the PoC.
- Data layer: **PostgreSQL 16 + pgvector** in Docker (one container). Replaces the v1
  SQLite + Chroma pair. Cloud migration happens after the PoC; architecture stays cloud-ready.
- Dev machine: Windows + Docker Desktop.

**Required concepts to cover (all must appear in the PoC):**

1. Agents (tool-using, planning/reasoning loops)
2. Multi-agent orchestration (supervisor/worker, routing, graph-based flows)
3. Context engineering (compression, retrieval, selection, token budgets)
4. Shared agent memory / scratchpad (short-term state vs. long-term memory)
5. LangChain + LangGraph
6. Tracing/observability (LangSmith or Langfuse)
7. Both harnesses: (a) eval/test harness, (b) agent runtime harness (retries, guardrails, limits)
8. MCP: consume existing servers + build ≥1 custom server
9. FastAPI + Docker packaging

Plus: evals are a first-class deliverable, with deterministic ground truth wherever possible.

---

## 2. The Product: ResolveDesk

**What it is:** a customer-support **chat** for an e-commerce store. A customer opens the
chat, describes a problem in their own words, and the system resolves it conversationally —
asking clarifying questions ("which order do you mean?"), taking real actions against the
store's systems (refunds), and **showing its work** in an actions panel ("looked up order
#A102", "checked returns policy", "issued refund ₹1,499").

**Conversation model:** one conversation = **one issue**, resolved over as many turns as it
takes. A new issue = a new conversation (enforced by the topic guard, §7). Multi-turn covers
clarification loops, entity carry-over, and a refund confirmation turn. Topic switches and
interruptions are explicitly out of scope.

**Key product decisions (locked):**

| Decision | Choice |
|---|---|
| Demo artifact | Streamlit chat UI on FastAPI — the thing screen-shared in interviews |
| Dialogue management | One issue, multi-turn; no free-form topic switching |
| Multi-turn evals | Replay ABCD customer turns through the API; agent generates responses |
| Transparency | User sees agent actions (tool calls, verdicts) in a UI panel |
| Mutations | Refund executes only after user confirmation + verifier approval |

**Where "tickets" went:** demoted to an internal case record — created automatically per
conversation, closed on resolution/escalation. Never user-facing. Keeps the metrics/case
language for interviews without confusing the product.

**Layman pitch:** "A support chatbot that doesn't just chat — it looks up your order, follows
the store's rules, asks before it refunds your money, and shows you everything it did. A
second AI checks its work before anything happens."

---

## 3. Data Sources (verified)

| Source | What | Access / license | Role |
|---|---|---|---|
| **Bitext Customer Support dataset** (Hugging Face) | 26,872 utterances, 27 intents | CDLA-Sharing-1.0 | Triage-router eval (intent accuracy, macro-F1) |
| **ABCD — Action-Based Conversations Dataset** (ASAPP, GitHub) | 10,042 multi-turn dialogues, 55 intents, actions annotated vs. policies | MIT | **Multi-turn replay evals**: feed scripted customer turns, score agent actions. Day 1–2 converter, timeboxed 3h |
| **Olist Brazilian e-commerce dataset** (Kaggle) | 100K real orders: status, items, payments, shipping | CC BY-NC-SA 4.0 | Loaded into **Postgres** = the store DB; ground truth for order/refund evals |
| Synthetic store policies (self-written) | ~5 docs: returns, shipping, warranty, damaged items, escalation | Ours | Verifier rules; pgvector source; injection surface |
| Synthetic product catalog | Olist categories + generated descriptions | Ours | Product-QA RAG + embedding-model comparison evals |

**Gotchas:** Olist review text is Portuguese — don't use it; use the shipped category
translation. Kaggle needs an account (Day 0; fallback: 5K Faker orders). ABCD actions follow
ASAPP's policies — score at action-type level, not exact match. "Customer Support on Twitter"
dataset deliberately skipped (unlabeled, dubious license).

---

## 4. Architecture

```
┌────────────────────────────────────────────────────────────┐
│  Streamlit chat UI                                         │
│  chat pane │ actions panel (tool calls, verdicts)          │
└──────────────────────────┬─────────────────────────────────┘
                           │ REST
┌──────────────────────────▼─────────────────────────────────┐
│  FastAPI                                                   │
│  POST /conversations                  start session        │
│  POST /conversations/{id}/messages    one user turn        │
│  GET  /conversations/{id}             history + actions    │
│  GET  /customers                      demo personas        │
│  POST /admin/escalations/{id}/decision  simulated staff    │
│  GET  /health                                                │
│  Per-turn response: { reply, actions[], status }           │
└──────────────────────────┬─────────────────────────────────┘
                           │ invokes, keyed by thread_id
┌──────────────────────────▼─────────────────────────────────┐
│  LangGraph supervisor (Postgres checkpointer per thread)   │
│                                                            │
│  TRIAGE (8B, every turn) ── same_issue ──► SPECIALIST (70B)│
│      │                            ├──────► ORDER-STATUS    │
│      ├─ new_issue ──► steer-back  ├──────► REFUND/RETURNS  │
│      └─ out_of_scope ─► refusal   └──────► PRODUCT-QA      │
│      template                  template                    │
│                                                            │
│  Specialist ── missing info ──► CLARIFY (interrupt, wait)  │
│  Specialist ── refund staged ─► CONFIRM (interrupt, user   │
│                                 approves) ──► VERIFIER ──► │
│                                 approve: execute via MCP   │
│                                 revise: loop (step-limited)│
│                                 escalate: HITL queue row   │
└───────┬──────────────────────────────┬─────────────────────┘
        │                              │
┌───────▼──────────────────────────────▼─────────────────────┐
│  PostgreSQL 16 + pgvector (one Docker container)           │
│  zone store (MCP-owned): orders, order_items, payments,    │
│    products, refunds                                       │
│  zone app: customers, conversations, messages, actions,    │
│    escalations, customer_memory                            │
│  zone vectors: policy_chunks, product_chunks_<model>       │
│  langgraph checkpointer tables                             │
└────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────┐
│  MCP servers                                               │
│  CUSTOM store-ops (Python, stdio): get_order,              │
│    get_customer_orders, check_refund_eligibility,          │
│    issue_refund (staged/commit), search_policy,            │
│    get_product_details                                     │
│  OFFICIAL: filesystem/fetch (best-effort, droppable)       │
└────────────────────────────────────────────────────────────┘
Observability: LangSmith or Langfuse (decide Day 1)
LLM: NVIDIA NIM hosted API, two-tier (70B specialist/verifier, 8B triage/summary)
```

**Design choices (interview ammunition):**

1. **Triage runs on every turn**, not just turn 1 — enables the topic guard (§7) and makes
   routing resilient to user drift. Cheap because it uses the 8B tier.
2. **Verifier is a separate node** — programmatic checks first (amount cap, return window,
   fresh DB state), LLM only for judgment calls. Assumes the specialist may be compromised.
3. **Mutations go through MCP tools, never generated SQL** — typed contracts, server-side
   caps, dry-run mode. Text-to-SQL deliberately excluded.
4. **Refund needs two approvals**: the user's (CONFIRM interrupt — human-in-the-loop as a
   conversation turn) and the verifier's.
5. **Two-tier models** — 8B triage/summary, 70B specialist/verifier. Cost per resolved
   conversation is a tracked metric.
6. **The `actions` table is the demo linchpin** — one write feeds the Streamlit panel, the
   eval tool-sequence scorer, and the cost report.
7. **All model names behind a factory** (`llm.py`) — one-line swap; enables the NIM-hosted →
   self-hosted → cloud story.

---

## 5. Data Layer

One Postgres instance, three zones with strict ownership:

**Zone store** — only the MCP server connects:
- `orders(order_id PK, customer_id, status, purchase_ts, delivered_ts, estimated_ts)`
- `order_items(order_id FK, product_id FK, price, freight)`
- `payments(order_id FK, type, installments, value)`
- `products(product_id PK, category, name_en, description, price)`
- `refunds(refund_id PK, order_id FK, amount, reason, status staged|issued|rejected, created_ts, approved_by)` — the only mutation target

**Zone app** — FastAPI/graph code:
- `customers(customer_id PK, name, email, persona_note)` — demo personas (no auth in PoC)
- `conversations(conversation_id PK, customer_id FK, thread_id, status active|resolved|escalated, intent, started_ts, ended_ts, outcome, summary)` — the internal case record
- `messages(message_id PK, conversation_id FK, turn_no, role, content, created_ts)`
- `actions(action_id PK, conversation_id FK, turn_no, node, tool, args_json, result_json, dry_run, created_ts)` — feeds UI panel + eval scorer + cost report
- `escalations(escalation_id PK, conversation_id FK, customer_id FK, reason, summary, proposed_action_json, amount, status pending|approved|rejected, created_ts, decided_ts)` — HITL queue
- `customer_memory(customer_id PK FK, summary, last_intents jsonb, updated_ts)` — long-term memory (Day 3, cuttable to read-only)

**Zone vectors** — pgvector:
- `policy_chunks(chunk_id PK, doc_name, section, content, embedding vector(N))`
- `product_chunks_<model>(chunk_id PK, product_id FK, content, embedding vector(N))` — one table per embedding model for the recall comparison (2–3 models; timeboxed, §8)

Plus LangGraph checkpointer tables (managed). Interaction rule: **nothing except the MCP
server ever opens the store zone** — including the verifier and eval scorers (scorers get
read-only diffs via MCP or a read replica connection).

---

## 6. Turn Lifecycle

```
user message → persist message, load thread state
  → TRIAGE (8B, with current-intent context)
      ├ same_issue   → resume/route to specialist
      ├ new_issue    → steer-back template ("let's finish X first,
      │                 or open a new conversation after") — logged
      └ out_of_scope → polite refusal template, no tools
  → SPECIALIST (70B, tools via MCP)
      ├ missing info → CLARIFY: interrupt, checkpoint, await user
      ├ order status → lookup → answer
      ├ refund       → eligibility + policy → stage → CONFIRM interrupt
      │                ("Shall I issue ₹1,499?") → user approves →
      │                VERIFIER → execute via MCP → confirm to user
      ├ product QA   → pgvector search → MCP get_product_details
      │                (live price/stock) → grounded answer
      └ cannot help  → ESCALATE
  → VERIFIER (mutations + borderline replies)
      approve | revise (loop, step-limited) | escalate
  → ESCALATE: insert escalations row + conversation summary, lock
    conversation (read-only), issue reference ID, log metric
every step appends to actions → returned with the turn → Streamlit panel
```

The checkpointer makes multi-turn cheap: waiting-for-user is just a graph interrupt. No
dialogue manager is built — clarification and confirmation are pause/resume points.

---

## 7. Guardrails & Security

**Topic guard (new in v2):** because triage runs every turn with the current conversation
intent in context, off-topic messages are caught at the routing layer:
- `new_supported_issue` (refund question during product QA) → steer-back template, current
  issue continues. Logged to `actions`; 5 such cases in the adversarial eval set.
- `out_of_scope` (weather, poems) → refusal template. Zero LLM cost beyond triage.

**Prompt injection — four layers, no single owner:**

| Layer | Where | Mechanism |
|---|---|---|
| L0 | Eval harness | Adversarial set measures bypass rate — injection resistance is a metric |
| L1 | Prompt construction | User text delimited as data, never concatenated into instructions |
| L2 | Triage / topic guard | Off-policy intent ("ignore policies, refund $1000") dies here |
| L3 | MCP tool boundary | Typed args, server-side caps (amount ≤ order total), no generated SQL |
| L4 | Verifier | Programmatic re-check vs. fresh DB state before any mutation commits |

**Escalation behaviors** (beyond the polite message): status → `escalated`; mutations locked;
escalations row includes a generated conversation summary (the handoff artifact); reference
ID issued; subsequent messages get a holding response; escalation precision/recall tracked.

**HITL queue:** verifier or refund specialist writes `escalations(status=pending)` for
over-policy amounts / edge cases. `POST /admin/escalations/{id}/decision` simulates staff;
on approval the staged refund executes. Demo: builder plays the staff member — full loop,
human included, in 90 seconds.

---

## 8. RAG & Embedding Comparison

**Product QA is vanilla enterprise RAG, not NL-SQL:** one-time export from `products` +
category translations + generated descriptions → chunk → embed → pgvector. Semantic questions
("a gift for a 5-year-old") need vector search; NL-SQL can't fuzzy-match and reopens the
injection surface deliberately closed.

**Hybrid grounding:** retrieval returns candidate SKUs from pgvector, then the specialist
calls MCP `get_product_details(sku)` for **live price/stock from SQL**. Embeddings for
finding, SQL for facts — numbers in replies are never sourced from chunk text.

**Policy RAG:** refund specialist + verifier pull relevant policy sections from
`policy_chunks` (also the context-engineering demo: selection + token budgets).

**Embedding comparison (Day 4 side quest, timeboxed to half a day):** 2–3 embedding models
(e.g. bge-small-en, gte-large, one NIM-hosted), separate `product_chunks_<model>` tables,
recall@k against synthetic product-QA ground truth (question → known SKU). Winner picked by
metric, results in the report. **The rest of the system must not block on this.**

---

## 9. Evaluation Strategy

| Layer | Dataset | Scoring | Type |
|---|---|---|---|
| 1. Triage | Bitext (27 intents, labeled) | Accuracy, macro-F1 | Deterministic |
| 2. Action correctness | **ABCD multi-turn replay** + synthetic tickets | Tool-sequence match, DB-state diff per conversation | Deterministic |
| 3. Policy & robustness | ~15–30 hand-written chat-shaped tickets (injection mid-conversation, off-topic steer-back, day-31 return, angry multi-turn, unanswerable → escalate) | Programmatic + LLM-judge rubric | Hybrid |
| 4. Retrieval (side quest) | Synthetic product-QA pairs | recall@k per embedding model | Deterministic |

**ABCD replay:** for each dialogue, reset the store zone to a fixture snapshot, open a
conversation, feed scripted customer turns one at a time through `POST .../messages`, capture
replies + actions, score action-type compliance vs. ABCD annotations. Deterministic on the
user side — that's what keeps multi-turn evals honest.

**Runner:** concurrency-limited (2–3) for NIM rate limits; response cache keyed on
(prompt, model); 25-item smoke set for iteration, full set for Day 4/5 baselines; regression
diff in CI. **Report:** per-intent accuracy, action correctness, policy compliance,
escalation precision/recall, injection bypass rate, cost per resolved conversation, p50/p95
latency — diffed vs. previous run.

---

## 10. Repo Structure

```
resolvedesk/
├── docker-compose.yml            # app + postgres/pgvector (+ langfuse optional)
├── Dockerfile
├── pyproject.toml                # locked deps from Day 1
├── .env.example
├── src/resolvedesk/
│   ├── api/main.py               # FastAPI: endpoints from §4
│   ├── ui/streamlit_app.py       # chat pane + actions panel
│   ├── graph/
│   │   ├── supervisor.py         # LangGraph graph definition
│   │   ├── state.py              # shared scratchpad schema
│   │   └── nodes/                # triage, specialists, verifier, clarify/confirm
│   ├── agents/                   # system prompts, per-agent config
│   ├── tools/                    # LangChain tools wrapping MCP clients
│   ├── context/                  # history summarization, retrieval, token budget
│   ├── memory/                   # customer_memory access
│   ├── harness/                  # retries, timeouts, step limits, dry-run
│   └── llm.py                    # NIM client factory (tiered)
├── mcp_servers/store_ops/        # CUSTOM MCP server (Python, stdio)
│   ├── server.py                 # tools from §4
│   └── db.py                     # the ONLY connection to the store zone
├── scripts/
│   ├── load_olist.py             # Olist → Postgres store zone
│   ├── ingest_policies.py        # policies → policy_chunks
│   ├── ingest_products.py        # products → product_chunks_<model>
│   └── abcd_convert.py           # ABCD → replay fixtures (timeboxed)
├── evals/
│   ├── datasets/                 # bitext_triage, abcd_replay, synthetic_chat
│   │                           #   (incl. adversarial + off-topic)
│   ├── runner.py                 # replay engine, cache, concurrency limit
│   ├── scorers.py                # §9 layers
│   └── reports/
├── policies/                     # synthetic store policy docs
└── tests/
```

---

## 11. Five-Day Plan

**Day 0 (evening prep, ~1–2h):** NIM API key; LangSmith/Langfuse decision; repo skeleton;
Kaggle token + Olist download; Bitext + ABCD downloads; Node LTS (official MCP servers);
`docker compose up postgres` sanity check.

| Day | Build | Done means | Cut line if behind |
|---|---|---|---|
| **1** | Data + MCP server | Olist→Postgres loaded; MCP server callable from Python client; policies written + embedded; **20-scenario tool-calling smoke test passes**; tracing live | Drop inventory/get_product_details stretch |
| **2** | Order-status chat loop (single agent) | Multi-turn conversation works over API: clarify → lookup → answer; harness: retries, timeouts, step limit, dry-run | Single-turn only |
| **3** | Full graph + memory + Streamlit skeleton | Triage (every turn) + 3 specialists + scratchpad; topic guard; basic chat UI showing replies + raw actions list | Cut product-QA specialist; memory read-only |
| **4** | Verifier + refund flow + evals | Refund with CONFIRM turn + verifier + HITL queue; ABCD replay runner; baseline report; adversarial set (incl. off-topic); embedding recall comparison (timeboxed half-day) | **Never cut evals** — shrink to ~100 items. Cut embedding comparison first, then product-QA |
| **5** | UI polish + Docker + regression + story | Actions panel rendered; `docker compose up` → working chat; evals re-run in container, diff vs. baseline; README with diagrams + metrics; demo rehearsed (incl. staff-approval moment) | Single Dockerfile; simplify actions panel |

**Non-negotiables:** evals exist by Day 4, Docker by Day 5, tracing from Day 1. Streamlit is
a Day-3 deliverable, not a Day-5 surprise.

---

## 12. Scope Decisions

- **RAG: YES, twice** (policy retrieval; product catalog) + embedding-model comparison as a
  timeboxed retrieval eval. **Text-to-SQL: deliberately NO** (typed MCP tools; stretch goal
  is a read-only analytics tool for the supervisor).
- **Multi-turn: YES, scoped** — one issue per conversation, clarification + confirmation
  loops via graph interrupts. **Topic switches / interruptions: NO** (handled by steer-back
  templates, talked about in interviews as backlog).
- **Auth: NO** — demo persona picker (`GET /customers`) instead of accounts.
- **Streaming tokens: NO** — actions panel polls per-turn responses.
- **pgvector vs. Chroma:** pgvector chosen — one Postgres instance for everything, and the
  embedding comparison becomes a SQL query away.

---

## 13. Blocker Analysis (pre-mortem, updated)

| # | Blocker | Mitigation | Kill criterion |
|---|---|---|---|
| 1 | **NIM rate limits (~40 RPM) vs. eval volume** | Concurrency-limited runner, response caching, two-tier models, smoke set | Full run >2h → shrink to 100 items |
| 2 | **Tool-calling reliability of open models on NIM** | Day 1 smoke test; `create_react_agent`; Pydantic structured-output fallback | >10% failure → collapse to 1 specialist |
| 3 | Model catalog churn on free tier | Model names in config, LLM factory | — |
| 4 | **MCP stdio quirks on Windows** | Python MCP SDK; Node preinstalled; test chain Day 1 morning | 30 min → drop official servers; custom is the requirement |
| 5 | Docker-on-Windows debugging eats Day 5 | One app image + official pgvector image; locked deps | >3h → demo locally; Docker is a checkbox, evals aren't |
| 6 | LangSmith free-tier trace limits | Sample tracing; alt: self-hosted Langfuse | Decide Day 1, don't switch mid-week |
| 7 | Olist requires Kaggle account | Day 0; GitHub mirrors | Fallback: 5K Faker orders |
| 8 | Olist content is Portuguese | No review text; category translation CSV | — |
| 9 | **ABCD converter effort eats Day 4** | Timebox 3h on Day 1–2; target 50–100 replay conversations | Not producing by end of Day 2 → drop ABCD, lean on synthetic multi-turn scripts |
| 10 | **LangGraph learning curve (first agent project)** | Prebuilt patterns; interrupts for clarify/confirm are the only custom control flow | No working agent by midday Day 2 → linear topology |
| 11 | **Streamlit scope creep** | Skeleton on Day 3 (list-based actions panel); polish only on Day 5 | Panel stays a raw list — functionality over looks |
| 12 | Writing eval content unbudgeted | Policies Day 1; LLM-draft adversarial, hand-fix | Minimum: 3 policies, 15 adversarial tickets |
| 13 | **pgvector/Postgres adds setup vs. SQLite** | Official `pgvector/pgvector:pg16` image; compose service on Day 0 | Postgres fighting back on Day 1 → fall back to SQLite + Chroma (v1 stack) |

**Verdict:** plan survives. #1 and #9 remain the only schedule killers — both solved by
shrinking scope, not heroics. #13 is new and cheap to de-risk on Day 0.

---

## 14. Presentation

**Resume bullets:**

> - Built **ResolveDesk**, a customer-facing multi-agent support chat (LangGraph, LangChain,
>   FastAPI, Streamlit, PostgreSQL/pgvector, Docker) that resolves order-status, refund, and
>   product queries conversationally — executing real actions against a store database
>   through a custom **MCP server**.
> - Designed a compliance-verifier agent plus a user confirmation turn (human-in-the-loop via
>   graph interrupts) gating all refunds; added layered prompt-injection defenses and a
>   topic guard, validated with an adversarial eval set.
> - Built an evaluation harness with deterministic ground truth (**ABCD multi-turn replay**,
>   Bitext intent labels, fixture order DB): intent accuracy, tool-sequence correctness,
>   DB-state diffs, and policy-compliance metrics — plus a pgvector embedding-model
>   comparison (recall@k) — with regression runs in CI and full tracing.

**Interview questions this answers well:**

1. "How do you make a support agent safe enough to touch real orders?" → confirm turn,
   verifier, typed tools, dry-run, HITL queue for over-policy amounts.
2. "How do you evaluate an agent that takes actions and holds conversations?" → ABCD replay,
   tool-sequence assertions, DB-state diffs.
3. "When do you split into multiple agents?" → risk boundaries; two of three specialists are
   deliberately simple and you'd say so unprompted.
4. "How do you control cost?" → model tiering, caching, context budgets, cost per resolved
   conversation.
5. "Why MCP instead of in-process tools?" → contract boundary; the store zone has exactly one
   door.
6. "How do you handle users going off-topic?" → every-turn triage + topic guard with
   steer-back templates, measured in the adversarial set.
7. "How did you pick your embedding model?" → didn't guess; recall@k comparison on pgvector
   with synthetic ground truth.

---

## 15. Bottom Line

ResolveDesk v2 is the same eval-maximizing bet with a better product shape: a chatbot makes
the safety features (confirm turn, verifier, HITL queue) *visible* instead of hidden backend
steps, and multi-turn context engineering becomes demoable live. The deterministic-eval core
is unchanged — the store DB and policies are ours, so actions have exact ground truth. The
new risks (Streamlit scope creep, Postgres setup) are smaller than the risks they replaced
and both have Day-0/Day-1 kill criteria.

**Next action: Day 0 prep** — including `docker compose up postgres` — then Day 1 scaffold
(Olist→Postgres loader, store-ops MCP server, policy ingestion, FastAPI + LangGraph + NIM
skeleton with the 20-scenario tool-calling smoke test).
