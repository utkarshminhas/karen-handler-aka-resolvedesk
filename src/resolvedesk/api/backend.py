"""Backend interface + an in-memory STUB implementation.

The stub fakes the graph with keyword routing and canned data so every UI state (clarify,
confirm, escalate, refusal) can be clicked through before any agent, MCP server or database
exists. It makes no network or DB calls. Swap it for the graph backend in `get_backend()`.
"""

import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from resolvedesk.api.schemas import (
    Action,
    ConversationDetail,
    ConversationStatus,
    ConversationSummary,
    Customer,
    Escalation,
    Message,
    PendingConfirm,
    TurnResponse,
)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Backend(Protocol):
    name: str

    def list_customers(self) -> list[Customer]: ...
    def start_conversation(self, customer_id: str) -> ConversationSummary: ...
    def send_message(self, conversation_id: UUID, content: str) -> TurnResponse: ...
    def confirm(self, conversation_id: UUID, proposal_id: UUID, decision: str) -> TurnResponse: ...
    def close(self, conversation_id: UUID) -> ConversationSummary: ...
    def get_conversation(self, conversation_id: UUID) -> ConversationDetail: ...
    def list_escalations(self, status: str | None) -> list[Escalation]: ...
    def decide_escalation(self, escalation_id: UUID, decision: str, staff_id: str,
                          note: str | None) -> Escalation: ...


# ------------------------------------------------------------------------------------------
# Stub implementation
# ------------------------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(UTC)


# Placeholder personas until scripts/make_fixtures.py maps real Olist repeat buyers.
STUB_CUSTOMERS = [
    Customer(customer_id="demo-ana", name="Ana Souza", email="ana@example.com",
             persona_note="[stub] Repeat buyer, 2 recent orders, one arrived damaged"),
    Customer(customer_id="demo-bruno", name="Bruno Lima", email="bruno@example.com",
             persona_note="[stub] One order still in transit"),
    Customer(customer_id="demo-carla", name="Carla Mendes", email="carla@example.com",
             persona_note="[stub] Wants a large refund that needs staff approval"),
]

STUB_ORDERS = {
    "demo-ana": [{"order_id": "A102", "status": "delivered", "total": "149.90", "delivered": "2018-08-03"},
                 {"order_id": "A087", "status": "delivered", "total": "59.00", "delivered": "2018-06-21"}],
    "demo-bruno": [{"order_id": "B311", "status": "shipped", "total": "320.40", "estimated": "2018-08-20"}],
    "demo-carla": [{"order_id": "C455", "status": "delivered", "total": "2899.00", "delivered": "2018-08-10"}],
}

REFUND_WORDS = ("refund", "money back", "broken", "damaged", "return")
ORDER_WORDS = ("order", "where", "track", "deliver", "arrive", "shipping")
PRODUCT_WORDS = ("product", "gift", "recommend", "looking for", "price of")
HUMAN_WORDS = ("human", "agent", "manager", "escalate", "speak to")
STAFF_REVIEW_LIMIT = Decimal("1000.00")


@dataclass
class _Conversation:
    conversation_id: UUID
    customer_id: str
    started_ts: datetime
    status: ConversationStatus = "active"
    intent: str | None = None
    ended_ts: datetime | None = None
    turn_no: int = 0
    messages: list[Message] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    pending: PendingConfirm | None = None
    reference_id: str | None = None

    def summary(self) -> ConversationSummary:
        return ConversationSummary(conversation_id=self.conversation_id, customer_id=self.customer_id,
                                   status=self.status, intent=self.intent, started_ts=self.started_ts)


