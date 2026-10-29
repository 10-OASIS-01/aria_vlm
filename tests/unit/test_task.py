import pytest

from aria_guidance.task import MAX_HISTORY, Task, parse_reply


def planned(status="instruction", instruction="Pick up the pen."):
    task = Task()
    task.start("Clear the mat")
    epoch, _, _ = task.begin_plan()
    assert task.finish_plan(epoch, {"status": status, "instruction": instruction})
    return task


def test_done_proposal_waits_for_confirmation():
    task = planned("done_proposed", "The task looks complete.")
    assert task.state == "awaiting_confirmation"
    assert parse_reply("Done.", task) == ("confirm", "Done.")
    assert task.confirm() and task.state == "completed" and not task.active


def test_next_after_done_rejects_completion():
    task = planned("done_proposed")
    assert parse_reply("next", task) == ("feedback", "next")
    assert task.feedback("The pen is still on the mat.")
    assert task.state == "ready"
    assert task.history[-1] == {
        "role": "user",
        "text": "No, the goal is not complete yet. The pen is still on the mat.",
    }


def test_done_without_proposal_is_feedback():
    task = planned()
    assert parse_reply("done", task) == ("feedback", "done")
    assert not task.confirm()


def test_cancel_drops_result_in_flight():
    task = Task()
    task.start("Clear the mat")
    epoch, _, _ = task.begin_plan()
    task.cancel()
    assert task.epoch == epoch + 1
    assert not task.finish_plan(epoch, {"status": "instruction", "instruction": "Go."})
    assert task.state == "cancelled" and task.history == []


def test_new_goal_drops_old_result_and_history():
    task = planned()
    old = task.epoch
    task.start("Stack the cups")
    assert task.history == [] and task.state == "ready" and task.last is None
    assert not task.finish_plan(old, {"status": "instruction", "instruction": "Go."})


def test_failed_plan_replans_on_reply():
    task = Task()
    task.start("Clear the mat")
    epoch, _, _ = task.begin_plan()
    task.fail_plan(epoch)
    assert task.state == "error" and task.begin_plan() is None
    assert task.feedback("next") and task.begin_plan() is not None


def test_feedback_refused_while_planning():
    task = Task()
    task.start("Clear the mat")
    task.begin_plan()
    assert not task.feedback("next")


def test_history_is_capped():
    task = planned()
    for i in range(MAX_HISTORY):
        task.feedback(f"reply {i}")
        epoch, _, _ = task.begin_plan()
        task.finish_plan(epoch, {"status": "instruction", "instruction": f"step {i}"})
    assert len(task.history) == MAX_HISTORY
    assert task.history[-1] == {"role": "assistant", "text": f"step {MAX_HISTORY - 1}"}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Cancel.", ("cancel", "Cancel.")),
        ("停止", ("cancel", "停止")),
        ("repeat", ("repeat", "repeat")),
        ("再说一遍", ("repeat", "再说一遍")),
        ("goal: stack the cups", ("goal", "stack the cups")),
        ("Note: the cup slipped", ("note", "the cup slipped")),
        ("下一步", ("feedback", "下一步")),
        ("The pen is behind the box", ("feedback", "The pen is behind the box")),
    ],
)
def test_reply_commands_during_a_task(text, expected):
    assert parse_reply(text, planned()) == expected


def test_reply_without_task():
    task = Task()
    assert parse_reply("Pick up the pen", task) == ("goal", "Pick up the pen")
    assert parse_reply("next", task)[0] == "none"
    assert parse_reply("  ", task)[0] == "none"


def test_finish_closes_any_active_task_and_drops_a_plan_in_flight():
    task = planned()
    assert parse_reply("Finish.", task) == ("finish", "Finish.")
    assert task.finish() and task.state == "completed" and not task.active
    assert not task.finish()
    task.start("Stack the cups")
    epoch, _, _ = task.begin_plan()
    assert task.finish()
    assert not task.finish_plan(epoch, {"status": "instruction", "instruction": "Go."})


def test_finish_without_a_task_is_nothing():
    assert parse_reply("finish", Task())[0] == "none"
