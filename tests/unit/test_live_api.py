import pytest
from fastapi.testclient import TestClient
from stubs import StubEngine

from aria_guidance import live
from aria_guidance.config import Settings
from aria_guidance.service import create_app

JPEG = b"\xff\xd8\xff\xe0 jpeg"


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path, save_frames=False)
    return TestClient(create_app(settings, engine=StubEngine(), load_model=False))


def test_state_flows_from_node_to_console(client):
    assert client.get("/api/live/state").json() == {"version": 0, "node_online": False}
    body = {"state": "planning", "task": "planning", "goal": "Clear the mat"}
    assert client.post("/v1/live/state", json=body).json() == {"version": 1}
    client.post("/v1/live/state", json=body)  # A heartbeat does not bump the version.
    state = client.get("/api/live/state", params={"after": 0}).json()
    assert state == {**body, "version": 1, "node_online": True}


def test_state_wait_times_out_with_the_current_state(client):
    client.post("/v1/live/state", json={"state": "idle"})
    state = client.get("/api/live/state", params={"after": 1, "wait": 0.1}).json()
    assert state["version"] == 1 and state["state"] == "idle"


def test_commands_queue_and_drain(client):
    client.post("/v1/live/state", json={"state": "idle"})
    assert (
        client.post("/api/live/command", json={"kind": "goal", "text": "Clear it"}).status_code
        == 202
    )
    client.post("/api/live/command", json={"kind": "talk", "text": "on"})
    commands = client.get("/v1/live/commands", params={"wait": 0}).json()
    assert commands == [{"kind": "goal", "text": "Clear it"}, {"kind": "talk", "text": "on"}]
    assert client.get("/v1/live/commands", params={"wait": 0}).json() == []


def test_commands_are_refused_while_the_node_is_away(client, monkeypatch):
    assert (
        client.post("/api/live/command", json={"kind": "reply", "text": "done"}).status_code == 503
    )
    client.post("/v1/live/state", json={"state": "idle"})
    monkeypatch.setattr(live, "NODE_TIMEOUT_S", 0.0)
    assert (
        client.post("/api/live/command", json={"kind": "reply", "text": "done"}).status_code == 503
    )


def test_bad_commands_are_rejected(client):
    client.post("/v1/live/state", json={"state": "idle"})
    assert client.post("/api/live/command", json={"kind": "shell", "text": "ls"}).status_code == 422
    assert (
        client.post("/api/live/command", json={"kind": "note", "text": "x" * 1501}).status_code
        == 422
    )


def test_preview_round_trip(client):
    assert client.get("/api/live/preview.jpg").status_code == 404
    assert client.put("/v1/live/preview", content=b"not a jpeg").status_code == 422
    client.put("/v1/live/preview", content=JPEG)
    response = client.get("/api/live/preview.jpg")
    assert response.content == JPEG and response.headers["content-type"] == "image/jpeg"


def test_node_writes_need_the_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_API_TOKEN", "secret")
    settings = Settings(data_dir=tmp_path, save_frames=False)
    client = TestClient(create_app(settings, engine=StubEngine(), load_model=False))
    assert client.post("/v1/live/state", json={"state": "idle"}).status_code == 401
    assert client.put("/v1/live/preview", content=JPEG).status_code == 401