class StubBackend:
    name = "stub"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._customers = {c.customer_id: c for c in STUB_CUSTOMERS}
        self._conversations: dict[UUID, _Conversation] = {}
        self._escalations: dict[UUID, Escalation] = {}

    # --- helpers -------------------------------------------------------------------------

    def _conv(self, conversation_id: UUID) -> _Conversation:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            raise NotFound(f"conversation {conversation_id} not found")
        return conv

    @staticmethod
    def _act(conv: _Conversation, out: list[Action], node: str, tool: str | None = None,
             args: dict[str, Any] | None = None, result: dict[str, Any] | None = None) -> None:
        a = Action(turn_no=conv.turn_no, seq=len(out), node=node, tool=tool, args=args or {},
                   result=result or {}, created_ts=_now())
        out.append(a)
        conv.actions.append(a)

    @staticmethod
    def _say(conv: _Conversation, role: str, content: str) -> None:
        conv.messages.append(Message(turn_no=conv.turn_no, role=role, content=content, created_ts=_now()))

    def _turn(self, conv: _Conversation, reply: str, actions: list[Action]) -> TurnResponse:
        self._say(conv, "assistant", reply)
        return TurnResponse(conversation_id=conv.conversation_id, turn_no=conv.turn_no, reply=reply,
                            actions=actions, status=conv.status, pending=conv.pending)

    def _escalate(self, conv: _Conversation, out: list[Action], reason: str,
                  amount: Decimal | None = None) -> str:
        ref = f"RD-{secrets.token_hex(3).upper()}"
        esc = Escalation(escalation_id=uuid4(), reference_id=ref, conversation_id=conv.conversation_id,
                         customer_id=conv.customer_id, reason=reason,
                         summary=f"[stub summary] Customer {conv.customer_id}: {reason}.",
                         amount=amount, status="pending", created_ts=_now())
        self._escalations[esc.escalation_id] = esc
        conv.status, conv.reference_id, conv.pending = "escalated", ref, None
        self._act(conv, out, "escalate", result={"reference_id": ref, "reason": reason})
        return ref

    # --- Backend API ---------------------------------------------------------------------

    def list_customers(self) -> list[Customer]:
        return list(self._customers.values())

    def start_conversation(self, customer_id: str) -> ConversationSummary:
        with self._lock:
            if customer_id not in self._customers:
                raise NotFound(f"customer {customer_id} not found")
            conv = _Conversation(conversation_id=uuid4(), customer_id=customer_id, started_ts=_now())
            self._conversations[conv.conversation_id] = conv
            return conv.summary()

    def send_message(self, conversation_id: UUID, content: str) -> TurnResponse:
        with self._lock:
            conv = self._conv(conversation_id)
            if conv.status == "resolved":
                raise Conflict("conversation is closed; start a new one")
            conv.turn_no += 1
            self._say(conv, "user", content)
            out: list[Action] = []

            if conv.status == "escalated":
                self._act(conv, out, "holding")
                return self._turn(conv, f"Your case {conv.reference_id} is with our team. We'll update "
                                        "this chat as soon as they decide.", out)
            if conv.pending:
                self._act(conv, out, "confirm", result={"waiting_for": "yes/no"})
                return self._turn(conv, "Please use the Yes or No buttons to confirm the refund first.", out)

            text = content.lower()
            orders = STUB_ORDERS.get(conv.customer_id, [])
            route = ("refund" if any(w in text for w in REFUND_WORDS) else
                     "escalate" if any(w in text for w in HUMAN_WORDS) else
                     "order_status" if any(w in text for w in ORDER_WORDS) else
                     "product_qa" if any(w in text for w in PRODUCT_WORDS) else "out_of_scope")

            if conv.intent and route not in (conv.intent, "escalate", "out_of_scope"):
                self._act(conv, out, "triage", result={"route": "new_issue", "current": conv.intent})
                return self._turn(conv, f"Let's finish your {conv.intent.replace('_', ' ')} question first. "
                                        "You can open a new chat for the other issue afterwards.", out)

            self._act(conv, out, "triage", result={"route": route})
            if route == "out_of_scope":
                return self._turn(conv, "I can help with orders, refunds and product questions. "
                                        "Is there something like that I can do for you?", out)
            conv.intent = conv.intent or route

            if route == "escalate":
                ref = self._escalate(conv, out, "customer asked for a human")
                return self._turn(conv, f"I've passed this to our team. Your reference is {ref}.", out)

            if route == "order_status":
                self._act(conv, out, "order_status", "get_customer_orders",
                          {"customer_id": "<session>"}, {"orders": orders})
                lines = "; ".join(f"{o['order_id']} is {o['status']}" for o in orders) or "no orders found"
                return self._turn(conv, f"Here's what I see: {lines}.", out)

            if route == "product_qa":
                self._act(conv, out, "product_qa", "search_products", {"query": content, "k": 3},
                          {"product_ids": ["p-001", "p-002"]})
                self._act(conv, out, "product_qa", "get_product_details", {"product_id": "p-001"},
                          {"name": "[stub] Wooden toy train", "price": "89.90"})
                return self._turn(conv, "[stub] A wooden toy train (R$ 89.90) is a popular pick.", out)

            # refund
            self._act(conv, out, "refund", "get_customer_orders", {"customer_id": "<session>"},
                      {"orders": orders})
            if not orders:
                return self._turn(conv, "I couldn't find any orders on your account.", out)
            order = orders[0]
            amount = Decimal(order["total"])
            self._act(conv, out, "refund", "check_refund_eligibility", {"order_id": order["order_id"]},
                      {"eligible": True, "max_amount": str(amount)})
            self._act(conv, out, "refund", "search_policy", {"query": "damaged item refund"},
                      {"sections": ["damaged-items §2"]})
            proposal_id = uuid4()
            self._act(conv, out, "refund", "stage_refund",
                      {"order_id": order["order_id"], "amount": str(amount)},
                      {"proposal_id": str(proposal_id), "status": "staged"})
            prompt = f"Shall I refund R$ {amount} for order {order['order_id']}?"
            conv.pending = PendingConfirm(proposal_id=proposal_id, order_id=order["order_id"],
                                          amount=amount, prompt=prompt)
            self._act(conv, out, "confirm", result={"waiting_for": "yes/no"})
            return self._turn(conv, prompt, out)

    def confirm(self, conversation_id: UUID, proposal_id: UUID, decision: str) -> TurnResponse:
        with self._lock:
            conv = self._conv(conversation_id)
            p = conv.pending
            if p is None or p.proposal_id != proposal_id:
                raise Conflict("no matching refund is waiting for confirmation")
            conv.turn_no += 1
            self._say(conv, "user", "Yes" if decision == "yes" else "No")
            out: list[Action] = []
            self._act(conv, out, "confirm", result={"decision": decision, "proposal_id": str(proposal_id)})
            conv.pending = None

            if decision == "no":
                self._act(conv, out, "refund", "expire_refund", {"proposal_id": str(proposal_id)},
                          {"status": "expired"})
                return self._turn(conv, "No problem, I haven't refunded anything. "
                                        "Is there anything else about this order?", out)

            if p.amount > STAFF_REVIEW_LIMIT:
                self._act(conv, out, "verifier", result={"verdict": "escalate",
                                                         "why": f"amount over R$ {STAFF_REVIEW_LIMIT}"})
                ref = self._escalate(conv, out, f"refund of R$ {p.amount} needs staff approval", p.amount)
                return self._turn(conv, f"This refund needs a quick check by our team. Reference {ref}. "
                                        "You'll see the outcome here.", out)

            self._act(conv, out, "verifier", result={"verdict": "approve",
                                                     "checks": ["amount_cap", "return_window", "ownership"]})
            self._act(conv, out, "executor", "commit_refund", {"proposal_id": str(proposal_id)},
                      {"status": "issued", "refund_id": "RF-STUB"})
            return self._turn(conv, f"Done. I've refunded R$ {p.amount} for order {p.order_id}. "
                                    "It should reach you in 5 to 7 days.", out)

    def close(self, conversation_id: UUID) -> ConversationSummary:
        with self._lock:
            conv = self._conv(conversation_id)
            if conv.status != "escalated":
                conv.status = "resolved"
            conv.ended_ts = conv.ended_ts or _now()
            conv.pending = None
            return conv.summary()

    def get_conversation(self, conversation_id: UUID) -> ConversationDetail:
        with self._lock:
            c = self._conv(conversation_id)
            return ConversationDetail(**c.summary().model_dump(), ended_ts=c.ended_ts,
                                      messages=list(c.messages), actions=list(c.actions),
                                      pending=c.pending, reference_id=c.reference_id)

    def list_escalations(self, status: str | None) -> list[Escalation]:
        with self._lock:
            items = sorted(self._escalations.values(), key=lambda e: e.created_ts, reverse=True)
            return [e for e in items if status is None or e.status == status]

    def decide_escalation(self, escalation_id: UUID, decision: str, staff_id: str,
                          note: str | None) -> Escalation:
        with self._lock:
            esc = self._escalations.get(escalation_id)
            if esc is None:
                raise NotFound(f"escalation {escalation_id} not found")
            if esc.status != "pending":
                raise Conflict(f"escalation already {esc.status}")
            esc = esc.model_copy(update={"status": "approved" if decision == "approve" else "rejected",
                                         "decided_ts": _now(), "decided_by": staff_id})
            self._escalations[escalation_id] = esc

            # Resume the conversation: approvals go through the same executor path.
            conv = self._conversations[esc.conversation_id]
            conv.turn_no += 1
            out: list[Action] = []
            self._act(conv, out, "staff_decision", args={"staff_id": staff_id, "note": note},
                      result={"decision": decision})
            if decision == "approve" and esc.amount is not None:
                self._act(conv, out, "executor", "commit_refund", {"reference_id": esc.reference_id},
                          {"status": "issued", "refund_id": "RF-STUB"})
                msg = f"Good news: our team approved your refund of R$ {esc.amount}. It's on its way."
            elif decision == "approve":
                msg = "Our team has reviewed your case and will contact you by email."
            else:
                msg = "Our team reviewed your case and couldn't approve it this time. " \
                      "You'll get an email with the details."
            self._say(conv, "assistant", msg)
            conv.status, conv.ended_ts = "resolved", _now()
            return esc


_backend: Backend = StubBackend()


def get_backend() -> Backend:
    return _backend
