import time

import pytest
from fastapi.testclient import TestClient
from stubs import StubEngine
from test_service_freshness import frames

from aria_guidance.config import Settings
from aria_guidance.service import create_app


@pytest.fixture
def engine():
    return StubEngine()


@pytest.fixture
def client(tmp_path, engine, monkeypatch):
    monkeypatch.setenv("ARIA_MAX_FRAME_AGE_MS", "5000")
    app = create_app(Settings(data_dir=tmp_path, save_frames=False), engine, load_model=False)
    return TestClient(app)


def plan(client, newest_ms=None, **body):
    newest_ms = time.time() * 1000 - 300 if newest_ms is None else newest_ms
    body = {"goal": "Clear the mat", "frames": frames(newest_ms), **body}
    return client.post("/v1/plan", json=body)


def test_plan_uses_caller_history(client, engine):
    history = [{"role": "assistant", "text": "Pick up the pen."}, {"role": "user", "text": "done"}]
    response = plan(client, history=history)
    assert response.status_code == 200, response.text
    assert response.json()["instruction"] == "Pick up the pen."
    assert response.json()["status"] == "instruction"
    assert engine.calls[0]["history"] == history


def test_done_is_a_proposal(client, engine):
    engine.reply = "DONE"
    assert plan(client).json()["status"] == "done_proposed"


def test_busy_model_returns_429(client, engine):
    engine.busy = True
    assert plan(client).status_code == 429


def test_loading_model_returns_503(client, engine):
    engine.state = "loading"
    assert plan(client).status_code == 503


def test_failed_inference_returns_503_with_reason(client, engine):
    engine.reply = ValueError("Model echoed the goal instead of answering")
    response = plan(client)
    assert response.status_code == 503
    assert "Model echoed the goal" in response.json()["detail"]


def test_stale_live_frames_return_422(client):
    response = plan(client, newest_ms=time.time() * 1000 - 11_400)
    assert response.status_code == 422
    assert "11.4 s old" in response.json()["detail"]


def test_recorded_frames_skip_age_check(client):
    assert plan(client, newest_ms=10_000, live=False).status_code == 200


def test_bad_history_role_is_rejected(client):
    assert plan(client, history=[{"role": "system", "text": "hi"}]).status_code == 422


def test_asr_route_reaches_transcribe(client):
    # No ASR model is configured, so the handler itself answers 503.
    response = client.post("/v1/asr", files={"file": ("a.wav", b"RIFF", "audio/wav")})
    assert response.status_code in {422, 503}
    assert response.status_code != 404
