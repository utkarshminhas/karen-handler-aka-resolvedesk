"""Contract tests for the API skeleton (stub backend). Every UI flow is exercised here."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from resolvedesk.api import backend as backend_mod
from resolvedesk.api.main import app


@pytest.fixture
def client(monkeypatch):
    # Fresh in-memory state per test
    monkeypatch.setattr(backend_mod, "_backend", backend_mod.StubBackend())
    return TestClient(app)


def start(client, customer_id="demo-ana"):
    r = client.post("/conversations", json={"customer_id": customer_id})
    assert r.status_code == 201
    return r.json()["conversation_id"]


def say(client, cid, text):
    r = client.post(f"/conversations/{cid}/messages", json={"content": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_customers(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["backend"] == "stub" and h["store_now"].startswith("2018-08-15")
    assert len(client.get("/customers").json()) == 3


def test_order_status_turn(client):
    cid = start(client)
    t = say(client, cid, "Where is my order?")
    assert t["status"] == "active" and t["pending"] is None
    assert [a["node"] for a in t["actions"]] == ["triage", "order_status"]
    assert "A102" in t["reply"]


def test_refund_confirm_yes(client):
    cid = start(client)
    t = say(client, cid, "My headphones arrived broken, I want a refund")
    p = t["pending"]
    assert p and p["order_id"] == "A102" and p["amount"] == "149.90"
    assert "stage_refund" in [a["tool"] for a in t["actions"]]

    blocked = say(client, cid, "hello?")             # chatting while a confirm is pending
    assert blocked["pending"] is not None

    r = client.post(f"/conversations/{cid}/confirm", json={"proposal_id": p["proposal_id"], "decision": "yes"})
    t2 = r.json()
    assert r.status_code == 200 and t2["pending"] is None
    assert [a["node"] for a in t2["actions"]] == ["confirm", "verifier", "executor"]
    assert t2["actions"][-1]["tool"] == "commit_refund"


def test_refund_confirm_no_expires(client):
    cid = start(client)
    p = say(client, cid, "refund please")["pending"]
    t = client.post(f"/conversations/{cid}/confirm", json={"proposal_id": p["proposal_id"], "decision": "no"}).json()
    assert t["actions"][-1]["result"]["status"] == "expired"


def test_confirm_without_pending_is_409(client):
    cid = start(client)
    r = client.post(f"/conversations/{cid}/confirm", json={"proposal_id": str(uuid4()), "decision": "yes"})
    assert r.status_code == 409


def test_large_refund_escalates_then_staff_approves(client):
    cid = start(client, "demo-carla")
    p = say(client, cid, "refund my order")["pending"]
    t = client.post(f"/conversations/{cid}/confirm", json={"proposal_id": p["proposal_id"], "decision": "yes"}).json()
    assert t["status"] == "escalated"

    holding = say(client, cid, "any news?")
    assert holding["actions"][0]["node"] == "holding"

    queue = client.get("/admin/escalations", params={"status": "pending"}).json()
    assert len(queue) == 1
    eid = queue[0]["escalation_id"]
    d = client.post(f"/admin/escalations/{eid}/decision", json={"decision": "approve", "staff_id": "staff-1"})
    assert d.status_code == 200 and d.json()["status"] == "approved"
    assert client.post(f"/admin/escalations/{eid}/decision",
                       json={"decision": "reject", "staff_id": "staff-1"}).status_code == 409

    conv = client.get(f"/conversations/{cid}").json()
    assert conv["status"] == "resolved"
    assert conv["actions"][-1]["node"] == "executor"
    assert "approved" in conv["messages"][-1]["content"]


def test_topic_guard(client):
    cid = start(client)
    say(client, cid, "where is my order")
    steer = say(client, cid, "also I want a refund")
    assert steer["actions"][0]["result"]["route"] == "new_issue"
    off = say(client, cid, "write me a poem")
    assert off["actions"][0]["result"]["route"] == "out_of_scope"


def test_close_then_message_is_409(client):
    cid = start(client)
    assert client.post(f"/conversations/{cid}/close").json()["status"] == "resolved"
    r = client.post(f"/conversations/{cid}/messages", json={"content": "hi"})
    assert r.status_code == 409


def test_not_found_and_validation(client):
    assert client.post("/conversations", json={"customer_id": "nobody"}).status_code == 404
    assert client.get(f"/conversations/{uuid4()}").status_code == 404
    cid = start(client)
    assert client.post(f"/conversations/{cid}/messages", json={"content": ""}).status_code == 422
