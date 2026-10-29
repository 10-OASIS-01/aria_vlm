import time

from conftest import wait_for


def heard(probe, node, count):
    """Waits for the count-th TTS, then lifts the speech guard so the next reply is spoken."""
    wait_for(lambda: len(probe.tts) >= count)
    node.speaking_until = 0.0
    return probe.tts[count - 1]


def test_goal_speaks_and_publishes_state(probe, node, camera, backend):
    probe.publish("/guidance/goal", "Pick up the pen")
    assert heard(probe, node, 1) == "Pick up the pen."
    assert backend.engine.calls[0]["goal"] == "Pick up the pen"
    wait_for(lambda: probe.state().get("task") == "awaiting_feedback")
    assert probe.state()["instruction"] == "Pick up the pen."


def test_done_sends_feedback_and_confirm_completes(probe, node, camera, backend):
    probe.publish("/guidance/goal", "Pick up the pen")
    heard(probe, node, 1)
    backend.engine.reply = "DONE"
    probe.publish("/guidance/reply", "done")
    assert heard(probe, node, 2).startswith("The task looks complete.")
    assert backend.engine.calls[1]["history"] == [
        {"role": "assistant", "text": "Pick up the pen."},
        {"role": "user", "text": "done"},
    ]
    probe.publish("/guidance/reply", "done")
    assert heard(probe, node, 3) == "Task complete."
    wait_for(lambda: probe.state().get("task") == "completed")
    assert len(backend.engine.calls) == 2


def test_cancel_during_slow_inference_drops_the_result(probe, node, camera, backend):
    backend.engine.delay = 1.5
    probe.publish("/guidance/goal", "Pick up the pen")
    wait_for(lambda: backend.engine.calls)
    probe.publish("/guidance/reply", "cancel")
    wait_for(lambda: not node.planning)
    time.sleep(0.5)
    assert probe.tts == []
    assert probe.state()["task"] == "cancelled"


def test_backend_restart_keeps_the_task(probe, node, camera, backend):
    probe.publish("/guidance/goal", "Pick up the pen")
    heard(probe, node, 1)
    backend.stop()
    probe.publish("/guidance/reply", "next")
    wait_for(lambda: probe.state().get("task") == "error")
    backend.start()
    probe.publish("/guidance/reply", "next")
    heard(probe, node, 2)
    call = backend.engine.calls[-1]
    assert call["goal"] == "Pick up the pen"
    assert call["history"][0] == {"role": "assistant", "text": "Pick up the pen."}
    assert [t["text"] for t in call["history"][1:]] == ["next", "next"]


def test_stopped_camera_sends_nothing(probe, node, camera, backend, executor):
    executor.remove_node(camera)
    time.sleep(1.7)  # FrameBuffer calls a window stale once its newest frame is 1.5 s old.
    probe.publish("/guidance/goal", "Pick up the pen")
    wait_for(lambda: "stale" in probe.state().get("detail", ""))
    assert backend.engine.calls == []
    assert probe.tts == []
