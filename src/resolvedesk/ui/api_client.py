"""Thin HTTP client for the ResolveDesk API. The UI never touches the database directly."""

from typing import Any

import httpx


class ApiError(Exception):
    """Raised with a readable message when the API is down or rejects a request."""


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise ApiError(f"Can't reach the API at {self.base_url} ({e.__class__.__name__}). "
                           "Is uvicorn running?") from e
        if r.is_error:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ApiError(f"API error {r.status_code}: {detail}")
        return r.json()

    def health(self) -> dict:
        return self._req("GET", "/health")

    def customers(self) -> list[dict]:
        return self._req("GET", "/customers")

    def start_conversation(self, customer_id: str) -> dict:
        return self._req("POST", "/conversations", json={"customer_id": customer_id})

    def send_message(self, conversation_id: str, content: str) -> dict:
        return self._req("POST", f"/conversations/{conversation_id}/messages", json={"content": content})

    def confirm(self, conversation_id: str, proposal_id: str, decision: str) -> dict:
        return self._req("POST", f"/conversations/{conversation_id}/confirm",
                         json={"proposal_id": proposal_id, "decision": decision})

    def close(self, conversation_id: str) -> dict:
        return self._req("POST", f"/conversations/{conversation_id}/close")

    def conversation(self, conversation_id: str) -> dict:
        return self._req("GET", f"/conversations/{conversation_id}")

    def escalations(self, status: str | None = None) -> list[dict]:
        return self._req("GET", "/admin/escalations", params={"status": status} if status else None)

    def decide(self, escalation_id: str, decision: str, staff_id: str, note: str | None = None) -> dict:
        return self._req("POST", f"/admin/escalations/{escalation_id}/decision",
                         json={"decision": decision, "staff_id": staff_id, "note": note})
