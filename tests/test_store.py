import pytest

from aria_guidance.store import Conflict, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def plan(store, sid, status, rid="plan-1"):
    session = store.reserve(sid, rid, "fingerprint")
    response = {"status": status, "instruction": "The task looks complete."}
    assert store.finish(sid, rid, session["version"], response)
    return store.get_session(sid)


def test_done_proposal_waits_for_confirmation(store):
    sid = store.create_session("Clear the mat", "aria")["id"]
    assert plan(store, sid, "done_proposed")["state"] == "awaiting_confirmation"
    assert store.feedback(sid, "confirm")["state"] == "completed"


def test_instruction_awaits_feedback(store):
    sid = store.create_session("Clear the mat", "aria")["id"]
    assert plan(store, sid, "instruction")["state"] == "awaiting_feedback"


@pytest.mark.parametrize("status", [None, "instruction"])
def test_confirm_without_proposal_is_rejected(store, status):
    sid = store.create_session("Clear the mat", "aria")["id"]
    if status:
        plan(store, sid, status)
    with pytest.raises(Conflict, match="Nothing to confirm"):
        store.feedback(sid, "confirm")


def test_feedback_after_proposal_rejects_completion(store):
    sid = store.create_session("Clear the mat", "aria")["id"]
    plan(store, sid, "done_proposed")
    session = store.feedback(sid, "next", "The pen is still on the mat.")
    assert session["state"] == "ready"
    assert session["history"][-1] == {
        "role": "user",
        "text": "No, the goal is not complete yet. The pen is still on the mat.",
    }


def test_cancel_invalidates_generation_in_flight(store):
    sid = store.create_session("Clear the mat", "aria")["id"]
    session = store.reserve(sid, "plan-1", "fingerprint")
    store.feedback(sid, "cancel")
    response = {"status": "instruction", "instruction": "Pick up the pen."}
    assert not store.finish(sid, "plan-1", session["version"], response)
    assert store.get_session(sid)["state"] == "cancelled"
    assert store.plan("plan-1")["state"] == "discarded"


def test_failure_returns_session_to_ready(store):
    sid = store.create_session("Clear the mat", "aria")["id"]
    session = store.reserve(sid, "plan-1", "fingerprint")
    store.fail(sid, "plan-1", session["version"], "ValueError: Model echoed the goal")
    assert store.get_session(sid)["state"] == "ready"
    assert store.plan("plan-1")["error"] == "ValueError: Model echoed the goal"
