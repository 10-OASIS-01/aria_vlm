from conftest import wait_for


def test_goal_reaches_tts(probe, node, camera, backend):
    probe.publish("/planner/goal", "Pick up the pen")
    wait_for(lambda: probe.tts)
    assert probe.tts == ["Pick up the pen."]
    assert backend.engine.calls[0]["goal"] == "Pick up the pen"
