import base64
import io
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from stubs import StubEngine

from aria_guidance.config import Settings
from aria_guidance.service import create_app


def frames(newest_ms):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buffer, "JPEG")
    image = base64.b64encode(buffer.getvalue()).decode("ascii")
    return [{"timestamp_ms": newest_ms - (19 - i) * 500, "image_base64": image} for i in range(20)]


@pytest.fixture
def client(tmp_path, monkeypatch):
    def make(max_age_ms=5000):
        monkeypatch.setenv("ARIA_MAX_FRAME_AGE_MS", str(max_age_ms))
        settings = Settings(data_dir=tmp_path, save_frames=False)
        return TestClient(create_app(settings, engine=StubEngine(), load_model=False))

    return make


def plan(client, source, newest_ms):
    sid = client.post("/api/sessions", json={"goal": "Clear the mat", "source": source}).json()
    body = {"request_id": str(uuid.uuid4()), "frames": frames(newest_ms)}
    return client.post(f"/api/sessions/{sid['id']}/plan", json=body)


def test_recent_live_frames_are_accepted(client):
    response = plan(client(), "aria", time.time() * 1000 - 300)
    assert response.status_code == 200, response.text
    assert response.json()["instruction"] == "Pick up the pen."


def test_old_live_frames_are_rejected_with_age(client):
    response = plan(client(), "aria", time.time() * 1000 - 11_400)
    assert response.status_code == 422
    assert "Newest frame is 11.4 s old" in response.json()["detail"]


def test_future_live_frames_are_rejected(client):
    assert plan(client(), "webcam", time.time() * 1000 + 4000).status_code == 422


def test_zero_disables_the_check(client):
    assert plan(client(max_age_ms=0), "aria", time.time() * 1000 - 11_400).status_code == 200


def test_replay_frames_are_not_checked(client):
    assert plan(client(), "replay", 10_000).status_code == 200
