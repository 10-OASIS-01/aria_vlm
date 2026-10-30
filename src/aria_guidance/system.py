"""The ROS processes the console starts and stops: guidance_node, the glasses, the test camera.

Each runs in its own process group with aria2_ros2's environment and logs to .runtime/logs. Pid files
outlive the backend, so a backend restart finds them again instead of killing the glasses stream.
"""

import os
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

LOG_TAIL_LINES = 6


def ros_services(settings, aria_vlm_root):
    """{name: (label, shell command)} for this machine, from Settings.

    Args:
        settings: Settings; ros_workspace, fastdds_profile and fake_image are read.
        aria_vlm_root: the aria_vlm checkout, for tools/fake_camera.py.

    Returns:
        The services the console may start. Nothing else can be run.
    """
    workspace = settings.ros_workspace
    setup = f"source {shlex.quote(str(workspace / 'activate.sh'))}"
    if settings.fastdds_profile:
        # Big DDS buffers, or 640x480 frames are dropped and the 10 s window never fills.
        setup += (
            f" && export FASTRTPS_DEFAULT_PROFILES_FILE={shlex.quote(settings.fastdds_profile)}"
        )
    setup += f" && cd {shlex.quote(str(workspace))} && exec "
    config = shlex.quote(str(workspace / "src/aria_driver/config/aria.yaml"))
    image = (
        f" --ros-args -p image_path:={shlex.quote(settings.fake_image)}"
        if settings.fake_image
        else ""
    )
    camera = shlex.quote(str(aria_vlm_root / "tools" / "fake_camera.py"))
    return {
        "guidance": ("guidance_node", setup + "ros2 launch aria_guidance_ros guidance.launch.py"),
        # The Rerun viewer stays off: with it on, RGB frames are lost (see aria_node docs).
        "glasses": (
            "Aria glasses",
            setup + "ros2 run aria_driver aria_node --ros-args -r __node:=aria "
            f"--params-file {config} -p viewer.enable:=false",
        ),
        "test_camera": ("Test camera", setup + f"python {camera}{image}"),
    }


class ProcessManager:
    # Both publish /aria/rgb/image_raw; starting one stops the other.
    EXCLUSIVE = (frozenset({"glasses", "test_camera"}),)

    def __init__(self, services, run_dir, log_dir):
        """Args:
        services: {name: (label, shell command)}.
        run_dir: where pid files live.
        log_dir: where each service's log goes.
        """
        self.services = services
        self.run_dir, self.log_dir = Path(run_dir), Path(log_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.children, self.stopping, self.started = {}, set(), {}
        self.lock = threading.Lock()

    def pid(self, name):
        try:
            return int((self.run_dir / f"{name}.pid").read_text())
        except (OSError, ValueError):
            return None

    def alive(self, name):
        child = self.children.get(name)
        if child is not None:
            return child.poll() is None
        pid = self.pid(name)  # Started by an earlier backend.
        if pid is None:
            return False
        try:
            os.killpg(pid, 0)
            # A dead leader not yet reaped by its old parent still has a group.
            return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def status(self):
        """[{name, label, state, exit_code, log}]; state is running, stopping, stopped or failed."""
        rows = []
        with self.lock:
            for name, (label, _) in self.services.items():
                child, running = self.children.get(name), self.alive(name)
                code = None if running or child is None else child.returncode
                if running:
                    state = "stopping" if name in self.stopping else "running"
                else:
                    self.stopping.discard(name)
                    # SIGINT from Stop exits 130 or -2; anything else nonzero is a failure.
                    state = "failed" if code not in (None, 0, 130, -2, -15) else "stopped"
                rows.append(
                    {
                        "name": name,
                        "label": label,
                        "state": state,
                        "exit_code": code,
                        "started_at": self.started.get(name),
                        "log": self.log_tail(name) if state == "failed" else [],
                    }
                )
        return rows

    def log_tail(self, name):
        try:
            lines = (self.log_dir / f"{name}.log").read_text(errors="replace").splitlines()
        except OSError:
            return []
        return [line for line in lines if line.strip()][-LOG_TAIL_LINES:]

    def start(self, name):
        if name not in self.services:
            raise KeyError(name)
        for group in self.EXCLUSIVE:
            if name in group:
                for other in group - {name}:
                    self.stop(other)
        with self.lock:
            if self.alive(name):
                return
            with open(self.log_dir / f"{name}.log", "w") as log:
                child = subprocess.Popen(
                    ["bash", "-c", self.services[name][1]],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,  # Own group: survives a backend restart, stops as one.
                )
            self.children[name] = child
            self.started[name] = time.time()  # Lets the console dismiss one failure, not all.
            (self.run_dir / f"{name}.pid").write_text(str(child.pid))

    def stop(self, name, timeout=15.0):
        """Ctrl-C to the whole group (nodes close cleanly), then SIGKILL after `timeout` s."""
        if name not in self.services:
            raise KeyError(name)
        with self.lock:
            if not self.alive(name) or name in self.stopping:
                return
            pgid = self.children[name].pid if name in self.children else self.pid(name)
            self.stopping.add(name)
            os.killpg(pgid, signal.SIGINT)

        def reap():
            end = time.monotonic() + timeout
            while time.monotonic() < end and self.alive(name):
                time.sleep(0.2)
            if self.alive(name):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            with self.lock:
                self.stopping.discard(name)

        threading.Thread(target=reap, daemon=True, name=f"stop-{name}").start()
