"""API contract (plan §4). The stub backend and, later, the real graph both speak these models."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ConversationStatus = Literal["active", "resolved", "escalated"]
EscalationStatus = Literal["pending", "approved", "rejected"]


# --- shared pieces -----------------------------------------------------------------------

class Customer(BaseModel):
    customer_id: str
    name: str
    email: str
    persona_note: str | None = None


class Action(BaseModel):
    """One step the system took. Feeds the UI actions panel, the eval scorer and cost report."""
    turn_no: int
    seq: int
    node: str                      # triage | order_status | refund | product_qa | verifier | executor | ...
    tool: str | None = None        # MCP tool name, if a tool was called
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    created_ts: datetime


class Message(BaseModel):
    turn_no: int
    role: Literal["user", "assistant"]
    content: str
    created_ts: datetime


class PendingConfirm(BaseModel):
    """Set when the graph is paused at CONFIRM, waiting for the customer's yes/no."""
    kind: Literal["confirm_refund"] = "confirm_refund"
    proposal_id: UUID
    order_id: str
    amount: Decimal
    currency: Literal["BRL"] = "BRL"
    prompt: str


# --- requests ----------------------------------------------------------------------------

class StartConversationRequest(BaseModel):
    customer_id: str


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class ConfirmRequest(BaseModel):
    proposal_id: UUID
    decision: Literal["yes", "no"]


class EscalationDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    staff_id: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


# --- responses ---------------------------------------------------------------------------

class ConversationSummary(BaseModel):
    conversation_id: UUID
    customer_id: str
    status: ConversationStatus
    intent: str | None = None
    started_ts: datetime


class TurnResponse(BaseModel):
    """What every turn returns: the reply, the actions taken this turn, and where things stand."""
    conversation_id: UUID
    turn_no: int
    reply: str
    actions: list[Action]
    status: ConversationStatus
    pending: PendingConfirm | None = None


class ConversationDetail(ConversationSummary):
    ended_ts: datetime | None = None
    messages: list[Message]
    actions: list[Action]
    pending: PendingConfirm | None = None
    reference_id: str | None = None     # set when escalated


class Escalation(BaseModel):
    escalation_id: UUID
    reference_id: str
    conversation_id: UUID
    customer_id: str
    reason: str
    summary: str
    amount: Decimal | None = None
    status: EscalationStatus
    created_ts: datetime
    decided_ts: datetime | None = None
    decided_by: str | None = None


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    backend: Literal["stub", "graph"]
    store_now: datetime
