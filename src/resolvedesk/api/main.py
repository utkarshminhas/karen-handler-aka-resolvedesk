"""ResolveDesk API (plan §4). Run: uvicorn resolvedesk.api.main:app --reload --port 8000"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from resolvedesk import __version__
from resolvedesk.api.backend import Backend, Conflict, NotFound, get_backend
from resolvedesk.api.schemas import (
    ConfirmRequest,
    ConversationDetail,
    ConversationSummary,
    Customer,
    Escalation,
    EscalationDecisionRequest,
    Health,
    SendMessageRequest,
    StartConversationRequest,
    TurnResponse,
)
from resolvedesk.config import get_settings

app = FastAPI(title="ResolveDesk API", version=__version__)

BackendDep = Annotated[Backend, Depends(get_backend)]


@app.exception_handler(NotFound)
async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(Conflict)
async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/health", response_model=Health)
def health(backend: BackendDep) -> Health:
    return Health(version=__version__, backend=backend.name, store_now=get_settings().store_now)


@app.get("/customers", response_model=list[Customer])
def list_customers(backend: BackendDep) -> list[Customer]:
    """Demo personas (no auth in the PoC)."""
    return backend.list_customers()


@app.post("/conversations", response_model=ConversationSummary, status_code=201)
def start_conversation(body: StartConversationRequest, backend: BackendDep) -> ConversationSummary:
    return backend.start_conversation(body.customer_id)


@app.post("/conversations/{conversation_id}/messages", response_model=TurnResponse)
def send_message(conversation_id: UUID, body: SendMessageRequest, backend: BackendDep) -> TurnResponse:
    """One customer turn."""
    return backend.send_message(conversation_id, body.content)


@app.post("/conversations/{conversation_id}/confirm", response_model=TurnResponse)
def confirm(conversation_id: UUID, body: ConfirmRequest, backend: BackendDep) -> TurnResponse:
    """Structured yes/no that resumes the CONFIRM interrupt."""
    return backend.confirm(conversation_id, body.proposal_id, body.decision)


@app.post("/conversations/{conversation_id}/close", response_model=ConversationSummary)
def close(conversation_id: UUID, backend: BackendDep) -> ConversationSummary:
    return backend.close(conversation_id)


@app.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: UUID, backend: BackendDep) -> ConversationDetail:
    """History + actions; the UI polls this."""
    return backend.get_conversation(conversation_id)


@app.get("/admin/escalations", response_model=list[Escalation])
def list_escalations(
    backend: BackendDep,
    status: Annotated[Literal["pending", "approved", "rejected"] | None, Query()] = None,
) -> list[Escalation]:
    return backend.list_escalations(status)


@app.post("/admin/escalations/{escalation_id}/decision", response_model=Escalation)
def decide_escalation(escalation_id: UUID, body: EscalationDecisionRequest,
                      backend: BackendDep) -> Escalation:
    """Simulated staff decision; resumes the conversation (approvals go through the executor)."""
    return backend.decide_escalation(escalation_id, body.decision, body.staff_id, body.note)

