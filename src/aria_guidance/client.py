"""Small HTTP client shared by replay scripts and the ROS bridge."""

import os
from uuid import uuid4

import httpx


class GuidanceClient:
    def __init__(self, url="http://127.0.0.1:8785", token=None, timeout=120):
        token = token if token is not None else os.getenv("ARIA_API_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(
            base_url=url.rstrip("/") + "/", headers=headers, timeout=timeout, trust_env=False
        )

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        if response.is_error:
            # Keep the server's reason (e.g. stale frames) instead of a bare "422" status line.
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise httpx.HTTPStatusError(
                f"{response.status_code}: {detail}", request=response.request, response=response
            )
        return response.json()

    def create_session(self, goal, source="aria", task_id=""):
        return self.request(
            "POST", "api/sessions", json={"goal": goal, "source": source, "task_id": task_id}
        )

    def plan(self, session_id, frames, request_id=None):
        return self.request(
            "POST",
            f"api/sessions/{session_id}/plan",
            json={
                "request_id": request_id or str(uuid4()),
                "frames": frames,
            },
        )

    def plan_v1(self, goal, history, frames, live=True):
        return self.request(
            "POST",
            "v1/plan",
            json={"goal": goal, "history": history, "frames": frames, "live": live},
        )

    def feedback(self, session_id, event="done", text=""):
        return self.request(
            "POST", f"api/sessions/{session_id}/feedback", json={"event": event, "text": text}
        )

    def event(self, session_id, **payload):
        return self.request("POST", f"api/sessions/{session_id}/events", json=payload)

    def transcribe(self, wav):
        return self.request("POST", "v1/asr", files={"file": ("speech.wav", wav, "audio/wav")})

    def close(self):
        self.http.close()
