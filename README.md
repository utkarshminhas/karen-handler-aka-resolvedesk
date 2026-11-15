# ResolveDesk

A customer-facing, multi-agent support **chat** for e-commerce — built as a 5-day personal
showcase project targeting GenAI Architect / GenAI SDE-3 interviews.

A customer opens the chat, describes a problem in their own words, and the system resolves it
conversationally: it triages intent, asks clarifying questions, looks up the real order,
checks store policy, asks for confirmation before refunding money, executes the action
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
- **Evals:** Bitext (triage), ABCD multi-turn replay (action correctness), synthetic
  adversarial set (injection, off-topic, policy edge cases), pgvector embedding comparison
- **Ops:** Docker / docker-compose, LangSmith or Langfuse tracing, GitHub Action regression
  on a smoke set

## Documentation

The full product plan — architecture, data-layer design, turn lifecycle, guardrails, eval
strategy, five-day build plan, and pre-mortem — lives in
**[resolvedesk-project-plan.md](resolvedesk-project-plan.md)** (v2). That file is the single
source of truth; this README stays intentionally short so the two never drift apart.
