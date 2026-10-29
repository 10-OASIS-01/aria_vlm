"""ROS fixtures: a real backend with a scripted model, a 5 Hz camera and the guidance node.

Run inside ROS: source aria2_ros2/activate.sh && python -m pytest aria_vlm/tests/integration
"""

import io
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

rclpy = pytest.importorskip("rclpy")

import uvicorn  # noqa: E402
from PIL import Image  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402
from std_msgs.msg import String  # noqa: E402

from aria_guidance.config import Settings  # noqa: E402
from aria_guidance.service import create_app  # noqa: E402
from aria_guidance_ros.guidance_node import GuidanceNode  # noqa: E402

# pytest 6 in the ROS venv ignores the pythonpath ini option.
ROOT = Path(__file__).parents[2]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "tools")]
from fake_camera import FakeCamera  # noqa: E402
from stubs import StubEngine  # noqa: E402


def wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out after {timeout} s")


class Backend:
    """The real FastAPI app on a free port; stop() and start() simulate a restart."""

    def __init__(self, data_dir):
        data_dir.mkdir()
        self.engine = StubEngine()
        self.settings = Settings(data_dir=data_dir, save_frames=False)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        app = create_app(self.settings, engine=self.engine, load_model=False)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        wait_for(lambda: self.server.started)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)


class Probe(Node):
    """Plays the operator: publishes like Foxglove panels and collects /aria/tts."""

    def __init__(self):
        super().__init__("probe")
        self.tts, self.pubs = [], {}
        self.create_subscription(String, "/aria/tts", lambda m: self.tts.append(m.data), 10)

    def publish(self, topic, text):
        if topic not in self.pubs:
            self.pubs[topic] = self.create_publisher(String, topic, 10)
        wait_for(lambda: self.pubs[topic].get_subscription_count() > 0)
        self.pubs[topic].publish(String(data=text))


@pytest.fixture(scope="session", autouse=True)
def ros():
    # A domain of its own, so live glasses on domain 42 never reach the tests.
    os.environ["ROS_DOMAIN_ID"] = "77"
    os.environ["ROS_LOCALHOST_ONLY"] = "1"
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def executor():
    executor = MultiThreadedExecutor()
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    yield executor
    executor.shutdown()
    thread.join(5)


@pytest.fixture
def backend(tmp_path):
    backend = Backend(tmp_path / "backend")
    backend.start()
    yield backend
    backend.stop()


@pytest.fixture
def node(executor, backend):
    node = GuidanceNode(parameter_overrides=[Parameter("backend_url", value=backend.url)])
    executor.add_node(node)
    yield node
    executor.remove_node(node)
    node.close()
    node.destroy_node()


@pytest.fixture
def camera(executor, node):
    """Prefills the node with a 10 s window, then publishes live 5 Hz frames."""
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buffer, "JPEG")
    now = time.time() * 1000
    for i in range(50):
        node.frames.add(now - 10_000 + i * 200, buffer.getvalue())
    camera = FakeCamera()
    executor.add_node(camera)
    yield camera
    executor.remove_node(camera)
    camera.destroy_node()


@pytest.fixture
def probe(executor):
    probe = Probe()
    executor.add_node(probe)
    yield probe
    executor.remove_node(probe)
    probe.destroy_node()
