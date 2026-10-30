import time

import pytest
from fastapi.testclient import TestClient
from stubs import StubEngine

from aria_guidance.config import Settings
from aria_guidance.service import create_app
from aria_guidance.system import ProcessManager, ros_services


def wait_state(manager, name, state, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        row = next(r for r in manager.status() if r["name"] == name)
        if row["state"] == state:
            return row
        time.sleep(0.05)
    raise AssertionError(f"{name} never reached {state}: {row}")


@pytest.fixture
def manager(tmp_path):
    services = {
        "guidance": ("guidance_node", "exec sleep 30"),
        "glasses": ("Aria glasses", "exec sleep 30"),
        "test_camera": ("Test camera", "trap 'exit 0' INT; while true; do sleep 0.1; done"),
        "broken": ("Broken", "echo 'Glasses not found over USB' >&2; exit 3"),
    }
    manager = ProcessManager(services, tmp_path / "run", tmp_path / "logs")
    yield manager
    for name in services:
        manager.stop(name, timeout=1)


def test_start_and_stop(manager):
    manager.start("guidance")
    wait_state(manager, "guidance", "running")
    manager.stop("guidance", timeout=2)
    wait_state(manager, "guidance", "stopped")


def test_glasses_and_test_camera_exclude_each_other(manager):
    manager.start("test_camera")
    wait_state(manager, "test_camera", "running")
    manager.start("glasses")
    wait_state(manager, "test_camera", "stopped")
    assert wait_state(manager, "glasses", "running")


def test_failure_shows_the_log_tail(manager):
    manager.start("broken")
    row = wait_state(manager, "broken", "failed")
    assert row["exit_code"] == 3 and row["log"] == ["Glasses not found over USB"]


def test_a_restarted_backend_finds_running_processes(manager, tmp_path):
    manager.start("glasses")
    wait_state(manager, "glasses", "running")
    again = ProcessManager(manager.services, tmp_path / "run", tmp_path / "logs")
    assert wait_state(again, "glasses", "running")
    again.stop("glasses", timeout=1)
    wait_state(again, "glasses", "stopped")


def test_commands_source_the_ros_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_FASTDDS_PROFILE", "/x/fastdds.xml")
    monkeypatch.setenv("ARIA_FAKE_IMAGE", "/x/scene.jpg")
    settings = Settings(data_dir=tmp_path, ros_workspace=tmp_path / "ws")
    services = ros_services(settings, tmp_path / "aria_vlm")
    assert set(services) == {"guidance", "glasses", "test_camera"}
    glasses = services["glasses"][1]
    assert f"source {tmp_path}/ws/activate.sh" in glasses and "viewer.enable:=false" in glasses
    assert "FASTRTPS_DEFAULT_PROFILES_FILE=/x/fastdds.xml" in glasses
    assert services["test_camera"][1].endswith(
        "fake_camera.py --ros-args -p image_path:=/x/scene.jpg"
    )


def test_api_runs_only_known_processes(tmp_path, manager):
    settings = Settings(data_dir=tmp_path, save_frames=False)
    client = TestClient(create_app(settings, StubEngine(), load_model=False, system=manager))
    assert client.post("/api/system/rm -rf/start").status_code == 404
    assert client.post("/api/system/guidance/restart").status_code == 404
    rows = client.post("/api/system/guidance/start").json()
    assert next(r for r in rows if r["name"] == "guidance")["state"] == "running"
    assert {r["name"] for r in client.get("/api/system").json()} == set(manager.services)


def test_log_tail_reads_only_the_end_of_a_long_log_without_colours(manager, tmp_path):
    (tmp_path / "logs" / "guidance.log").write_text(
        "first line, far too old\n" + "x" * 200_000 + "\n\x1b[31mfailed\x1b[0m here\n\nlast\n"
    )
    assert manager.log_tail("guidance", 2) == ["failed here", "last"]
    assert len(manager.log_tail("guidance", 500)) == 2  # The cut-off line is not shown.


def test_processes_write_python_output_straight_through(tmp_path):
    services = {"echo": ("Echo", "echo unbuffered=$PYTHONUNBUFFERED")}
    manager = ProcessManager(services, tmp_path / "run", tmp_path / "logs")
    manager.start("echo")
    wait_state(manager, "echo", "stopped")
    assert manager.log_tail("echo") == ["unbuffered=1"]


def test_log_route_serves_a_known_process_only(manager, tmp_path, monkeypatch):
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    settings = Settings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings, StubEngine(), load_model=False, system=manager))
    assert client.get("/api/system/guidance/log").json() == {"exists": False, "lines": []}
    (tmp_path / "logs" / "guidance.log").write_text("".join(f"line {i}\n" for i in range(300)))
    body = client.get("/api/system/guidance/log").json()
    assert body["exists"] and len(body["lines"]) == 200 and body["lines"][-1] == "line 299"
    assert client.get("/api/system/nope/log").status_code == 404
    assert client.get("/api/system/..%2F..%2Fsecret/log").status_code == 404
