import pytest

from aria_guidance.engine import parse_output

GOAL = "Put the orange pen into the green mug."


@pytest.mark.parametrize("raw", ["DONE", "done.", "Done!"])
def test_done_only_proposes_completion(raw):
    assert parse_output(raw, GOAL) == ("The task looks complete.", "done_proposed")


def test_sentence_mentioning_done_is_an_instruction():
    text = "Place the pen in the mug and you are done."
    assert parse_output(text, GOAL) == (text, "instruction")


def test_ask_prefix_is_a_clarification():
    expected = ("Which mug is green?", "clarification")
    assert parse_output("ASK: Which mug is green?", GOAL) == expected


def test_empty_clarification_is_rejected():
    with pytest.raises(ValueError, match="empty clarification"):
        parse_output("ASK:", GOAL)


@pytest.mark.parametrize("raw", ["ASK: Put the orange pen into the green mug?", f"{GOAL[:-1]}?"])
def test_goal_echoed_as_question_is_rejected(raw):
    with pytest.raises(ValueError, match="echoed the goal"):
        parse_output(raw, GOAL)


def test_goal_repeated_as_instruction_is_allowed():
    assert parse_output(GOAL, GOAL) == (GOAL, "instruction")


def test_long_output_is_not_spoken():
    with pytest.raises(ValueError, match="too long"):
        parse_output(" ".join(["word"] * 46), GOAL)


@pytest.mark.parametrize("raw", ["", "<think>planning", "<think>planning</think>"])
def test_reasoning_or_empty_output_is_rejected(raw):
    with pytest.raises(ValueError, match="no usable instruction"):
        parse_output(raw, GOAL)


def test_closed_reasoning_is_stripped():
    assert parse_output("<think>scene</think>Pick up the pen.", GOAL) == (
        "Pick up the pen.",
        "instruction",
    )
