"""ResolveDesk chat UI. Run: streamlit run src/resolvedesk/ui/streamlit_app.py

Talks only to the FastAPI backend (RESOLVEDESK_API_URL, default http://localhost:8000).
Skeleton: functional over pretty (plan §11 Day 3 / §13 #11).
"""

import json
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import streamlit as st

from resolvedesk.config import get_settings
from resolvedesk.ui.api_client import ApiClient, ApiError

st.set_page_config(page_title="ResolveDesk", layout="wide")

STATUS_COLOR = {"active": "blue", "resolved": "green", "escalated": "orange"}


@st.cache_resource
def client(base_url: str) -> ApiClient:
    return ApiClient(base_url)


def call(fn: Callable[..., Any], *args: Any) -> Any:
    """Run an API call; show the error in the page instead of crashing."""
    try:
        return fn(*args)
    except ApiError as e:
        st.error(str(e))
        return None


def fmt_action(a: dict) -> str:
    line = f"**{a['node']}**"
    if a.get("tool"):
        args = ", ".join(f"{k}={v}" for k, v in a["args"].items())
        line += f" · `{a['tool']}({args})`"
    if a.get("result"):
        res = json.dumps(a["result"], ensure_ascii=False)
        line += f" → {res if len(res) <= 120 else res[:117] + '...'}"
    return line


# --- sidebar: connection + persona + conversation controls --------------------------------

api_url = st.sidebar.text_input("API URL", value=get_settings().resolvedesk_api_url)
api = client(api_url)

health = call(api.health)
if health:
    st.sidebar.caption(f"API ok · backend: **{health['backend']}** · store date: {health['store_now'][:10]}")
else:
    st.stop()

customers = call(api.customers) or []
by_id = {c["customer_id"]: c for c in customers}
customer_id = st.sidebar.selectbox(
    "Chat as", options=list(by_id), format_func=lambda cid: by_id[cid]["name"], key="customer_id")
if customer_id and by_id[customer_id].get("persona_note"):
    st.sidebar.caption(by_id[customer_id]["persona_note"])

# A different persona means a different conversation
if st.session_state.get("conv_customer") != customer_id:
    st.session_state.pop("conversation_id", None)

if st.sidebar.button("New conversation", type="primary", width="stretch", disabled=not customer_id):
    conv = call(api.start_conversation, customer_id)
    if conv:
        st.session_state.conversation_id = conv["conversation_id"]
        st.session_state.conv_customer = customer_id
        st.rerun()

conversation_id = st.session_state.get("conversation_id")
if conversation_id and st.sidebar.button("Close conversation", width="stretch"):
    call(api.close, conversation_id)
    st.rerun()

st.sidebar.button("Refresh", width="stretch")  # any click reruns and re-polls the API

# --- main --------------------------------------------------------------------------------

st.title("ResolveDesk")
chat_tab, staff_tab = st.tabs(["Customer chat", "Staff queue"])

with chat_tab:
    if not conversation_id:
        st.info("Pick a persona in the sidebar and start a new conversation.")
    else:
        conv = call(api.conversation, conversation_id)
        if conv:
            chat_col, actions_col = st.columns([3, 2], gap="large")

            with chat_col:
                st.badge(conv["status"], color=STATUS_COLOR[conv["status"]])
                if conv.get("reference_id"):
                    st.caption(f"Reference: {conv['reference_id']}")
                for m in conv["messages"]:
                    with st.chat_message(m["role"]):
                        st.write(m["content"])

                pending = conv.get("pending")
                if pending:
                    with st.container(border=True):
                        st.write(pending["prompt"])
                        yes, no, _ = st.columns([1, 1, 4])
                        if yes.button("Yes, refund", type="primary"):
                            call(api.confirm, conversation_id, pending["proposal_id"], "yes")
                            st.rerun()
                        if no.button("No"):
                            call(api.confirm, conversation_id, pending["proposal_id"], "no")
                            st.rerun()

                if conv["status"] == "resolved":
                    st.caption("This conversation is closed. Start a new one for another issue.")
                elif prompt := st.chat_input("Describe your issue", disabled=bool(pending)):
                    call(api.send_message, conversation_id, prompt)
                    st.rerun()

            with actions_col:
                st.subheader("Actions")
                if not conv["actions"]:
                    st.caption("Nothing yet. Every lookup, check and decision will show here.")
                by_turn: dict[int, list[dict]] = defaultdict(list)
                for a in conv["actions"]:
                    by_turn[a["turn_no"]].append(a)
                for turn in sorted(by_turn, reverse=True):
                    st.markdown(f"**Turn {turn}**")
                    for a in by_turn[turn]:
                        st.markdown(f"- {fmt_action(a)}")
                with st.expander("Raw actions JSON"):
                    st.json(conv["actions"])

with staff_tab:
    st.caption("Simulated staff console. Approving a refund resumes the customer's conversation.")
    staff_id = st.text_input("Staff ID", value="staff-1", key="staff_id")
    show = st.radio("Show", ["pending", "all"], horizontal=True)
    escalations = call(api.escalations, "pending" if show == "pending" else None) or []
    if not escalations:
        st.info("No escalations to show.")
    for e in escalations:
        with st.container(border=True):
            amount = f" · R$ {e['amount']}" if e.get("amount") else ""
            st.markdown(f"**{e['reference_id']}** · {e['customer_id']}{amount} · status: {e['status']}")
            st.write(e["reason"])
            st.caption(e["summary"])
            if e["status"] == "pending":
                a, r, _ = st.columns([1, 1, 4])
                if a.button("Approve", key=f"approve-{e['escalation_id']}", type="primary"):
                    call(api.decide, e["escalation_id"], "approve", staff_id)
                    st.rerun()
                if r.button("Reject", key=f"reject-{e['escalation_id']}"):
                    call(api.decide, e["escalation_id"], "reject", staff_id)
                    st.rerun()
            elif e.get("decided_by"):
                st.caption(f"Decided by {e['decided_by']} at {e['decided_ts'][:19]}")
