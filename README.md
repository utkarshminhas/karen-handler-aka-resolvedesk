# ResolveDesk

A customer-facing, multi-agent support **chat** for e-commerce — built as a 5-day personal
showcase project targeting GenAI Architect / GenAI SDE-3 interviews.

A customer opens the chat, describes a problem in their own words, and the system resolves it
conversationally: it triages intent, asks clarifying questions, looks up the real order,
checks store policy, asks for confirmation before refunding money, commits the refund through a deterministic (non-LLM) executor
against the store database through a custom MCP server — and shows everything it did in an
actions panel. A verifier agent checks policy compliance before any mutation commits, and
over-policy cases land in a human-review queue.

One conversation = one issue, resolved over multiple turns.

## Stack

- **Agents/orchestration:** LangChain + LangGraph (supervisor graph, checkpointer per thread,
  interrupts for clarify/confirm turns)
- **LLM:** hosted NVIDIA NIM API, two-tier (8B triage/summary, 70B specialist/verifier)
- **Backend:** FastAPI · **UI:** Streamlit (chat pane + actions panel)
- **Data:** PostgreSQL 16 + pgvector (store data, app data, checkpoints, vectors in one
  instance) · **MCP:** custom Python store-ops server
- **Evals:** Bitext (triage), goal-driven simulated-user scenarios over fixture orders (action correctness), synthetic
  adversarial set (injection, off-topic, policy edge cases), pgvector embedding comparison
- **Ops:** Docker / docker-compose, LangSmith or Langfuse tracing, GitHub Action regression
  on a smoke set

## Getting started

```bash
cp .env.example .env               # then set passwords + NVIDIA_API_KEY
docker compose up -d               # Postgres 16 + pgvector on localhost:5433
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt -r requirements-dev.txt
uv pip install -e .                # makes src/resolvedesk importable
```

Run the app (two terminals):

```bash
uvicorn resolvedesk.api.main:app --reload --port 8000     # API, docs at /docs
streamlit run src/resolvedesk/ui/streamlit_app.py         # UI at http://localhost:8501
pytest                                                    # API contract tests
```

The API currently runs a **stub backend** (`src/resolvedesk/api/backend.py`): keyword routing
and canned data, no LLM, MCP or database calls. It will be replaced by the LangGraph backend
behind the same `Backend` interface.

Data (raw files land in `data/`, git-ignored):

```bash
python scripts/download_data.py    # Olist (Kaggle) + Bitext (Hugging Face); --abcd optional
python scripts/load_olist.py       # Olist -> store zone, as-of STORE_NOW
python scripts/prepare_bitext.py   # Bitext -> evals/datasets/bitext_sample.jsonl
```

Dependencies: edit `requirements.in` / `requirements-dev.in`, then re-lock with
`uv pip compile requirements.in --python-version 3.12 -o requirements.txt`.

The database schema lives in `db/init/` and runs only on a fresh volume. To rebuild it
from scratch: `docker compose down -v && docker compose up -d`.

Zones are Postgres schemas with their own roles: `store_ops` (MCP server only) can read the
store and write only `refunds`; `resolvedesk_app` (API/graph) has no access to `store` at all.

## Documentation

The full product plan — architecture, data-layer design, turn lifecycle, guardrails, eval
strategy, five-day build plan, and pre-mortem — lives in
**[resolvedesk-project-plan.md](resolvedesk-project-plan.md)** (v2.1). That file is the single
source of truth; this README stays intentionally short so the two never drift apart.
